import os
import unittest
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.buffer_service import BufferService, scoped_key
from src.domain.entities import Channel


class BufferServiceTests(unittest.TestCase):
    def test_get_and_clear_buffer_drains_atomically_with_the_scoped_key(self):
        # El drenado debe ser una sola operación atómica (lrange+del en Lua) para
        # que dos tareas de Celery concurrentes no procesen el mismo buffer dos
        # veces. Verificamos que se invoca el script con la clave correcta y que
        # se concatenan los mensajes drenados.
        with patch(
            "src.application.buffer_service._drain_buffer",
            return_value=["Hola", "buenas", "todo bien?"],
        ) as drain_mock:
            text = BufferService.get_and_clear_buffer("50688888888", Channel.WHATSAPP)

        self.assertEqual(text, "Hola buenas todo bien?")
        drain_mock.assert_called_once_with(keys=[scoped_key("buffer", Channel.WHATSAPP, "50688888888")])

    def test_get_and_clear_buffer_handles_empty_drain(self):
        with patch("src.application.buffer_service._drain_buffer", return_value=[]):
            self.assertEqual(BufferService.get_and_clear_buffer("50688888888", Channel.WHATSAPP), "")

        with patch("src.application.buffer_service._drain_buffer", return_value=None):
            self.assertEqual(BufferService.get_and_clear_buffer("50688888888", Channel.WHATSAPP), "")


if __name__ == "__main__":
    unittest.main()
