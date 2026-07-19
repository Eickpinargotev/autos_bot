import re

from src.application.buffer_service import BufferService, redis_client, scoped_key
from src.application.message_catalog import get_messages_for_node, mensajes_db
from src.application.runtime_context import (
    AD_REPORT_TEXT,
    WELCOME_REPORT_CONTEXT,
    clear_user_runtime_context,
    consume_ad_report,
    consume_keyword_report,
    consume_welcome_context,
    get_ad_reminder_stage,
    has_ad_context,
    mark_report_once,
    register_keyword_context,
    set_welcome_context,
)
from src.domain.entities import Channel, InboundMessage, MessageType, OrchestratorAction
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.repositories.report_repository import ReportRepository
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.keyword_registry_repository import KeywordRegistryRepository
from src.infrastructure.tasks.celery_app import cancel_scheduled_tasks, process_buffered_messages, schedule_keyword_programmed_messages
from src.core.config import settings


class ConversationOrchestrator:
    def handle(self, message: InboundMessage) -> list[OrchestratorAction]:
        if message.from_me:
            return []

        if not (message.message_type == MessageType.TEXT and (message.text or "") == "/d"):
            ConversationLogRepository.log_inbound(
                client_id=message.user_id,
                canal=message.channel,
                sender_name=message.user_name,
                message_type=message.message_type,
                text=message.text or "",
                event_type=message.event_type,
            )

        if message.event_type == "group_join":
            return self._handle_group_join(message)

        if message.message_type == MessageType.TEXT:
            return self._handle_text(message)

        if message.message_type in (MessageType.IMAGE, MessageType.DOCUMENT):
            if BufferService.add_image_info_count(message.user_id, message.channel):
                return [self._send(message, "No podemos ver imagenes/documentos, si deseas que alguien la revise, avísame y te contacto con un asesor")]
            return [self._send(message, "Ayuda solicitada: el usuario insiste enviando varias imagenes/documentos")]

        if message.message_type == MessageType.AUDIO:
            seq = BufferService.add_message(message.user_id, "texto transcrito", message.channel)
            process_buffered_messages.apply_async((message.channel.value, message.user_id, message.user_name, seq), countdown=settings.MESSAGE_BUFFER_SECONDS)
            return []

        return []

    def _handle_text(self, message: InboundMessage) -> list[OrchestratorAction]:
        text = message.text or ""
        repo = PostgresUserRepo()

        if text == "/d":
            ConversationLogRepository.delete_conversation(message.user_id, message.channel)
            KeywordRegistryRepository.delete(message.user_id, message.channel)
            repo.unblock_user(message.user_id, channel=message.channel)
            clear_user_runtime_context(message.channel, message.user_id, cancel_scheduled=True, clear_reports=True)
            return [self._send(message, "Historial y bloqueos limpiados.", skip_conversation_log=True)]

        if text.startswith("/block"):
            repo.block_user(message.user_id, reason="Bloqueado por comando", channel=message.channel)
            clear_user_runtime_context(message.channel, message.user_id, cancel_scheduled=True, clear_reports=True)
            return [self._send(message, "Usuario bloqueado.")]

        match_grupo = re.search(r'grupo\["(.*?)"\]', text, re.IGNORECASE)
        if match_grupo:
            if not has_ad_context(message.channel, message.user_id):
                return []
            return self._handle_group_join(message)

        keyword = text.strip().lower()
        if keyword in {"tareas", "transporte"}:
            return self._handle_keyword_flow(message, keyword, repo)

        if repo.is_blocked(message.user_id, channel=message.channel):
            self._handle_blocked_text(message)
            return []

        match_add = re.search(r'add\["(.*?)"\]', text, re.IGNORECASE)
        if match_add:
            from src.application.publicidad_service import PublicidadService
            PublicidadService.handle_publicidad_entry(message.user_id, match_add.group(1), message.user_name, message.channel)
            return []

        scheduled_key = scoped_key("scheduled_tasks", message.channel, message.user_id)
        is_in_ad_flow = redis_client.exists(scheduled_key)

        if is_in_ad_flow:
            return []

        seq = BufferService.add_message(message.user_id, text, message.channel)
        process_buffered_messages.apply_async((message.channel.value, message.user_id, message.user_name, seq), countdown=settings.MESSAGE_BUFFER_SECONDS)
        return []

    def _handle_group_join(self, message: InboundMessage) -> list[OrchestratorAction]:
        if not has_ad_context(message.channel, message.user_id):
            return []

        cancel_scheduled_tasks(message.channel.value, message.user_id)
        welcome_msgs = get_messages_for_node("WELCOME", "W")
        if not welcome_msgs:
            welcome_msgs = ["📲 Gracias por unirse a nuestro grupo del curso teórico!!!\n\nRecuerde:\n\n🎯 Por política de transparencia no cobramos nada antes del curso y pagas en efectivo hasta ese mismo día.\n\n🎯 Traer documento de identidad \n\n🎯 Traer material para tomar notas (Cuaderno y lapicero) \n\nPor favor presentarse unos 10 minutos antes para hacer la matrícula e iniciar de la mejor manera la obtención de su licencia.\n\nBendiciones"]

        repo = PostgresUserRepo()
        repo.block_user(message.user_id, reason="Ingreso a grupo", days=12, channel=message.channel)
        set_welcome_context(message.channel, message.user_id)
        BufferService.get_and_clear_buffer(message.user_id, message.channel)

        return [self._send(message, msg) for msg in welcome_msgs]

    def _handle_blocked_text(self, message: InboundMessage):
        if consume_welcome_context(message.channel, message.user_id):
            if mark_report_once(message.channel, message.user_id, WELCOME_REPORT_CONTEXT):
                report_msg = mensajes_db.get("WELCOME", {}).get("W", {}).get("reporte", "Respondieron a mensaje de bienvenida al grupo")
                ReportRepository.create_report(
                    nombre=message.user_name,
                    numero=message.user_id,
                    problema=report_msg,
                    link_whatsapp=f"https://wa.me/{message.user_id}",
                )
            return

        if consume_ad_report(message.channel, message.user_id):
            ReportRepository.create_report(
                nombre=message.user_name,
                numero=message.user_id,
                problema=AD_REPORT_TEXT,
                link_whatsapp=f"https://wa.me/{message.user_id}",
            )
            if get_ad_reminder_stage(message.channel, message.user_id) == 1:
                from src.infrastructure.tasks.celery_app import cancel_ad_reminder_stage

                cancel_ad_reminder_stage(message.channel.value, message.user_id, 2)
            return

        keyword_report = consume_keyword_report(message.channel, message.user_id)
        if keyword_report:
            ReportRepository.create_report(
                nombre=message.user_name,
                numero=message.user_id,
                problema=keyword_report,
                link_whatsapp=f"https://wa.me/{message.user_id}",
            )

    def _handle_keyword_flow(
        self,
        message: InboundMessage,
        keyword: str,
        repo: PostgresUserRepo,
    ) -> list[OrchestratorAction]:
        node = "T1" if keyword == "tareas" else "H1"
        first_messages = get_messages_for_node("KEYWORD", node)
        if not first_messages:
            return []

        cancel_scheduled_tasks(message.channel.value, message.user_id)
        repo.block_user(message.user_id, reason=f"Flujo keyword {keyword}", channel=message.channel)
        KeywordRegistryRepository.register_if_missing(message.user_id, message.user_name, message.channel, keyword)
        register_keyword_context(message.channel, message.user_id)
        schedule_keyword_programmed_messages.apply_async((message.channel.value, message.user_id))
        return [self._send(message, msg) for msg in first_messages]

    @staticmethod
    def _send(message: InboundMessage, text: str, skip_conversation_log: bool = False) -> OrchestratorAction:
        return OrchestratorAction(
            action="send_now",
            channel=message.channel,
            user_id=message.user_id,
            text=text,
            skip_conversation_log=skip_conversation_log,
        )
