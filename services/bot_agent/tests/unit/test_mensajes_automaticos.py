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
from unittest.mock import call, patch

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
        # `PostgresUserRepo` se sustituye porque, sin él, `is_blocked` consulta
        # la base de desarrollo de verdad: un número que quedó bloqueado por
        # otra prueba corta el flujo antes de tiempo y estos casos pasarían (o
        # fallarían) por un motivo que no tiene nada que ver con lo que miden.
        with patch(
            "src.application.conversation_orchestrator.seguimiento_service.registrar_uso_codigo"
        ) as cobro, patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ) as log, patch(
            "src.application.conversation_orchestrator.BufferService.add_image_info_count",
            return_value=True,
        ), patch(
            "src.application.conversation_orchestrator.PostgresUserRepo"
        ) as repo, patch(
            "src.application.conversation_orchestrator.BufferService.add_message", return_value=1
        ), patch(
            "src.application.conversation_orchestrator.process_buffered_messages"
        ):
            repo.return_value.is_blocked.return_value = False
            acciones = ConversationOrchestrator().handle(mensaje)
        return acciones, cobro, log

    def test_una_imagen_recibe_respuesta_pero_no_se_cobra(self):
        acciones, cobro, log = self._procesar(_entrante(MessageType.IMAGE))

        self.assertTrue(acciones, "el cliente debe recibir el aviso")
        self.assertIn("imagen", acciones[0].text.lower())
        log.assert_called_once()
        cobro.assert_not_called()

    def test_un_documento_tampoco_se_cobra(self):
        acciones, cobro, _ = self._procesar(_entrante(MessageType.DOCUMENT))

        self.assertTrue(acciones)
        self.assertIn("documento", acciones[0].text.lower())
        cobro.assert_not_called()

    def test_un_video_recibe_su_propio_aviso(self):
        acciones, cobro, _ = self._procesar(_entrante(MessageType.VIDEO))

        self.assertTrue(acciones, "un video tampoco se puede revisar por este medio")
        self.assertIn("video", acciones[0].text.lower())
        cobro.assert_not_called()

    def test_despues_de_un_reporte_la_media_no_recibe_ninguna_respuesta(self):
        """Regresión del diagnóstico 50671404012: una imagen entraba por la
        rama automática antes de que se comprobara la pausa del handoff."""
        for tipo in (MessageType.IMAGE, MessageType.DOCUMENT, MessageType.VIDEO):
            with self.subTest(tipo=tipo), patch(
                "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
            ), patch(
                "src.application.conversation_orchestrator.ConversationLogRepository.log_tool_event"
            ), patch(
                "src.infrastructure.repositories.bloqueos_permanentes_repository.esta_bloqueado",
                return_value=False,
            ), patch(
                "src.application.conversation_orchestrator.PostgresUserRepo"
            ) as repo, patch.object(
                ConversationOrchestrator, "_responder_por_media"
            ) as responder:
                repo.return_value.is_blocked.return_value = True
                acciones = ConversationOrchestrator().handle(_entrante(tipo))

            self.assertEqual(acciones, [])
            responder.assert_not_called()

    def test_un_enlace_recibe_el_aviso_sin_gastar_un_turno_del_modelo(self):
        acciones, cobro, _ = self._procesar(_entrante(MessageType.TEXT, "mira esto https://ejemplo.com/x"))

        self.assertTrue(acciones)
        self.assertIn("enlace", acciones[0].text.lower())
        cobro.assert_not_called()

    def test_un_texto_normal_no_se_confunde_con_un_enlace(self):
        """El acuse de enlace no puede comerse una consulta corriente."""
        acciones, _, _ = self._procesar(
            _entrante(MessageType.TEXT, "buenas, cuanto cuesta el curso? gracias")
        )

        self.assertEqual(acciones, [])

    def test_un_sticker_no_se_responde_pero_si_queda_en_el_historial(self):
        """El bot lo deja pasar; el panel lo muestra.

        No se responde porque es un gesto, no una consulta —y en WhatsApp cada
        envío consume la cuota del plan, así que contestarle deja sin respuesta
        al mensaje que sí importaba—. Pero sí se registra: quien lee el chat en
        el panel necesita ver lo que pasó de verdad, sin huecos.
        """
        acciones, cobro, log = self._procesar(_entrante(MessageType.STICKER))

        self.assertEqual(acciones, [], "no se responde")
        cobro.assert_not_called()
        log.assert_called_once()
        self.assertEqual(log.call_args.kwargs["event_type"], "sticker_ignorado")

    def test_la_media_deja_anotado_que_se_envio_el_aviso(self):
        """Esa etiqueta es la que el panel muestra entre corchetes."""
        for tipo in (MessageType.IMAGE, MessageType.DOCUMENT, MessageType.VIDEO):
            with self.subTest(tipo=tipo):
                _, _, log = self._procesar(_entrante(tipo))
                self.assertEqual(log.call_args.kwargs["event_type"], "media_avisada")

    def test_un_correo_no_es_un_enlace(self):
        """Dar un correo es de lo más normal aquí; no puede disparar el acuse."""
        from src.application.conversation_orchestrator import _ENLACE

        for texto in (
            "mi correo es ana@gmail.com",
            "escribeme a juan.perez@hotmail.com por favor",
            "son 50.000 colones",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(_ENLACE.search(texto))

    def test_varios_adjuntos_crean_reporte_interno_y_no_envian_la_razon(self):
        mensaje = _entrante(MessageType.IMAGE)
        with patch(
            "src.application.conversation_orchestrator.BufferService.add_image_info_count",
            return_value=False,
        ), patch(
            "src.application.conversation_orchestrator.mark_report_once", return_value=True
        ), patch(
            "src.application.conversation_orchestrator.ReportRepository.create_report"
        ) as reporte, patch(
            "src.application.conversation_orchestrator.PostgresUserRepo"
        ) as repo, patch(
            "src.application.conversation_orchestrator.seguimiento_service.registrar_derivacion"
        ), patch(
            "src.application.conversation_orchestrator.clear_user_runtime_context"
        ):
            acciones = ConversationOrchestrator()._responder_por_media(mensaje, "MEDIA_IMAGEN")

        self.assertEqual(acciones, [])
        reporte.assert_called_once()
        self.assertIn("revisión del equipo", reporte.call_args.kwargs["problema"])
        repo.return_value.block_user.assert_called_once()

    def test_los_avisos_de_media_no_exponen_la_derivacion_interna(self):
        from src.application.message_catalog import get_messages_for_node

        for nodo in ("MEDIA_IMAGEN", "MEDIA_DOCUMENTO", "MEDIA_VIDEO", "MEDIA_ENLACE"):
            with self.subTest(nodo=nodo):
                texto = " ".join(get_messages_for_node("AUTOMATICO", nodo)).lower()
                self.assertNotIn("asesor", texto)
                self.assertNotIn("ayuda solicitada", texto)

        self.assertEqual(get_messages_for_node("AUTOMATICO", "MEDIA_INSISTE"), [])

    def test_reconoce_las_formas_en_que_se_pega_un_enlace(self):
        from src.application.conversation_orchestrator import _ENLACE

        for texto in (
            "mira https://fb.com/x",
            "www.ejemplo.cr",
            "vean ejemplo.com/promo",
            "pagina: WWW.Ejemplo.COM",
        ):
            with self.subTest(texto=texto):
                self.assertIsNotNone(_ENLACE.search(texto))

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
            "src.application.conversation_orchestrator.palabras_clave_repository.buscar",
            return_value={"id": 1, "palabra": "tareas"},
        ), patch(
            "src.application.conversation_orchestrator.palabras_clave_repository.textos_de",
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


class PublicidadDeFacebookTests(unittest.TestCase):
    def test_una_respuesta_conserva_solo_el_ultimo_recordatorio(self):
        mensaje = _entrante(MessageType.TEXT, "consulta del cliente")

        with patch(
            "src.application.conversation_orchestrator.consume_welcome_context",
            return_value=False,
        ), patch(
            "src.application.conversation_orchestrator.consume_ad_report",
            return_value=True,
        ), patch(
            "src.application.conversation_orchestrator.ReportRepository.create_report"
        ) as reporte, patch(
            "src.infrastructure.tasks.celery_app.cancel_ad_reminder_stage"
        ) as cancelar:
            ConversationOrchestrator()._handle_blocked_text(mensaje)

        reporte.assert_called_once()
        self.assertEqual(
            cancelar.call_args_list,
            [call("whatsapp", "50688888888", 1), call("whatsapp", "50688888888", 2)],
        )

    def test_la_ciudad_del_anuncio_dispara_publicidad_sin_preguntarla(self):
        mensaje = _entrante(MessageType.TEXT, "¡Hola! Quiero más información")
        mensaje.advertisement_text = "🚔🚔 LAUREL 🚔🚔\n\nCURSO TEÓRICO PARA LICENCIAS"

        with patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.application.conversation_orchestrator.palabras_clave_repository.buscar",
            return_value=None,
        ), patch(
            "src.application.conversation_orchestrator.PostgresUserRepo"
        ) as repo, patch(
            "src.application.publicidad_service.PublicidadService._buscar_clave",
            return_value="LAUREL",
        ), patch(
            "src.application.publicidad_service.PublicidadService.handle_publicidad_entry"
        ) as publicidad, patch(
            "src.application.conversation_orchestrator.BufferService.add_message"
        ) as buffer:
            repo.return_value.is_blocked.return_value = False
            acciones = ConversationOrchestrator().handle(mensaje)

        self.assertEqual(acciones, [])
        publicidad.assert_called_once_with(
            "50688888888", "LAUREL", "Ana", Channel.WHATSAPP
        )
        buffer.assert_not_called()

    def test_un_anuncio_sin_clave_del_catalogo_sigue_el_flujo_normal(self):
        mensaje = _entrante(MessageType.TEXT, "¡Hola! Quiero más información")
        mensaje.advertisement_text = "Promoción general sin una ciudad conocida"

        with patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.application.conversation_orchestrator.palabras_clave_repository.buscar",
            return_value=None,
        ), patch(
            "src.application.conversation_orchestrator.PostgresUserRepo"
        ) as repo, patch(
            "src.application.publicidad_service.PublicidadService._buscar_clave",
            return_value="",
        ), patch(
            "src.application.publicidad_service.PublicidadService.handle_publicidad_entry"
        ) as publicidad, patch(
            "src.application.conversation_orchestrator.BufferService.add_message",
            return_value=4,
        ) as buffer, patch(
            "src.application.conversation_orchestrator.process_buffered_messages.apply_async"
        ):
            repo.return_value.is_blocked.return_value = False
            acciones = ConversationOrchestrator().handle(mensaje)

        self.assertEqual(acciones, [])
        publicidad.assert_not_called()
        buffer.assert_called_once()

    def test_el_mensaje_citado_llega_al_buffer_como_contexto(self):
        mensaje = _entrante(MessageType.TEXT, "¿Vuelvo a llenar este?")
        mensaje.quoted_text = "Formulario para solicitar la cita teórica"

        with patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.application.conversation_orchestrator.palabras_clave_repository.buscar",
            return_value=None,
        ), patch(
            "src.application.conversation_orchestrator.PostgresUserRepo"
        ) as repo, patch(
            "src.application.conversation_orchestrator.BufferService.add_message",
            return_value=5,
        ) as buffer, patch(
            "src.application.conversation_orchestrator.process_buffered_messages.apply_async"
        ):
            repo.return_value.is_blocked.return_value = False
            ConversationOrchestrator().handle(mensaje)

        texto_enviado = buffer.call_args.args[1]
        self.assertIn("¿Vuelvo a llenar este?", texto_enviado)
        self.assertIn("Mensaje citado por el cliente", texto_enviado)
        self.assertIn("Formulario para solicitar la cita teórica", texto_enviado)


