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
        self.assertIn("/internal/proyectos/{proyecto_id}/rag/sync/{chunk_id}", routes)
        self.assertIn(
            "/internal/proyectos/{proyecto_id}/conversaciones/{canal}/{client_id}/olvidar",
            routes,
        )
        self.assertIn(
            "/internal/proyectos/{proyecto_id}/conversaciones/{canal}/{client_id}/responder",
            routes,
        )

    def test_responder_desde_panel_envia_sin_log_de_ia_y_registra_al_dueno(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"), patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.conversacion_pertenece",
            return_value=True,
        ), patch(
            "src.infrastructure.webhooks.app.ChannelSenderRegistry.send"
        ) as enviar, patch(
            "src.infrastructure.webhooks.app.human_intervention.registrar"
        ) as registrar:
            resultado = webhook_app.responder_como_dueno(
                7, "whatsapp", "506", {"texto": "Le atiendo yo"}, token="secreto"
            )

        self.assertEqual(resultado, {"status": "ok"})
        enviar.assert_called_once_with(Channel.WHATSAPP, "506", "Le atiendo yo", log_conversation=False)
        registrar.assert_called_once_with(Channel.WHATSAPP, "506", "Le atiendo yo")

    def test_responder_no_envia_una_conversacion_ajena(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"), patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.conversacion_pertenece",
            return_value=False,
        ), patch("src.infrastructure.webhooks.app.ChannelSenderRegistry.send") as enviar:
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.responder_como_dueno(
                    7, "whatsapp", "506", {"texto": "hola"}, token="secreto"
                )
        self.assertEqual(ctx.exception.status_code, 404)
        enviar.assert_not_called()


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

    def test_el_mismo_message_id_se_procesa_una_sola_vez(self):
        """Una reentrega del proveedor no puede repetir comandos ni respuestas."""
        evento = self._evento()
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            primero = webhook_app._procesar_evento(evento)
            repetido = webhook_app._procesar_evento(evento)

        self.assertEqual(primero["status"], "ok")
        self.assertEqual(repetido["status"], "duplicate")
        handler_mock.assert_called_once()

    def test_el_payload_original_se_guarda_sanitizado_como_evento_oculto(self):
        evento = self._evento()
        evento["authorization"] = "Bearer secreto"
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ), patch(
            "src.infrastructure.webhooks.app.ConversationLogRepository.log_tool_event"
        ) as log:
            webhook_app._procesar_evento(evento)

        llamada = log.call_args.kwargs
        self.assertEqual(llamada["tool_name"], "wasender.webhook")
        self.assertEqual(llamada["event_type"], "provider_webhook")
        self.assertEqual(llamada["input_data"]["authorization"], "[REDACTADO]")

    def test_sin_message_id_no_se_descartan_textos_repetidos(self):
        """Dos mensajes iguales pueden ser reales; solo el id permite deduplicar."""
        evento = self._evento()
        evento["data"]["messages"]["key"].pop("id")
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            webhook_app._procesar_evento(evento)
            webhook_app._procesar_evento(evento)

        self.assertEqual(handler_mock.call_count, 2)

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

    def test_el_lid_saliente_se_resuelve_al_telefono_del_cliente(self):
        """El dueño y el cliente deben terminar en la misma conversación."""
        evento = self._saliente("Hola")
        evento["data"]["messages"]["key"]["remoteJid"] = "258540019138808@lid"

        with patch(
            "src.infrastructure.webhooks.app.wasender.numero_para_envio",
            return_value="593983512981",
        ) as resolver_mock, patch(
            "src.infrastructure.webhooks.app.outbound_registry.es_envio_del_bot",
            return_value=False,
        ), patch(
            "src.infrastructure.webhooks.app.clientes_whatsapp_repo.vincular_conversacion"
        ) as vincular_mock, patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(
                evento, cliente_id=7, wasender_api_key="clave-del-negocio"
            )

        self.assertEqual(resultado["status"], "intervencion_humana")
        resolver_mock.assert_called_once_with("258540019138808", "clave-del-negocio")
        vincular_mock.assert_called_once_with(7, "whatsapp", "593983512981")
        self.assertEqual(handler_mock.call_args.kwargs["user_id"], "593983512981")

    def test_el_eco_de_api_tambien_se_compara_con_el_telefono_resuelto(self):
        evento = self._saliente("respuesta del bot")
        evento["data"]["messages"]["key"]["remoteJid"] = "258540019138808@lid"

        with patch(
            "src.infrastructure.webhooks.app.wasender.numero_para_envio",
            return_value="593983512981",
        ), patch(
            "src.infrastructure.webhooks.app.outbound_registry.es_envio_del_bot",
            return_value=True,
        ) as propio_mock, patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(
                evento, cliente_id=7, wasender_api_key="clave-del-negocio"
            )

        self.assertEqual(resultado["status"], "ignored")
        self.assertEqual(propio_mock.call_args.args[0], "593983512981")
        handler_mock.assert_not_called()


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

    def test_la_salida_del_grupo_queda_registrada(self):
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            resultado = webhook_app._procesar_evento(
                self._evento("remove", ["50688888888@s.whatsapp.net"])
            )

        self.assertEqual(resultado["status"], "group_leave")
        handler_mock.assert_called_once()
        llamada = handler_mock.call_args
        self.assertEqual(llamada.kwargs["user_id"], "50688888888")
        self.assertEqual(llamada.kwargs["event_type"], "group_leave")

    def test_prefiere_el_telefono_si_el_participante_tambien_trae_lid(self):
        evento = self._evento(
            "add",
            [{"id": "258540019138808@lid", "participantPn": "593983512981@s.whatsapp.net"}],
        )
        with patch(
            "src.infrastructure.webhooks.app.MessageHandler.handle_incoming_message"
        ) as handler_mock:
            webhook_app._procesar_evento(evento)

        self.assertEqual(handler_mock.call_args.kwargs["user_id"], "593983512981")


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
                webhook_app.sincronizar_chunk(1, 1, token="cualquiera")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_rejects_wrong_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"):
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.sincronizar_chunk(1, 1, token="incorrecto")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_syncs_chunk_with_valid_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"), patch(
            "src.infrastructure.webhooks.app.rag_service.sync_chunk_id",
            return_value={"upserted": 1, "deleted": 0, "ignored": 0},
        ) as sync_mock:
            resultado = webhook_app.sincronizar_chunk(1, 7, token="secreto")

        self.assertEqual(resultado["status"], "ok")
        self.assertEqual(resultado["upserted"], 1)
        sync_mock.assert_called_once_with(7)


