import os
import unittest


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.domain.entities import Channel
from src.infrastructure.channels.senders import ChannelSenderRegistry


class ChannelSenderTests(unittest.TestCase):
    def test_whatsapp_sender_is_pending_http_implementation(self):
        sender = ChannelSenderRegistry.get(Channel.WHATSAPP)

        with self.assertRaisesRegex(NotImplementedError, "WhatsApp HTTP sender pendiente de implementar"):
            sender.send_message_sync("50688888888", "Hola")


if __name__ == "__main__":
    unittest.main()
