import time

from celery import Celery, Task
from celery.signals import before_task_publish
from celery.schedules import crontab
from src.core.config import settings
from src.application.buffer_service import BufferService, redis_client, scoped_key
from src.application.project_context import ambito_proyecto, proyecto_actual
from src.application.message_catalog import get_node_data
from src.application.reminder_service import ReminderService
from src.application.horario_recordatorios import (
    planificar_recordatorio,
    planificar_secuencia,
    segundos_hasta_horario_permitido,
)
from src.application.drenaje_recordatorios import (
    DrenajeNoDisponible,
    TurnoDrenaje,
    confirmar_envio as confirmar_drenaje,
    liberar_turno as liberar_drenaje,
    solicitar_turno as solicitar_turno_drenaje,
)
from src.application.runtime_context import (
    RUNTIME_TTL_SECONDS,
    ad_report_consumed,
    get_ad_task_id,
    has_ad_context,
    has_keyword_context,
    save_ad_task_id,
    set_ad_reminder_stage,
    set_keyword_active_report,
)
from src.domain.entities import Channel, MessageType
from src.application import seguimiento_service, transcripcion_service
from src.infrastructure.repositories import clientes_whatsapp_repo
from src.infrastructure.channels.senders import ChannelSenderRegistry
from src.infrastructure.channels.outbound_coordinator import (
    CoordinacionSalidaNoDisponible,
    PrioridadSalida,
    SalidaOcupada,
)
from src.infrastructure.channels.wasender import WasenderNoConfigurado
from src.infrastructure.repositories import envios_repository
from src.infrastructure.repositories import palabras_clave_repository
from src.infrastructure.repositories import instrucciones_repository
from src.infrastructure.repositories.conversation_state_repo import ConversationStateRepo
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.repositories.report_repository import ReportRepository
from src.infrastructure.evals.conversation_shots import (
    ConversationShotBuilder,
    ConversationShotRepository,
    ShotTraceCollector,
)
from src.application.agent_pipeline import run_agent_turn

class TareaDeProyecto(Task):
    """Propaga el proyecto de la tarea que agenda a la tarea agendada."""

    def __call__(self, *args, **kwargs):
        headers = getattr(self.request, "headers", None) or {}
        with ambito_proyecto(headers.get("proyecto_id", proyecto_actual())):
            return self.run(*args, **kwargs)


celery_app = Celery("bot_agent_tasks", broker=settings.REDIS_URL, task_cls=TareaDeProyecto)


@before_task_publish.connect
def _propagar_proyecto(headers=None, **_kwargs):
    """Añade el ámbito sin reemplazar `Task.apply_async` ni el aislamiento de tests."""
    if headers is not None:
        headers.setdefault("proyecto_id", proyecto_actual())

# Con broker Redis, una tarea con countdown/ETA queda "sin ack" hasta que se
# ejecuta. Si el countdown supera el visibility_timeout (1h por defecto), Redis
# re-entrega la tarea y el cliente recibe el mensaje DUPLICADO una vez por hora.
# El timeout debe superar con margen el countdown más largo que agendamos
# (recordatorios de publicidad, hasta PUB_DELAY_3_SEC).
#
# Y también el de las PALABRAS CLAVE, que desde que se administran en el panel ya
# no son un número del código: el dueño del negocio escribe los minutos. El tope
# que él puede poner es `MAX_RECORDATORIO_MINUTOS`, y el panel lo valida con ese
# mismo número (`palabras_clave.MAX_MINUTOS`). Si allí sube y aquí no, un
# recordatorio a 15 días se re-entregaría cada pocos días y el cliente lo
# recibiría una y otra vez.
MAX_RECORDATORIO_MINUTOS = 20160  # 14 días

_max_countdown_seconds = max(
    settings.PUB_DELAY_1_SEC,
    settings.PUB_DELAY_2_SEC,
    settings.PUB_DELAY_3_SEC,
    MAX_RECORDATORIO_MINUTOS * 60,
    86400,
)
celery_app.conf.broker_transport_options = {
    "visibility_timeout": _max_countdown_seconds * 2,
}
# Nadie consulta resultados de tareas (no hay result backend); esto evita que
# alguien lo configure a futuro y Redis se llene de claves celery-task-meta-*.
celery_app.conf.task_ignore_result = True
celery_app.conf.broker_connection_retry_on_startup = True

