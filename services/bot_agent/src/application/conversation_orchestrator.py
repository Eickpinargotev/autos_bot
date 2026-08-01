import re

from src.application import seguimiento_service
from src.application.buffer_service import BufferService, redis_client, scoped_key
from src.application.message_catalog import mensajes_del_negocio, mensajes_db
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


# Cuando una persona del negocio entra a la conversación, el bot deja de
# responder durante este plazo. Es largo a propósito: si un humano ya está
# atendiendo, que el bot vuelva a interrumpir a los pocos días sería peor que
# no contestar.
_DIAS_BLOQUEO_POR_INTERVENCION = 12


class ConversationOrchestrator:
    def handle(self, message: InboundMessage) -> list[OrchestratorAction]:
        if message.from_me:
            return self._handle_intervencion_humana(message)

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
            nodo = "MEDIA" if BufferService.add_image_info_count(message.user_id, message.channel) else "MEDIA_INSISTE"
            return self._responder_automatico(message, nodo)

        if message.message_type == MessageType.STICKER:
            return self._responder_automatico(message, "STICKER")

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

    def _responder_automatico(self, message: InboundMessage, nodo: str) -> list[OrchestratorAction]:
        """Acuse fijo a lo que el bot no puede leer (imágenes, documentos, stickers).

        **No se factura, y es a propósito.** Estos avisos no pasan por el modelo
        ni entregan nada del negocio: son la respuesta a algo que el bot no puede
        atender. Cobrarlos convertiría en ingreso el que un cliente mande cinco
        stickers seguidos, que es justo el caso en el que no se hizo trabajo
        alguno. Por eso aquí NO se llama a `registrar_uso_codigo` — a diferencia
        de la palabra clave o la bienvenida al grupo, que sí entregan contenido
        del negocio y sí se cobran. Cubierto por
        `tests/unit/test_mensajes_automaticos.py`.
        """
        mensajes = mensajes_del_negocio("AUTOMATICO", nodo)
        return [self._send(message, texto) for texto in mensajes]

    def _handle_intervencion_humana(self, message: InboundMessage) -> list[OrchestratorAction]:
        """El dueño escribió con su propio número: a partir de aquí atiende él.

        El bot se calla y el chat queda bloqueado 12 días. Durante ese plazo
        siguen funcionando los comandos (`/d`, `/block`) y los mensajes
        programados, pero el agente no vuelve a intervenir: dos voces
        respondiendo lo mismo confunden al cliente y desautorizan a la persona.

        Solo aplica a WhatsApp, que es donde el dueño escribe desde el mismo
        número del bot; en Telegram los mensajes propios son del propio bot.
        """
        if message.channel != Channel.WHATSAPP:
            return []

        try:
            # Se registra en el log para que la conversación siga siendo
            # reconstruible: si no, el visor mostraría un hueco inexplicable.
            ConversationLogRepository.append_message(
                client_id=message.user_id,
                canal=message.channel,
                message={
                    "direction": "outbound",
                    "author": "dueño",
                    "sender_id": "humano",
                    "sender_name": "Asesor",
                    "message_type": "text",
                    "text": message.text or "",
                    "event_type": "intervencion_humana",
                },
            )
            # También al historial simplificado: si no, el resumen del cliente
            # mostraría al bot hablando solo y un hueco donde atendió la persona.
            seguimiento_service.registrar_mensaje(
                client_id=message.user_id,
                canal=message.channel,
                autor="dueño",
                texto=message.text or "",
            )
            seguimiento_service.registrar_intervencion_humana(message.user_id, message.channel)

            PostgresUserRepo().block_user(
                message.user_id,
                reason="Intervención de un asesor humano",
                days=_DIAS_BLOQUEO_POR_INTERVENCION,
                channel=message.channel,
            )
            # Se cancelan los recordatorios ya agendados: llegarían encima de la
            # conversación que la persona está teniendo.
            clear_user_runtime_context(
                message.channel, message.user_id, cancel_scheduled=True, clear_reports=False
            )
            BufferService.get_and_clear_buffer(message.user_id, message.channel)
        except Exception as e:
            print(f"Error registrando la intervención humana: {e}")

        return []

    def _handle_group_join(self, message: InboundMessage) -> list[OrchestratorAction]:
        if not has_ad_context(message.channel, message.user_id):
            return []

        cancel_scheduled_tasks(message.channel.value, message.user_id)
        welcome_msgs = mensajes_del_negocio("WELCOME", "W")
        # Bienvenida al grupo: la dispara el evento de ingreso, sin pasar por el
        # modelo. Se factura como mensaje de código.
        if not welcome_msgs:
            welcome_msgs = ["📲 Gracias por unirse a nuestro grupo del curso teórico!!!\n\nRecuerde:\n\n🎯 Por política de transparencia no cobramos nada antes del curso y pagas en efectivo hasta ese mismo día.\n\n🎯 Traer documento de identidad \n\n🎯 Traer material para tomar notas (Cuaderno y lapicero) \n\nPor favor presentarse unos 10 minutos antes para hacer la matrícula e iniciar de la mejor manera la obtención de su licencia.\n\nBendiciones"]

        repo = PostgresUserRepo()
        repo.block_user(message.user_id, reason="Ingreso a grupo", days=12, channel=message.channel)
        set_welcome_context(message.channel, message.user_id)
        BufferService.get_and_clear_buffer(message.user_id, message.channel)

        seguimiento_service.registrar_uso_codigo(
            message.user_id, message.channel, origen="bienvenida", mensajes=len(welcome_msgs)
        )
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
        first_messages = mensajes_del_negocio("KEYWORD", node)
        if not first_messages:
            return []

        cancel_scheduled_tasks(message.channel.value, message.user_id)
        repo.block_user(message.user_id, reason=f"Flujo keyword {keyword}", channel=message.channel)
        KeywordRegistryRepository.register_if_missing(message.user_id, message.user_name, message.channel, keyword)
        register_keyword_context(message.channel, message.user_id)
        schedule_keyword_programmed_messages.apply_async((message.channel.value, message.user_id))
        # La palabra clave la detecta el código, no el modelo: se factura con la
        # tarifa por mensaje, no sobre tokens.
        seguimiento_service.registrar_uso_codigo(
            message.user_id, message.channel, origen="keyword", mensajes=len(first_messages)
        )
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
