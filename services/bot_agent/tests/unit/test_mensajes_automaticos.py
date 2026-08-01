"""Qué se cobra y qué no.

La regla del negocio: se factura el trabajo que el sistema hace por el cliente
—un turno del modelo, o la entrega de contenido curado del negocio—. Un acuse
fijo a algo que el bot no puede leer no es ninguna de las dos cosas.

Esto no es una preferencia estética: sin la regla, un cliente que manda cinco
stickers seguidos genera cinco cargos por un trabajo que nunca se hizo, y eso
sale en la factura del negocio. Por eso está fijado con tests: es el tipo de
cosa que se rompe sola al añadir un caso nuevo.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.conversation_orchestrator import ConversationOrchestrator
from src.domain.entities import Channel, InboundMessage, MessageType


def _entrante(tipo: MessageType, texto: str = "") -> InboundMessage:
    return InboundMessage(
        channel=Channel.WHATSAPP,
        user_id="50688888888",
        user_name="Ana",
        message_type=tipo,
        text=texto,
    )


class NoSeCobraTests(unittest.TestCase):
    """Los acuses automáticos responden, pero no generan un cargo."""

    def _procesar(self, mensaje: InboundMessage):
        with patch(
            "src.application.conversation_orchestrator.seguimiento_service.registrar_uso_codigo"
        ) as cobro, patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.application.conversation_orchestrator.BufferService.add_image_info_count",
            return_value=True,
        ):
            acciones = ConversationOrchestrator().handle(mensaje)
        return acciones, cobro

    def test_una_imagen_recibe_respuesta_pero_no_se_cobra(self):
        acciones, cobro = self._procesar(_entrante(MessageType.IMAGE))

        self.assertTrue(acciones, "el cliente debe recibir el aviso")
        self.assertIn("imágenes", acciones[0].text.lower())
        cobro.assert_not_called()

    def test_un_documento_tampoco_se_cobra(self):
        acciones, cobro = self._procesar(_entrante(MessageType.DOCUMENT))

        self.assertTrue(acciones)
        cobro.assert_not_called()

    def test_un_sticker_recibe_su_propia_respuesta_y_no_se_cobra(self):
        """Un sticker no es una consulta ilegible: es un gesto. Contestarle
        «no podemos ver imágenes» suena a error donde no lo hubo."""
        acciones, cobro = self._procesar(_entrante(MessageType.STICKER))

        self.assertTrue(acciones)
        self.assertNotIn("no podemos ver", acciones[0].text.lower())
        cobro.assert_not_called()

    def test_el_texto_de_los_avisos_no_esta_en_el_codigo(self):
        """Regla del repo: el texto de negocio vive en `mensajes.json`."""
        import inspect

        from src.application import conversation_orchestrator

        fuente = inspect.getsource(conversation_orchestrator)
        self.assertNotIn("No podemos ver imagenes/documentos", fuente)


class SiSeCobraTests(unittest.TestCase):
    """Lo que sí entrega contenido del negocio sigue facturándose."""

    def test_la_palabra_clave_si_genera_cargo(self):
        mensaje = _entrante(MessageType.TEXT, "tareas")

        with patch(
            "src.application.conversation_orchestrator.seguimiento_service.registrar_uso_codigo"
        ) as cobro, patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.application.conversation_orchestrator.mensajes_del_negocio",
            return_value=["curso teórico: enlace"],
        ), patch(
            "src.application.conversation_orchestrator.cancel_scheduled_tasks"
        ), patch(
            "src.application.conversation_orchestrator.PostgresUserRepo"
        ), patch(
            "src.application.conversation_orchestrator.KeywordRegistryRepository"
        ), patch(
            "src.application.conversation_orchestrator.register_keyword_context"
        ), patch(
            "src.application.conversation_orchestrator.schedule_keyword_programmed_messages"
        ):
            acciones = ConversationOrchestrator().handle(mensaje)

        self.assertEqual(len(acciones), 1)
        cobro.assert_called_once()
        self.assertEqual(cobro.call_args.kwargs["origen"], "keyword")


class MensajesEditablesTests(unittest.TestCase):
    """El negocio edita sus mensajes en el panel; el archivo es el respaldo."""

    def test_manda_lo_editado_en_el_panel(self):
        from src.application import message_catalog

        with patch(
            "src.infrastructure.repositories.plantillas_repository.textos_de",
            return_value=["texto del panel"],
        ):
            self.assertEqual(
                message_catalog.mensajes_del_negocio("KEYWORD", "T1"), ["texto del panel"]
            )

    def test_sin_nada_en_la_base_cae_al_archivo(self):
        """Una base recién creada no puede dejar al bot mudo con un cliente."""
        from src.application import message_catalog

        with patch(
            "src.infrastructure.repositories.plantillas_repository.textos_de", return_value=[]
        ):
            desde_archivo = message_catalog.mensajes_del_negocio("KEYWORD", "T1")

        self.assertEqual(desde_archivo, message_catalog.get_messages_for_node("KEYWORD", "T1"))
        self.assertTrue(desde_archivo)

    def test_los_fragmentos_del_agente_no_son_editables(self):
        """Los textos que el prompt referencia por id no salen del panel:
        cambiarlos desde ahí rompería el contrato con el modelo."""
        from src.application.message_catalog import CLAVES_EDITABLES

        categorias = {categoria for categoria, _ in CLAVES_EDITABLES}
        self.assertEqual(categorias, {"KEYWORD", "WELCOME"})

    def test_el_adjunto_viaja_como_marcador_en_el_texto(self):
        from src.infrastructure.repositories import plantillas_repository

        with patch.object(
            plantillas_repository,
            "partes_de",
            return_value=[{"orden": 1, "texto": "Mira esto", "media_tipo": "imagen", "media_ref": "1AbC"}],
        ):
            self.assertEqual(
                plantillas_repository.textos_de("TAREAS"), ["Mira esto\nImagen=1AbC"]
            )


if __name__ == "__main__":
    unittest.main()
