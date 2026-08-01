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
