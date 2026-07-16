"""Agente único de conversación (modelo único).

Una sola decisión LLM por turno reemplaza al trío recepción/clasificador/router
del FSM. El modelo recibe las instrucciones estáticas (UNIFIED_AGENT_PROMPT) más
el catálogo de fragmentos literales como system prompt (cacheable) y los datos
del turno como JSON en el mensaje del usuario.

El código NUNCA confía en que el modelo cumpla el esquema: toda salida pasa por
`_validated_decision`, y cualquier error degrada a un fallback seguro.
"""

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI

from src.application.fragment_catalog import catalog_for_prompt
from src.core.config import settings
from src.core.prompts import FOLLOWUP_AGENT_PROMPT, UNIFIED_AGENT_PROMPT
from src.domain.entities import Channel
from src.infrastructure.logging.tool_call_logger import ToolCallLogger


client = OpenAI(
    api_key=settings.OPENAI_API_KEY or "test",
    timeout=settings.OPENAI_TIMEOUT_SECONDS,
    max_retries=settings.OPENAI_MAX_RETRIES,
)

RAG_TOKEN = "[[rag]]"
MAX_MESSAGES_PER_TURN = 4

# Aclaración de seguridad cuando no hay modelo disponible o la llamada falla:
# no adivinamos la intención con reglas; preguntamos con las opciones.
SAFE_CLARIFY_MESSAGE = (
    "Con gusto le ayudo. ¿Busca ayuda con su licencia, alquiler de vehículo "
    "para la prueba, clases de manejo o dictamen médico?"
)


@dataclass
class AgentDecision:
    action: str
    messages: list[str] = field(default_factory=list)
    rag_query: str = ""
    pending: str = ""
    report: str = ""
    city: str = ""
    confidence: float = 0.0
    source: str = "llm"


@dataclass
class FollowupDecision:
    send: bool
    message: str = ""


def _system_prompt() -> str:
    return f"{UNIFIED_AGENT_PROMPT}\n\n═══ CATÁLOGO DE FRAGMENTOS ═══\n\n{catalog_for_prompt()}"


