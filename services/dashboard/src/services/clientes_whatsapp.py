"""Clientes (negocios) que conectan su WhatsApp, y la URL de webhook de cada uno.

Esta tabla representa PROYECTOS, no a la persona que escribe al bot. El cliente
final vive dentro de su proyecto, en conversaciones y `seguimiento_clientes`.

Lo que se administra desde aquí es una credencial: el token de la URL es lo
único que autentica los eventos que entran al bot. Por eso se puede rotar sin
redeplegar y por eso desactivar un cliente corta su webhook al instante.
"""

import re
import secrets
import unicodedata
from typing import Any

import httpx

from src.core.config import settings
from src.db import pool

# Eventos que hay que marcar en WasenderAPI para que el flujo funcione. Se
# declaran aquí (y no solo en la plantilla) porque son parte del contrato con
# el proveedor: si el bot deja de recibir uno, esta lista es dónde mirar.
EVENTOS_REQUERIDOS: list[dict[str, str]] = [
    {
        "nombre": "messages.received",
        "para": "Los mensajes que escriben los clientes. Sin esto el bot no contesta nada.",
    },
    {
        "nombre": "message.sent",
        "para": (
            "Los mensajes que salen del número. Es como se detecta que el dueño "
            "escribió desde su teléfono: ahí el bot se calla y el chat queda "
            "bloqueado 12 días."
        ),
    },
    {
        "nombre": "group-participants.update",
        "para": (
            "Ingresos al grupo del curso. Al entrar, se cancelan los recordatorios "
            "pendientes de la publicidad y el chat queda bloqueado 12 días."
        ),
    },
    {
        "nombre": "session.status",
        "para": "Avisa si la sesión de WhatsApp se desconecta. Opcional pero recomendado.",
    },
]

# Eventos que NO conviene activar, con el motivo. Marcar de más no es gratis:
# cada evento es una petición al webhook y algunos duplican lo que ya llega.
EVENTOS_DESACONSEJADOS: list[dict[str, str]] = [
    {
        "nombre": "messages.upsert",
        "para": "Duplica messages.received y message.sent: el mismo mensaje llegaría dos veces.",
    },
    {
        "nombre": "messages-group.received",
        "para": "Mensajes DENTRO del grupo. El bot no atiende grupos; solo interesa quién entra.",
    },
    {
        "nombre": "message-receipt.update",
        "para": "Recibos de entrega y lectura. Es muchísimo tráfico y no cambia ninguna decisión.",
    },
]


