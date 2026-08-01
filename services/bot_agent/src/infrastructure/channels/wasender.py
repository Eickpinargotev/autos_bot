"""Cliente de WasenderAPI: envío y traducción de eventos entrantes de WhatsApp.

Diferencia clave con Telegram: WasenderAPI recibe la media por **URL pública**,
no como binario subido. Por eso el envío de imagen/video no descarga el archivo
(a diferencia de `outbound_attachments`, que sí lo hace para Telegram): resuelve
la referencia a una URL y deja que WasenderAPI la descargue.

Aviso sobre la documentación: la API pública documenta los endpoints
(`/api/send-message`, `/api/status`, webhooks) pero no publica el detalle exacto
de los payloads. Por eso toda la lectura de eventos pasa por `mensaje_entrante`,
que tolera varias formas del mismo dato y se puede ajustar en un solo sitio
cuando haya una cuenta real contra la que verificar.
"""

from typing import Any

import httpx

from src.core.config import settings
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.channels import outbound_registry


class WasenderNoConfigurado(RuntimeError):
    """Se intentó usar WhatsApp sin credenciales de WasenderAPI."""


def configurado() -> bool:
    return bool(settings.WASENDER_API_KEY)


def _headers() -> dict[str, str]:
    if not configurado():
        raise WasenderNoConfigurado(
            "WhatsApp está inactivo: falta WASENDER_API_KEY en el entorno."
        )
    return {
        "Authorization": f"Bearer {settings.WASENDER_API_KEY}",
        "Content-Type": "application/json",
    }


def _url(ruta: str) -> str:
    return f"{settings.WASENDER_API_URL.rstrip('/')}{ruta}"


