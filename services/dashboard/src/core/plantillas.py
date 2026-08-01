"""Motor de plantillas y helpers compartidos por todas las rutas."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.templating import Jinja2Templates

from src.core import navegacion, security
from src.core.config import settings

plantillas = Jinja2Templates(directory="src/templates")


def _usd(microusd: Any) -> str:
    """Micro-USD (entero) a dólares legibles."""
    try:
        return f"${int(microusd or 0) / 1_000_000:,.4f}"
    except (TypeError, ValueError):
        return "$0.0000"


# Zona horaria de quien lee el panel, no la del servidor: un chat revisado a
# las 9 de la mañana en Costa Rica tiene que decir 9, esté el contenedor donde
# esté. Si el nombre de zona fuera inválido se usa la del sistema en vez de
# tumbar el arranque por un detalle de presentación.
try:
    _ZONA = ZoneInfo(settings.ZONA_HORARIA)
except ZoneInfoNotFoundError:
    print(f"AVISO: zona horaria desconocida '{settings.ZONA_HORARIA}'; se usa la del servidor.")
    _ZONA = None


def _local(valor: datetime) -> datetime:
    return valor.astimezone(_ZONA) if _ZONA else valor.astimezone()


_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fecha(valor: Any) -> str:
    if isinstance(valor, datetime):
        return _local(valor).strftime("%d/%m/%Y %H:%M")
    return str(valor or "")


def _hora(valor: Any) -> str:
    if isinstance(valor, datetime):
        return _local(valor).strftime("%H:%M")
    return str(valor or "")


def _dia_largo(valor: Any) -> str:
    """'martes 8 de julio de 2026', para separar los días en el visor de chats."""
    if not isinstance(valor, datetime):
        return str(valor or "")
    local = _local(valor)
    return f"{_DIAS[local.weekday()]} {local.day} de {_MESES[local.month - 1]} de {local.year}"


def _dia_clave(valor: Any) -> str:
    """Clave estable del día local, para agrupar mensajes sin repetir formato."""
    if isinstance(valor, datetime):
        return _local(valor).strftime("%Y-%m-%d")
    return str(valor or "")


plantillas.env.filters["usd"] = _usd
plantillas.env.filters["fecha"] = _fecha
plantillas.env.filters["hora"] = _hora
plantillas.env.filters["dia_largo"] = _dia_largo
plantillas.env.filters["dia_clave"] = _dia_clave


def render(request: Request, nombre: str, usuario: dict[str, Any] | None, **contexto):
    """Renderiza una página con el contexto común (usuario, CSRF, avisos)."""
    token_sesion = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    es_admin = bool(usuario and usuario["rol"] == security.ROL_ADMIN)
    secciones = navegacion.secciones_para(es_admin, request.url.path) if usuario else []
    seccion_actual, pagina_actual = navegacion.ubicacion(secciones)
    datos = {
        "request": request,
        "usuario": usuario,
        "csrf": security.token_csrf(token_sesion) if token_sesion else "",
        "es_admin": es_admin,
        "secciones": secciones,
        "seccion_actual": seccion_actual,
        "pagina_actual": pagina_actual,
        "aviso": request.query_params.get("aviso", ""),
        "error": request.query_params.get("error", ""),
        **contexto,
    }
    # Starlette moderno espera (request, nombre, contexto): la firma vieja
    # (nombre, contexto) interpreta el nombre como el request.
    return plantillas.TemplateResponse(request, nombre, datos)
