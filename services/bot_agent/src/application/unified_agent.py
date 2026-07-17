"""Agentes de conversación (arquitectura supervisor / workers).

Cada agente comparte el CONTRATO COMÚN y aporta su playbook y su catálogo:
- SupervisorAgent: recepción/coordinación (saludo, ambiguo, queja, WIN, cierre,
  dudas sueltas) y enrutamiento a los especialistas (action="route").
- SpecialistAgent(area): ejecuta el proceso de SU área (GENERAL, ALQUILER,
  CLASES, DICTAMEN) y devuelve el turno con action="defer" si el tema no es suyo.

El código NUNCA confía en que el modelo cumpla el esquema: toda salida pasa por
`_validated_decision`, y cualquier error degrada a un fallback seguro.
"""

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI

from src.application.fragment_catalog import AREA_FRAGMENTS, SPECIALIST_AREAS, catalog_for_prompt
from src.core.config import settings
from src.core.prompts import (
    AGENT_COMMON_CONTRACT,
    AREA_PROMPT_BODIES,
    FOLLOWUP_AGENT_PROMPT,
    SPECIALIST_OUTPUT_SCHEMA,
    SUPERVISOR_OUTPUT_SCHEMA,
    SUPERVISOR_PROMPT_BODY,
)
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


def _decision_llm_kwargs() -> dict:
    """Parámetros extra de las llamadas de DECISIÓN.

    Los agentes deciden sin razonamiento (reasoning_effort="none"): respuestas
    directas y sin tokens de reasoning facturados. Si OPENAI_MODEL se cambia a
    un modelo que no acepte el parámetro, se deja OPENAI_REASONING_EFFORT="".
    """
    if settings.OPENAI_REASONING_EFFORT:
        return {"reasoning_effort": settings.OPENAI_REASONING_EFFORT}
    return {}


@dataclass
class AgentDecision:
    action: str
    messages: list[str] = field(default_factory=list)
    rag_query: str = ""
    pending: str = ""
    report: str = ""
    city: str = ""
    target: str = ""
    confidence: float = 0.0
    source: str = "llm"


@dataclass
class FollowupDecision:
    send: bool
    message: str = ""


# System prompts ensamblados una vez por proceso (estables → prompt caching).
_system_prompt_cache: dict[str, str] = {}


def _system_prompt_for(role: str) -> str:
    if role not in _system_prompt_cache:
        if role == "SUPERVISOR":
            body = SUPERVISOR_PROMPT_BODY
            schema = SUPERVISOR_OUTPUT_SCHEMA
        else:
            body = AREA_PROMPT_BODIES[role]
            schema = SPECIALIST_OUTPUT_SCHEMA
        catalog = catalog_for_prompt(AREA_FRAGMENTS.get(role, ()))
        _system_prompt_cache[role] = (
            f"{AGENT_COMMON_CONTRACT}\n{schema}\n{body}"
            f"\n\n═══ TU CATÁLOGO DE FRAGMENTOS ═══\n\n{catalog}"
        )
    return _system_prompt_cache[role]


class _DecisionAgent:
    """Base: llamada LLM + validación + fallback + logging."""

    role = "SUPERVISOR"

    def _valid_actions(self) -> set[str]:
        raise NotImplementedError

    def decide(
        self,
        text: str,
        state,
        client_id: str = "",
        canal: Channel | str = "",
        internal_note: str = "",
    ) -> AgentDecision:
        turn_data = {
            "mensaje": text,
            "historial": state.conversation_history,
            "pendiente": state.last_question,
            "reporte_pendiente": state.pending_report,
            "recordatorios_enviados": state.reminder_level,
        }
        if internal_note:
            turn_data["nota_interna"] = internal_note
        started = time.monotonic()
        input_data = {
            "text": text,
            "role": self.role,
            "history_turns": len(state.conversation_history),
            "pending": state.last_question,
            "pending_report": bool(state.pending_report),
            "internal_note": internal_note,
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
                    {"role": "system", "content": _system_prompt_for(self.role)},
                    {"role": "user", "content": json.dumps(turn_data, ensure_ascii=False)},
                ],
                **_decision_llm_kwargs(),
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
                    tool_name=f"agent.decide.{self.role.lower()}",
                    input_data=input_data,
                    output_data=self._decision_output(decision),
                    error=exc,
                    text=f"Agente {self.role} usó fallback: {decision.action}",
                    duration_ms=ToolCallLogger._duration_ms(started),
                )
            return decision

    def _validated_decision(self, data: dict, text: str) -> AgentDecision:
        action = str(data.get("action") or "reply")
        if action not in self._valid_actions():
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
        target = str(data.get("target") or "").strip().upper()

        # Coherencia RAG: la etiqueta y la consulta van juntas o no van.
        has_rag_token = any(RAG_TOKEN in m for m in messages)
        if has_rag_token and not rag_query:
            rag_query = text.strip()
        if rag_query and not has_rag_token and action in {"reply", "close"}:
            messages.insert(0, RAG_TOKEN)
            messages = messages[:MAX_MESSAGES_PER_TURN]

        if action == "route" and target not in SPECIALIST_AREAS:
            # Ruta inválida: degrada a aclaración segura.
            action = "reply"
            target = ""
            if not messages:
                messages = [SAFE_CLARIFY_MESSAGE]
        if action != "route":
            target = ""
        if action == "city_invitation" and not city:
            city = text.strip()
        if action == "handoff" and not report:
            report = f"El agente derivó a un asesor: {text[:240]}"
        if action == "defer" and not report:
            report = "El especialista devolvió el turno: fuera de su área."
        if action == "reply" and not messages:
            messages = [SAFE_CLARIFY_MESSAGE]
            pending = pending or "Qué servicio necesita el cliente"

        return AgentDecision(
            action=action,
            messages=messages,
            rag_query=rag_query,
            pending=pending,
            report=report,
            city=city,
            target=target,
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
            tool_name=f"agent.decide.{self.role.lower()}",
            input_data=input_data,
            output_data={**self._decision_output(decision), "source": source},
            text=f"Agente {self.role} decidió: {decision.action}",
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
            "target": decision.target,
            "confidence": decision.confidence,
        }


class SupervisorAgent(_DecisionAgent):
    role = "SUPERVISOR"

    def _valid_actions(self) -> set[str]:
        return {"reply", "route", "handoff", "close", "city_invitation"}


class SpecialistAgent(_DecisionAgent):
    def __init__(self, area: str):
        if area not in SPECIALIST_AREAS:
            raise ValueError(f"Área desconocida: {area}")
        self.role = area

    def _valid_actions(self) -> set[str]:
        return {"reply", "defer", "handoff", "close", "city_invitation"}


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
                **_decision_llm_kwargs(),
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
