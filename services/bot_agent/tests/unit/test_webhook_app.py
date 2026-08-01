import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.domain.entities import Channel, MessageType
from src.infrastructure.webhooks import app as webhook_app


class WebhookAppTests(unittest.TestCase):
    def test_health_route_exists(self):
        self.assertEqual(webhook_app.health(), {"status": "ok"})

    def test_expected_routes_exist(self):
        routes = {getattr(route, "path", "") for route in webhook_app.app.routes}
        self.assertIn("/webhooks/wasender", routes)
        self.assertIn("/webhooks/wasender/{token}", routes)
        self.assertIn("/internal/rag/sync/{chunk_id}", routes)


class WasenderWebhookTests(unittest.TestCase):
    """El webhook hace que el bot conteste y gaste tokens: no puede quedar abierto."""

    def _evento(self, **clave) -> dict:
        return {
            "event": "messages.received",
            "data": {
                "messages": {
                    "key": {"remoteJid": "50688888888@s.whatsapp.net", "id": "ABC123", **clave},
                    "message": {"conversation": "hola"},
                }
            },
        }

    def test_disabled_without_configured_secret(self):
        with patch("src.infrastructure.webhooks.app.settings.WASENDER_WEBHOOK_SECRET", ""):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(webhook_app.wasender_webhook({}, x_webhook_signature="", token="cualquiera"))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_rejects_wrong_secret(self):
        with patch("src.infrastructure.webhooks.app.settings.WASENDER_WEBHOOK_SECRET", "secreto"):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(webhook_app.wasender_webhook({}, x_webhook_signature="", token="incorrecto"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_accepts_secret_from_header(self):
        """WasenderAPI puede mandar el secreto por cabecera en vez de query."""
        with patch("src.infrastructure.webhooks.app.settings.WASENDER_WEBHOOK_SECRET", "secreto"), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = asyncio.run(
                webhook_app.wasender_webhook(self._evento(), x_webhook_signature="secreto")
            )

        self.assertEqual(resultado["status"], "ok")
        _, kwargs = handler_mock.call_args
        self.assertEqual(kwargs["user_id"], "50688888888")
        self.assertEqual(kwargs["content"], "hola")
        self.assertEqual(kwargs["msg_type"], MessageType.TEXT)
        self.assertEqual(kwargs["channel"], Channel.WHATSAPP)

    def test_ignores_group_messages(self):
        evento = {
            "event": "messages-group.received",
            "data": {
                "messages": {
                    "key": {"remoteJid": "12345@g.us"},
                    "message": {"conversation": "hola grupo"},
                }
            },
        }
        with patch("src.infrastructure.webhooks.app.settings.WASENDER_WEBHOOK_SECRET", "secreto"), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = asyncio.run(webhook_app.wasender_webhook(evento, x_webhook_signature="", token="secreto"))

        self.assertEqual(resultado["status"], "ignored")
        handler_mock.assert_not_called()


class MensajesSalientesTests(unittest.TestCase):
    """El bot y el dueño comparten el número: hay que distinguir quién escribió.

    Es la diferencia entre "el bot respondió" y "una persona entró a atender y
    el bot debe callarse 12 días". Si se confundieran, el bot se bloquearía solo
    en su primera respuesta.
    """

    def _saliente(self, texto: str = "buenas, le atiendo yo") -> dict:
        return {
            "event": "message.sent",
            "data": {
                "messages": {
                    "key": {"remoteJid": "50688888888@s.whatsapp.net", "id": "MSG-1", "fromMe": True},
                    "message": {"conversation": texto},
                }
            },
        }

    def test_el_eco_del_propio_bot_se_ignora(self):
        with patch(
            "src.infrastructure.webhooks.app.outbound_registry.es_envio_del_bot", return_value=True
        ), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(self._saliente())

        self.assertEqual(resultado["status"], "ignored")
        handler_mock.assert_not_called()

    def test_el_dueno_escribiendo_es_intervencion_humana(self):
        with patch(
            "src.infrastructure.webhooks.app.outbound_registry.es_envio_del_bot", return_value=False
        ), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(self._saliente())

        self.assertEqual(resultado["status"], "intervencion_humana")
        _, kwargs = handler_mock.call_args
        self.assertTrue(kwargs["from_me"])
        self.assertEqual(kwargs["user_id"], "50688888888")
        self.assertEqual(kwargs["channel"], Channel.WHATSAPP)


class IngresoAlGrupoTests(unittest.TestCase):
    """Unirse al grupo cierra el flujo de publicidad."""

    def _evento(self, accion: str, participantes: list) -> dict:
        return {
            "event": "group-participants.update",
            "data": {"id": "120363@g.us", "action": accion, "participants": participantes},
        }

    def test_el_alta_dispara_un_group_join_por_participante(self):
        evento = self._evento("add", ["50688888888@s.whatsapp.net", "50677777777@s.whatsapp.net"])
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(evento)

        self.assertEqual(resultado["status"], "group_join")
        self.assertEqual(handler_mock.call_count, 2)
        numeros = {llamada.kwargs["user_id"] for llamada in handler_mock.call_args_list}
        self.assertEqual(numeros, {"50688888888", "50677777777"})
        for llamada in handler_mock.call_args_list:
            self.assertEqual(llamada.kwargs["event_type"], "group_join")

    def test_las_salidas_del_grupo_no_hacen_nada(self):
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(
                self._evento("remove", ["50688888888@s.whatsapp.net"])
            )

        self.assertEqual(resultado["status"], "ignored")
        handler_mock.assert_not_called()


class WebhookPorClienteTests(unittest.TestCase):
    """Cada negocio tiene su URL; el token de la ruta es la credencial."""

    def setUp(self):
        from src.infrastructure.repositories import clientes_whatsapp_repo

        clientes_whatsapp_repo.limpiar_cache()

    def test_token_desconocido_se_rechaza(self):
        with patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.por_token", return_value=None
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(webhook_app.wasender_webhook_cliente("inventado", {}))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_si_el_negocio_tiene_secreto_se_exige(self):
        """Una credencial configurada no puede saltarse omitiéndola."""
        cliente = {"id": 7, "nombre": "Escuela", "wasender_webhook_secret": "s3creto"}
        with patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.por_token", return_value=cliente
        ), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(webhook_app.wasender_webhook_cliente("token-bueno", {}, x_webhook_signature=""))
        self.assertEqual(ctx.exception.status_code, 401)
        handler_mock.assert_not_called()

    def test_con_el_secreto_correcto_pasa(self):
        cliente = {"id": 7, "nombre": "Escuela", "wasender_webhook_secret": "s3creto"}
        with patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.por_token", return_value=cliente
        ), patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.registrar_evento"
        ), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ):
            resultado = asyncio.run(
                webhook_app.wasender_webhook_cliente("token-bueno", {}, x_webhook_signature="s3creto")
            )
        self.assertEqual(resultado["status"], "ignored")

    def test_sin_secreto_configurado_manda_solo_el_token(self):
        """No todas las sesiones de WasenderAPI tienen secreto; sin él el token basta."""
        cliente = {"id": 7, "nombre": "Escuela", "wasender_webhook_secret": ""}
        with patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.por_token", return_value=cliente
        ), patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.registrar_evento"
        ), patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ):
            resultado = asyncio.run(
                webhook_app.wasender_webhook_cliente("token-bueno", {}, x_webhook_signature="")
            )
        self.assertEqual(resultado["status"], "ignored")

    def test_token_valido_procesa_y_deja_rastro(self):
        cliente = {"id": 7, "nombre": "Escuela de manejo"}
        evento = {
            "event": "messages.received",
            "data": {
                "messages": {
                    "key": {"remoteJid": "50688888888@s.whatsapp.net", "id": "X1"},
                    "message": {"conversation": "hola"},
                }
            },
        }
        with patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.por_token", return_value=cliente
        ), patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.registrar_evento"
        ) as registrar_mock, patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = asyncio.run(webhook_app.wasender_webhook_cliente("token-bueno", evento))

        self.assertEqual(resultado["status"], "ok")
        registrar_mock.assert_called_once_with(7, "messages.received")
        handler_mock.assert_called_once()


class RagSyncEndpointTests(unittest.TestCase):
    def test_disabled_without_internal_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", ""):
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.sincronizar_chunk(1, token="cualquiera")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_rejects_wrong_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"):
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.sincronizar_chunk(1, token="incorrecto")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_syncs_chunk_with_valid_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"), patch(
            "src.infrastructure.webhooks.app.rag_service.sync_chunk_id",
            return_value={"upserted": 1, "deleted": 0, "ignored": 0},
        ) as sync_mock:
            resultado = webhook_app.sincronizar_chunk(7, token="secreto")

        self.assertEqual(resultado["status"], "ok")
        self.assertEqual(resultado["upserted"], 1)
        sync_mock.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
