"""Tests deterministas de la política de retención del historial (20 días).

Cubre dos mecanismos:
  - TTL deslizante en Redis para el estado/historial (no depende de un servidor
    Redis: solo verifica que `set` se llama con `ex`).
  - Purga programada del log durable en NocoDB (mockea httpx vía los helpers).
"""

import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("NOCODB_CONVERSATION_SHOTS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.repositories import nocodb_retention
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.conversation_state_repo import (
    CONVERSATION_STATE_TTL_SECONDS,
    ConversationState,
    ConversationStateRepo,
)
from src.infrastructure.evals.conversation_shots import ConversationShotRepository


NOW = datetime(2026, 6, 22, 12, 0, 0)


class DateHelperTests(unittest.TestCase):
    def test_parse_timestamp_accepts_iso_with_and_without_tz(self):
        self.assertIsNotNone(nocodb_retention.parse_timestamp("2026-06-20T10:00:00"))
        self.assertIsNotNone(nocodb_retention.parse_timestamp("2026-06-20T10:00:00-06:00"))
        self.assertIsNotNone(nocodb_retention.parse_timestamp("2026-06-20 10:00:00"))
        self.assertIsNotNone(nocodb_retention.parse_timestamp("2026-06-20"))

    def test_parse_timestamp_returns_none_on_garbage(self):
        self.assertIsNone(nocodb_retention.parse_timestamp("no-es-fecha"))
        self.assertIsNone(nocodb_retention.parse_timestamp(""))
        self.assertIsNone(nocodb_retention.parse_timestamp(None))

    def test_is_expired_respects_window(self):
        # 20 días antes de NOW = 2026-06-02 12:00
        self.assertTrue(nocodb_retention.is_expired("2026-05-01T10:00:00", 20, NOW))
        self.assertFalse(nocodb_retention.is_expired("2026-06-20T10:00:00", 20, NOW))

    def test_is_expired_is_conservative_when_unparseable(self):
        # Si no se puede fechar, NO se borra.
        self.assertFalse(nocodb_retention.is_expired("garbage", 20, NOW))


class LastActivityTests(unittest.TestCase):
    def _record(self, payload: dict) -> dict:
        return {"id": "x", "fields": {"json_mensajes": json.dumps(payload)}}

    def test_prefers_latest_among_candidates(self):
        record = self._record(
            {
                "created_at": "2026-05-01T10:00:00",
                "updated_at": "2026-06-10T10:00:00",
                "messages": [{"created_at": "2026-06-21T09:00:00"}],
            }
        )
        last = ConversationLogRepository._last_activity(record)
        self.assertEqual(last, datetime(2026, 6, 21, 9, 0, 0))

    def test_returns_none_when_no_dates(self):
        record = self._record({"messages": []})
        self.assertIsNone(ConversationLogRepository._last_activity(record))


class PurgeConversationsTests(unittest.TestCase):
    def _conv(self, rid: str, updated_at: str) -> dict:
        return {"id": rid, "fields": {"json_mensajes": json.dumps({"updated_at": updated_at})}}

    def test_deletes_only_expired_records(self):
        records = [
            self._conv("vieja", "2026-05-01T10:00:00"),   # vencida -> borrar
            self._conv("fresca", "2026-06-20T10:00:00"),  # reciente -> conservar
            self._conv("limite", "2026-06-02T13:00:00"),  # dentro de los 20d -> conservar
        ]
        with patch.object(settings, "NOCODB_CONVERSATIONS_URL", "http://nocodb/conv"), \
             patch.object(nocodb_retention, "iter_records", return_value=iter(records)), \
             patch.object(nocodb_retention, "delete_records", return_value=1) as delete_mock:
            deleted = ConversationLogRepository.purge_older_than(20, now=NOW)

        self.assertEqual(deleted, 1)
        delete_mock.assert_called_once()
        _, passed_ids = delete_mock.call_args.args
        self.assertEqual(passed_ids, ["vieja"])

    def test_noop_when_url_not_configured(self):
        with patch.object(settings, "NOCODB_CONVERSATIONS_URL", ""), \
             patch.object(nocodb_retention, "iter_records") as iter_mock:
            deleted = ConversationLogRepository.purge_older_than(20, now=NOW)
        self.assertEqual(deleted, 0)
        iter_mock.assert_not_called()


class PurgeShotsTests(unittest.TestCase):
    def _shot(self, rid: str, fecha_hora: str) -> dict:
        return {"id": rid, "fields": {"fecha_hora": fecha_hora}}

    def test_deletes_only_expired_shots(self):
        records = [
            self._shot("s_vieja", "2026-05-01 10:00:00"),
            self._shot("s_fresca", "2026-06-21 10:00:00"),
        ]
        with patch.object(settings, "NOCODB_CONVERSATION_SHOTS_URL", "http://nocodb/shots"), \
             patch.object(nocodb_retention, "iter_records", return_value=iter(records)), \
             patch.object(nocodb_retention, "delete_records", return_value=1) as delete_mock:
            deleted = ConversationShotRepository.purge_older_than(20, now=NOW)

        self.assertEqual(deleted, 1)
        _, passed_ids = delete_mock.call_args.args
        self.assertEqual(passed_ids, ["s_vieja"])

    def test_noop_when_shots_url_not_configured(self):
        with patch.object(settings, "NOCODB_CONVERSATION_SHOTS_URL", ""), \
             patch.object(nocodb_retention, "iter_records") as iter_mock:
            deleted = ConversationShotRepository.purge_older_than(20, now=NOW)
        self.assertEqual(deleted, 0)
        iter_mock.assert_not_called()


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
