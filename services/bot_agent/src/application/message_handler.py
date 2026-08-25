from src.domain.entities import Channel, InboundMessage, MessageType
from src.application.conversation_orchestrator import ConversationOrchestrator
import time

from src.infrastructure.channels.senders import ChannelSenderRegistry
from src.infrastructure.channels.outbound_coordinator import PrioridadSalida
from src.infrastructure.repositories import instrucciones_repository
from src.application.project_context import ambito_proyecto

class MessageHandler:
    """Entrada de un mensaje para los canales que NO tienen bucle propio.

    Telegram atiende desde `telegram_channel`, que despacha sus propias salidas
    coordinadas. WhatsApp entra por el webhook, donde no hay a quién
    "responder": la contestación es una llamada nueva a WasenderAPI. Por eso las
    respuestas inmediatas se envían aquí — antes solo se registraban y se
    devolvían, y como el webhook descarta ese retorno, en WhatsApp quedaban
    anotadas en el panel como enviadas sin que el cliente recibiera nada.
    """

    @staticmethod
    def handle_incoming_message(
        user_id: str,
        content: str,
        msg_type: MessageType,
        user_name: str = "Desconocido",
        is_command: bool = False,
        channel: Channel | str = Channel.TELEGRAM,
        from_me: bool = False,
        event_type: str = "message",
        message_id: str = "",
        raw_payload: dict | None = None,
        advertisement_text: str = "",
        quoted_text: str = "",
        proyecto_id: int = 0,
    ):
        channel_value = channel if isinstance(channel, Channel) else Channel(channel)
        inbound = InboundMessage(
            channel=channel_value,
            user_id=user_id,
            user_name=user_name,
            message_type=msg_type,
            text=content,
            from_me=from_me,
            event_type=event_type,
            message_id=message_id,
            # Lo necesita la nota de voz: la media viaja cifrada dentro del
            # evento y hay que reenviarlo entero para que el proveedor la
            # descifre. Ninguna otra rama lo mira.
            raw_payload=raw_payload,
            advertisement_text=advertisement_text,
            quoted_text=quoted_text,
            proyecto_id=int(proyecto_id or 0),
        )
        with ambito_proyecto(proyecto_id):
            actions = ConversationOrchestrator().handle(inbound)
            enviado = None
            enviados = 0
            for action in actions:
                if action.action != "send_now" or not action.text:
                    continue
                if enviados:
                    time.sleep(instrucciones_repository.intervalo_entre_mensajes())
                # Se recorren TODAS: un flujo de palabra clave devuelve varios
                # mensajes seguidos y antes solo salía el primero.
                # `ChannelSenderRegistry.send` ya registra el saliente.
                ChannelSenderRegistry.send(
                    action.channel,
                    action.user_id,
                    action.text,
                    log_conversation=not action.skip_conversation_log,
                    prioridad=PrioridadSalida.INTERACTIVA,
                )
                if enviado is None:
                    enviado = action.text
                enviados += 1
        if enviado is not None:
            return enviado
        if is_command:
            return "Command processed"
        if msg_type == MessageType.TEXT:
            return "Text buffered"
        if msg_type == MessageType.AUDIO:
            return "Audio buffered"
        return None
