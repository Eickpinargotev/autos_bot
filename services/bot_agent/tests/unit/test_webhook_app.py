import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.infrastructure.webhooks import app as webhook_app


class WebhookAppTests(unittest.TestCase):
    def test_health_route_exists(self):
        self.assertEqual(webhook_app.health(), {"status": "ok"})

    def test_nocodb_rag_route_exists(self):
        routes = {getattr(route, "path", "") for route in webhook_app.app.routes}
        self.assertIn("/webhooks/nocodb-rag-chunks", routes)

    def test_rag_webhook_disabled_without_configured_token(self):
        # El webhook escribe en la base de conocimiento del RAG: sin token
        # configurado debe quedar deshabilitado (503), nunca abierto.
        with patch("src.infrastructure.webhooks.app.settings.NOCODB_RAG_WEBHOOK_TOKEN", ""):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(webhook_app.nocodb_rag_chunks_webhook({}, token="cualquiera"))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_rag_webhook_rejects_wrong_token(self):
        with patch("src.infrastructure.webhooks.app.settings.NOCODB_RAG_WEBHOOK_TOKEN", "secreto"):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(webhook_app.nocodb_rag_chunks_webhook({}, token="incorrecto"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rag_webhook_accepts_valid_token(self):
        with patch("src.infrastructure.webhooks.app.settings.NOCODB_RAG_WEBHOOK_TOKEN", "secreto"), patch(
            "src.infrastructure.webhooks.app.rag_service.sync_chunk_event",
            return_value={"upserted": 1, "deleted": 0, "ignored": 0},
        ) as sync_mock:
            result = asyncio.run(webhook_app.nocodb_rag_chunks_webhook({"rows": []}, token="secreto"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["upserted"], 1)
        sync_mock.assert_called_once_with({"rows": []})


if __name__ == "__main__":
    unittest.main()
