"""Tests deterministas del registro de consumo facturable (`uso_eventos`).

Lo que se protege aquí es el dinero: que cada hecho quede imputado en la
categoría correcta, con el costo real congelado, y que un fallo de la base nunca
interrumpa la atención al cliente.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application import seguimiento_service as svc
from src.domain.entities import Channel
from src.infrastructure.repositories import billing_repository

_MODULO = "src.infrastructure.repositories.billing_repository"


def _usage(prompt=1000, cached=0, completion=200):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )


class EventoLlmTests(unittest.TestCase):
    def test_imputa_al_periodo_abierto_y_a_la_tarifa_vigente(self):
        """El periodo y la tarifa se resuelven DENTRO del INSERT.

        Si se cachearan en el proceso, los eventos posteriores a un cierre de
        periodo (o a un cambio de precios) caerían en el sitio equivocado
        durante la ventana de caché.
        """
        with patch(f"{_MODULO}.ejecutar", return_value=1) as ejecutar_mock:
            billing_repository.registrar_evento_llm(
                client_id="5061",
                canal=Channel.WHATSAPP,
                origen="agente",
                modelo="gpt-5.4-mini",
                tokens_entrada=1000,
                tokens_cacheados=200,
                tokens_salida=300,
                costo_real_microusd=1950,
            )

        sql, params = ejecutar_mock.call_args.args
        self.assertIn("FROM periodos_facturacion", sql)
        self.assertIn("cerrado_en IS NULL", sql)
        self.assertIn("FROM tarifas", sql)
        self.assertIn("vigente_desde <= NOW()", sql)
        # El costo de venta se deriva del real por el multiplicador de la tarifa.
        self.assertIn("ROUND(%s * COALESCE(t.multiplicador_llm, 1))", sql)
        self.assertEqual(params[0], "5061")
        self.assertEqual(params[1], "whatsapp")
        self.assertEqual(params[2], "agente")
        self.assertEqual(params[7], 1950)  # costo real congelado

    def test_un_fallo_de_base_no_propaga(self):
        with patch(f"{_MODULO}.ejecutar", side_effect=RuntimeError("db caída")):
            self.assertFalse(
                billing_repository.registrar_evento_llm(
                    client_id="5061",
                    canal="whatsapp",
                    origen="agente",
                    modelo="m",
                    tokens_entrada=1,
                    tokens_cacheados=0,
                    tokens_salida=1,
                    costo_real_microusd=1,
                )
            )


class EventoCodigoTests(unittest.TestCase):
    def test_se_cobra_por_mensaje_y_sin_costo_real(self):
        """Un mensaje disparado por código no le cuesta nada al proveedor."""
        with patch(f"{_MODULO}.ejecutar", return_value=1) as ejecutar_mock:
            billing_repository.registrar_evento_codigo(
                client_id="5061", canal=Channel.TELEGRAM, origen="keyword", mensajes=3
            )

        sql, params = ejecutar_mock.call_args.args
        self.assertIn("'codigo'", sql)
        self.assertIn("%s * COALESCE(t.precio_mensaje_codigo_microusd, 0)", sql)
        self.assertIn(", 0,", sql)  # costo_real_microusd fijo en cero
        self.assertEqual(params[3], 3)
        self.assertEqual(params[4], 3)

    def test_no_registra_nada_sin_mensajes(self):
        with patch(f"{_MODULO}.ejecutar") as ejecutar_mock:
            self.assertFalse(
                billing_repository.registrar_evento_codigo(
                    client_id="5061", canal="telegram", origen="keyword", mensajes=0
                )
            )
        ejecutar_mock.assert_not_called()


class IntegracionConSeguimientoTests(unittest.TestCase):
    """El servicio de seguimiento es quien alimenta el libro mayor."""

    def test_uso_de_llm_registra_el_costo_real_calculado_una_sola_vez(self):
        """La fórmula del costo real vive en `costo_microusd`, no duplicada en SQL."""
        esperado = svc.costo_microusd(1000, 200, 300)

        with patch.object(svc.redis_client, "pipeline", return_value=MagicMock()), patch.object(
            svc.billing_repository, "registrar_evento_llm"
        ) as evento_mock:
            svc.registrar_uso_llm("5061", Channel.WHATSAPP, _usage(1000, 200, 300), origen="rag")

        kwargs = evento_mock.call_args.kwargs
        self.assertEqual(kwargs["costo_real_microusd"], esperado)
        self.assertEqual(kwargs["origen"], "rag")
        self.assertEqual(kwargs["tokens_cacheados"], 200)

    def test_cada_modelo_se_cobra_a_su_propio_precio(self):
        """Desde que hay un modelo por tarea, un precio único falsea la factura.

        El supervisor cuesta 10x lo que el auxiliar: cobrar todo al mismo precio
        infla lo barato y regala lo caro, y el cliente paga la diferencia.
        """
        from src.infrastructure.repositories.precios_repository import PrecioModelo

        caro = PrecioModelo(entrada_usd_1m=2.0, cacheado_usd_1m=0.20, salida_usd_1m=12.0)
        barato = PrecioModelo(entrada_usd_1m=0.20, cacheado_usd_1m=0.02, salida_usd_1m=1.25)

        with patch.object(svc.precios_repository, "precio_de", return_value=caro):
            costo_supervisor = svc.costo_microusd(1000, 0, 100, "gpt-5.6-terra")
        with patch.object(svc.precios_repository, "precio_de", return_value=barato):
            costo_auxiliar = svc.costo_microusd(1000, 0, 100, "gpt-5.4-nano")

        # 1000 * 2.00/1M + 100 * 12.00/1M = 3200 micro-USD
        self.assertEqual(costo_supervisor, 3200)
        # 1000 * 0.20/1M + 100 * 1.25/1M = 325 micro-USD
        self.assertEqual(costo_auxiliar, 325)

    def test_el_modelo_que_atendio_llega_al_libro_mayor(self):
        """Sin esto, el desglose por modelo del panel sería inventado."""
        with patch.object(svc.redis_client, "pipeline", return_value=MagicMock()), patch.object(
            svc.billing_repository, "registrar_evento_llm"
        ) as evento_mock:
            svc.registrar_uso_llm(
                "5061", Channel.WHATSAPP, _usage(10, 0, 5),
                origen="agente", modelo="gpt-5.6-terra",
            )

        self.assertEqual(evento_mock.call_args.kwargs["modelo"], "gpt-5.6-terra")

    def test_el_audio_se_cobra_por_segundo_y_no_por_minuto_entero(self):
        """Una nota de voz de 8 segundos no puede facturarse como un minuto."""
        from src.infrastructure.repositories.precios_repository import PrecioModelo

        precio = PrecioModelo(audio_usd_minuto=0.006)
        with patch.object(svc.precios_repository, "precio_de", return_value=precio):
            ocho_segundos = svc.costo_audio_microusd(8, "gpt-4o-transcribe")
            un_minuto = svc.costo_audio_microusd(60, "gpt-4o-transcribe")

        self.assertEqual(un_minuto, 6000)          # 0.006 USD = 6000 micro-USD
        self.assertEqual(ocho_segundos, 800)       # 8/60 de eso, no el minuto entero
        self.assertLess(ocho_segundos, un_minuto)

    def test_sin_usage_no_se_registra_consumo(self):
        with patch.object(svc.billing_repository, "registrar_evento_llm") as evento_mock:
            svc.registrar_uso_llm("5061", Channel.WHATSAPP, None)
        evento_mock.assert_not_called()

    def test_keyword_se_factura_como_codigo_y_no_como_llm(self):
        """La palabra clave la detecta el código: no puede cobrarse como turno de LLM."""
        with patch.object(svc.billing_repository, "registrar_evento_codigo") as evento_mock:
            svc.registrar_uso_codigo("5061", Channel.TELEGRAM, origen="keyword", mensajes=2)

        kwargs = evento_mock.call_args.kwargs
        self.assertEqual(kwargs["origen"], "keyword")
        self.assertEqual(kwargs["mensajes"], 2)


if __name__ == "__main__":
    unittest.main()