def _slug(nombre: str) -> str:
    """'Escuela de Manejo' -> 'escuela-de-manejo'."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", str(nombre or "")) if unicodedata.category(c) != "Mn"
    )
    limpio = re.sub(r"[^a-z0-9]+", "-", sin_tildes.lower()).strip("-")
    return limpio[:60] or "cliente"


def _token_nuevo() -> str:
    """64 caracteres de un generador criptográfico: la credencial del webhook."""
    return secrets.token_hex(32)


def url_webhook(token: str) -> str:
    base = (settings.PUBLIC_WEBHOOK_BASE_URL or "").rstrip("/")
    return f"{base}/webhooks/wasender/{token}"


def base_configurada() -> bool:
    return bool(settings.PUBLIC_WEBHOOK_BASE_URL)


# Husos con los que se trabaja hoy. Se ofrecen como lista cerrada en vez de un
# campo libre: un nombre de zona mal escrito no falla al guardar, falla después
# al mostrar las horas, y ahí ya nadie lo relaciona con esto.
ZONAS_HORARIAS = [
    "America/Costa_Rica",
    "America/Guayaquil",
    "America/Bogota",
    "America/Mexico_City",
    "America/Santiago",
    "America/Argentina/Buenos_Aires",
    "Europe/Madrid",
    "UTC",
]


def _con_url(fila: dict[str, Any] | None) -> dict[str, Any] | None:
    if fila:
        fila["url_webhook"] = url_webhook(fila["webhook_token"])
    return fila


def listar() -> list[dict[str, Any]]:
    filas = pool.consultar(
        """
        SELECT c.*, u.usuario AS cuenta
        FROM clientes_whatsapp c
        LEFT JOIN dashboard_usuarios u ON u.id = c.usuario_id
        ORDER BY c.nombre
        """
    )
    for fila in filas:
        _con_url(fila)
    return filas


def obtener(negocio_id: int) -> dict[str, Any] | None:
    return _con_url(
        pool.consultar_uno(
            """
            SELECT c.*, u.usuario AS cuenta, u.activo AS cuenta_activa
            FROM clientes_whatsapp c
            LEFT JOIN dashboard_usuarios u ON u.id = c.usuario_id
            WHERE c.id = %s
            """,
            (int(negocio_id),),
        )
    )


def por_usuario(usuario_id: int) -> dict[str, Any] | None:
    """El negocio al que pertenece una cuenta de acceso, si es la de alguno.

    Lo usa «Mi cuenta»: una cuenta de negocio sin decir de qué negocio es no
    dice nada. Para el administrador devuelve None, que es lo correcto: él no es
    de ningún cliente.
    """
    return _con_url(
        pool.consultar_uno("SELECT * FROM clientes_whatsapp WHERE usuario_id = %s", (int(usuario_id),))
    )


def resumen_actividad(proyecto_id: int) -> dict[str, Any]:
    """Las cifras agregadas de un único proyecto."""
    fila = pool.consultar_uno(
        """
        SELECT
          (SELECT COUNT(DISTINCT (client_id, canal)) FROM conversation_messages WHERE proyecto_id = %s) AS conversaciones,
          (SELECT COUNT(*) FROM seguimiento_clientes WHERE proyecto_id = %s) AS clientes,
          (SELECT COALESCE(SUM(costo_real_microusd), 0) FROM uso_eventos WHERE proyecto_id = %s) AS real_microusd,
          (SELECT COUNT(*) FROM reportes WHERE proyecto_id = %s AND NOT revisado) AS reportes_pendientes,
          (SELECT MAX(created_at) FROM conversation_messages WHERE proyecto_id = %s) AS ultima_actividad
        """,
        (int(proyecto_id),) * 5,
    )
    return fila or {}


def crear(nombre: str, numero: str = "") -> dict[str, Any]:
    nombre = str(nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente no puede estar vacío.")

    slug = _slug(nombre)
    if pool.consultar_uno("SELECT id FROM clientes_whatsapp WHERE slug = %s", (slug,)):
        raise ValueError(f"Ya existe un cliente con el identificador '{slug}'.")

    creado = pool.consultar_uno(
        """
        INSERT INTO clientes_whatsapp (nombre, slug, webhook_token, numero)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (nombre[:120], slug, _token_nuevo(), str(numero or "").strip()[:40]),
    )
    # El proyecto nace con el mismo catálogo conversacional base que sus
    # prompts; a partir de aquí cada cuenta lo versiona de forma independiente.
    from src.services import fragmentos

    fragmentos.sembrar_catalogos_faltantes(creado["id"])
    return creado


def rotar_token(cliente_id: int) -> dict[str, Any] | None:
    """Cambia el token: la URL vieja deja de funcionar en cuanto expira el caché.

    Después de rotar hay que pegar la URL nueva en WasenderAPI. Mientras tanto
    los eventos se rechazan con 401, que es justo lo que se busca cuando se
    rota por sospecha de filtración.
    """
    return pool.consultar_uno(
        "UPDATE clientes_whatsapp SET webhook_token = %s WHERE id = %s RETURNING *",
        (_token_nuevo(), int(cliente_id)),
    )


