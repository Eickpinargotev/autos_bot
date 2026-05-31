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
    OTHER = "other"

class UserState(Enum):
    INICIO = "INICIO"
    DICTAMEN = "DICTAMEN"
    CLASES = "CLASES"
    GENERAL = "GENERAL"
    ALQUILER = "ALQUILER"
    QUEJAS = "QUEJAS"
    WIN = "WIN"
    PUBLICIDAD = "PUBLICIDAD"
    KEYWORD = "KEYWORD"
    WELCOME = "WELCOME"

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
    from_me: bool = False
    event_type: str = "message"

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
