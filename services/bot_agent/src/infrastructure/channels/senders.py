import httpx

from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.channels import wasender
from src.infrastructure.channels.base_channel import ChannelSender
from src.infrastructure.channels.outbound_attachments import (
    OutboundAttachment,
    cleanup_attachment,
    download_drive_image,
    parse_outbound_message,
    url_publica,
)
from src.infrastructure.repositories import clientes_whatsapp_repo
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

    def send_video_sync(self, user_id: str, referencia: str, caption: str = ""):
        """Telegram acepta una URL directa como `video`, sin subir el binario."""
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendVideo"
        response = httpx.post(
            url,
            json={"chat_id": user_id, "video": url_publica(referencia), "caption": caption},
            timeout=settings.OUTBOUND_IMAGE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


class WhatsAppSender(ChannelSender):
    """WhatsApp a través de WasenderAPI.

    Dos diferencias con Telegram:

    - La media NO se sube: WasenderAPI la descarga desde una URL pública. Por
      eso aquí no se usa `download_drive_image` — solo se resuelve a URL.
    - La credencial no es del despliegue sino del NEGOCIO dueño del número, y se
      resuelve por destinatario. Telegram tiene un token global porque hay un
      solo bot; en WhatsApp cada negocio enlaza su propia sesión, así que la
      clave se busca en la base (y se administra desde el panel) en cada envío.
    """

    channel = Channel.WHATSAPP

    def _api_key(self, user_id: str) -> str:
        return clientes_whatsapp_repo.api_key_de_envio(Channel.WHATSAPP.value, user_id)

    def send_message_sync(self, user_id: str, text: str):
        wasender.enviar_texto(user_id, text, self._api_key(user_id))

    def send_image_sync(self, user_id: str, attachment: OutboundAttachment, caption: str = ""):
        # `attachment.image_id` conserva la referencia original (ID de Drive o URL).
        wasender.enviar_imagen(
            user_id, url_publica(attachment.image_id), caption, self._api_key(user_id)
        )

    def send_image_url_sync(self, user_id: str, referencia: str, caption: str = ""):
        wasender.enviar_imagen(user_id, url_publica(referencia), caption, self._api_key(user_id))

    def send_video_sync(self, user_id: str, referencia: str, caption: str = ""):
        wasender.enviar_video(user_id, url_publica(referencia), caption, self._api_key(user_id))


class ChannelSenderRegistry:
    _senders: dict[Channel, ChannelSender] = {
        Channel.TELEGRAM: TelegramSender(),
        Channel.WHATSAPP: WhatsAppSender(),
    }

    @classmethod
    def get(cls, channel: Channel | str) -> ChannelSender:
        channel_value = channel if isinstance(channel, Channel) else Channel(channel)
        return cls._senders[channel_value]

    @classmethod
    def send(cls, channel: Channel | str, user_id: str, text: str, log_conversation: bool = True):
        sender = cls.get(channel)
        parsed = parse_outbound_message(text)

        if not parsed.image_ids and not parsed.video_ids:
            sender.send_message_sync(user_id, text)
            if log_conversation:
                ConversationLogRepository.log_outbound(client_id=user_id, canal=channel, text=text)
            return

        # El texto acompaña a la PRIMERA media como pie; el resto va sin pie para
        # no repetirlo en cada archivo.
        enviado_algo = False
        pie_pendiente = parsed.clean_text

        for image_id in parsed.image_ids:
            if cls._enviar_imagen(sender, user_id, image_id, pie_pendiente):
                enviado_algo = True
                pie_pendiente = ""

        for video_id in parsed.video_ids:
            try:
                sender.send_video_sync(user_id, video_id, pie_pendiente)
                enviado_algo = True
                pie_pendiente = ""
            except Exception as e:
                print(f"Error enviando video {video_id} por {sender.channel}: {e}")

        # Si ninguna media salió, al menos que el cliente reciba el texto.
        if not enviado_algo and parsed.clean_text:
            sender.send_message_sync(user_id, parsed.clean_text)
            if log_conversation:
                ConversationLogRepository.log_outbound(client_id=user_id, canal=channel, text=parsed.clean_text)
            return

        if enviado_algo and log_conversation:
            ConversationLogRepository.log_outbound(client_id=user_id, canal=channel, text=text)

    @classmethod
    def _enviar_imagen(cls, sender: ChannelSender, user_id: str, image_id: str, caption: str) -> bool:
        """Envía una imagen por el canal, subiéndola solo si el canal lo exige."""
        enviar_por_url = getattr(sender, "send_image_url_sync", None)
        if enviar_por_url is not None:
            try:
                enviar_por_url(user_id, image_id, caption)
                return True
            except Exception as e:
                print(f"Error enviando imagen {image_id} por {sender.channel}: {e}")
                return False

        attachment = download_drive_image(image_id)
        if not attachment:
            return False
        try:
            sender.send_image_sync(user_id, attachment, caption)
            return True
        except Exception as e:
            print(f"Error enviando imagen {image_id} por {sender.channel}: {e}")
            return False
        finally:
            cleanup_attachment(attachment)
