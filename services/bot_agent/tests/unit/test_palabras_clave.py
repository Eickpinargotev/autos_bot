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
    """Sobre el mensaje entero: es un disparador, no interpretación."""

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


class VariantesDeLaPalabraTests(unittest.TestCase):
    def setUp(self):
        palabras_clave_repository.limpiar_cache()
        self.addCleanup(palabras_clave_repository.limpiar_cache)
        self.fila = {"id": 1, "palabra": "tareas"}
        parche = patch.object(palabras_clave_repository, "consultar", return_value=[self.fila])
        self.consultar = parche.start()
        self.addCleanup(parche.stop)

    def test_una_sola_entrada_cubre_las_variantes(self):
        for texto in ("tareas", "tarea", "TAREAS", "tareas.", " ¡TAREAS! ",
                      "«tareas»", "¿ tareas ?", "tares", "terea", "tereas", "tareass"):
            with self.subTest(texto=texto):
                self.assertIs(palabras_clave_repository.buscar(texto), self.fila)
        self.consultar.assert_called_once()

    def test_singular_configurado_tambien_acepta_plural(self):
        self.fila["palabra"] = "tarea"
        self.assertIs(palabras_clave_repository.buscar("tareas"), self.fila)

    def test_no_interpreta_frases_comandos_signos_internos_ni_dos_errores(self):
        for texto in ("quiero tareas", "no quiero tareas", "tareas por favor", "/tareas",
                      "/block", 'grupo["tareas"]', "ta.reas", "t@reas", "tareas2",
                      "😀tareas", "teora", "", "!!!", "a" * 10000):
            with self.subTest(texto=texto):
                self.assertIsNone(palabras_clave_repository.buscar(texto))

    def test_las_entradas_existentes_exactas_ganan(self):
        filas = [{"id": i, "palabra": palabra} for i, palabra in enumerate(
            ("tarea", "tareas", "tareas.", "tares", "terea", "tereas"), start=1
        )]
        self.consultar.return_value = filas
        for fila in filas:
            with self.subTest(palabra=fila["palabra"]):
                self.assertIs(palabras_clave_repository.buscar(fila["palabra"]), fila)

    def test_normalizada_exacta_gana_a_plural_y_a_error(self):
        otra = {"id": 2, "palabra": "tarea"}
        self.consultar.return_value = [self.fila, otra]
        self.assertIs(palabras_clave_repository.buscar("¡tarea!"), otra)

    def test_empate_de_errores_no_elige_segun_orden_de_la_base(self):
        otra = {"id": 2, "palabra": "mareas"}
        for filas in ([self.fila, otra], [otra, self.fila]):
            palabras_clave_repository.limpiar_cache()
            self.consultar.return_value = filas
            self.assertIsNone(palabras_clave_repository.buscar("pareas"))

    def test_empate_de_puntuacion_no_elige_una_fila(self):
        self.consultar.return_value = [self.fila, {"id": 2, "palabra": "tareas."}]
        self.assertIsNone(palabras_clave_repository.buscar("¡tareas!"))

    def test_plural_sin_error_gana_a_un_error(self):
        self.consultar.return_value = [self.fila, {"id": 2, "palabra": "terea"}]
        self.assertIs(palabras_clave_repository.buscar("¡tarea!"), self.fila)

    def test_palabras_cortas_solo_aceptan_puntuacion(self):
        self.fila["palabra"] = "pago"
        self.assertIs(palabras_clave_repository.buscar("¡pago!"), self.fila)
        for texto in ("paga", "pagos", "pgo"):
            self.assertIsNone(palabras_clave_repository.buscar(texto))

    def test_frase_configurada_conserva_coincidencia_completa(self):
        self.fila["palabra"] = "curso teorico"
        for texto in ("curso teorico", "¡curso teorico!"):
            self.assertIs(palabras_clave_repository.buscar(texto), self.fila)
        self.assertIsNone(palabras_clave_repository.buscar("curso teorica"))

    def test_conserva_tildes_y_enie(self):
        self.fila["palabra"] = "teórico"
        self.assertIsNone(palabras_clave_repository.buscar("teorico" + "x"))
        self.assertEqual(palabras_clave_repository._sin_puntuacion_exterior("¡AÑO!"), "año")
        self.assertEqual(palabras_clave_repository._sin_puntuacion_exterior("¡TEÓRICO!"), "teórico")

    def test_solo_consulta_activas_del_proyecto_y_aisla_cache(self):
        from src.application.project_context import ambito_proyecto

        with ambito_proyecto(10):
            self.assertIs(palabras_clave_repository.buscar("terea"), self.fila)
        self.consultar.assert_called_once()
        sql, parametros = self.consultar.call_args.args
        self.assertIn("AND activa", sql)
        self.assertEqual(parametros, (10,))
        self.consultar.return_value = []
        with ambito_proyecto(11):
            self.assertIsNone(palabras_clave_repository.buscar("tareas"))
        with ambito_proyecto(10):
            self.assertIs(palabras_clave_repository.buscar("terea"), self.fila)
        self.assertEqual(self.consultar.call_count, 2)

    def test_desactivar_o_borrar_se_refleja_al_vencer_cache(self):
        self.assertIs(palabras_clave_repository.buscar("terea"), self.fila)
        self.consultar.return_value = []
        with patch.object(palabras_clave_repository.time, "monotonic", return_value=float("inf")):
            self.assertIsNone(palabras_clave_repository.buscar("terea"))

    def test_sin_proyecto_o_sin_base_no_hay_coincidencia(self):
        from src.application.project_context import ambito_proyecto

        with ambito_proyecto(None):
            self.assertIsNone(palabras_clave_repository.buscar("terea"))
        self.consultar.assert_not_called()
        self.consultar.side_effect = Exception("caída")
        self.assertIsNone(palabras_clave_repository.buscar("terea"))


class DistanciaAcotadaTests(unittest.TestCase):
    def test_coincide_con_levenshtein_completa_para_inserciones_borrados_y_cambios(self):
        from itertools import product

        def distancia_completa(a, b):
            anterior = list(range(len(b) + 1))
            for i, letra in enumerate(a, start=1):
                actual = [i]
                for j, otra in enumerate(b, start=1):
                    actual.append(min(actual[-1] + 1, anterior[j] + 1,
                                      anterior[j - 1] + (letra != otra)))
                anterior = actual
            return anterior[-1]

        palabras = ["".join(letras) for largo in range(5) for letras in product("ab", repeat=largo)]
        for a in palabras:
            for b in palabras:
                self.assertEqual(palabras_clave_repository._distancia_hasta_un_cambio(a, b),
                                 min(2, distancia_completa(a, b)), (a, b))


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