# Tarea periódica de retención: una vez al día purga el historial de
# conversaciones (NocoDB) vencido. La hora es UTC (~02:00 en Costa Rica), un
# horario de baja actividad. Requiere que el worker corra con beat (`-B`).
celery_app.conf.beat_schedule = {
    "purgar-conversaciones-vencidas": {
        "task": "src.infrastructure.tasks.celery_app.purge_expired_conversations",
        "schedule": crontab(hour=8, minute=0),
    },
    # Las bandejas del panel (reportes revisados, preguntas entendidas) caducan
    # cada hora y no una vez al día: el panel promete «se borra en 24 horas» y
    # con una pasada diaria podían ser 48.
    "purgar-bandejas-atendidas": {
        "task": "src.infrastructure.tasks.celery_app.purge_bandejas",
        "schedule": crontab(minute=0),
    },
    # Red de seguridad del seguimiento: vuelca a Postgres los buffers que no se
    # volcaron inline (costos de followups sin mensaje, caídas de la base).
    "volcar-seguimiento-pendiente": {
        "task": "src.infrastructure.tasks.celery_app.flush_seguimiento_pendiente",
        "schedule": crontab(minute="*/5"),
    },
    # Cola de envíos manuales del dashboard. El dashboard solo inserta filas en
    # estado 'pendiente'; enviarlas es cosa del bot, que es quien tiene los
    # canales configurados. Cada 10s para que un envío se sienta inmediato.
    "procesar-envios-pendientes": {
        "task": "src.infrastructure.tasks.celery_app.procesar_envios_pendientes",
        "schedule": 10.0,
    },
}

# Candado de procesamiento por conversación. Debe superar el peor caso de un
# turno (varias llamadas al LLM encadenadas con timeout de 30s); si el worker
# muere a mitad de turno, el candado expira solo y la conversación no queda
# trabada.
_PROCESSING_LOCK_TTL_SECONDS = 120


@celery_app.task
def process_buffered_messages(channel: str, user_id: str, user_name: str = "Desconocido", seq: int | None = None):
    channel_value = Channel(channel)

    # Un solo turno en proceso a la vez por conversación. Sin este candado, un
    # mensaje que llega mientras el turno anterior sigue esperando al LLM crea
    # dos turnos del agente en paralelo del MISMO usuario: el turno más lento
    # pisa el estado del más nuevo y las respuestas salen desordenadas. Si el candado
    # está tomado, la tarea se reagenda SIN drenar el buffer: el debounce por
    # `seq` sigue decidiendo cuál tarea procesa la ráfaga.
    lock_key = scoped_key("processing", channel_value, user_id)
    if not redis_client.set(lock_key, "1", nx=True, ex=_PROCESSING_LOCK_TTL_SECONDS):
        process_buffered_messages.apply_async((channel, user_id, user_name, seq), countdown=2)
        return
    try:
        _process_buffered_messages_locked(channel_value, user_id, user_name, seq)
    finally:
        redis_client.delete(lock_key)


def _process_buffered_messages_locked(channel_value: Channel, user_id: str, user_name: str, seq: int | None):
    # La pausa puede haberse creado después de encolar este turno (por ejemplo,
    # otro mensaje del mismo cliente generó un reporte mientras este esperaba
    # el debounce). No basta con comprobarla en el webhook: una tarea vieja no
    # debe hacer hablar al agente después del handoff.
    if PostgresUserRepo().is_blocked(user_id, channel=channel_value):
        BufferService.get_and_clear_buffer(user_id, channel_value)
        return

    if seq is None:
        # Compatibilidad con tareas agendadas por versiones anteriores.
        text = BufferService.get_and_clear_buffer(user_id, channel_value)
    else:
        # Debounce: solo procesa la tarea del último mensaje de la ráfaga.
        drained = BufferService.drain_if_current(user_id, channel_value, seq)
        if drained is None:
            return  # Llegó un mensaje más nuevo; esta tarea quedó obsoleta.
        text = drained
    if not text.strip():
        return # Buffer was empty or cleared by a command

    ReminderService.cancel(channel_value, user_id)
    state_before = ConversationStateRepo.get(channel_value, user_id)
    with ShotTraceCollector() as shot_collector:
        result = run_agent_turn(channel_value, user_id, text, user_name=user_name)
    state_after = ConversationStateRepo.get(channel_value, user_id)

    _save_conversation_shot(
        channel=channel_value,
        user_id=user_id,
        user_name=user_name,
        user_message=text,
        bot_replies=result.replies,
        state_before=state_before,
        state_after=state_after,
        trace_events=shot_collector.events,
    )

    if result.replies:
        for indice, msg in enumerate(result.replies):
            if indice:
                time.sleep(instrucciones_repository.intervalo_entre_mensajes())
            ChannelSenderRegistry.send(
                channel_value, user_id, msg, prioridad=PrioridadSalida.INTERACTIVA
            )

    if result.reminder:
        schedule_smart_reminder_for_result(channel_value, user_id, result.reminder)


def _save_conversation_shot(
    *,
    channel: Channel | str,
    user_id: str,
    user_name: str,
    user_message: str,
    bot_replies: list[str],
    state_before,
    state_after,
    trace_events: list[dict],
):
    _, fecha_hora, shot = ConversationShotBuilder.build(
        channel=channel,
        user_id=user_id,
        user_name=user_name,
        user_message=user_message,
        bot_replies=bot_replies,
        state_before=state_before,
        state_after=state_after,
        trace_events=trace_events,
    )
    ConversationShotRepository.save(
        fecha_hora=fecha_hora,
        id_user=user_id,
        chanel=channel,
        shot=shot,
    )

