"""La noche se omite solo para recordatorios, usando la hora del negocio."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.application.horario_recordatorios import (
    ajustar_hora,
    planificar_recordatorio,
    planificar_secuencia,
    segundos_hasta_horario_permitido,
    segundos_para_recordatorio,
    segundos_para_secuencia,
)
from src.infrastructure.repositories.conversation_state_repo import ConversationState
from src.infrastructure.tasks import celery_app as tasks
from src.infrastructure.channels.outbound_coordinator import PrioridadSalida
from src.application.drenaje_recordatorios import TurnoDrenaje


ZONA = ZoneInfo("America/Costa_Rica")


def _fecha(hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 8, 24, hora, minuto, tzinfo=ZONA)


def test_un_recordatorio_de_2230_mas_una_hora_se_mueve_a_las_7():
    segundos = segundos_para_recordatorio(
        3600, ahora=_fecha(22, 30), zona_horaria="America/Costa_Rica"
    )

    assert segundos == 8 * 3600 + 30 * 60


def test_los_recordatorios_posteriores_conservan_su_separacion_desde_las_7():
    countdowns = segundos_para_secuencia(
        [3600, 7200], ahora=_fecha(22, 30), zona_horaria="America/Costa_Rica"
    )

    assert countdowns == [8 * 3600 + 30 * 60, 9 * 3600 + 30 * 60]


def test_la_planificacion_marca_toda_la_secuencia_afectada_por_la_noche():
    planes = planificar_secuencia(
        [3600, 7200], ahora=_fecha(22, 30), zona_horaria="America/Costa_Rica"
    )

    assert [plan.aplazado_por_silencio for plan in planes] == [True, True]
    assert planificar_recordatorio(
        60, ahora=_fecha(14), zona_horaria="America/Costa_Rica"
    ).aplazado_por_silencio is False


def test_limites_exactos_del_horario():
    assert ajustar_hora(_fecha(22, 59), "America/Costa_Rica").astimezone(ZONA) == _fecha(22, 59)
    assert ajustar_hora(_fecha(23, 0), "America/Costa_Rica").astimezone(ZONA) == datetime(
        2026, 8, 25, 7, 0, tzinfo=ZONA
    )
    assert ajustar_hora(_fecha(6, 59), "America/Costa_Rica").astimezone(ZONA) == _fecha(7, 0)
    assert ajustar_hora(_fecha(7, 0), "America/Costa_Rica").astimezone(ZONA) == _fecha(7, 0)


def test_una_tarea_inteligente_que_despierta_de_noche_se_reagenda_sin_llm_ni_envio():
    state = ConversationState(
        flow="AGENT", last_question="¿Desea continuar?", awaiting_reply=True,
        reminder_level=0,
    )
    tarea = MagicMock(id="reagendada")
    with patch.object(
        tasks.instrucciones_repository,
        "configuracion_recordatorios",
        return_value={"habilitado": True, "intervalo_minutos": 60},
    ), patch.object(
        tasks.BufferService, "has_pending", return_value=False
    ), patch.object(
        tasks, "PostgresUserRepo"
    ) as repo, patch.object(
        tasks.ConversationStateRepo, "get", return_value=state
    ), patch.object(
        tasks, "segundos_hasta_horario_permitido", return_value=3600
    ), patch.object(
        tasks.send_smart_reminder, "apply_async", return_value=tarea
    ) as reagendar, patch.object(
        tasks.ReminderService, "save_task"
    ) as guardar, patch.object(
        tasks.ChannelSenderRegistry, "send"
    ) as enviar, patch(
        "src.application.unified_agent.FollowupAgent"
    ) as agente:
        repo.return_value.is_blocked.return_value = False
        tasks.send_smart_reminder("whatsapp", "506", 1)

    reagendar.assert_called_once_with(("whatsapp", "506", 1, True), countdown=3600)
    guardar.assert_called_once_with("whatsapp", "506", "reagendada")
    agente.assert_not_called()
    enviar.assert_not_called()


def test_publicidad_y_palabra_clave_tambien_se_reagendan_de_noche():
    tarea_publicidad = MagicMock(id="ad-7am")
    tarea_keyword = MagicMock(id="kw-7am")
    pieza = {
        "id": 10, "activo": True, "texto": "recordatorio", "media_tipo": "",
        "media_ref": "", "reporte": "",
    }
    with patch.object(tasks, "has_ad_context", return_value=True), patch.object(
        tasks, "ad_report_consumed", return_value=False
    ), patch.object(tasks, "has_keyword_context", return_value=True), patch.object(
        tasks.palabras_clave_repository, "pieza", return_value=pieza
    ), patch.object(
        tasks, "segundos_hasta_horario_permitido", return_value=1800
    ), patch.object(
        tasks.send_ad_reminder, "apply_async", return_value=tarea_publicidad
    ) as reagendar_ad, patch.object(
        tasks.send_keyword_reminder, "apply_async", return_value=tarea_keyword
    ) as reagendar_kw, patch.object(tasks, "redis_client"), patch.object(
        tasks, "save_ad_task_id"
    ), patch.object(tasks, "set_ad_reminder_stage") as etapa_ad, patch.object(
        tasks, "set_keyword_active_report"
    ) as etapa_kw, patch.object(tasks.ChannelSenderRegistry, "send") as enviar:
        tasks.send_ad_reminder("whatsapp", "506", "publicidad", 1)
        tasks.send_keyword_reminder("whatsapp", "506", 10, 1)

    reagendar_ad.assert_called_once_with(
        ("whatsapp", "506", "publicidad", 1, True), countdown=1800
    )
    reagendar_kw.assert_called_once_with(
        ("whatsapp", "506", 10, 1, True), countdown=1800
    )
    etapa_ad.assert_not_called()
    etapa_kw.assert_not_called()
    enviar.assert_not_called()


def test_fuera_de_la_noche_no_hay_espera():
    assert segundos_hasta_horario_permitido(
        ahora=_fecha(14), zona_horaria="America/Costa_Rica"
    ) == 0


def test_publicidad_y_keyword_comparten_el_turno_de_drenaje_del_negocio():
    pieza = {
        "id": 10, "activo": True, "texto": "recordatorio", "media_tipo": "",
        "media_ref": "", "reporte": "",
    }
    tarea = MagicMock(id="kw-espera")
    with patch.object(tasks, "has_ad_context", return_value=True), patch.object(
        tasks, "ad_report_consumed", return_value=False
    ), patch.object(tasks, "has_keyword_context", return_value=True), patch.object(
        tasks.palabras_clave_repository, "pieza", return_value=pieza
    ), patch.object(
        tasks,
        "solicitar_turno_drenaje",
        side_effect=[TurnoDrenaje(token="ad"), TurnoDrenaje(espera_segundos=420)],
    ) as reloj, patch.object(
        tasks, "confirmar_drenaje"
    ) as confirmar, patch.object(
        tasks.send_keyword_reminder, "apply_async", return_value=tarea
    ) as reagendar, patch.object(tasks.ChannelSenderRegistry, "send") as enviar:
        tasks.send_ad_reminder("whatsapp", "5061", "publicidad", 3, True)
        tasks.send_keyword_reminder("whatsapp", "5062", 10, 1, True)

    assert reloj.call_count == 2
    confirmar.assert_called_once()
    enviar.assert_called_once_with(
        "whatsapp", "5061", "publicidad", prioridad=PrioridadSalida.RECORDATORIO
    )
    reagendar.assert_called_once_with(
        ("whatsapp", "5062", 10, 1, True), countdown=420
    )