class UnifiedAgent:
    VALID_ACTIONS = {"reply", "handoff", "close", "city_invitation"}

    def decide(
        self,
        text: str,
        state,
        client_id: str = "",
        canal: Channel | str = "",
    ) -> AgentDecision:
        turn_data = {
            "mensaje": text,
            "historial": state.conversation_history,
            "pendiente": state.last_question,
            "reporte_pendiente": state.pending_report,
            "recordatorios_enviados": state.reminder_level,
        }
        started = time.monotonic()
        input_data = {
            "text": text,
            "history_turns": len(state.conversation_history),
            "pending": state.last_question,
            "pending_report": bool(state.pending_report),
            "uses_openai": bool(settings.OPENAI_API_KEY),
        }
        if not settings.OPENAI_API_KEY:
            decision = self._fallback_decision(text)
            self._log_decision(client_id, canal, input_data, decision, started, "fallback")
            return decision

        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": json.dumps(turn_data, ensure_ascii=False)},
                ],
            )
            data = json.loads(completion.choices[0].message.content or "{}")
            decision = self._validated_decision(data, text)
            self._log_decision(client_id, canal, input_data, decision, started, "success")
            return decision
        except Exception as exc:
            decision = self._fallback_decision(text)
            decision.source = "fallback_after_error"
            if client_id and canal:
                ToolCallLogger.error(
                    client_id=client_id,
                    canal=canal,
                    tool_name="agent.decide",
                    input_data=input_data,
                    output_data=self._decision_output(decision),
                    error=exc,
                    text=f"Agente usó fallback: {decision.action}",
                    duration_ms=ToolCallLogger._duration_ms(started),
                )
            return decision

    def _validated_decision(self, data: dict, text: str) -> AgentDecision:
        action = str(data.get("action") or "reply")
        if action not in self.VALID_ACTIONS:
            action = "reply"

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            raw_messages = [raw_messages] if raw_messages else []
        messages = [str(m).strip() for m in raw_messages if str(m or "").strip()]
        messages = messages[:MAX_MESSAGES_PER_TURN]

        rag_query = str(data.get("rag_query") or "").strip()
        pending = str(data.get("pending") or "").strip()
        report = str(data.get("report") or "").strip()
        city = str(data.get("city") or "").strip()

        # Coherencia RAG: la etiqueta y la consulta van juntas o no van.
        has_rag_token = any(RAG_TOKEN in m for m in messages)
        if has_rag_token and not rag_query:
            rag_query = text.strip()
        if rag_query and not has_rag_token:
            messages.insert(0, RAG_TOKEN)
            messages = messages[:MAX_MESSAGES_PER_TURN]

        if action == "city_invitation" and not city:
            city = text.strip()
        if action == "handoff" and not report:
            report = f"El agente derivó a un asesor: {text[:240]}"
        if action == "reply" and not messages:
            action = "reply"
            messages = [SAFE_CLARIFY_MESSAGE]
            pending = pending or "Qué servicio necesita el cliente"

        return AgentDecision(
            action=action,
            messages=messages,
            rag_query=rag_query,
            pending=pending,
            report=report,
            city=city,
            confidence=float(data.get("confidence") or 0.0),
        )

    def _fallback_decision(self, text: str) -> AgentDecision:
        return AgentDecision(
            action="reply",
            messages=[SAFE_CLARIFY_MESSAGE],
            pending="Qué servicio necesita el cliente",
            source="fallback",
        )

    def _log_decision(
        self,
        client_id: str,
        canal: Channel | str,
        input_data: dict,
        decision: AgentDecision,
        started: float,
        source: str,
    ):
        if not client_id or not canal:
            return
        ToolCallLogger.success(
            client_id=client_id,
            canal=canal,
            tool_name="agent.decide",
            input_data=input_data,
            output_data={**self._decision_output(decision), "source": source},
            text=f"Agente decidió: {decision.action}",
            duration_ms=ToolCallLogger._duration_ms(started),
        )

    @staticmethod
    def _decision_output(decision: AgentDecision) -> dict:
        return {
            "action": decision.action,
            "messages": decision.messages,
            "rag_query": decision.rag_query,
            "pending": decision.pending,
            "report": decision.report,
            "city": decision.city,
            "confidence": decision.confidence,
        }


class FollowupAgent:
    """Decide y redacta el recordatorio cuando el cliente no respondió."""

    def decide(
        self,
        state,
        client_id: str = "",
        canal: Channel | str = "",
    ) -> FollowupDecision:
        if not settings.OPENAI_API_KEY:
            # Sin modelo no hay recordatorio "inteligente"; preferimos el
            # silencio a un mensaje genérico fuera de contexto.
            return FollowupDecision(send=False)

        turn_data = {
            "historial": state.conversation_history,
            "pendiente": state.last_question,
            "recordatorios_enviados": state.reminder_level,
        }
        started = time.monotonic()
        input_data = {
            "pending": state.last_question,
            "history_turns": len(state.conversation_history),
            "reminder_level": state.reminder_level,
        }
        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": FOLLOWUP_AGENT_PROMPT},
                    {"role": "user", "content": json.dumps(turn_data, ensure_ascii=False)},
                ],
            )
            data = json.loads(completion.choices[0].message.content or "{}")
            message = str(data.get("message") or "").strip()
            decision = FollowupDecision(send=bool(data.get("send")) and bool(message), message=message)
            if client_id and canal:
                ToolCallLogger.success(
                    client_id=client_id,
                    canal=canal,
                    tool_name="followup.decide",
                    input_data=input_data,
                    output_data={"send": decision.send, "message": decision.message},
                    text=f"Recordatorio inteligente: {'enviar' if decision.send else 'omitir'}",
                    duration_ms=ToolCallLogger._duration_ms(started),
                )
            return decision
        except Exception as exc:
            if client_id and canal:
                ToolCallLogger.error(
                    client_id=client_id,
                    canal=canal,
                    tool_name="followup.decide",
                    input_data=input_data,
                    error=exc,
                    duration_ms=ToolCallLogger._duration_ms(started),
                )
            return FollowupDecision(send=False)