class OlvidarConversacionEndpointTests(unittest.TestCase):
    """Borra estado: mismo guardarraíl que el reindexado, nunca abierto."""

    def test_disabled_without_internal_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", ""):
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.olvidar_conversacion(1, "whatsapp", "50688888888", token="cualquiera")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_rejects_wrong_token(self):
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"):
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.olvidar_conversacion(1, "whatsapp", "50688888888", token="incorrecto")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_canal_inventado_se_rechaza(self):
        """Un canal que no existe no debe llegar a construir claves de Redis."""
        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"):
            with self.assertRaises(HTTPException) as ctx:
                webhook_app.olvidar_conversacion(1, "signal", "50688888888", token="secreto")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_olvida_estado_recordatorios_y_buffer(self):
        from src.application import buffer_service, conversation_reset
        from src.infrastructure.repositories.conversation_state_repo import (
            ConversationState,
            ConversationStateRepo,
        )

        ConversationStateRepo.set(Channel.WHATSAPP, "50688888888", ConversationState(flow="CURSO"))
        buffer_service.BufferService.add_message("50688888888", "hola", Channel.WHATSAPP)

        with patch("src.infrastructure.webhooks.app.settings.INTERNAL_API_TOKEN", "secreto"), patch.object(
            conversation_reset.ReminderService, "cancel"
        ) as cancel_mock, patch.object(
            conversation_reset, "_cancelar_tareas_programadas"
        ) as programadas_mock:
            resultado = webhook_app.olvidar_conversacion(1, "whatsapp", "50688888888", token="secreto")

        self.assertEqual(resultado["status"], "ok")
        cancel_mock.assert_called_once()
        programadas_mock.assert_called_once()
        # El hilo ya no existe: el siguiente mensaje entra como conversación nueva.
        self.assertEqual(ConversationStateRepo.get(Channel.WHATSAPP, "50688888888").flow, "INICIO")
        self.assertEqual(
            buffer_service.redis_client.llen(
                buffer_service.scoped_key("buffer", Channel.WHATSAPP, "50688888888")
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