def alternar_activo(cliente_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno(
        "UPDATE clientes_whatsapp SET activo = NOT activo WHERE id = %s RETURNING *",
        (int(cliente_id),),
    )


def eliminar(cliente_id: int) -> int:
    """Borra el negocio. Su webhook desaparece con él: la URL pasa a dar 401."""
    return pool.ejecutar("DELETE FROM clientes_whatsapp WHERE id = %s", (int(cliente_id),))


def actualizar_config(
    cliente_id: int, *, nombre: str = "", numero: str = "", zona_horaria: str = "", notas: str = ""
) -> dict[str, Any] | None:
    nombre = str(nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente no puede estar vacío.")
    if zona_horaria and zona_horaria not in ZONAS_HORARIAS:
        raise ValueError("Zona horaria no reconocida.")

    return _con_url(
        pool.consultar_uno(
            """
            UPDATE clientes_whatsapp
            SET nombre = %s, numero = %s, zona_horaria = %s, notas = %s
            WHERE id = %s
            RETURNING *
            """,
            (
                nombre[:120],
                str(numero or "").strip()[:40],
                zona_horaria or "America/Costa_Rica",
                str(notas or "").strip(),
                int(cliente_id),
            ),
        )
    )


def actualizar_credenciales(cliente_id: int, api_key: str = "", webhook_secret: str = "") -> None:
    """Guarda las dos credenciales que WasenderAPI entrega por sesión.

    Son exactamente las de su pantalla: el **API Access Token** (para enviar) y
    el **Webhook Secret** (que acompaña a cada evento entrante). No se pide una
    URL: el dominio de WasenderAPI es el mismo para todos y vive en el entorno.

    Un campo vacío significa «no lo toques», no «bórralo»: el formulario nunca
    muestra lo guardado, así que enviarlo en blanco es lo normal cuando solo se
    estaba cambiando lo otro. Para quitarlos de verdad está `borrar_credenciales`.
    """
    if str(api_key or "").strip():
        pool.ejecutar(
            "UPDATE clientes_whatsapp SET wasender_api_key = %s WHERE id = %s",
            (str(api_key).strip(), int(cliente_id)),
        )
    if str(webhook_secret or "").strip():
        pool.ejecutar(
            "UPDATE clientes_whatsapp SET wasender_webhook_secret = %s WHERE id = %s",
            (str(webhook_secret).strip(), int(cliente_id)),
        )


def estado_wasender(api_key: str) -> dict[str, str]:
    """Estado de la sesión sin devolver ni registrar la credencial.

    Se usa únicamente en el perfil administrativo. Una caída del proveedor no
    convierte la configuración en un error del dashboard: se distingue entre
    token inválido, sesión desconectada y estado temporalmente no comprobable.
    """
    api_key = str(api_key or "").strip()
    if not api_key:
        return {
            "codigo": "sin_configurar",
            "texto": "Sin API Token",
            "clase": "error",
        }

    try:
        respuesta = httpx.get(
            f"{settings.WASENDER_API_URL.rstrip('/')}/api/status",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.WASENDER_STATUS_TIMEOUT_SECONDS,
        )
        if respuesta.status_code in (401, 403):
            return {"codigo": "invalido", "texto": "Token rechazado", "clase": "error"}
        respuesta.raise_for_status()
        cuerpo = respuesta.json() or {}
        estado = str(cuerpo.get("status") or "").strip().lower()
        if not estado and isinstance(cuerpo.get("data"), dict):
            estado = str(cuerpo["data"].get("status") or "").strip().lower()
    except Exception:
        return {
            "codigo": "no_disponible",
            "texto": "Estado no disponible",
            "clase": "alerta",
        }

    if estado == "connected":
        return {"codigo": "conectado", "texto": "Wasender conectado", "clase": "ok"}
    if estado:
        return {
            "codigo": "desconectado",
            "texto": f"Wasender: {estado}",
            "clase": "alerta",
        }
    return {
        "codigo": "desconocido",
        "texto": "Estado no reconocido",
        "clase": "alerta",
    }


def borrar_credenciales(cliente_id: int) -> int:
    return pool.ejecutar(
        "UPDATE clientes_whatsapp SET wasender_api_key = '', wasender_webhook_secret = '' "
        "WHERE id = %s",
        (int(cliente_id),),
    )


def vincular_cuenta(cliente_id: int, usuario_id: int | None) -> None:
    """Ata la cuenta de acceso con la que el negocio entra a su panel.

    Es lo que permite «entrar como» desde el perfil: sin cuenta vinculada no hay
    a quién suplantar.
    """
    pool.ejecutar(
        "UPDATE clientes_whatsapp SET usuario_id = %s WHERE id = %s",
        (usuario_id, int(cliente_id)),
    )


def actualizar_numero(cliente_id: int, numero: str) -> int:
    return pool.ejecutar(
        "UPDATE clientes_whatsapp SET numero = %s WHERE id = %s",
        (str(numero or "").strip()[:40], int(cliente_id)),
    )
