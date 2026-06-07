import os
import unittest


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.rag_service import RagService


class RagServiceSourceTests(unittest.TestCase):
    def test_source_summaries_include_chunk_content(self):
        service = RagService.__new__(RagService)
        chunks = [
            {
                "external_id": "nocodb:mlk30zxjzj4lfd8:abc",
                "score": 0.81234,
                "text": "Pregunta: Atienden domingos?\nRespuesta: Atendemos según disponibilidad.",
            }
        ]

        output = service._search_log_output(chunks)

        self.assertEqual(output["chunk_count"], 1)
        self.assertEqual(output["sources"][0]["source_id"], "nocodb:mlk30zxjzj4lfd8:abc")
        self.assertEqual(output["sources"][0]["score"], 0.8123)
        self.assertIn("Atendemos según disponibilidad", output["sources"][0]["content"])


if __name__ == "__main__":
    unittest.main()
