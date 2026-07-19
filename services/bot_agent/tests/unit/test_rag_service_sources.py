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


class RagAnswerPromptContractTests(unittest.TestCase):
    """Contratos del prompt de generación de respuesta del RAG.

    Lección del transcript 2026-07-19: el prompt hablaba de "chunks" como un
    informe interno y el modelo espejó ese vocabulario en el mensaje al
    cliente ("los requisitos que sí aparecen en los chunks"). El prompt debe
    dejar claro que "answer" se envía literal y que la mecánica interna es
    invisible.
    """

    def _prompt(self) -> str:
        service = RagService.__new__(RagService)
        return service._answer_prompt(
            question="¿Requisitos para la prueba B1?",
            context="ALQUILER",
            last_question="",
            conversation_history=[],
            chunks=[{"external_id": "kb:1", "score": 0.9, "text": "Requisitos: ..."}],
        )

    def test_declara_que_answer_se_envia_literal(self):
        prompt = self._prompt()
        self.assertIn("TAL CUAL al cliente", prompt)
        self.assertIn("mensaje exacto que leerá el cliente", prompt)

    def test_declara_la_mecanica_interna_como_invisible(self):
        prompt = self._prompt()
        self.assertIn("La mecánica interna es invisible para el cliente", prompt)
        self.assertIn("No narres el proceso en tercera persona", prompt)

    def test_los_datos_no_se_etiquetan_con_jerga_interna(self):
        # La sección de datos se llama "Base de conocimiento" y va marcada como
        # contexto interno; la etiqueta vieja "Chunks recuperados" no vuelve.
        prompt = self._prompt()
        self.assertIn("Base de conocimiento (contexto interno, no la menciones):", prompt)
        self.assertNotIn("Chunks recuperados", prompt)


if __name__ == "__main__":
    unittest.main()
