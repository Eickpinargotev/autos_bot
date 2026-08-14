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

import time
from typing import Any

import httpx

from src.core.config import settings
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.channels import outbound_registry


class WasenderNoConfigurado(RuntimeError):
    """Se intentó usar WhatsApp sin credenciales de WasenderAPI."""


def _headers(api_key: str) -> dict[str, str]:
    """Cabeceras de una llamada a WasenderAPI.

    La clave llega como argumento, no se lee del entorno: es del NEGOCIO que
    responde (`clientes_whatsapp.wasender_api_key`, administrada desde el panel)
    y cada uno tiene la suya. Quien resuelve cuál toca es `WhatsAppSender`.
    """
    if not api_key:
        raise WasenderNoConfigurado(
            "WhatsApp está inactivo para este negocio: falta su API key de "
            "WasenderAPI. Se configura en el panel, en el perfil del negocio."
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _url(ruta: str) -> str:
    return f"{settings.WASENDER_API_URL.rstrip('/')}{ruta}"


def enviar(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    """POST /api/send-message. Devuelve la respuesta ya decodificada.

    WasenderAPI limita el ritmo de envío según el plan (el de prueba admite un
    mensaje por minuto) y responde **429** con `retry_after` en segundos. Sin
    reintento, ese 429 tumba el turno entero y el cliente se queda sin
    respuesta: es exactamente lo que pasa cuando dos mensajes seguidos generan
    dos envíos. Se espera lo que el proveedor indica y se reintenta.

    Se respeta el `retry_after` recibido en vez de una espera inventada, y se
    limita el número de intentos: si el plan no da para el ritmo de la
    conversación, hay que subir de plan, no acumular tareas esperando.
    """
    for intento in range(settings.WASENDER_MAX_REINTENTOS_429 + 1):
        respuesta = httpx.post(
            _url("/api/send-message"),
            headers=_headers(api_key),
            json=payload,
            timeout=settings.WASENDER_TIMEOUT_SECONDS,
        )
        if respuesta.status_code != 429 or intento == settings.WASENDER_MAX_REINTENTOS_429:
            break

        espera = _segundos_de_espera(respuesta)
        print(f"WasenderAPI limitó el ritmo de envío; se reintenta en {espera}s.")
        time.sleep(espera)

    respuesta.raise_for_status()
    try:
        return respuesta.json()
    except Exception:
        return {}


def _segundos_de_espera(respuesta: httpx.Response) -> float:
    """Cuánto pide esperar el proveedor tras un 429.

    El dato viene en el cuerpo (`retry_after`) y, por convención HTTP, puede
    venir también en la cabecera `Retry-After`. Se acota por arriba para que un
    valor absurdo del proveedor no deje una tarea de Celery colgada.
    """
    crudo: Any = None
    try:
        crudo = (respuesta.json() or {}).get("retry_after")
    except Exception:
        crudo = None
    if crudo in (None, ""):
        crudo = respuesta.headers.get("Retry-After")

    try:
        segundos = float(crudo)
    except (TypeError, ValueError):
        segundos = settings.WASENDER_ESPERA_429_POR_DEFECTO

    # +1s de colchón: el proveedor redondea hacia abajo y reintentar en el
    # segundo exacto vuelve a dar 429.
    return max(1.0, min(segundos + 1.0, settings.WASENDER_ESPERA_429_MAXIMA))


def enviar_texto(destino: str, texto: str, api_key: str) -> dict[str, Any]:
    return _enviar_y_recordar({"to": destino, "text": texto}, destino, texto, api_key)


def enviar_imagen(destino: str, url_imagen: str, texto: str = "", api_key: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"to": destino, "imageUrl": url_imagen}
    if texto:
        payload["text"] = texto
    return _enviar_y_recordar(payload, destino, texto, api_key)


def enviar_video(destino: str, url_video: str, texto: str = "", api_key: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"to": destino, "videoUrl": url_video}
    if texto:
        payload["text"] = texto
    return _enviar_y_recordar(payload, destino, texto, api_key)


def _enviar_y_recordar(payload: dict[str, Any], destino: str, texto: str, api_key: str) -> dict[str, Any]:
    """Envía y anota el mensaje como propio.

    El bot y el dueño del negocio comparten el número, así que los eventos de
    mensajes salientes son ambiguos: sin esta anotación el bot leería su propia
    respuesta como una intervención humana y se bloquearía solo. Ver
    `outbound_registry`.

    Se anota con el destino YA traducido, no con el identificador interno: el
    eco de ese mismo mensaje volverá por el webhook con el número, y las dos
    puntas tienen que coincidir para reconocerlo.
    """
    numero = numero_para_envio(destino, api_key)
    payload = {**payload, "to": numero}
    respuesta = enviar(payload, api_key)
    outbound_registry.recordar_envio(numero, texto, respuesta)
    return respuesta


def estado_sesion(api_key: str) -> dict[str, Any]:
    """GET /api/status: si la sesión de WhatsApp de ese negocio sigue vinculada."""
    respuesta = httpx.get(
        _url("/api/status"),
        headers=_headers(api_key),
        timeout=settings.WASENDER_TIMEOUT_SECONDS,
    )
    respuesta.raise_for_status()
    return respuesta.json()


# --- Eventos entrantes -------------------------------------------------------

_TIPOS = {
    "conversation": MessageType.TEXT,
    "extendedTextMessage": MessageType.TEXT,
    "imageMessage": MessageType.IMAGE,
    "videoMessage": MessageType.VIDEO,
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


def es_lid(jid: Any) -> bool:
    """¿Ese identificador es un LID y no un número?

    WhatsApp dejó de exponer siempre el teléfono: según el `addressingMode`, el
    `remoteJid` llega como **LID** (`258540019138808@lid`), un identificador
    interno. Sirve para reconocer al cliente, pero NO como destino: enviarle ahí
    devuelve 422 «The provided JID does not exist on WhatsApp».
    """
    return "@lid" in str(jid or "")


# LID -> número, resuelto contra la libreta de la sesión. Se cachea porque la
# libreta entera son cientos de contactos y no cambia de un mensaje al otro.
_CONTACTOS_TTL_SEGUNDOS = 600
_contactos_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _mapa_lid_a_numero(api_key: str) -> dict[str, str]:
    guardado = _contactos_cache.get(api_key)
    ahora = time.monotonic()
    if guardado and (ahora - guardado[0]) < _CONTACTOS_TTL_SEGUNDOS:
        return guardado[1]

    mapa: dict[str, str] = {}
    try:
        respuesta = httpx.get(
            _url("/api/contacts"),
            headers=_headers(api_key),
            timeout=settings.WASENDER_TIMEOUT_SECONDS,
        )
        respuesta.raise_for_status()
        for contacto in respuesta.json().get("data") or []:
            lid = _solo_numero(contacto.get("lid"))
            numero = _solo_numero(contacto.get("id"))
            if lid and numero and not es_lid(contacto.get("id")):
                mapa[lid] = numero
    except Exception as e:
        print(f"Error leyendo la libreta de contactos de WasenderAPI: {e}")
        return (guardado[1] if guardado else {})

    _contactos_cache[api_key] = (ahora, mapa)
    return mapa


def numero_para_envio(destino: str, api_key: str) -> str:
    """El destino tal como WasenderAPI lo acepta.

    Red de seguridad para las conversaciones que quedaron guardadas con el LID
    como identificador (y para cualquier evento que no traiga `senderPn`).

    Se consulta la libreta SIEMPRE, no solo cuando el destino "parece" un LID:
    al guardarse se le quita el sufijo `@lid`, y unos dígitos sueltos no se
    distinguen de un teléfono — ambos caben en 14-15 cifras. La búsqueda es
    contra un diccionario en memoria (una llamada HTTP cada 10 min), y un
    teléfono real nunca es clave del mapa, así que pasa de largo.

    Si no se puede traducir, se devuelve tal cual: mejor el error del proveedor
    que uno inventado aquí.
    """
    if not api_key:
        return destino
    return _mapa_lid_a_numero(api_key).get(_solo_numero(destino), destino)


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

    # Solo la atención uno a uno. Es una lista BLANCA, no negra: WhatsApp tiene
    # más tipos de chat de los que uno recuerda (grupos `@g.us`, difusiones,
    # estados, y los canales `@newsletter`), y con una lista negra se cuela el
    # que se olvidó. Pasó: un canal `120363...@newsletter` entró como si fuera
    # un cliente y quedó en la lista del panel con su propio "número".
    if "@" in remitente and not remitente.endswith(("@s.whatsapp.net", "@lid")):
        return None

    from_me = bool(clave.get("fromMe") or mensaje.get("fromMe"))

    # `remoteJid` puede venir como LID según el `addressingMode` de la sesión.
    # La documentación de WasenderAPI señala `cleanedSenderPn`/`senderPn` como
    # los campos de los que sacar el teléfono, y ese es el identificador que
    # queremos: el LID no sirve para responder, ni para el enlace wa.me, ni le
    # dice nada a quien lee el panel.
    #
    # Solo para mensajes ENTRANTES: en un saliente el "sender" es el negocio, y
    # tomarlo como identificador de la conversación la ataría al número propio.
    # Ahí el LID se traduce al enviar (`numero_para_envio`).
    user_id = _solo_numero(remitente)
    if es_lid(remitente) and not from_me:
        user_id = _solo_numero(_primero(clave, "cleanedSenderPn", "senderPn")) or user_id
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
