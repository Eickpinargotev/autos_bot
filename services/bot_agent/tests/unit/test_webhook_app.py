import os
import unittest


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


if __name__ == "__main__":
    unittest.main()
