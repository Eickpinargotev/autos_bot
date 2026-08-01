from src.domain.entities import Channel, InboundMessage, MessageType
from src.application.conversation_orchestrator import ConversationOrchestrator
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

class MessageHandler:
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
        )
        actions = ConversationOrchestrator().handle(inbound)
        for action in actions:
            if action.action == "send_now" and action.text:
                if not action.skip_conversation_log:
                    ConversationLogRepository.log_outbound(
                        client_id=action.user_id,
                        canal=action.channel,
                        text=action.text,
                    )
                return action.text
        if is_command:
            return "Command processed"
        if msg_type == MessageType.TEXT:
            return "Text buffered"
        if msg_type == MessageType.AUDIO:
            return "Audio buffered"
        return None