import time
import random

@celery_app.task
def transcribir_nota_de_voz(channel: str, user_id: str, user_name: str, payload: dict):
    """Convierte una nota de voz en texto y la mete al flujo como si fuera texto.

    Corre en el worker y no en el webhook porque descifrar + descargar +
    transcribir tarda segundos: bloquear la respuesta del webhook haría que
    WasenderAPI reintentara el evento creyendo que se cayó, y el cliente
    recibiría la respuesta por duplicado.

    El audio NO se guarda en ningún sitio. Lo único que queda es la
    transcripción, que entra al historial como texto del cliente.
    """
    canal = Channel(channel)
    repo = PostgresUserRepo()
    if repo.is_blocked(user_id, channel=canal):
        ConversationLogRepository.log_inbound(
            client_id=user_id,
            canal=canal,
            sender_name=user_name,
            message_type=MessageType.AUDIO,
            text="",
            event_type="audio_bloqueado",
        )
        return {"transcrito": False, "segundos": 0, "bloqueado": True}

    api_key = clientes_whatsapp_repo.api_key_de_envio(canal.value, user_id)
    resultado = transcripcion_service.transcribir(payload, api_key)

    # Se cobra lo que se transcribió aunque el texto salga vacío: el proveedor
    # cobra por el audio procesado, no por el resultado.
    if resultado.segundos:
        seguimiento_service.registrar_uso_audio(
            user_id, canal, resultado.segundos, resultado.modelo
        )

    # El bloqueo pudo aparecer mientras el proveedor transcribía. Guardamos lo
    # recibido para que el asesor lo vea, pero no enviamos acuse ni lo pasamos
    # al agente.
    if repo.is_blocked(user_id, channel=canal):
        ConversationLogRepository.log_inbound(
            client_id=user_id,
            canal=canal,
            sender_name=user_name,
            message_type=MessageType.AUDIO,
            text=resultado.texto if resultado.hay_texto else "",
            event_type="audio_bloqueado",
        )
        return {
            "transcrito": bool(resultado.hay_texto),
            "segundos": resultado.segundos,
            "bloqueado": True,
        }

    if not resultado.hay_texto:
        # No se pudo entender. Se acusa con el texto fijo del negocio en vez de
        # dejar al cliente esperando una respuesta que no va a llegar.
        ConversationLogRepository.log_inbound(
            client_id=user_id,
            canal=canal,
            sender_name=user_name,
            message_type=MessageType.AUDIO,
            text="",
            event_type="audio_no_transcrito",
        )
        for texto in get_node_data("AUTOMATICO", "MEDIA_AUDIO_ILEGIBLE").get("mensajes", []):
            ChannelSenderRegistry.send(
                canal, user_id, texto, prioridad=PrioridadSalida.INTERACTIVA
            )
        return {"transcrito": False, "segundos": resultado.segundos}

    # En el historial queda el TEXTO, marcado como que vino de un audio. El
    # `event_type` es lo que permite al panel mostrar "[Audio transcrito]" sin
    # que el LLM vea nunca esa etiqueta: él solo recibe el texto.
    ConversationLogRepository.log_inbound(
        client_id=user_id,
        canal=canal,
        sender_name=user_name,
        message_type=MessageType.AUDIO,
        text=resultado.texto,
        event_type="audio_transcrito",
    )

    seq = BufferService.add_message(user_id, resultado.texto, canal)
    process_buffered_messages.apply_async(
        (canal.value, user_id, user_name, seq), countdown=settings.MESSAGE_BUFFER_SECONDS
    )
    return {"transcrito": True, "segundos": resultado.segundos}


@celery_app.task
def send_delayed_message_sequence(channel: str, user_id: str, messages: list[str]):
    intervalo = instrucciones_repository.intervalo_entre_mensajes()
    for indice, msg in enumerate(messages):
        if indice:
            time.sleep(intervalo)
        ChannelSenderRegistry.send(
            channel, user_id, msg, prioridad=PrioridadSalida.INTERACTIVA
        )

@celery_app.task
def send_single_message(channel: str, user_id: str, message: str):
    ChannelSenderRegistry.send(channel, user_id, message)

