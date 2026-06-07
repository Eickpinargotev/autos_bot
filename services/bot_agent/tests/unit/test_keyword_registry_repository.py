import os
import unittest
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_INVITACIONES_URL", "http://nocodb.test/invitaciones")
os.environ.setdefault("NOCODB_REPORTES_URL", "http://nocodb.test/reportes")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.domain.entities import Channel
from src.infrastructure.repositories.keyword_registry_repository import KeywordRegistryRepository


class KeywordRegistryRepositoryTests(unittest.TestCase):
    def test_register_if_missing_creates_record_when_absent(self):
        get_response = MagicMock()
        get_response.json.return_value = {"list": []}
        get_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.raise_for_status.return_value = None

        with patch("src.infrastructure.repositories.keyword_registry_repository.httpx.get", return_value=get_response) as get_mock, patch(
            "src.infrastructure.repositories.keyword_registry_repository.httpx.post",
            return_value=post_response,
        ) as post_mock:
            result = KeywordRegistryRepository.register_if_missing("5061", "Cliente", Channel.WHATSAPP, "tareas")

        self.assertTrue(result)
        self.assertIn("where=", get_mock.call_args.args[0])
        post_mock.assert_called_once()
        fields = post_mock.call_args.kwargs["json"]["fields"]
        self.assertEqual(fields["registro"], "5061")
        self.assertEqual(fields["nombre"], "Cliente")
        self.assertEqual(fields["canal"], "whatsapp")
        self.assertEqual(fields["palabra clave"], "tareas")
        self.assertIn("fecha de creacion", fields)

    def test_register_if_missing_skips_existing_record(self):
        get_response = MagicMock()
        get_response.json.return_value = {"list": [{"id": "rec1", "fields": {"registro": "5061"}}]}
        get_response.raise_for_status.return_value = None

        with patch("src.infrastructure.repositories.keyword_registry_repository.httpx.get", return_value=get_response), patch(
            "src.infrastructure.repositories.keyword_registry_repository.httpx.post"
        ) as post_mock:
            result = KeywordRegistryRepository.register_if_missing("5061", "Cliente", Channel.WHATSAPP, "transporte")

        self.assertTrue(result)
        post_mock.assert_not_called()

    def test_delete_removes_existing_record(self):
        get_response = MagicMock()
        get_response.json.return_value = {"list": [{"id": "rec1", "fields": {"registro": "5061"}}]}
        get_response.raise_for_status.return_value = None
        delete_response = MagicMock()
        delete_response.raise_for_status.return_value = None

        with patch("src.infrastructure.repositories.keyword_registry_repository.httpx.get", return_value=get_response), patch(
            "src.infrastructure.repositories.keyword_registry_repository.httpx.request",
            return_value=delete_response,
        ) as request_mock:
            result = KeywordRegistryRepository.delete("5061", Channel.WHATSAPP)

        self.assertTrue(result)
        request_mock.assert_called_once()
        self.assertEqual(request_mock.call_args.args[0], "DELETE")
        self.assertEqual(request_mock.call_args.kwargs["json"], [{"id": "rec1"}])

    def test_exists_returns_true_when_record_is_present(self):
        get_response = MagicMock()
        get_response.json.return_value = {"list": [{"id": "rec1", "fields": {"registro": "5061"}}]}
        get_response.raise_for_status.return_value = None

        with patch("src.infrastructure.repositories.keyword_registry_repository.httpx.get", return_value=get_response):
            self.assertTrue(KeywordRegistryRepository.exists("5061", Channel.WHATSAPP))


if __name__ == "__main__":
    unittest.main()
