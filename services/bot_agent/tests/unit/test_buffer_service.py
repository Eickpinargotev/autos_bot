import os
import unittest
from unittest.mock import MagicMock, patch


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

    def test_add_message_returns_sequence_number(self):
        pipe = MagicMock()
        pipe.execute.return_value = [1, 3, True, True]  # rpush, incr, expire, expire
        with patch("src.application.buffer_service.redis_client.pipeline", return_value=pipe):
            seq = BufferService.add_message("50688888888", "Hola", Channel.WHATSAPP)

        self.assertEqual(seq, 3)
        pipe.rpush.assert_called_once_with(scoped_key("buffer", Channel.WHATSAPP, "50688888888"), "Hola")
        pipe.incr.assert_called_once_with(scoped_key("buffer_seq", Channel.WHATSAPP, "50688888888"))

    def test_drain_if_current_returns_text_when_sequence_matches(self):
        with patch(
            "src.application.buffer_service._drain_if_current",
            return_value=["Hola", "buenas"],
        ) as drain_mock:
            text = BufferService.drain_if_current("50688888888", Channel.WHATSAPP, 3)

        self.assertEqual(text, "Hola buenas")
        drain_mock.assert_called_once_with(
            keys=[
                scoped_key("buffer", Channel.WHATSAPP, "50688888888"),
                scoped_key("buffer_seq", Channel.WHATSAPP, "50688888888"),
            ],
            args=["3"],
        )

    def test_drain_if_current_returns_none_when_task_is_stale(self):
        # Llegó un mensaje más nuevo: el script Lua devuelve nil y la tarea
        # obsoleta no debe procesar nada.
        with patch("src.application.buffer_service._drain_if_current", return_value=None):
            self.assertIsNone(BufferService.drain_if_current("50688888888", Channel.WHATSAPP, 2))


if __name__ == "__main__":
    unittest.main()
