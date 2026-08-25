"""Los tiempos editables del panel gobiernan las tareas del bot."""

import os
import unittest
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.infrastructure.tasks import celery_app as tasks
from src.infrastructure.channels.outbound_coordinator import PrioridadSalida
from src.application.message_handler import MessageHandler
from src.domain.entities import Channel, MessageType, OrchestratorAction


class TiemposDeMensajesTests(unittest.TestCase):
    def test_una_respuesta_frena_los_intermedios_aunque_la_tarea_ya_haya_despertado(self):
        with patch.object(tasks, "has_ad_context", return_value=True), patch.object(
            tasks, "ad_report_consumed", return_value=True
        ), patch.object(tasks, "set_ad_reminder_stage") as etapa, patch.object(
            tasks.ChannelSenderRegistry, "send"
        ) as enviar:
            tasks.send_ad_reminder("whatsapp", "506", "intermedio", 1)
            tasks.send_ad_reminder("whatsapp", "506", "último", 3)

        enviar.assert_called_once_with(
            "whatsapp", "506", "último", prioridad=PrioridadSalida.RECORDATORIO
        )
        etapa.assert_called_once_with("whatsapp", "506", 3)

    def test_los_mensajes_inmediatos_del_sistema_respetan_la_pausa(self):
        acciones = [
            OrchestratorAction("send_now", Channel.WHATSAPP, "506", "uno"),
            OrchestratorAction("send_now", Channel.WHATSAPP, "506", "dos"),
        ]
        with patch(
            "src.application.message_handler.ConversationOrchestrator.handle",
            return_value=acciones,
        ), patch(
            "src.application.message_handler.instrucciones_repository.intervalo_entre_mensajes",
            return_value=5,
        ), patch(
            "src.application.message_handler.ChannelSenderRegistry.send"
        ) as enviar, patch("src.application.message_handler.time.sleep") as dormir:
            MessageHandler.handle_incoming_message(
                "506", "hola", MessageType.TEXT, channel=Channel.WHATSAPP, proyecto_id=1
            )

        self.assertEqual(enviar.call_count, 2)
        dormir.assert_called_once_with(5)

    def test_la_secuencia_espera_entre_mensajes_pero_no_despues_del_ultimo(self):
        with patch.object(
            tasks.instrucciones_repository, "intervalo_entre_mensajes", return_value=5
        ), patch.object(tasks.ChannelSenderRegistry, "send") as enviar, patch.object(
            tasks.time, "sleep"
        ) as dormir:
            tasks.send_delayed_message_sequence("whatsapp", "506", ["uno", "dos", "tres"])

        self.assertEqual(enviar.call_count, 3)
        self.assertEqual(dormir.call_args_list, [call(5), call(5)])

    def test_publicidad_usa_los_tres_tiempos_del_proyecto(self):
        config = {
            "publicidad_recordatorio_1_segundos": 600,
            "publicidad_recordatorio_2_segundos": 7200,
            "publicidad_recordatorio_3_segundos": 86400,
        }
        tareas = [MagicMock(id=f"t{i}") for i in range(3)]
        with patch.object(
            tasks.instrucciones_repository, "configuracion_tiempos_mensajes", return_value=config
        ), patch.object(
            tasks.send_ad_reminder, "apply_async", side_effect=tareas
        ) as agendar, patch.object(tasks, "redis_client"):
            tasks.schedule_ad_programmed_messages(
                "whatsapp", "506", "lunes", "10000", "8:00", "https://chat.whatsapp.com/x"
            )

        self.assertEqual(
            [llamada.kwargs["countdown"] for llamada in agendar.call_args_list],
            [600, 7200, 86400],
        )


if __name__ == "__main__":
    unittest.main()