@celery_app.task
def send_ad_reminder(
    channel: str,
    user_id: str,
    message: str,
    stage: int,
    aplazado_por_silencio: bool | None = None,
):
    if not has_ad_context(channel, user_id):
        return
    # La revocación puede cruzarse con una tarea que ya despertó. La respuesta
    # del cliente también se comprueba aquí para que nunca salgan las etapas
    # intermedias; el tercer recordatorio se conserva por regla de publicidad.
    if stage < 3 and ad_report_consumed(channel, user_id):
        return
    espera = segundos_hasta_horario_permitido()
    if espera:
        _reagendar_ad(
            channel, user_id, message, stage, True, espera,
            motivo="horario_silencioso",
        )
        return

    turno = _solicitar_drenaje(channel, aplazado_por_silencio)
    if turno.espera_segundos:
        _reagendar_ad(
            channel, user_id, message, stage, aplazado_por_silencio,
            turno.espera_segundos,
            motivo="drenaje_nocturno",
        )
        return

    try:
        ChannelSenderRegistry.send(
            channel, user_id, message, prioridad=PrioridadSalida.RECORDATORIO
        )
    except SalidaOcupada as exc:
        _liberar_drenaje_seguro(channel, turno)
        _reagendar_ad(
            channel, user_id, message, stage, aplazado_por_silencio,
            exc.espera_segundos,
            motivo="respuesta_interactiva_prioritaria",
        )
        return
    except CoordinacionSalidaNoDisponible:
        _liberar_drenaje_seguro(channel, turno)
        _reagendar_ad(
            channel, user_id, message, stage, aplazado_por_silencio, 30,
            motivo="coordinacion_no_disponible",
        )
        return
    except Exception:
        _liberar_drenaje_seguro(channel, turno)
        raise
    _confirmar_drenaje_seguro(channel, turno)
    set_ad_reminder_stage(channel, user_id, stage)

@celery_app.task
def send_keyword_reminder(
    channel: str,
    user_id: str,
    pieza_id: int,
    stage: int,
    aplazado_por_silencio: bool | None = None,
):
    """Manda un recordatorio de palabra clave, releyéndolo en el momento.

    El texto NO viaja dentro de la tarea: entre que se agenda y que sale pueden
    pasar días, y lo que tiene que llegar es lo que el negocio tenga escrito
    ahora. Releerlo también hace que apagar o borrar un recordatorio en el panel
    sirva de algo para los que ya estaban agendados.
    """
    if not has_keyword_context(channel, user_id):
        return

    pieza = palabras_clave_repository.pieza(pieza_id)
    if not pieza or not pieza.get("activo"):
        return

    texto = palabras_clave_repository.texto_para_enviar(pieza)
    if not texto:
        return

    espera = segundos_hasta_horario_permitido()
    if espera:
        _reagendar_keyword(
            channel, user_id, pieza_id, stage, True, espera,
            motivo="horario_silencioso",
        )
        return

    turno = _solicitar_drenaje(channel, aplazado_por_silencio)
    if turno.espera_segundos:
        _reagendar_keyword(
            channel, user_id, pieza_id, stage, aplazado_por_silencio,
            turno.espera_segundos,
            motivo="drenaje_nocturno",
        )
        return

    try:
        ChannelSenderRegistry.send(
            channel, user_id, texto, prioridad=PrioridadSalida.RECORDATORIO
        )
    except SalidaOcupada as exc:
        _liberar_drenaje_seguro(channel, turno)
        _reagendar_keyword(
            channel, user_id, pieza_id, stage, aplazado_por_silencio,
            exc.espera_segundos,
            motivo="respuesta_interactiva_prioritaria",
        )
        return
    except CoordinacionSalidaNoDisponible:
        _liberar_drenaje_seguro(channel, turno)
        _reagendar_keyword(
            channel, user_id, pieza_id, stage, aplazado_por_silencio, 30,
            motivo="coordinacion_no_disponible",
        )
        return
    except Exception:
        _liberar_drenaje_seguro(channel, turno)
        raise
    _confirmar_drenaje_seguro(channel, turno)
    set_keyword_active_report(channel, user_id, stage, pieza.get("reporte") or "")


def _registrar_aplazamiento(channel: str, motivo: str, espera: int | float) -> None:
    print(
        "Recordatorio aplazado "
        f"proyecto={proyecto_actual()} canal={channel} motivo={motivo} "
        f"espera_segundos={max(1, int(espera))}"
    )


def _solicitar_drenaje(
    channel: str, aplazado_por_silencio: bool | None
) -> TurnoDrenaje:
    try:
        return solicitar_turno_drenaje(channel, aplazado_por_silencio)
    except DrenajeNoDisponible:
        return TurnoDrenaje(espera_segundos=30)


def _liberar_drenaje_seguro(channel: str, turno: TurnoDrenaje) -> None:
    try:
        liberar_drenaje(channel, turno)
    except DrenajeNoDisponible:
        pass


def _confirmar_drenaje_seguro(channel: str, turno: TurnoDrenaje) -> None:
    try:
        confirmar_drenaje(channel, turno)
    except DrenajeNoDisponible as exc:
        # El mensaje ya salió: propagar causaría un duplicado. La reserva queda
        # con TTL y frena temporalmente a los demás aunque Redis haya fallado.
        print(
            "No se pudo confirmar el reloj del recordatorio; "
            f"proyecto={proyecto_actual()} canal={channel} "
            f"error={type(exc).__name__}"
        )