class MensajesEditablesTests(unittest.TestCase):
    """El negocio edita sus mensajes en el panel; el archivo es el respaldo."""

    def test_manda_lo_editado_en_el_panel(self):
        from src.application import message_catalog

        with patch(
            "src.infrastructure.repositories.plantillas_repository.textos_de",
            return_value=["texto del panel"],
        ):
            self.assertEqual(
                message_catalog.mensajes_del_negocio("WELCOME", "W"), ["texto del panel"]
            )

    def test_sin_nada_en_la_base_cae_al_archivo(self):
        """Una base recién creada no puede dejar al bot mudo con un cliente."""
        from src.application import message_catalog

        with patch(
            "src.infrastructure.repositories.plantillas_repository.textos_de", return_value=[]
        ):
            desde_archivo = message_catalog.mensajes_del_negocio("WELCOME", "W")

        self.assertEqual(desde_archivo, message_catalog.get_messages_for_node("WELCOME", "W"))
        self.assertTrue(desde_archivo)

    def test_los_fragmentos_del_agente_no_son_editables(self):
        """Los textos que el prompt referencia por id no salen del panel:
        cambiarlos desde ahí rompería el contrato con el modelo.

        Y las palabras clave tampoco están ya aquí: se mudaron a su propia tabla
        (`palabras_clave`), con sus recordatorios y sus minutos."""
        from src.application.message_catalog import CLAVES_EDITABLES

        categorias = {categoria for categoria, _ in CLAVES_EDITABLES}
        self.assertEqual(categorias, {"WELCOME"})

    def test_el_adjunto_viaja_como_marcador_en_el_texto(self):
        from src.infrastructure.repositories import plantillas_repository

        with patch.object(
            plantillas_repository,
            "partes_de",
            return_value=[{"orden": 1, "texto": "Mira esto", "media_tipo": "imagen", "media_ref": "1AbC"}],
        ):
            self.assertEqual(
                plantillas_repository.textos_de("BIENVENIDA_GRUPO"), ["Mira esto\nImagen=1AbC"]
            )


if __name__ == "__main__":
    unittest.main()