def enviar(payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/send-message. Devuelve la respuesta ya decodificada."""
    respuesta = httpx.post(
        _url("/api/send-message"),
        headers=_headers(),
        json=payload,
        timeout=settings.WASENDER_TIMEOUT_SECONDS,
    )
    respuesta.raise_for_status()
    try:
        return respuesta.json()
    except Exception:
        return {}


def enviar_texto(destino: str, texto: str) -> dict[str, Any]:
    return _enviar_y_recordar({"to": destino, "text": texto}, destino, texto)


def enviar_imagen(destino: str, url_imagen: str, texto: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"to": destino, "imageUrl": url_imagen}
    if texto:
        payload["text"] = texto
    return _enviar_y_recordar(payload, destino, texto)


def enviar_video(destino: str, url_video: str, texto: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"to": destino, "videoUrl": url_video}
    if texto:
        payload["text"] = texto
    return _enviar_y_recordar(payload, destino, texto)


def _enviar_y_recordar(payload: dict[str, Any], destino: str, texto: str) -> dict[str, Any]:
    """Envía y anota el mensaje como propio.

    El bot y el dueño del negocio comparten el número, así que los eventos de
    mensajes salientes son ambiguos: sin esta anotación el bot leería su propia
    respuesta como una intervención humana y se bloquearía solo. Ver
    `outbound_registry`.
    """
    respuesta = enviar(payload)
    outbound_registry.recordar_envio(destino, texto, respuesta)
    return respuesta


def estado_sesion() -> dict[str, Any]:
    """GET /api/status: si la sesión de WhatsApp sigue vinculada."""
    respuesta = httpx.get(
        _url("/api/status"),
        headers=_headers(),
        timeout=settings.WASENDER_TIMEOUT_SECONDS,
    )
    respuesta.raise_for_status()
    return respuesta.json()


# --- Eventos entrantes -------------------------------------------------------

_TIPOS = {
    "conversation": MessageType.TEXT,
    "extendedTextMessage": MessageType.TEXT,
    "imageMessage": MessageType.IMAGE,
    "videoMessage": MessageType.OTHER,
    "documentMessage": MessageType.DOCUMENT,
    "audioMessage": MessageType.AUDIO,
    "pttMessage": MessageType.AUDIO,
    "stickerMessage": MessageType.STICKER,
}


def _primero(datos: dict[str, Any], *claves: str) -> Any:
    for clave in claves:
        valor = datos.get(clave)
        if valor not in (None, ""):
            return valor
    return None


def nombre_evento(evento: dict[str, Any]) -> str:
    """Nombre del evento tal como lo manda WasenderAPI ('messages.received'…)."""
    return str(_primero(evento, "event", "type", "eventType") or "")


def _solo_numero(jid: Any) -> str:
    """'50688888888@s.whatsapp.net' -> '50688888888'."""
    return str(jid or "").split("@", 1)[0].split(":", 1)[0]


def ingresos_a_grupo(evento: dict[str, Any]) -> list[str]:
    """Números que acaban de ENTRAR a un grupo, según `group-participants.update`.

    Es el evento que cierra el flujo de publicidad: la invitación lleva el link
    del grupo, y unirse es la señal de que ya no hay que seguir recordándoselo.

    Solo cuenta la acción de alta. Las salidas, ascensos a admin y demás llegan
    por el mismo evento y no significan nada para el flujo; devolver una lista
    (y no un solo número) es porque WhatsApp agrupa varias altas en un evento.
    """
    datos = evento.get("data") or evento
    if not isinstance(datos, dict):
        return []

    accion = str(_primero(datos, "action", "type", "participantAction") or "").lower()
    if accion not in ("add", "invite", "join"):
        return []

    crudos = _primero(datos, "participants", "participant", "jids") or []
    if not isinstance(crudos, list):
        crudos = [crudos]

    numeros = []
    for crudo in crudos:
        if isinstance(crudo, dict):
            crudo = _primero(crudo, "id", "jid", "phone", "number")
        numero = _solo_numero(crudo)
        if numero and numero.isdigit():
            numeros.append(numero)
    return numeros


def mensaje_entrante(evento: dict[str, Any]) -> InboundMessage | None:
    """Traduce un evento de WasenderAPI a `InboundMessage`.

    Devuelve None cuando el evento no es un mensaje de chat individual: eventos
    de grupo, de estado, recibos de lectura, etc. Es deliberado que el filtro
    sea explícito — procesar un evento equivocado le cobraría al cliente un
    turno que nunca ocurrió.
    """
    datos = evento.get("data") or evento
    mensaje = datos.get("messages") or datos.get("message") or datos
    if isinstance(mensaje, list):
        mensaje = mensaje[0] if mensaje else {}
    if not isinstance(mensaje, dict):
        return None

    clave = mensaje.get("key") or {}
    remitente = str(_primero(clave, "remoteJid") or _primero(mensaje, "from", "chatId", "remoteJid") or "")
    if not remitente:
        return None

    # Los chats de grupo y las difusiones no son atención uno a uno.
    if "@g.us" in remitente or "broadcast" in remitente:
        return None

    from_me = bool(clave.get("fromMe") or mensaje.get("fromMe"))
    user_id = _solo_numero(remitente)
    mensaje_id = str(_primero(clave, "id") or _primero(mensaje, "id", "msgId", "messageId") or "")

    contenido = mensaje.get("message") or {}
    tipo = MessageType.OTHER
    texto = ""
    if isinstance(contenido, dict):
        for nombre, valor in contenido.items():
            if nombre in _TIPOS:
                tipo = _TIPOS[nombre]
                if isinstance(valor, str):
                    texto = valor
                elif isinstance(valor, dict):
                    texto = str(_primero(valor, "text", "caption", "conversation") or "")
                break
    if not texto:
        texto = str(_primero(mensaje, "text", "body", "conversation") or "")
        if texto and tipo is MessageType.OTHER:
            tipo = MessageType.TEXT

    nombre_remitente = str(_primero(mensaje, "pushName", "notifyName", "senderName") or "Desconocido")

    return InboundMessage(
        channel=Channel.WHATSAPP,
        user_id=user_id,
        user_name=nombre_remitente,
        message_type=tipo,
        text=texto,
        raw_payload=evento,
        from_me=from_me,
        message_id=mensaje_id,
    )
