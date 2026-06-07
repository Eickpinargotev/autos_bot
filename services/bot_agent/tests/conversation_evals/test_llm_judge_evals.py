import os
import unittest


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_INVITACIONES_URL", "http://nocodb.test/invitaciones")
os.environ.setdefault("NOCODB_REPORTES_URL", "http://nocodb.test/reportes")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")

from tests.conversation_evals.judges import SemanticJudge
from tests.conversation_evals.runner import ConversationEvalRunner
from tests.conversation_evals.schemas import CapturedConversationShot, EvalExpected


@unittest.skipUnless(SemanticJudge.enabled(), "RUN_LLM_EVALS=1 y OPENAI_API_KEY son requeridos para el juez LLM")
class LlmJudgeConversationEvalTests(unittest.TestCase):
    def test_llm_judge_can_evaluate_a_reviewed_shot(self):
        runner = ConversationEvalRunner()
        judge = SemanticJudge()
        shot = CapturedConversationShot(
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
                "expected_behavior": "Debe avanzar por la rama de usuario sin teórico ganado.",
            },
        )
        case = runner.case_from_shot(
            shot,
            metadata={"chanel": "whatsapp", "id_user": "5061", "shot_id": "5061_20260606_174533"},
            expected=EvalExpected(
                legacy_state="GENERAL",
                next_flow="GENERAL",
                next_node="G4",
                must_call_tools=["state.set"],
            ),
        )
        result = runner.run_case(case)
        runner.assert_expected(case, result)
        judgement = judge.evaluate(case, result)
        self.assertTrue(judgement["passed"], judgement.get("failure_reason", ""))


if __name__ == "__main__":
    unittest.main()
