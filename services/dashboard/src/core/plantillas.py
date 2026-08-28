"""Motor de plantillas y helpers compartidos por todas las rutas."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from src.core import navegacion, security
from src.core.config import settings
from src.services import clientes_whatsapp, mensajeria, trazabilidad

plantillas = Jinja2Templates(directory="src/templates")


def _usd(microusd: Any) -> str:
    """Micro-USD (entero) a dólares legibles, con los decimales que hagan falta.

    Los decimales se ADAPTAN al tamaño. Con un formato fijo de 4 decimales, un
    consumo real de $0.00008 se mostraba como "$0.0001" y uno de $0.00002 como
    "$0.0000" — es decir, gratis. A esta escala se trabaja: transcribir una nota
    de voz de 8 segundos cuesta $0.0008, y un turno cacheado del supervisor
    ronda los $0.0006. Redondearlos a cero le esconde al negocio justo lo que
    está pagando.

    Al revés también: mostrar "$12.3400" en un total del mes es ruido. Por eso
    los importes grandes se quedan en dos decimales, como cualquier factura.
    """
    try:
        valor = int(microusd or 0) / 1_000_000
    except (TypeError, ValueError):
        return "$0.00"

    magnitud = abs(valor)
    if magnitud >= 1:
        return f"${valor:,.2f}"
    if magnitud >= 0.01:
        return f"${valor:,.4f}"
    if valor == 0:
        return "$0.00"
    # Por debajo de un centavo: 6 decimales llegan hasta el micro-USD, que es la
    # unidad mínima con la que el sistema guarda el dinero. Más sería inventado.
    return f"${valor:,.6f}"


# Zona horaria de quien lee el panel, no la del servidor: un chat revisado a
# las 9 de la mañana en Costa Rica tiene que decir 9, esté el contenedor donde
# esté. Si el nombre de zona fuera inválido se usa la del sistema en vez de
# tumbar el arranque por un detalle de presentación.
#
# Esta es la de TODO EL DESPLIEGUE y solo se usa como respaldo. La que manda es
# la del PROYECTO (`clientes_whatsapp.zona_horaria`), porque el reloj que
# importa es el de su negocio: un reporte que llegó a las 9 de la mañana en
# Costa Rica tiene que decir 9 aunque el servidor esté en Fráncfort. Ver
# `_zona_de_contexto`.
try:
    _ZONA_DESPLIEGUE = ZoneInfo(settings.ZONA_HORARIA)
except ZoneInfoNotFoundError:
    print(f"AVISO: zona horaria desconocida '{settings.ZONA_HORARIA}'; se usa la del servidor.")
    _ZONA_DESPLIEGUE = None

# Las zonas ya resueltas. `ZoneInfo` lee del disco la primera vez y no hace
# falta repetirlo por cada fecha de una tabla de doscientas filas.
_ZONAS: dict[str, ZoneInfo | None] = {}


def _zona_de_contexto(contexto: Any) -> ZoneInfo | None:
    """La zona del proyecto que se está mirando, o la del despliegue.

    El proyecto viaja en el contexto de la plantilla (lo pone `render`), así que
    los filtros lo leen de ahí en vez de recibirlo página por página: un filtro
    de fecha que hubiera que pasarle la zona en cada `| fecha` se olvidaría en la
    mitad de los sitios, que es justo cómo se cuelan las horas equivocadas.
    """
    proyecto = None
    try:
        proyecto = contexto.get("proyecto")
    except AttributeError:
        pass

    nombre = str((proyecto or {}).get("zona_horaria") or "")
    if not nombre:
        return _ZONA_DESPLIEGUE
    if nombre not in _ZONAS:
        try:
            _ZONAS[nombre] = ZoneInfo(nombre)
        except ZoneInfoNotFoundError:
            # El campo es una lista cerrada en el panel, pero un valor viejo en
            # la base no debe dejar la página en blanco por una hora mal puesta.
            print(f"AVISO: zona horaria desconocida '{nombre}'; se usa la del despliegue.")
            _ZONAS[nombre] = _ZONA_DESPLIEGUE
    return _ZONAS[nombre]


def _local(valor: datetime, zona: ZoneInfo | None = None) -> datetime:
    zona = zona if zona is not None else _ZONA_DESPLIEGUE
    return valor.astimezone(zona) if zona else valor.astimezone()


_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


# Los cuatro filtros de fecha reciben el contexto (`pass_context`) para poder
# leer de él el proyecto y, con él, su zona horaria. Sin esto, la zona sería la
# del despliegue en todas partes y el campo «zona horaria» del proyecto no
# serviría para nada.

@pass_context
def _fecha(contexto: Any, valor: Any) -> str:
    if isinstance(valor, datetime):
        return _local(valor, _zona_de_contexto(contexto)).strftime("%d/%m/%Y %H:%M")
    return str(valor or "")


@pass_context
def _hora(contexto: Any, valor: Any) -> str:
    if isinstance(valor, datetime):
        return _local(valor, _zona_de_contexto(contexto)).strftime("%H:%M")
    return str(valor or "")


@pass_context
def _dia_largo(contexto: Any, valor: Any) -> str:
    """'martes 8 de julio de 2026', para separar los días en el visor de chats."""
    if not isinstance(valor, datetime):
        return str(valor or "")
    local = _local(valor, _zona_de_contexto(contexto))
    return f"{_DIAS[local.weekday()]} {local.day} de {_MESES[local.month - 1]} de {local.year}"


@pass_context
def _dia_clave(contexto: Any, valor: Any) -> str:
    """Clave estable del día local, para agrupar mensajes sin repetir formato.

    Tiene que usar la MISMA zona que `_dia_largo`: si una agrupara por el día del
    servidor y la otra rotulara con el del proyecto, un mensaje de las 11 de la
    noche aparecería bajo la cabecera del día siguiente.
    """
    if isinstance(valor, datetime):
        return _local(valor, _zona_de_contexto(contexto)).strftime("%Y-%m-%d")
    return str(valor or "")


def _tiempo(minutos: Any) -> str:
    """«1440» → «1 d». Los minutos son la unidad; esto solo los hace legibles.

    La conversión vive en el servicio de palabras clave (es suya) y aquí solo se
    expone como filtro: escribirla otra vez en Jinja sería tenerla en dos sitios
    que se pueden separar.
    """
    from src.services.palabras_clave import como_texto

    return como_texto(minutos)


plantillas.env.filters["tiempo"] = _tiempo
plantillas.env.filters["usd"] = _usd
plantillas.env.filters["fecha"] = _fecha
plantillas.env.filters["hora"] = _hora
plantillas.env.filters["dia_largo"] = _dia_largo
plantillas.env.filters["dia_clave"] = _dia_clave


def _proyecto_de(
    request: Request, usuario: dict[str, Any] | None, es_admin: bool
) -> dict[str, Any] | None:
    """El proyecto al que pertenece la cuenta con la que se está dentro.

    Va en el contexto común y no en cada ruta porque lo pinta el ARMAZÓN (la
    marca del lateral y la ventana de «Mi cuenta»), que está en todas las
    páginas. Sin esto, el panel no decía en ningún sitio dentro de qué proyecto
    estabas: se leía «cliente» como rol y el nombre del proyecto no aparecía.

    Para el administrador es `None` a propósito: no es de ningún proyecto, es de
    la plataforma. Es una consulta por índice único y solo para cuentas de
    proyecto, así que no se le cobra al administrador ni una.
    """
    if not usuario or es_admin:
        return None
    if usuario.get("_proyecto"):
        return usuario["_proyecto"]
    if getattr(request.state, "proyecto_resuelto", False):
        return getattr(request.state, "proyecto_actual", None)
    proyecto = clientes_whatsapp.por_usuario(usuario["id"])
    request.state.proyecto_actual = proyecto
    request.state.proyecto_resuelto = True
    return proyecto


def pendientes_de(es_admin: bool, proyecto: dict[str, Any] | None = None) -> dict[str, int]:
    """Cuántas cosas sin atender tiene cada página del menú, para su pastilla.

    Solo lo que se puede quedar esperando sin que nadie lo mire: son bandejas
    que llena el bot. Son dos cuentas sobre tablas cuyo contenido atendido se
    purga solo a las 24 horas, no un recuento de todo el histórico.
    """
    if es_admin:
        return {"/admin/incidencias": mensajeria.contar_incidencias_abiertas()}
    if not proyecto:
        return {}
    conteos = trazabilidad.contar_pendientes(proyecto["id"])
    return {"/reportes": conteos["reportes"], "/preguntas": conteos["preguntas"]}


def render(request: Request, nombre: str, usuario: dict[str, Any] | None, **contexto):
    """Renderiza una página con el contexto común (usuario, CSRF, avisos)."""
    token_sesion = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    es_admin = bool(usuario and usuario["rol"] == security.ROL_ADMIN)
    secciones = navegacion.secciones_para(es_admin, request.url.path) if usuario else []
    seccion_actual, pagina_actual = navegacion.ubicacion(secciones)
    # Si la ruta ya tuvo que resolver el proyecto, se reutiliza: sobrescribirlo
    # al final del diccionario no evitaba que antes se hiciera otra consulta.
    proyecto_actual = contexto.get("proyecto") if "proyecto" in contexto else _proyecto_de(
        request, usuario, es_admin
    )
    datos = {
        "request": request,
        "usuario": usuario,
        "csrf": security.token_csrf(token_sesion) if token_sesion else "",
        "es_admin": es_admin,
        # El proyecto de la cuenta actual. Lo usa el armazón (marca del lateral
        # y ventana de «Mi cuenta»); una página puede sobrescribirlo por su
        # contexto si necesita otro, igual que cualquier otra variable.
        "proyecto": proyecto_actual,
        "secciones": secciones,
        "seccion_actual": seccion_actual,
        "pagina_actual": pagina_actual,
        # Las pastillas del menú lateral. Una página puede pasar las suyas (la
        # ruta del fragmento lo hace) y entonces mandan las suyas.
        "pendientes": pendientes_de(es_admin, proyecto_actual) if usuario else {},
        # Tramos navegables de la cabecera. Una página de detalle (el perfil de
        # un cliente) añade el suyo pasando `miga_final`, y así «Clientes» sigue
        # llevando al listado en vez de quedarse como texto muerto.
        "migas": navegacion.migas(es_admin, request.url.path) if usuario else [],
        "miga_final": "",
        "aviso": request.query_params.get("aviso", ""),
        "error": request.query_params.get("error", ""),
        **contexto,
    }
    # Starlette moderno espera (request, nombre, contexto): la firma vieja
    # (nombre, contexto) interpreta el nombre como el request.
    return plantillas.TemplateResponse(request, nombre, datos)


def render_fragmento(
    request: Request, nombre: str, usuario: dict[str, Any] | None, **contexto
):
    """Renderiza HTML parcial sin reconstruir el armazón de la página.

    Los fragmentos nunca pintan menú, migas ni pastillas de pendientes. Antes
    pagaban igualmente la búsqueda del proyecto y dos COUNT del lateral en cada
    refresco en vivo. Solo se conserva el contexto realmente compartido: sesión,
    CSRF, avisos y el proyecto que la ruta ya conozca para los filtros de fecha.
    """
    token_sesion = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    datos = {
        "request": request,
        "usuario": usuario,
        "csrf": security.token_csrf(token_sesion) if token_sesion else "",
        "es_admin": bool(usuario and usuario["rol"] == security.ROL_ADMIN),
        "proyecto": contexto.get("proyecto"),
        "aviso": request.query_params.get("aviso", ""),
        "error": request.query_params.get("error", ""),
        **contexto,
    }
    return plantillas.TemplateResponse(request, nombre, datos)
