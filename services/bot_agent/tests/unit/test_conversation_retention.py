"""Tests deterministas de la política de retención del historial (20 días).

Cubre tres mecanismos:
  - Normalización de fechas (`fechas.py`), que decide qué está vencido.
  - Purga del log durable y de los shots en Postgres: se mockea la capa de
    acceso a la base, así que no hace falta un Postgres levantado.
  - TTL deslizante en Redis para el estado de conversación (solo verifica que
    `set` se llama con `ex`).
"""

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.evals.conversation_shots import ConversationShotRepository
from src.infrastructure.repositories import fechas
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.conversation_state_repo import (
    CONVERSATION_STATE_TTL_SECONDS,
    ConversationState,
    ConversationStateRepo,
)


NOW = datetime(2026, 6, 22, 12, 0, 0)


class DateHelperTests(unittest.TestCase):
    def test_parse_timestamp_accepts_iso_with_and_without_tz(self):
        self.assertIsNotNone(fechas.parse_timestamp("2026-06-20T10:00:00"))
        self.assertIsNotNone(fechas.parse_timestamp("2026-06-20T10:00:00-06:00"))
        self.assertIsNotNone(fechas.parse_timestamp("2026-06-20 10:00:00"))
        self.assertIsNotNone(fechas.parse_timestamp("2026-06-20"))

    def test_parse_timestamp_returns_none_on_garbage(self):
        self.assertIsNone(fechas.parse_timestamp("no-es-fecha"))
        self.assertIsNone(fechas.parse_timestamp(""))
        self.assertIsNone(fechas.parse_timestamp(None))

    def test_is_expired_respects_window(self):
        # 20 días antes de NOW = 2026-06-02 12:00
        self.assertTrue(fechas.is_expired("2026-05-01T10:00:00", 20, NOW))
        self.assertFalse(fechas.is_expired("2026-06-20T10:00:00", 20, NOW))

    def test_is_expired_is_conservative_when_unparseable(self):
        # Si no se puede fechar, NO se borra.
        self.assertFalse(fechas.is_expired("garbage", 20, NOW))


class PurgeConversationsTests(unittest.TestCase):
    def test_uses_cutoff_derived_from_days(self):
        """El corte que va al SQL son exactamente `days` días antes de `now`."""
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.consultar",
            return_value=[{"conversaciones": 3}],
        ) as consultar_mock:
            borradas = ConversationLogRepository.purge_older_than(20, now=NOW)

        self.assertEqual(borradas, 3)
        _, params = consultar_mock.call_args.args
        self.assertEqual(params[0], NOW - timedelta(days=20))

    def test_purge_is_scoped_per_conversation_not_per_message(self):
        """La retención es por conversación: el corte se compara con MAX(created_at).

        Si se comparara mensaje a mensaje, a un cliente activo se le borrarían
        los mensajes viejos y se perdería el arranque de su conversación.
        """
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.consultar",
            return_value=[{"conversaciones": 0}],
        ) as consultar_mock:
            ConversationLogRepository.purge_older_than(20, now=NOW)

        sql, _ = consultar_mock.call_args.args
        self.assertIn("GROUP BY client_id, canal", sql)
        self.assertIn("MAX(created_at)", sql)

    def test_noop_when_days_is_zero(self):
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.consultar"
        ) as consultar_mock:
            self.assertEqual(ConversationLogRepository.purge_older_than(0, now=NOW), 0)
        consultar_mock.assert_not_called()

    def test_returns_zero_when_database_fails(self):
        """Un fallo de la base no puede romper la tarea programada de purga."""
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.consultar",
            side_effect=RuntimeError("db caída"),
        ):
            self.assertEqual(ConversationLogRepository.purge_older_than(20, now=NOW), 0)


class PurgeShotsTests(unittest.TestCase):
    def test_deletes_shots_older_than_cutoff(self):
        with patch(
            "src.infrastructure.evals.conversation_shots.ejecutar", return_value=4
        ) as ejecutar_mock:
            borrados = ConversationShotRepository.purge_older_than(20, now=NOW)

        self.assertEqual(borrados, 4)
        _, params = ejecutar_mock.call_args.args
        self.assertEqual(params[0], NOW - timedelta(days=20))

    def test_noop_when_days_is_zero(self):
        with patch("src.infrastructure.evals.conversation_shots.ejecutar") as ejecutar_mock:
            self.assertEqual(ConversationShotRepository.purge_older_than(0, now=NOW), 0)
        ejecutar_mock.assert_not_called()


class RedisTtlTests(unittest.TestCase):
    def test_retention_constant_matches_setting(self):
        expected = settings.CONVERSATION_RETENTION_DAYS * 24 * 60 * 60
        self.assertEqual(CONVERSATION_STATE_TTL_SECONDS, expected)

    def test_conversation_state_set_applies_sliding_ttl(self):
        with patch(
            "src.infrastructure.repositories.conversation_state_repo.redis_client"
        ) as redis_mock:
            ConversationStateRepo.set(Channel.WHATSAPP, "506999", ConversationState())
        _, kwargs = redis_mock.set.call_args
        self.assertEqual(kwargs.get("ex"), CONVERSATION_STATE_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main()
