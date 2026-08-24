from enum import Enum
from dataclasses import dataclass
from typing import Any, Optional

class Channel(str, Enum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"

class MessageType(Enum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    VIDEO = "video"
    # Los stickers se distinguen de OTHER porque se IGNORAN por completo: no son
    # una consulta que el bot no pueda leer, son un gesto. Cualquier acuse ("no
    # podemos ver imágenes", o incluso un agradecimiento) suena a error donde no
    # lo hubo, y en WhatsApp además gasta la cuota de envío del minuto.
    STICKER = "sticker"
    OTHER = "other"

@dataclass
class Message:
    user_id: str
    content: str
    message_type: MessageType
    timestamp: float

@dataclass
class AdvertisementData:
    dia: str
    valor: str
    hora: str

@dataclass
class InboundMessage:
    channel: Channel
    user_id: str
    user_name: str
    message_type: MessageType
    text: str = ""
    raw_payload: Optional[dict[str, Any]] = None
    # Texto del anuncio desde el que abrió WhatsApp. Se mantiene separado del
    # mensaje visible (que suele ser el genérico «Quiero más información»): el
    # primero sirve para reconocer la ciudad y el segundo es lo que debe verse
    # en la conversación.
    advertisement_text: str = ""
    # Contenido del mensaje al que el cliente responde. WhatsApp lo entrega en
    # contextInfo.quotedMessage; se conserva como contexto para entender
    # referencias naturales como "esto" o "ese formulario".
    quoted_text: str = ""
    from_me: bool = False
    event_type: str = "message"
    # Id del mensaje en el proveedor. En WhatsApp sirve para saber si un mensaje
    # saliente lo mandó el bot o una persona del negocio (comparten número).
    message_id: str = ""
    proyecto_id: int = 0

@dataclass
class OutboundMessage:
    channel: Channel
    user_id: str
    text: str

@dataclass
class OrchestratorAction:
    action: str
    channel: Channel
    user_id: str
    text: str = ""
    skip_conversation_log: bool = False
