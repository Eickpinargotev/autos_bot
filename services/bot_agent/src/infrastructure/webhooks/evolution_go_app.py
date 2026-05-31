from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.application.conversation_orchestrator import ConversationOrchestrator
from src.application.rag_service import RagService
from src.core.config import settings
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.channels.senders import ChannelSenderRegistry

app = FastAPI(title="WhatsApp Evolution Go Webhook")
orchestrator = ConversationOrchestrator()
rag_service = RagService()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhooks/evolution-go")
async def evolution_go_webhook(payload: dict[str, Any], token: str = Query(default="")):
    if settings.EVOLUTION_WEBHOOK_TOKEN and token != settings.EVOLUTION_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    inbound = _map_evolution_payload(payload)
    if inbound is None:
        return {"status": "ignored"}

    actions = orchestrator.handle(inbound)
    for action in actions:
        if action.action == "send_now" and action.text:
            ChannelSenderRegistry.send(
                action.channel,
                action.user_id,
                action.text,
                log_conversation=not action.skip_conversation_log,
            )

    return {"status": "ok", "actions": len(actions)}


@app.post("/webhooks/nocodb-rag-chunks")
async def nocodb_rag_chunks_webhook(payload: dict[str, Any], token: str = Query(default="")):
    if settings.NOCODB_RAG_WEBHOOK_TOKEN and token != settings.NOCODB_RAG_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    result = rag_service.sync_chunk_event(payload)
    return {"status": "ok", **result}


def _map_evolution_payload(payload: dict[str, Any]) -> InboundMessage | None:
    event = str(payload.get("event") or payload.get("Event") or "").upper()
    data = payload.get("data") or payload.get("Data") or payload

    if event.startswith("GROUP"):
        user_id = _extract_group_user_id(data)
        if not user_id:
            return None
        return InboundMessage(
            channel=Channel.WHATSAPP,
            user_id=user_id,
            user_name=user_id,
            message_type=MessageType.TEXT,
            raw_payload=payload,
            event_type="group_join",
        )

    key = data.get("key") or data.get("Key") or {}
    info = data.get("Info") or data.get("info") or {}
    message = data.get("message") or data.get("Message") or {}

    remote_jid = key.get("remoteJid") or key.get("RemoteJid") or info.get("Chat") or data.get("Chat")
    user_id = _phone_from_jid(remote_jid)
    if not user_id:
        return None

    from_me = bool(key.get("fromMe") or key.get("FromMe") or info.get("IsFromMe"))
    msg_type = _message_type(message, info)
    text = _message_text(message)
    user_name = data.get("pushName") or data.get("PushName") or info.get("PushName") or user_id

    return InboundMessage(
        channel=Channel.WHATSAPP,
        user_id=user_id,
        user_name=str(user_name),
        message_type=msg_type,
        text=text,
        raw_payload=payload,
        from_me=from_me,
        event_type="message",
    )


def _message_text(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    if message.get("conversation"):
        return str(message["conversation"])
    extended = message.get("extendedTextMessage") or {}
    if extended.get("text"):
        return str(extended["text"])
    return ""


def _message_type(message: dict[str, Any], info: dict[str, Any]) -> MessageType:
    media_type = str(info.get("MediaType") or info.get("mediaType") or "").lower()
    if media_type == "audio" or "audioMessage" in message:
        return MessageType.AUDIO
    if media_type == "document" or "documentMessage" in message:
        return MessageType.DOCUMENT
    if media_type == "image" or "imageMessage" in message:
        return MessageType.IMAGE
    if _message_text(message):
        return MessageType.TEXT
    return MessageType.OTHER


def _extract_group_user_id(data: dict[str, Any]) -> str:
    participants = data.get("participants") or data.get("Participants") or []
    if participants:
        first = participants[0]
        if isinstance(first, dict):
            return _phone_from_jid(first.get("id") or first.get("jid") or first.get("JID"))
        return _phone_from_jid(str(first))
    return _phone_from_jid(data.get("participant") or data.get("Participant") or "")


def _phone_from_jid(value: Any) -> str:
    if not value:
        return ""
    raw = str(value)
    return raw.split("@", 1)[0].replace("+", "").strip()
