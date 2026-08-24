import re

from src.application import seguimiento_service
from src.application.buffer_service import BufferService, redis_client, scoped_key
from src.application.message_catalog import mensajes_del_negocio, mensajes_db
from src.application import human_intervention
from src.application.runtime_context import (
    AD_REPORT_TEXT,
    MEDIA_REPORT_CONTEXT,
    WELCOME_REPORT_CONTEXT,
    clear_user_runtime_context,
    consume_ad_report,
    consume_keyword_report,
    consume_welcome_context,
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
from src.infrastructure.repositories import palabras_clave_repository
from src.infrastructure.tasks.celery_app import (
    cancel_scheduled_tasks,
    process_buffered_messages,
    schedule_keyword_programmed_messages,
    transcribir_nota_de_voz,
)
from src.core.config import settings


# Lo que el bot no puede leer se acusa con un texto fijo, distinto por tipo:
# "recibí tu imagen" y "recibí tu documento" no son lo mismo para quien escribe.
# Los textos viven en `mensajes.json` (nodo AUTOMATICO) y el negocio los edita
# desde el panel; aquí solo está la correspondencia tipo -> nodo.
_NODO_POR_MEDIA = {
    MessageType.IMAGE: "MEDIA_IMAGEN",
    MessageType.DOCUMENT: "MEDIA_DOCUMENTO",
    MessageType.VIDEO: "MEDIA_VIDEO",
}

# Detección de enlaces. No es interpretación de lenguaje natural —eso es del
# LLM y por regla del repo no se hace con regex—: es reconocer una URL, que es
# una estructura, igual que los marcadores `Imagen=` o los comandos `/d`.
# Cubre `http(s)://`, `www.` y el dominio suelto (`ejemplo.com/algo`), que es
# como la gente pega los enlaces en WhatsApp.
# El `(?<![@\w.])` del dominio suelto excluye los CORREOS: sin él,
# "mi correo es ana@gmail.com" se leería como un enlace y el cliente recibiría
# el acuse en vez de que lo atienda el agente. Dar un correo es lo más normal
# del mundo en esta conversación.
# Qué hizo el bot con cada tipo de mensaje. Se guarda en `event_type` para que
# el PANEL pueda mostrarlo entre corchetes ("[Sticker · ignorado]"). El LLM no
# ve nunca esta etiqueta: a él le llega solo el texto, así que un audio le
# aparece exactamente igual que si el cliente lo hubiera escrito.
_EVENTO_POR_TIPO = {
    MessageType.STICKER: "sticker_ignorado",
    MessageType.IMAGE: "media_avisada",
    MessageType.DOCUMENT: "media_avisada",
    MessageType.VIDEO: "media_avisada",
}


def _evento_de(message: InboundMessage) -> str:
    """El `event_type` con el que se registra, sin pisar los que ya venían."""
    if message.event_type and message.event_type != "message":
        return message.event_type
    return _EVENTO_POR_TIPO.get(message.message_type, message.event_type or "message")


_ENLACE = re.compile(
    r"(https?://"
    r"|(?<![@\w.])www\."
    r"|(?<![@\w.])[a-z0-9][a-z0-9\-]*\.(?:com|net|org|io|co|cr|es|app|me|gl|ly|be|info|biz|tv)\b)",
    re.IGNORECASE,
)


class ConversationOrchestrator:
    @staticmethod
    def _branch(message: InboundMessage, branch: str, data: dict | None = None) -> None:
        ConversationLogRepository.log_tool_event(
            client_id=message.user_id,
            canal=message.channel,
            tool_name=f"branch.{branch}",
            status="selected",
            input_data=data or {},
            output_data={"branch": branch},
            text=f"Rama automática: {branch}",
            event_type="automatic_branch",
        )

    def handle(self, message: InboundMessage) -> list[OrchestratorAction]:
        if message.from_me:
            self._branch(message, "human_intervention")
            return self._handle_intervencion_humana(message)

        # El audio se registra MÁS TARDE, desde la tarea de transcripción y ya
        # con su texto: anotarlo aquí dejaría una fila vacía y otra con el
        # contenido, el mismo mensaje dos veces en el chat del panel.
        # `/d` no se registra porque su efecto es borrar el historial.
        es_borrado = message.message_type == MessageType.TEXT and (message.text or "") == "/d"
        if not es_borrado and message.message_type != MessageType.AUDIO:
            ConversationLogRepository.log_inbound(
                client_id=message.user_id,
                canal=message.channel,
                sender_name=message.user_name,
                message_type=message.message_type,
                text=message.text or "",
                quoted_text=message.quoted_text,
                event_type=_evento_de(message),
            )

        # Los bloqueos absolutos se comprueban antes que stickers, eventos de
        # grupo, palabras clave o adjuntos. Registrar el inbound arriba sigue
        # siendo útil para el panel, pero ninguna rama automática puede ganarles.
        from src.infrastructure.repositories import bloqueos_permanentes_repository
        if bloqueos_permanentes_repository.esta_bloqueado(message.user_id, message.channel):
            self._branch(message, "permanent_block")
            return []

        repo = PostgresUserRepo()
        if repo.is_blocked_for_reason(
            message.user_id,
            human_intervention.MOTIVO_PAUSA_IA,
            channel=message.channel,
        ) is True:
            self._branch(message, "temporary_block", {"source": "human_intervention"})
            return []

        # Un sticker es un gesto, no una consulta: NO se responde. Queda en el
        # historial (arriba) para que quien lea el chat en el panel vea lo que
        # pasó de verdad, pero sin respuesta, sin LLM y sin cobro — y sin gastar
        # la cuota de envío del minuto, que dejaría sin contestar al mensaje que
        # sí importaba.
        if message.message_type == MessageType.STICKER:
            self._branch(message, "sticker_ignored")
            return []

        if message.event_type == "group_join":
            return self._handle_group_join(message)

        # Los textos comprueban el bloqueo dentro de `_handle_text` porque los
        # comandos de operación y algunos disparadores tienen reglas propias.
        # La media no tenía ninguna comprobación: después de crear un reporte
        # el chat quedaba bloqueado para texto, pero una imagen todavía recibía
        # el aviso automático «no puedo revisarla». Un handoff significa
        # silencio completo de la IA, también para imagen, documento, video y
        # audio, hasta que el equipo levante la pausa.
        if (
            message.message_type != MessageType.TEXT
            and repo.is_blocked(
                message.user_id, channel=message.channel, include_permanent=False
            )
        ):
            self._branch(message, "temporary_block")
            return []

        if message.message_type == MessageType.TEXT:
            return self._handle_text(message)

        if message.message_type in _NODO_POR_MEDIA:
            self._branch(message, "media_auto_reply", {"message_type": message.message_type.value})
            return self._responder_por_media(message, _NODO_POR_MEDIA[message.message_type])

        if message.message_type == MessageType.AUDIO:
            return self._handle_audio(message)

        return []

    def _handle_audio(self, message: InboundMessage) -> list[OrchestratorAction]:
        """Una nota de voz se transcribe y sigue el camino de un mensaje de texto.

        La transcripción NO ocurre aquí: descifrar la media, bajarla y pasarla
        por el modelo tarda segundos, y esto corre dentro del webhook. Se delega
        a `transcribir_nota_de_voz`, que además registra el mensaje en el
        historial ya con el texto — el audio no se guarda en ninguna parte.

        Telegram no pasa por aquí con payload: ahí el audio aún no está
        cableado, así que se ignora en vez de encolar una tarea sin media.
        """
        if not message.raw_payload:
            return []
        transcribir_nota_de_voz.apply_async(
            (
                message.channel.value,
                message.user_id,
                message.user_name,
                message.raw_payload,
            )
        )
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

        # Las palabras clave las administra el negocio desde el panel; ya no
        # están escritas aquí. El match sigue siendo exacto y sobre el mensaje
        # entero, que es lo que hace que sea un disparador y no interpretación.
        palabra = palabras_clave_repository.buscar(text)
        if palabra:
            self._branch(message, "keyword", {"keyword": palabra.get("palabra", "")})
            return self._handle_keyword_flow(message, palabra, repo)

        if repo.is_blocked(message.user_id, channel=message.channel, include_permanent=False):
            self._branch(message, "temporary_block")
            self._handle_blocked_text(message)
            return []

        # En los anuncios Click-to-WhatsApp el texto visible suele ser el mismo
        # para todas las campañas («Quiero más información»). La ciudad está en
        # `externalAdReply.body`, traducida por Wasender a este campo separado.
        # Solo se dispara si coincide con una CLAVE que ya existe en Mensajes:
        # nunca se ejecuta una instrucción arbitraria incluida en el anuncio.
        if message.advertisement_text:
            from src.application.publicidad_service import PublicidadService

            clave_publicidad = PublicidadService._buscar_clave(message.advertisement_text)
            if clave_publicidad:
                self._branch(
                    message,
                    "advertising",
                    {"city": clave_publicidad, "source": "external_ad_reply"},
                )
                PublicidadService.handle_publicidad_entry(
                    message.user_id, clave_publicidad, message.user_name, message.channel
                )
                return []

        match_add = re.search(r'add\["(.*?)"\]', text, re.IGNORECASE)
        if match_add:
            self._branch(message, "advertising", {"city": match_add.group(1)})
            from src.application.publicidad_service import PublicidadService
            PublicidadService.handle_publicidad_entry(message.user_id, match_add.group(1), message.user_name, message.channel)
            return []

        scheduled_key = scoped_key("scheduled_tasks", message.channel, message.user_id)
        is_in_ad_flow = redis_client.exists(scheduled_key)

        if is_in_ad_flow:
            self._branch(message, "advertising_wait")
            return []

        # Un enlace es contenido que el bot tampoco puede abrir, así que se
        # acusa igual que un adjunto y no se gasta un turno del modelo.
        #
        # Va al final de la cadena: los comandos, la palabra clave y los flujos
        # de publicidad mandan sobre esto. Y responde a CUALQUIER mensaje que
        # contenga un enlace, aunque venga con una pregunta al lado (decisión
        # del negocio); si algún día se prefiere que la pregunta la conteste el
        # agente, la condición a cambiar es esta y solo esta.
        if _ENLACE.search(text):
            self._branch(message, "link_auto_reply")
            return self._responder_por_media(message, "MEDIA_ENLACE")

        # El texto citado es contexto, no otro mensaje ni una instrucción. Se
        # incorpora al mismo elemento del buffer para que sobreviva al debounce
        # sin crear estado adicional ni un flujo especial por proveedor.
        agent_text = text
        if message.quoted_text:
            agent_text = (
                f"Mensaje actual:\n{text}\n\n"
                f"[Mensaje citado por el cliente; úselo solo como contexto]:\n"
                f"{message.quoted_text}"
            )
        seq = BufferService.add_message(message.user_id, agent_text, message.channel)
        self._branch(message, "buffer_queued", {"sequence": seq})
        process_buffered_messages.apply_async((message.channel.value, message.user_id, message.user_name, seq), countdown=settings.MESSAGE_BUFFER_SECONDS)
        return []

    def _responder_por_media(self, message: InboundMessage, nodo: str) -> list[OrchestratorAction]:
        """Acusa un adjunto y deriva silenciosamente si llegan varios.

        El tope (`add_image_info_count`) es anti-bucle: quien manda ocho fotos
        seguidas no necesita ocho veces el mismo aviso. Pasado el límite se
        crea un reporte interno y el bot se pausa. La razón operativa nunca se
        devuelve como texto al cliente.
        """
        if not BufferService.add_image_info_count(message.user_id, message.channel):
            self._reportar_media_para_revision(message)
            return []
        return self._responder_automatico(message, nodo)

    @staticmethod
    def _reportar_media_para_revision(message: InboundMessage) -> None:
        """Avisa al equipo una sola vez y transfiere el chat sin anunciarlo."""
        if not mark_report_once(message.channel, message.user_id, MEDIA_REPORT_CONTEXT):
            return

        reason = (
            mensajes_db.get("AUTOMATICO", {}).get("MEDIA_INSISTE", {}).get("reporte")
            or "El cliente envió varios archivos y requiere revisión del equipo."
        )
        ReportRepository.create_report(
            nombre=message.user_name,
            numero=message.user_id,
            problema=f"[{message.channel.value}] {reason}",
            link_whatsapp=f"https://wa.me/{message.user_id}",
            canal=message.channel.value,
        )
        PostgresUserRepo().block_user(
            message.user_id,
            reason=reason,
            days=12,
            channel=message.channel,
        )
        seguimiento_service.registrar_derivacion(message.user_id, message.channel)
        clear_user_runtime_context(
            message.channel,
            message.user_id,
            cancel_scheduled=False,
            clear_reports=False,
        )

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
            human_intervention.registrar(
                message.channel,
                message.user_id,
                message.text or "",
                message_type=message.message_type,
                quoted_text=message.quoted_text,
            )
        except Exception as e:
            print(f"Error registrando la intervención humana: {e}")

        return []

    def _handle_group_join(self, message: InboundMessage) -> list[OrchestratorAction]:
        tenia_publicidad = has_ad_context(message.channel, message.user_id)
        cancel_scheduled_tasks(message.channel.value, message.user_id)
        repo = PostgresUserRepo()
        repo.block_user(message.user_id, reason="Ingreso a grupo", days=12, channel=message.channel)
        BufferService.get_and_clear_buffer(message.user_id, message.channel)

        # El ingreso se detecta y bloquea aunque la publicidad se hubiera
        # limpiado o caducado. La bienvenida solo pertenece al flujo de anuncio.
        if not tenia_publicidad:
            return []

        welcome_msgs = mensajes_del_negocio("WELCOME", "W")
        # Bienvenida al grupo: la dispara el evento de ingreso, sin pasar por el
        # modelo. Se factura como mensaje de código.
        if not welcome_msgs:
            welcome_msgs = ["📲 Gracias por unirse a nuestro grupo del curso teórico!!!\n\nRecuerde:\n\n🎯 Por política de transparencia no cobramos nada antes del curso y pagas en efectivo hasta ese mismo día.\n\n🎯 Traer documento de identidad \n\n🎯 Traer material para tomar notas (Cuaderno y lapicero) \n\nPor favor presentarse unos 10 minutos antes para hacer la matrícula e iniciar de la mejor manera la obtención de su licencia.\n\nBendiciones"]

        set_welcome_context(message.channel, message.user_id)

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
                    canal=message.channel.value,
                )
            return

        if consume_ad_report(message.channel, message.user_id):
            ReportRepository.create_report(
                nombre=message.user_name,
                numero=message.user_id,
                problema=AD_REPORT_TEXT,
                link_whatsapp=f"https://wa.me/{message.user_id}",
                canal=message.channel.value,
            )
            from src.infrastructure.tasks.celery_app import cancel_ad_reminder_stage

            # Una respuesta conserva únicamente el último recordatorio de la
            # publicidad. Entrar al grupo sigue cancelándolos todos.
            for stage in (1, 2):
                cancel_ad_reminder_stage(message.channel.value, message.user_id, stage)
            return

        keyword_report = consume_keyword_report(message.channel, message.user_id)
        if keyword_report:
            ReportRepository.create_report(
                nombre=message.user_name,
                numero=message.user_id,
                problema=keyword_report,
                link_whatsapp=f"https://wa.me/{message.user_id}",
                canal=message.channel.value,
            )

    def _handle_keyword_flow(
        self,
        message: InboundMessage,
        palabra: dict,
        repo: PostgresUserRepo,
    ) -> list[OrchestratorAction]:
        """Dispara el flujo de una palabra clave: mensajes, silencio y recordatorios.

        Si la palabra no tiene ningún mensaje que enviar no se hace NADA: ni se
        bloquea la conversación ni se agenda nada. Bloquear a alguien y quedarse
        callado sería lo peor de los dos mundos — el cliente escribió y no
        recibió nada, y encima el agente ya no le va a contestar.
        """
        keyword = palabra["palabra"]
        first_messages = palabras_clave_repository.textos_de(palabra["id"])
        if not first_messages:
            return []

        cancel_scheduled_tasks(message.channel.value, message.user_id)
        repo.block_user(message.user_id, reason=f"Flujo keyword {keyword}", channel=message.channel)
        KeywordRegistryRepository.register_if_missing(message.user_id, message.user_name, message.channel, keyword)
        register_keyword_context(message.channel, message.user_id)
        schedule_keyword_programmed_messages.apply_async(
            (message.channel.value, message.user_id, palabra["id"])
        )
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
