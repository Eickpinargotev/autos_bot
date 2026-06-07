import httpx

from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.channels.base_channel import ChannelSender
from src.infrastructure.channels.outbound_attachments import (
    OutboundAttachment,
    cleanup_attachment,
    download_drive_image,
    parse_outbound_message,
)
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository


class TelegramSender(ChannelSender):
    channel = Channel.TELEGRAM

    def send_message_sync(self, user_id: str, text: str):
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        response = httpx.post(url, json={"chat_id": user_id, "text": text}, timeout=10.0)
        response.raise_for_status()

    def send_image_sync(self, user_id: str, attachment: OutboundAttachment, caption: str = ""):
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(attachment.path, "rb") as image_file:
            response = httpx.post(
                url,
                data={"chat_id": user_id, "caption": caption},
                files={"photo": (attachment.image_id, image_file, attachment.content_type)},
                timeout=settings.OUTBOUND_IMAGE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()


class WhatsAppPendingHttpSender(ChannelSender):
    channel = Channel.WHATSAPP

    def send_message_sync(self, user_id: str, text: str):
        raise NotImplementedError("WhatsApp HTTP sender pendiente de implementar")

    def send_image_sync(self, user_id: str, attachment: OutboundAttachment, caption: str = ""):
        raise NotImplementedError("WhatsApp HTTP sender pendiente de implementar")


class ChannelSenderRegistry:
    _senders: dict[Channel, ChannelSender] = {
        Channel.TELEGRAM: TelegramSender(),
        Channel.WHATSAPP: WhatsAppPendingHttpSender(),
    }

    @classmethod
    def get(cls, channel: Channel | str) -> ChannelSender:
        channel_value = channel if isinstance(channel, Channel) else Channel(channel)
        return cls._senders[channel_value]

    @classmethod
    def send(cls, channel: Channel | str, user_id: str, text: str, log_conversation: bool = True):
        sender = cls.get(channel)
        parsed = parse_outbound_message(text)
        if not parsed.image_ids:
            sender.send_message_sync(user_id, text)
            if log_conversation:
                ConversationLogRepository.log_outbound(client_id=user_id, canal=channel, text=text)
            return

        sent_image = False
        for index, image_id in enumerate(parsed.image_ids):
            attachment = download_drive_image(image_id)
            if not attachment:
                continue
            try:
                sender.send_image_sync(user_id, attachment, parsed.clean_text if index == 0 else "")
                sent_image = True
            except Exception as e:
                print(f"Error enviando imagen {image_id} por {sender.channel}: {e}")
            finally:
                cleanup_attachment(attachment)

        if not sent_image and parsed.clean_text:
            sender.send_message_sync(user_id, parsed.clean_text)
            if log_conversation:
                ConversationLogRepository.log_outbound(client_id=user_id, canal=channel, text=parsed.clean_text)
            return

        if sent_image and log_conversation:
            ConversationLogRepository.log_outbound(client_id=user_id, canal=channel, text=text)
