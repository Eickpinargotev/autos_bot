import json
import time
from dataclasses import dataclass

from openai import OpenAI

from src.core.config import settings
from src.core.prompts import RECEPTION_AGENT_PROMPT
from src.domain.entities import Channel
from src.infrastructure.logging.tool_call_logger import ToolCallLogger


client = OpenAI(api_key=settings.OPENAI_API_KEY or "test")


@dataclass
class ReceptionDecision:
    action: str
    flow: str = ""
    has_question: bool = False
    question: str = ""
    answer_source: str = "none"
    answer: str = ""
    clarifying_question: str = ""
    handoff_reason: str = ""
    confidence: float = 0.0


class ReceptionAgent:
    VALID_ACTIONS = {"answer_and_start_flow", "answer_and_clarify", "start_flow", "clarify", "handoff", "close"}
    VALID_FLOWS = {"GENERAL", "Alquiler", "CLASES", "DICTAMEN", "QUEJA", "WIN", ""}
    VALID_ANSWER_SOURCES = {"prompt_rules", "rag", "none"}

    def decide(
        self,
        text: str,
        conversation_history: list[dict],
        client_id: str = "",
        canal: Channel | str = "",
    ) -> ReceptionDecision:
        started = time.monotonic()
        input_data = {
            "text": text,
            "history_turns": len(conversation_history),
            "uses_openai": bool(settings.OPENAI_API_KEY),
        }
        if not settings.OPENAI_API_KEY:
            decision = self._fallback_decision(text, conversation_history)
            self._log_decision(client_id, canal, input_data, decision, started, "fallback")
            return decision

        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Devuelve JSON estricto."},
                    {
                        "role": "user",
                        "content": RECEPTION_AGENT_PROMPT.format(
                            mensaje=text,
                            conversation_history=json.dumps(conversation_history, ensure_ascii=False),
                        ),
                    },
                ],
            )
            data = json.loads(completion.choices[0].message.content or "{}")
            decision = self._validated_decision(data, text, conversation_history)
            self._log_decision(client_id, canal, input_data, decision, started, "success")
            return decision
        except Exception as exc:
            decision = self._fallback_decision(text, conversation_history)
            if client_id and canal:
                ToolCallLogger.error(
                    client_id=client_id,
                    canal=canal,
                    tool_name="reception.decide",
                    input_data=input_data,
                    output_data=self._decision_output(decision, source="fallback_after_error"),
                    error=exc,
                    text=f"Recepción usó fallback: {decision.action}",
                    duration_ms=ToolCallLogger._duration_ms(started),
                )
            return decision

    def _validated_decision(self, data: dict, text: str, conversation_history: list[dict] | None = None) -> ReceptionDecision:
        action = str(data.get("action") or "clarify")
        flow = str(data.get("flow") or "")
        answer_source = str(data.get("answer_source") or "none")

        if action not in self.VALID_ACTIONS:
            action = "clarify"
        if flow not in self.VALID_FLOWS:
            flow = ""
        if answer_source not in self.VALID_ANSWER_SOURCES:
            answer_source = "none"

        has_question = bool(data.get("has_question"))
        question = str(data.get("question") or "").strip()
        if has_question and not question:
            question = text.strip()

        decision = ReceptionDecision(
            action=action,
            flow=flow,
            has_question=has_question,
            question=question,
            answer_source=answer_source,
            answer=str(data.get("answer") or "").strip(),
            clarifying_question=str(data.get("clarifying_question") or "").strip(),
            handoff_reason=str(data.get("handoff_reason") or "").strip(),
            confidence=float(data.get("confidence") or 0.0),
        )
        return self._normalized_decision(decision, text, conversation_history or [])

    def _fallback_decision(self, text: str, conversation_history: list[dict] | None = None) -> ReceptionDecision:
        # Sin modelo de IA disponible (o si la llamada falla) no intentamos
        # adivinar la intención con reglas: degradamos de forma segura pidiendo
        # una aclaración con las opciones de servicio. La interpretación del
        # lenguaje natural del cliente es responsabilidad exclusiva del LLM.
        return ReceptionDecision(
            "clarify",
            clarifying_question=self.clarifying_question_for(text),
            confidence=0.0,
        )

    def _normalized_decision(
        self,
        decision: ReceptionDecision,
        text: str = "",
        conversation_history: list[dict] | None = None,
    ) -> ReceptionDecision:
        if decision.has_question and decision.answer and decision.answer_source == "prompt_rules":
            decision.answer = ""
            decision.answer_source = "rag"
            if decision.action == "answer_and_start_flow":
                decision.action = "answer_and_clarify"
                decision.flow = ""

        if decision.action in {"answer_and_start_flow", "start_flow"} and not decision.flow:
            decision.action = "answer_and_clarify" if decision.answer or decision.has_question else "clarify"

        # Solo agregamos una pregunta de descubrimiento genérica cuando no hay
        # nada más que enviar. Si el modelo ya devolvió una respuesta (p. ej. un
        # saludo cálido con opciones), no añadimos una segunda pregunta encima,
        # para no contestar con dos mensajes redundantes.
        if (
            decision.action in {"answer_and_clarify", "clarify"}
            and not decision.clarifying_question
            and not decision.answer
        ):
            decision.clarifying_question = self.clarifying_question_for(decision.question)

        if decision.action == "handoff" and not decision.handoff_reason:
            decision.handoff_reason = "Recepción derivó a asesor por falta de certeza o caso sensible."

        if decision.answer and decision.answer_source == "none":
            decision.answer_source = "rag" if decision.has_question else "prompt_rules"

        if not decision.has_question:
            decision.question = ""
            if decision.answer_source == "rag":
                decision.answer_source = "none"
            # Invariante P2 (tabla de decisión, estado inválido P2): sin una
            # pregunta detectada NO hay respuesta previa. Un start_flow no
            # antepone cortesía, felicitaciones ni comentarios antes del flujo
            # formal; answer_and_start_flow sin pregunta degrada a start_flow.
            if decision.action in {"start_flow", "answer_and_start_flow"}:
                decision.action = "start_flow"
                decision.answer = ""
                decision.answer_source = "none"

        return decision

    def clarifying_question_for(self, text: str) -> str:
        return "Con gusto. ¿Desea que sigamos con ese proceso o prefiere que le ayude con otro trámite?"

    def _log_decision(
        self,
        client_id: str,
        canal: Channel | str,
        input_data: dict,
        decision: ReceptionDecision,
        started: float,
        source: str,
    ):
        if not client_id or not canal:
            return
        ToolCallLogger.success(
            client_id=client_id,
            canal=canal,
            tool_name="reception.decide",
            input_data=input_data,
            output_data=self._decision_output(decision, source=source),
            text=f"Recepción decidió: {decision.action}",
            duration_ms=ToolCallLogger._duration_ms(started),
        )

    @staticmethod
    def _decision_output(decision: ReceptionDecision, source: str) -> dict:
        return {
            "action": decision.action,
            "flow": decision.flow,
            "has_question": decision.has_question,
            "question": decision.question,
            "answer_source": decision.answer_source,
            "has_answer": bool(decision.answer),
            "has_clarifying_question": bool(decision.clarifying_question),
            "handoff_reason": decision.handoff_reason,
            "confidence": decision.confidence,
            "source": source,
        }
