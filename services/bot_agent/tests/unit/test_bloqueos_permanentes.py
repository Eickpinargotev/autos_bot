import unittest
from unittest.mock import MagicMock, patch

from src.application.conversation_orchestrator import ConversationOrchestrator
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.repositories import bloqueos_permanentes_repository


class BloqueosPermanentesTests(unittest.TestCase):
    def test_la_consulta_exige_mismo_negocio_canal_y_numero(self):
        with patch.object(
            bloqueos_permanentes_repository, "consultar_uno", return_value={"id": 4}
        ) as consulta:
            resultado = bloqueos_permanentes_repository.esta_bloqueado(
                "50688887777", Channel.WHATSAPP
            )

        self.assertTrue(resultado)
        sql, parametros = consulta.call_args.args
        self.assertIn("b.proyecto_id = %s", sql)
        self.assertIn("b.numero = %s", sql)
        self.assertEqual(parametros, (1, "whatsapp", "50688887777"))

    def test_un_bloqueado_permanente_no_dispara_ninguna_respuesta(self):
        mensaje = InboundMessage(
            channel=Channel.WHATSAPP,
            user_id="50688887777",
            user_name="Cliente",
            message_type=MessageType.IMAGE,
        )
        with patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch.object(
            bloqueos_permanentes_repository, "esta_bloqueado", return_value=True
        ), patch(
            "src.application.conversation_orchestrator.ConversationOrchestrator._responder_por_media"
        ) as responder:
            acciones = ConversationOrchestrator().handle(mensaje)

        self.assertEqual(acciones, [])
        responder.assert_not_called()

    def test_ingreso_a_grupo_se_bloquea_aunque_no_haya_contexto_publicitario(self):
        mensaje = InboundMessage(
            channel=Channel.WHATSAPP,
            user_id="50688887777",
            user_name="",
            message_type=MessageType.OTHER,
            event_type="group_join",
        )
        repo = MagicMock()
        with patch(
            "src.application.conversation_orchestrator.has_ad_context", return_value=False
        ), patch(
            "src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo
        ), patch(
            "src.application.conversation_orchestrator.cancel_scheduled_tasks"
        ) as cancelar, patch.object(
            ConversationOrchestrator, "_send"
        ) as enviar:
            acciones = ConversationOrchestrator()._handle_group_join(mensaje)

        repo.block_user.assert_called_once_with(
            "50688887777", reason="Ingreso a grupo", days=12, channel=Channel.WHATSAPP
        )
        cancelar.assert_called_once_with("whatsapp", "50688887777")
        enviar.assert_not_called()
        self.assertEqual(acciones, [])


if __name__ == "__main__":
    unittest.main()