def _reagendar_ad(
    channel: str,
    user_id: str,
    message: str,
    stage: int,
    aplazado_por_silencio: bool | None,
    espera: int | float,
    *,
    motivo: str,
) -> None:
    espera = max(1, int(espera))
    task = send_ad_reminder.apply_async(
        (channel, user_id, message, stage, aplazado_por_silencio), countdown=espera
    )
    key = scoped_key("scheduled_tasks", channel, user_id)
    redis_client.rpush(key, task.id)
    redis_client.expire(key, RUNTIME_TTL_SECONDS)
    save_ad_task_id(channel, user_id, stage, task.id)
    _registrar_aplazamiento(channel, motivo, espera)


def _reagendar_keyword(
    channel: str,
    user_id: str,
    pieza_id: int,
    stage: int,
    aplazado_por_silencio: bool | None,
    espera: int | float,
    *,
    motivo: str,
) -> None:
    espera = max(1, int(espera))
    task = send_keyword_reminder.apply_async(
        (channel, user_id, pieza_id, stage, aplazado_por_silencio), countdown=espera
    )
    key = scoped_key("scheduled_tasks", channel, user_id)
    redis_client.rpush(key, task.id)
    redis_client.expire(key, RUNTIME_TTL_SECONDS)
    _registrar_aplazamiento(channel, motivo, espera)

def cancel_scheduled_tasks(channel: str, user_id: str):
    """
    Cancela todas las tareas de Celery programadas para un usuario.
    """
    key = scoped_key("scheduled_tasks", channel, user_id)
    task_ids = redis_client.lrange(key, 0, -1)
    if task_ids:
        for tid in task_ids:
            celery_app.control.revoke(tid, terminate=True)
        redis_client.delete(key)

def cancel_ad_reminder_stage(channel: str, user_id: str, stage: int):
    task_id = get_ad_task_id(channel, user_id, stage)
    if task_id:
        celery_app.control.revoke(task_id, terminate=True)
        redis_client.delete(f"{scoped_key('ad_reminder_task', channel, user_id)}:{stage}")

def schedule_smart_reminder_for_result(channel: Channel | str, user_id: str, reminder: dict):
    channel_value = Channel(channel).value if isinstance(channel, str) else channel.value
    plan = planificar_recordatorio(reminder.get("seconds", 3600))
    task = send_smart_reminder.apply_async(
        (
            channel_value,
            user_id,
            reminder.get("level", 1),
            plan.aplazado_por_silencio,
        ),
        countdown=plan.segundos,
    )
    ReminderService.save_task(channel, user_id, task.id)

@celery_app.task
def send_smart_reminder(
    channel: str,
    user_id: str,
    level: int = 1,
    aplazado_por_silencio: bool | None = None,
):
    """Recordatorio inteligente: retoma la conversación si quedó algo pendiente.

    El LLM decide si conviene recordar y redacta el mensaje; el código aplica
    las medidas de seguridad duras (anti-bucle): buffer pendiente, cliente
    bloqueado, nada pendiente, tope de recordatorios o tarea obsoleta.
    """
    config_recordatorios = instrucciones_repository.configuracion_recordatorios()
    if not config_recordatorios.get("habilitado", True):
        return

    # El usuario pudo responder justo cuando vence el recordatorio: su mensaje
    # sigue en el buffer (aún sin procesar), pero ya respondió. No enviamos el
    # recordatorio: el process_buffered_messages pendiente cancelará/reagendará
    # según el nuevo estado.
    if BufferService.has_pending(user_id, channel):
        return

    if PostgresUserRepo().is_blocked(user_id, channel=channel):
        return

    state = ConversationStateRepo.get(channel, user_id)
    if not state.awaiting_reply or not state.last_question:
        return
    if state.reminder_level >= settings.FOLLOWUP_MAX_REMINDERS:
        return
    if state.reminder_level >= level:
        return  # Tarea vieja: este nivel ya se envió.

    # Se comprueba al despertar, además de ajustar la hora al agendar. Esto
    # cubre tareas que ya estaban en Redis antes de desplegar esta regla.
    espera = segundos_hasta_horario_permitido()
    if espera:
        _reagendar_smart(
            channel, user_id, level, True, espera, motivo="horario_silencioso"
        )
        return

    turno = _solicitar_drenaje(channel, aplazado_por_silencio)
    if turno.espera_segundos:
        _reagendar_smart(
            channel, user_id, level, aplazado_por_silencio,
            turno.espera_segundos,
            motivo="drenaje_nocturno",
        )
        return

    from src.application.unified_agent import FollowupAgent

    try:
        decision = FollowupAgent().decide(state, client_id=user_id, canal=channel)
    except Exception:
        _liberar_drenaje_seguro(channel, turno)
        raise
    if not decision.send or not decision.message:
        _liberar_drenaje_seguro(channel, turno)
        return

    # La llamada al LLM tarda segundos: el cliente pudo escribir en ese lapso.
    # Se re-verifica el buffer y que el estado no haya cambiado antes de enviar,
    # para no cruzar un recordatorio con una conversación ya avanzada.
    if BufferService.has_pending(user_id, channel):
        _liberar_drenaje_seguro(channel, turno)
        return
    current = ConversationStateRepo.get(channel, user_id)
    if (
        current.last_question != state.last_question
        or current.reminder_level != state.reminder_level
        or not current.awaiting_reply
    ):
        _liberar_drenaje_seguro(channel, turno)
        return
    state = current

    try:
        ChannelSenderRegistry.send(
            channel,
            user_id,
            decision.message,
            prioridad=PrioridadSalida.RECORDATORIO,
        )
    except SalidaOcupada as exc:
        _liberar_drenaje_seguro(channel, turno)
        _reagendar_smart(
            channel, user_id, level, aplazado_por_silencio,
            exc.espera_segundos,
            motivo="respuesta_interactiva_prioritaria",
        )
        return
    except CoordinacionSalidaNoDisponible:
        _liberar_drenaje_seguro(channel, turno)
        _reagendar_smart(
            channel, user_id, level, aplazado_por_silencio, 30,
            motivo="coordinacion_no_disponible",
        )
        return
    except Exception:
        _liberar_drenaje_seguro(channel, turno)
        raise
    _confirmar_drenaje_seguro(channel, turno)

    state.reminder_level = level
    state.last_messages = [decision.message]
    state.conversation_history = [
        *state.conversation_history,
        {
            "flow": "AGENT",
            "node": "",
            "type": "smart_reminder",
            "user": "",
            "bot": [decision.message],
            "pending": state.last_question,
        },
    ][-settings.AGENT_HISTORY_LIMIT:]
    ConversationStateRepo.set(channel, user_id, state)

    if level < settings.FOLLOWUP_MAX_REMINDERS:
        minutos = max(1, min(int(config_recordatorios.get("intervalo_minutos") or 60), 20160))
        plan = planificar_recordatorio(minutos * 60)
        task = send_smart_reminder.apply_async(
            (channel, user_id, level + 1, plan.aplazado_por_silencio),
            countdown=plan.segundos,
        )
        ReminderService.save_task(channel, user_id, task.id)


