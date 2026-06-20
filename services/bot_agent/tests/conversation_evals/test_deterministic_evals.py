import os
import unittest


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_INVITACIONES_URL", "http://nocodb.test/invitaciones")
os.environ.setdefault("NOCODB_REPORTES_URL", "http://nocodb.test/reportes")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from tests.conversation_evals.runner import ConversationEvalRunner
from tests.conversation_evals.schemas import (
    CapturedConversationShot,
    EvalExpected,
    ReplyClassificationMock,
)


class DeterministicConversationEvalTests(unittest.TestCase):
    def test_runner_can_simulate_captured_shot(self):
        shot = _sample_shot()
        runner = ConversationEvalRunner()
        # Test determinista: mockeamos la clasificación ("No" -> negative) para no
        # depender del LLM real, según la regla de conversation_evals.
        case = ConversationEvalRunner.case_from_shot(
            shot,
            metadata={"chanel": "whatsapp", "id_user": "5061", "shot_id": "5061_20260606_174533"},
            expected=EvalExpected(
                legacy_state="GENERAL",
                next_flow="GENERAL",
                next_node="G4",
                must_call_tools=["state.set"],
                must_not_call_tools=["rag.answer_question", "report.create"],
            ),
        )
        case.mocked_tools.reply_classification = ReplyClassificationMock(intent="negative")
        result = runner.run_case(case)

        self.assertEqual(result.final_flow, "GENERAL")
        self.assertEqual(result.final_node, "G4")


def _sample_shot() -> CapturedConversationShot:
    return CapturedConversationShot(
        state_before={
            "flow": "GENERAL",
            "node": "G1",
            "last_question": "Ya tiene el teórico ganado???",
            "awaiting_reply": True,
        },
        history=[{"user": "hola", "bot": ["Ya tiene el teórico ganado???"]}],
        turn={"user_message": "No", "bot_replies": []},
        review={
            "status": "reviewed",
            "tags": ["normal_answer"],
            "observed_error": "",
            "expected_behavior": "Debe avanzar por la rama de usuario sin teórico ganado.",
        },
    )


if __name__ == "__main__":
    unittest.main()
