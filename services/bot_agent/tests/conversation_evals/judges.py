import json
import os

from openai import OpenAI

from src.core.config import settings
from tests.conversation_evals.schemas import EvalRunResult, ShotEvalCase


JUDGE_PROMPT = """
Evalúa una respuesta de un agente conversacional FSM para una escuela de manejo.

Debes juzgar solo criterios semánticos; los estados y herramientas ya fueron evaluados por código.

Caso:
{case_json}

Resultado:
{result_json}

Devuelve JSON estricto:
{{
  "passed": true|false,
  "answers_user_question": true|false,
  "is_grounded": true|false,
  "resumes_pending_state": true|false,
  "has_unwanted_handoff": true|false,
  "score": 0.0,
  "failure_reason": "vacío si passed=true"
}}
"""


class SemanticJudge:
    def __init__(self, model: str | None = None):
        self.model = model or settings.EVAL_JUDGE_MODEL or settings.OPENAI_MODEL or "gpt-4o-mini"
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY or "test")

    @staticmethod
    def enabled() -> bool:
        return os.getenv("RUN_LLM_EVALS") == "1" and bool(settings.OPENAI_API_KEY)

    def evaluate(self, case: ShotEvalCase, result: EvalRunResult) -> dict:
        completion = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Eres un juez estricto de calidad conversacional. Devuelve JSON."},
                {
                    "role": "user",
                    "content": JUDGE_PROMPT.format(
                        case_json=case.model_dump_json() if hasattr(case, "model_dump_json") else case.json(),
                        result_json=result.model_dump_json() if hasattr(result, "model_dump_json") else result.json(),
                    ),
                },
            ],
        )
        raw = completion.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "passed": bool(data.get("passed")),
            "answers_user_question": bool(data.get("answers_user_question")),
            "is_grounded": bool(data.get("is_grounded")),
            "resumes_pending_state": bool(data.get("resumes_pending_state")),
            "has_unwanted_handoff": bool(data.get("has_unwanted_handoff")),
            "score": float(data.get("score") or 0.0),
            "failure_reason": str(data.get("failure_reason") or ""),
        }