def _reagendar_smart(
    channel: str,
    user_id: str,
    level: int,
    aplazado_por_silencio: bool | None,
    espera: int | float,
    *,
    motivo: str,
) -> None:
    espera = max(1, int(espera))
    task = send_smart_reminder.apply_async(
        (channel, user_id, level, aplazado_por_silencio), countdown=espera
    )
    ReminderService.save_task(channel, user_id, task.id)
    _registrar_aplazamiento(channel, motivo, espera)

@celery_app.task
def create_flow_report_and_block(channel: str, user_id: str, report_reason: str):
    state = ConversationStateRepo.get(channel, user_id)
    ReportRepository.create_report(
        nombre=state.user_name,
        numero=user_id,
        problema=f"[{channel}] {report_reason}",
        link_whatsapp=f"https://wa.me/{user_id}",
        canal=channel,
    )
    PostgresUserRepo().block_user(user_id, reason=report_reason, days=12, channel=channel)
    from src.application.runtime_context import clear_user_runtime_context

    clear_user_runtime_context(channel, user_id, cancel_scheduled=False, clear_reports=False)

@celery_app.task
def schedule_ad_programmed_messages(channel: str, user_id: str, dia: str, valor: str, hora: str, enlace: str):
    # This prepares the 3 messages with the extracted details
    msg1 = "📌 Hola!!!  No recibí respuesta a nuestra conversación.  Podemos continuar???"
    msg2 = "👋🏻Vi que no se unió al grupo👋🏻\n\n*¿¿¿Tiene alguna duda duda antes de unirse al grupo???"
    msg3 = f"📌Hola!!!\n\nLe comparto la información de nuestro curso.\n\nFecha: {dia}\n\nHora: {hora}\nValor: {valor} colones\n\nUnirse al grupo: {enlace}\n\nLe esperamos"
    
    # El countdown se toma del proyecto al crear la conversación. Cambiarlo en
    # el panel afecta los anuncios nuevos, no mueve tareas que ya estaban en Redis.
    config = instrucciones_repository.configuracion_tiempos_mensajes()
    tiempos = [
        max(5, min(int(config.get(f"publicidad_recordatorio_{i}_segundos") or respaldo), 1209600))
        for i, respaldo in ((1, 7200), (2, 72000), (3, 82800))
    ]
    planes = planificar_secuencia(tiempos)
    tareas = [
        send_ad_reminder.apply_async(
            (channel, user_id, mensaje, etapa, plan.aplazado_por_silencio),
            countdown=plan.segundos,
        )
        for mensaje, etapa, plan in zip((msg1, msg2, msg3), (1, 2, 3), planes)
    ]
    t1, t2, t3 = tareas
    
    # Store IDs to allow cancellation
    key = scoped_key("scheduled_tasks", channel, user_id)
    redis_client.rpush(key, t1.id, t2.id, t3.id)
    redis_client.expire(key, 86400) # Expire in 24h
    save_ad_task_id(channel, user_id, 1, t1.id)
    save_ad_task_id(channel, user_id, 2, t2.id)
    save_ad_task_id(channel, user_id, 3, t3.id)

