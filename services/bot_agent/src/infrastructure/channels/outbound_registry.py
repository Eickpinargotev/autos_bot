"""Memoria corta de lo que el propio bot acaba de enviar por WhatsApp.

Existe por un problema que no tiene otra solución limpia: en WhatsApp el bot y
el dueño del negocio **comparten el mismo número**. Los eventos de mensajes
salientes (`message.sent`, y los `messages.upsert` con `fromMe`) llegan igual
los haya escrito el dueño desde su teléfono o los haya mandado el bot por la
API. Si no se distinguen, cada respuesta del bot se leería como una
intervención humana y el bot se bloquearía a sí mismo 12 días en el primer
turno.

Cómo se distinguen, en orden de fiabilidad:

1. **Por id del mensaje**: al enviar, WasenderAPI devuelve el id que tendrá el
   mensaje. Se guarda; si el evento saliente trae ese id, es nuestro. Es exacto.
2. **Por huella del texto** (respaldo): si la respuesta del envío no trae id
   —la API no documenta el formato exacto y puede cambiar— se compara el texto
   normalizado con lo que acabamos de mandar a ESE destinatario. Puede fallar
   solo si el dueño escribe, palabra por palabra, el mismo texto que el bot
   dentro de la ventana; el costo de ese falso positivo (el bot sigue callado
   cuando ya iba a callarse) es mucho menor que el del falso negativo.

Todo vive en Redis con TTL: es información desechable, no estado del negocio.
"""

import hashlib
from typing import Any

from src.application.buffer_service import redis_client, scoped_key
from src.domain.entities import Channel

# Cuánto se recuerda un id enviado. Los eventos salientes llegan en segundos;
# una hora cubre de sobra reintentos y colas atrasadas del proveedor.
TTL_ID_SEGUNDOS = 3600
# La huella del texto vive menos: es el camino heurístico y no conviene que una
# coincidencia se arrastre.
TTL_TEXTO_SEGUNDOS = 900


def _clave_id(mensaje_id: str) -> str:
    return f"wa_envio_propio:{mensaje_id}"


def _clave_texto(canal: Channel | str, user_id: str, texto: str) -> str:
    huella = hashlib.sha256(_normalizar(texto).encode("utf-8")).hexdigest()[:32]
    return f"{scoped_key('wa_envio_texto', canal, user_id)}:{huella}"


def _normalizar(texto: str) -> str:
    return " ".join(str(texto or "").split()).lower()


def id_de_respuesta(respuesta: Any) -> str:
    """Saca el id del mensaje de la respuesta de envío de WasenderAPI.

    La API no publica el detalle del payload, así que se toleran las formas
    habituales en vez de fijar una sola: si ninguna calza, se devuelve "" y el
    respaldo por texto se encarga.
    """
    if not isinstance(respuesta, dict):
        return ""

    datos = respuesta.get("data")
    if not isinstance(datos, dict):
        datos = respuesta

    for clave in ("msgId", "messageId", "id", "key"):
        valor = datos.get(clave)
        if isinstance(valor, dict):
            valor = valor.get("id")
        if valor not in (None, ""):
            return str(valor)
    return ""


def recordar_envio(user_id: str, texto: str, respuesta: Any, canal: Channel | str = Channel.WHATSAPP) -> None:
    """Anota un mensaje que acabamos de mandar. Nunca rompe el envío."""
    try:
        mensaje_id = id_de_respuesta(respuesta)
        if mensaje_id:
            redis_client.setex(_clave_id(mensaje_id), TTL_ID_SEGUNDOS, "1")
        if texto:
            redis_client.setex(_clave_texto(canal, user_id, texto), TTL_TEXTO_SEGUNDOS, "1")
    except Exception as e:
        print(f"Error recordando el envío propio: {e}")


def es_envio_del_bot(
    user_id: str,
    mensaje_id: str = "",
    texto: str = "",
    canal: Channel | str = Channel.WHATSAPP,
) -> bool:
    """¿Ese mensaje saliente lo mandó el bot (y no una persona del negocio)?"""
    try:
        if mensaje_id and redis_client.exists(_clave_id(mensaje_id)):
            return True
        if texto and redis_client.exists(_clave_texto(canal, user_id, texto)):
            return True
        return False
    except Exception as e:
        # Si Redis no responde hay que elegir un error. Darlo por ajeno
        # bloquearía al cliente 12 días por un mensaje que mandó el propio bot,
        # en silencio y sin que nadie lo note. Darlo por propio, en cambio,
        # solo hace que el bot siga hablando cuando el dueño ya intervino, y el
        # siguiente mensaje del dueño (con Redis sano) lo corrige. Además es el
        # caso frecuente: casi todo lo que sale por ese número lo manda el bot.
        print(f"Error consultando los envíos propios: {e}")
        return True
