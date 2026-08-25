"""Las palabras clave ya no están escritas en el código: son filas del panel.

Lo delicado es el AGENDADO. Tres cosas que tienen que cumplirse siempre:

* Los minutos se cuentan desde que se disparó la palabra, igual que se ven y se
  validan en el panel. Contarlos de dos formas distintas garantiza que algún día
  no coincidan.
* El texto del recordatorio se relee al salir, no viaja dentro de la tarea:
  entre agendar y enviar pueden pasar días.
* El tope de minutos del panel no puede superar lo que el `visibility_timeout`
  de Celery aguanta, o Redis re-entrega la tarea y el cliente recibe el mismo
  recordatorio una y otra vez.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.infrastructure.repositories import palabras_clave_repository
from src.infrastructure.tasks import celery_app as tasks
from src.infrastructure.channels.outbound_coordinator import PrioridadSalida


class MatchDeLaPalabraTests(unittest.TestCase):
    """Exacto y sobre el mensaje entero: es un disparador, no interpretación."""

    def setUp(self):
        palabras_clave_repository.limpiar_cache()
        self.addCleanup(palabras_clave_repository.limpiar_cache)
        parche = patch.object(
            palabras_clave_repository,
            "consultar",
            return_value=[{"id": 1, "palabra": "examen"}],
        )
        parche.start()
        self.addCleanup(parche.stop)

    def test_la_palabra_sola_dispara(self):
        self.assertIsNotNone(palabras_clave_repository.buscar("examen"))

    def test_da_igual_como_la_escriba(self):
        for texto in ("EXAMEN", "  Examen  ", "eXaMeN"):
            with self.subTest(texto=texto):
                self.assertIsNotNone(palabras_clave_repository.buscar(texto))

    def test_dentro_de_una_frase_no_dispara(self):
        """«tengo dudas del examen» es una consulta, no el disparador."""
        self.assertIsNone(palabras_clave_repository.buscar("tengo dudas del examen"))
        self.assertIsNone(palabras_clave_repository.buscar("examen de manejo"))

    def test_si_la_base_falla_no_se_rompe_la_conversacion(self):
        """Quedarse sin palabras clave un rato degrada; tirar el mensaje rompe."""
        palabras_clave_repository.limpiar_cache()
        with patch.object(palabras_clave_repository, "consultar", side_effect=Exception("caída")):
            self.assertIsNone(palabras_clave_repository.buscar("examen"))


class AgendadoDeRecordatoriosTests(unittest.TestCase):
    def test_cada_recordatorio_se_agenda_a_SUS_minutos_desde_ahora(self):
        """No en cascada: es como se ven y se validan en el panel."""
        piezas = [
            {"id": 10, "orden": 1, "minutos": 60},
            {"id": 11, "orden": 2, "minutos": 180},
        ]
        with patch.object(
            tasks.palabras_clave_repository, "piezas_de", return_value=piezas
        ), patch.object(tasks.send_keyword_reminder, "apply_async") as agendar, patch.object(
            tasks, "redis_client"
        ):
            agendar.return_value = MagicMock(id="t")
            tasks.schedule_keyword_programmed_messages("whatsapp", "50688888888", 1)

        countdowns = [llamada.kwargs["countdown"] for llamada in agendar.call_args_list]
        self.assertEqual(countdowns, [3600, 10800])

    def test_un_recordatorio_sin_minutos_no_se_agenda(self):
        with patch.object(
            tasks.palabras_clave_repository,
            "piezas_de",
            return_value=[{"id": 10, "orden": 1, "minutos": 0}],
        ), patch.object(tasks.send_keyword_reminder, "apply_async") as agendar, patch.object(
            tasks, "redis_client"
        ):
            tasks.schedule_keyword_programmed_messages("whatsapp", "50688888888", 1)

        agendar.assert_not_called()

    def test_el_tope_del_panel_cabe_en_el_visibility_timeout(self):
        """Si no, Redis re-entrega la tarea y el recordatorio llega repetido."""
        tope_segundos = tasks.MAX_RECORDATORIO_MINUTOS * 60
        visibility = tasks.celery_app.conf.broker_transport_options["visibility_timeout"]

        self.assertGreater(visibility, tope_segundos)


class EnvioDelRecordatorioTests(unittest.TestCase):
    def test_el_texto_se_relee_al_salir(self):
        """Entre agendar y enviar pueden pasar días: llega lo que esté escrito
        AHORA, no lo que había cuando el cliente escribió la palabra."""
        with patch.object(tasks, "has_keyword_context", return_value=True), patch.object(
            tasks.palabras_clave_repository,
            "pieza",
            return_value={"id": 10, "orden": 1, "activo": True, "texto": "texto nuevo",
                          "media_tipo": "", "media_ref": "", "reporte": "contestaron"},
        ) as leer, patch.object(tasks, "set_keyword_active_report"), patch.object(
            tasks.ChannelSenderRegistry, "send"
        ) as enviar:
            tasks.send_keyword_reminder("whatsapp", "50688888888", 10, 1)

        leer.assert_called_once_with(10)
        enviar.assert_called_once_with(
            "whatsapp",
            "50688888888",
            "texto nuevo",
            prioridad=PrioridadSalida.RECORDATORIO,
        )

    def test_un_recordatorio_apagado_no_sale(self):
        """Apagarlo en el panel tiene que servir también para los ya agendados."""
        with patch.object(tasks, "has_keyword_context", return_value=True), patch.object(
            tasks.palabras_clave_repository,
            "pieza",
            return_value={"id": 10, "activo": False, "texto": "hola", "media_tipo": "", "media_ref": ""},
        ), patch.object(tasks.ChannelSenderRegistry, "send") as enviar:
            tasks.send_keyword_reminder("whatsapp", "50688888888", 10, 1)

        enviar.assert_not_called()

    def test_un_recordatorio_borrado_no_sale(self):
        with patch.object(tasks, "has_keyword_context", return_value=True), patch.object(
            tasks.palabras_clave_repository, "pieza", return_value=None
        ), patch.object(tasks.ChannelSenderRegistry, "send") as enviar:
            tasks.send_keyword_reminder("whatsapp", "50688888888", 10, 1)

        enviar.assert_not_called()

    def test_el_adjunto_viaja_como_marcador_en_el_texto(self):
        pieza = {"texto": "Mira esto", "media_tipo": "imagen", "media_ref": "1AbC"}

        self.assertEqual(
            palabras_clave_repository.texto_para_enviar(pieza), "Mira esto\nImagen=1AbC"
        )


if __name__ == "__main__":
    unittest.main()