@celery_app.task
def purge_expired_conversations():
    """Borra el historial de conversaciones que lleva inactivo más de N días.

    Política: `settings.CONVERSATION_RETENTION_DAYS` días desde la última
    interacción. El estado/historial en Redis ya expira solo por TTL; aquí se
    limpia el registro durable en Postgres (log de conversaciones y shots).
    La agenda la dispara Celery beat a diario.
    """
    days = settings.CONVERSATION_RETENTION_DAYS
    conversations = ConversationLogRepository.purge_older_than(days)
    shots = ConversationShotRepository.purge_older_than(days)

    # Las sesiones de envío tienen su propio plazo (12 días) y no el de las
    # conversaciones: son un histórico de trabajo, no el hilo con un cliente.
    lotes = envios_repository.purgar_lotes_vencidos()
    if lotes:
        print(f"[retención] {lotes} sesiones de envío caducadas")

    print(
        f"[retención] Purga >{days}d: {conversations} conversaciones y "
        f"{shots} shots eliminados de Postgres"
    )
    return {"conversations": conversations, "shots": shots}


@celery_app.task
def purge_bandejas():
    """Caduca lo ya atendido de las dos bandejas del panel del proyecto.

    Los reportes revisados (7 días) y las preguntas entendidas (24 horas) no son
    historial: son cosas por hacer, y una lista de cosas por hacer llena de
    cosas hechas deja de servir. Lo PENDIENTE no se toca en ninguna de las dos,
    tenga la edad que tenga.

    Va aparte de la retención de conversaciones y **cada hora**, no una vez al
    día: con una pasada diaria, «se borra en 24 horas» podía tardar 48, y lo que
    se le promete al dueño del negocio en pantalla tiene que cumplirse.
    """
    from src.infrastructure.repositories.report_repository import ReportRepository
    from src.infrastructure.repositories.unanswered_question_repository import (
        UnansweredQuestionRepository,
    )

    reportes = ReportRepository.purge_reviewed()
    preguntas = UnansweredQuestionRepository.purge_answered()
    if reportes or preguntas:
        print(f"[retención] Bandejas: {reportes} reportes y {preguntas} preguntas caducados")
    return {"reportes": reportes, "preguntas": preguntas}


@celery_app.task
def flush_seguimiento_pendiente():
    """Vuelca a Postgres los buffers de seguimiento/resumen mensual pendientes."""
    from src.application import seguimiento_service

    intentos = seguimiento_service.flush_pendientes()
    return {"buffers": intentos}


@celery_app.task
def schedule_keyword_programmed_messages(channel: str, user_id: str, palabra_id: int):
    """Agenda los recordatorios de una palabra clave, cada uno a su minuto.

    Los minutos se cuentan desde AHORA (desde que se disparó la palabra), no en
    cascada desde el recordatorio anterior: es como se ven en el panel y como se
    validan ahí, y contar de las dos formas distintas sería garantizar que en
    algún momento no coinciden.

    Los ids de tarea se guardan para poder revocarlos: si el cliente entra al
    grupo, escribe otra palabra o el dueño interviene, los recordatorios
    pendientes se cancelan (`cancel_scheduled_tasks`).
    """
    tasks = []
    piezas = [
        pieza
        for pieza in palabras_clave_repository.piezas_de(palabra_id, "recordatorio")
        if int(pieza.get("minutos") or 0) >= 1
    ]
    planes = planificar_secuencia(
        [int(pieza.get("minutos") or 0) * 60 for pieza in piezas]
    )
    for pieza, plan in zip(piezas, planes):
        task = send_keyword_reminder.apply_async(
            (
                channel,
                user_id,
                pieza["id"],
                pieza["orden"],
                plan.aplazado_por_silencio,
            ),
            countdown=plan.segundos,
        )
        tasks.append(task.id)

    key = scoped_key("scheduled_tasks", channel, user_id)
    if tasks:
        redis_client.rpush(key, *tasks)
        redis_client.expire(key, RUNTIME_TTL_SECONDS)


# --- Cola de envíos manuales del dashboard -----------------------------------

def _texto_de_la_parte(parte: dict) -> str:
    """Arma el texto de una parte añadiendo el marcador de media si la tiene.

    Se reutiliza el mismo formato `Imagen=` / `Video=` que ya entiende
    `ChannelSenderRegistry.send`, en vez de abrir un segundo camino de envío.
    """
    texto = (parte.get("texto") or "").strip()
    tipo = parte.get("media_tipo") or ""
    referencia = (parte.get("media_ref") or "").strip()
    if not tipo or not referencia:
        return texto
    marcador = "Imagen" if tipo == "imagen" else "Video"
    return f"{texto}\n\n{marcador}={referencia}".strip()


