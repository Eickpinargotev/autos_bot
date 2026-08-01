import os
import unittest
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.domain.entities import Channel
from src.infrastructure.repositories.keyword_registry_repository import KeywordRegistryRepository

_MODULO = "src.infrastructure.repositories.keyword_registry_repository"


class KeywordRegistryRepositoryTests(unittest.TestCase):
    def test_register_if_missing_es_atomico(self):
        """Registrar-si-no-existe se resuelve en una sola sentencia.

        Con SELECT + INSERT en dos pasos, dos mensajes simultáneos del mismo
        cliente podían crear filas duplicadas; el ON CONFLICT lo impide en la
        propia base.
        """
        with patch(f"{_MODULO}.ejecutar", return_value=1) as ejecutar_mock:
            resultado = KeywordRegistryRepository.register_if_missing(
                "5061", "Cliente", Channel.WHATSAPP, "tareas"
            )

        self.assertTrue(resultado)
        ejecutar_mock.assert_called_once()
        sql, params = ejecutar_mock.call_args.args
        self.assertIn("ON CONFLICT (registro, canal) DO NOTHING", sql)
        self.assertEqual(params, ("5061", "whatsapp", "Cliente", "tareas"))

    def test_register_if_missing_usa_nombre_por_defecto(self):
        with patch(f"{_MODULO}.ejecutar", return_value=1) as ejecutar_mock:
            KeywordRegistryRepository.register_if_missing("5061", "", Channel.TELEGRAM, "transporte")

        _, params = ejecutar_mock.call_args.args
        self.assertEqual(params[2], "Desconocido")

    def test_register_if_missing_no_propaga_errores_de_base(self):
        """El registro es accesorio: no puede romper la atención al cliente."""
        with patch(f"{_MODULO}.ejecutar", side_effect=RuntimeError("db caída")):
            self.assertFalse(
                KeywordRegistryRepository.register_if_missing("5061", "Cliente", Channel.WHATSAPP, "tareas")
            )

    def test_delete_borra_por_registro_y_canal(self):
        with patch(f"{_MODULO}.ejecutar", return_value=1) as ejecutar_mock:
            self.assertTrue(KeywordRegistryRepository.delete("5061", Channel.WHATSAPP))

        sql, params = ejecutar_mock.call_args.args
        self.assertIn("DELETE FROM keyword_registros", sql)
        self.assertEqual(params, ("5061", "whatsapp"))

    def test_exists_devuelve_true_cuando_hay_fila(self):
        with patch(f"{_MODULO}.consultar_uno", return_value={"id": 1, "registro": "5061"}):
            self.assertTrue(KeywordRegistryRepository.exists("5061", Channel.WHATSAPP))

    def test_exists_devuelve_false_cuando_no_hay_fila(self):
        with patch(f"{_MODULO}.consultar_uno", return_value=None):
            self.assertFalse(KeywordRegistryRepository.exists("5061", Channel.WHATSAPP))

    def test_exists_no_propaga_errores_de_base(self):
        """`exists` corre en el camino caliente de fragment_catalog."""
        with patch(f"{_MODULO}.consultar_uno", side_effect=RuntimeError("db caída")):
            self.assertFalse(KeywordRegistryRepository.exists("5061", Channel.WHATSAPP))


if __name__ == "__main__":
    unittest.main()