def _espera_entre_partes() -> float:
    """Pausa elegida por el negocio antes de la siguiente parte."""
    return float(instrucciones_repository.intervalo_entre_mensajes())


def _clasificar_error(exc: Exception) -> tuple[str, str]:
    """Separa lo que el cliente puede arreglar de lo que debe ver el administrador.

    Devuelve (mensaje_para_el_cliente, detalle_tecnico). Si el mensaje al
    cliente queda vacío, la interfaz solo ofrece reportarlo: significa que la
    causa no está en sus manos.
    """
    detalle = f"{type(exc).__name__}: {exc}"

    # El motivo real de un 4xx viene en el CUERPO de la respuesta, no en el
    # mensaje de la excepción: sin esto, "chat not found" nunca se detectaría y
    # el cliente vería un error en blanco que no puede arreglar.
    cuerpo = ""
    respuesta = getattr(exc, "response", None)
    if respuesta is not None:
        try:
            cuerpo = respuesta.text or ""
        except Exception:
            cuerpo = ""
        if cuerpo:
            detalle = f"{detalle} | respuesta: {cuerpo[:1000]}"

    texto = f"{exc} {cuerpo}".lower()

    if isinstance(exc, WasenderNoConfigurado):
        return ("WhatsApp todavía no está conectado. Avísale al administrador.", detalle)
    if "chat not found" in texto or "chat_id is empty" in texto or "user not found" in texto:
        return ("El ID de destino no existe o el cliente nunca escribió al bot.", detalle)
    if "bot was blocked" in texto or "blocked by the user" in texto:
        return ("Esa persona bloqueó al bot, no se le puede escribir.", detalle)
    if "imagen" in texto or "image" in texto or "video" in texto or "media" in texto:
        return ("No se pudo abrir el archivo adjunto. Revisa que el enlace sea público.", detalle)
    if "message is too long" in texto or "text is too long" in texto:
        return ("El texto es demasiado largo para esta plataforma. Acórtalo.", detalle)
    return ("", detalle)


@celery_app.task
def procesar_envios_pendientes():
    """Envía lo que el dashboard dejó en cola.

    Cada envío es una CADENA de partes que salen una tras otra con una pausa
    aleatoria. Si una parte falla a mitad, se marca el error indicando por cuál
    se quedó: al reintentar se retoma desde ahí, para no repetirle al cliente
    los mensajes que ya recibió.

    Los envíos manuales se facturan como mensajes de código (no pasan por el
    modelo), igual que la palabra clave o los flujos programados.
    """
    envios_repository.rescatar_atascados()

    procesados = {"enviados": 0, "errores": 0, "partes": 0}
    for envio in envios_repository.tomar_pendientes():
        with ambito_proyecto(int(envio.get("proyecto_id") or 0)):
            _procesar_envio_pendiente(envio, procesados)
    return procesados


def _procesar_envio_pendiente(envio, procesados):
    """Procesa una fila dentro del ámbito de las credenciales que la crearon."""
    partes = envio.get("partes") or []
    if not partes:
        envios_repository.marcar_error(
            envio["proyecto_id"], envio["id"], "El mensaje quedó sin contenido.", "sin partes"
        )
        procesados["errores"] += 1
        return

    # Se retoma donde se quedó el intento anterior.
    desde = int(envio.get("partes_enviadas") or 0)
    fallo = None

    for indice in range(desde, len(partes)):
        if indice > desde:
            time.sleep(_espera_entre_partes())
        try:
            ChannelSenderRegistry.send(
                envio["canal"],
                envio["destino_id"],
                _texto_de_la_parte(partes[indice]),
                log_conversation=False,
            )
        except Exception as exc:
            fallo = (indice, exc)
            break
        envios_repository.marcar_parte_enviada(
            envio["proyecto_id"], envio["id"], indice + 1
        )
        procesados["partes"] += 1

    if fallo is not None:
        indice, exc = fallo
        mensaje_cliente, detalle = _clasificar_error(exc)
        if indice > 0:
            mensaje_cliente = (
                f"Se enviaron las primeras {indice} parte(s) y falló la {indice + 1}. "
                f"{mensaje_cliente}"
            ).strip()
        envios_repository.marcar_error(
            envio["proyecto_id"], envio["id"], mensaje_cliente,
            f"parte {indice + 1}: {detalle}"
        )
        procesados["errores"] += 1
        return

    envios_repository.marcar_enviado(envio["proyecto_id"], envio["id"])
    seguimiento_service.registrar_uso_codigo(
        envio["destino_id"], envio["canal"], origen="envio_manual", mensajes=len(partes)
    )
    procesados["enviados"] += 1
