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

from src.application import seguimiento_service
from src.application.fragment_catalog import SPECIALIST_AREAS, catalog_for_prompt
from src.core.config import settings
from src.core.modelos import kwargs_de_decision
from src.core.prompts import (
    AGENT_COMMON_CONTRACT,
    AREA_PROMPT_BODIES,
    FOLLOWUP_AGENT_BODY,
    FOLLOWUP_TECHNICAL_CONTRACT,
    SPECIALIST_OUTPUT_SCHEMA,
    SUPERVISOR_OUTPUT_SCHEMA,
    SUPERVISOR_PROMPT_BODY,
)
from src.domain.entities import Channel
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from src.infrastructure.evals.conversation_shots import ShotTraceCollector
from src.infrastructure.logging.trace_sanitizer import MAX_MODEL_BYTES, sanitize
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories import instrucciones_repository


client = OpenAI(
    api_key=settings.OPENAI_API_KEY or "test",
    timeout=settings.OPENAI_TIMEOUT_SECONDS,
    max_retries=settings.OPENAI_MAX_RETRIES,
)

RAG_TOKEN = "[[rag]]"
MAX_MESSAGES_PER_TURN = 4


def _record_model_trace(
    *, client_id: str, canal: Channel | str, agent: str, model: str,
    request: dict, response=None, usage=None, status: str = "success",
    error: Exception | str = "", duration_ms: int | None = None,
) -> None:
    """Registra la llamada real; nunca una cadena de pensamiento privada."""
    ShotTraceCollector.record_model_event(
        agent=agent, model=model, request=request, response=response, usage=usage,
        status=status, error=error, duration_ms=duration_ms,
    )
    if client_id and canal:
        ConversationLogRepository.log_tool_event(
            client_id=client_id,
            canal=canal,
            tool_name=f"llm.{agent.lower()}",
            status=status,
            input_data=sanitize({"model": model, "request": request}, MAX_MODEL_BYTES),
            output_data=sanitize({"response": response, "usage": usage}, MAX_MODEL_BYTES),
            error=str(error or "")[:2000],
            text=f"Llamada LLM {agent}: {status}",
            duration_ms=duration_ms,
            event_type="model_call",
        )

# Aclaración de seguridad cuando no hay modelo disponible o la llamada falla:
# no adivinamos la intención con reglas; preguntamos con las opciones.
SAFE_CLARIFY_MESSAGE = (
    "Con gusto le ayudo. ¿Busca ayuda con su licencia, alquiler de vehículo "
    "para la prueba, clases de manejo o dictamen médico?"
)


def _decision_response_format(valid_actions: set[str]) -> dict:
    """Structured Outputs de OpenAI (json_schema con strict=True).

    El proveedor garantiza que la salida cumple el esquema al pie de la letra:
    campos exactos, tipos exactos y acciones/targets dentro del enum. Es el
    método más fuerte que ofrece la API (más que el JSON mode `json_object`).
    `_validated_decision` sigue siendo la autoridad para la coherencia
    SEMÁNTICA (etiqueta RAG + consulta, defer hacia sí mismo, mensajes vacíos):
    el esquema garantiza la forma, no el sentido.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": sorted(valid_actions)},
                    "messages": {"type": "array", "items": {"type": "string"}},
                    "rag_query": {"type": "string"},
                    "pending": {"type": "string"},
                    "report": {"type": "string"},
                    "city": {"type": "string"},
                    "target": {"type": "string", "enum": ["", *SPECIALIST_AREAS]},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "action", "messages", "rag_query", "pending",
                    "report", "city", "target", "confidence",
                ],
            },
        },
    }


FOLLOWUP_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "followup_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "send": {"type": "boolean"},
                "message": {"type": "string"},
            },
            "required": ["send", "message"],
        },
    },
}


# Los parámetros por modelo (temperature / reasoning_effort) los resuelve
# `core.modelos`: dependen de la FAMILIA del modelo, no del agente, y mandar el
# que no toca hace fallar todas las llamadas — no las degrada, las tumba.


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
    tipo = role.lower()
    if role == "SUPERVISOR":
        body_base = SUPERVISOR_PROMPT_BODY
        schema = SUPERVISOR_OUTPUT_SCHEMA
    else:
        body_base = AREA_PROMPT_BODIES[role]
        schema = SPECIALIST_OUTPUT_SCHEMA
    # Se consulta en cada uso para que una versión recién guardada tenga efecto
    # inmediato. Solo la parte estable y protegida queda cacheada por rol.
    body = instrucciones_repository.activas(tipo) or body_base
    if role not in _system_prompt_cache:
        _system_prompt_cache[role] = f"{AGENT_COMMON_CONTRACT}\n{schema}"
    catalog = catalog_for_prompt(role=role)
    return (
        f"{_system_prompt_cache[role]}"
        f"\n\n═══ TU CATÁLOGO DE FRAGMENTOS ═══\n\n{catalog}"
        f"\n\n{body}"
    )


class _DecisionAgent:
    """Base: llamada LLM + validación + fallback + logging."""

    role = "SUPERVISOR"
    # Cada agente declara con qué modelo trabaja. El supervisor decide sobre el
    # prompt grande y enruta, así que se le da el modelo más capaz; el
    # especialista ya sabe de qué área habla y le basta con uno más barato.
    modelo = settings.OPENAI_MODEL_ESPECIALISTA

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
            # El modelo queda en la traza: permite diagnosticar despliegues
            # con un modelo distinto al esperado sin adivinar. Es el del AGENTE,
            # no el global: supervisor y especialistas ya no usan el mismo.
            "model": self.modelo,
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
            messages = [
                {"role": "system", "content": _system_prompt_for(self.role)},
                {"role": "user", "content": json.dumps(turn_data, ensure_ascii=False)},
            ]
            response_format = _decision_response_format(self._valid_actions())
            completion = client.chat.completions.create(
                model=self.modelo,
                response_format=response_format,
                messages=messages,
                **kwargs_de_decision(self.modelo),
            )
            seguimiento_service.registrar_uso_llm(
                client_id,
                canal,
                getattr(completion, "usage", None),
                origen="agente",
                modelo=self.modelo,
            )
            data = json.loads(completion.choices[0].message.content or "{}")
            decision = self._validated_decision(data, text)
            _record_model_trace(
                client_id=client_id, canal=canal, agent=self.role, model=self.modelo,
                request={"messages": messages, "response_format": response_format},
                response=data, usage=getattr(completion, "usage", None),
                duration_ms=ToolCallLogger._duration_ms(started),
            )
            self._log_decision(client_id, canal, input_data, decision, started, "success")
            return decision
        except Exception as exc:
            decision = self._fallback_decision(text)
            decision.source = "fallback_after_error"
            _record_model_trace(
                client_id=client_id, canal=canal, agent=self.role, model=self.modelo,
                request={"turn": turn_data}, status="error", error=exc,
                duration_ms=ToolCallLogger._duration_ms(started),
            )
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
        if action == "defer":
            # El defer puede llevar destino sugerido; nunca hacia sí mismo.
            if target not in SPECIALIST_AREAS or target == self.role:
                target = ""
        elif action != "route":
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
    modelo = settings.OPENAI_MODEL_SUPERVISOR

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
        # Decisión chica y muy acotada (¿retomo o no, y con qué frase?): va al
        # modelo auxiliar, que cuesta una fracción del que enruta.
        modelo = settings.OPENAI_MODEL_AUXILIAR
        input_data = {
            "pending": state.last_question,
            "model": modelo,
            "history_turns": len(state.conversation_history),
            "reminder_level": state.reminder_level,
        }
        try:
            body = instrucciones_repository.activas("recordatorio") or FOLLOWUP_AGENT_BODY
            inicio_body = "═══ CUÁNDO NO ENVIAR"
            if inicio_body in body:
                body = body[body.index(inicio_body):]
            prompt = f"{FOLLOWUP_TECHNICAL_CONTRACT}\n{body}"
            completion = client.chat.completions.create(
                model=modelo,
                response_format=FOLLOWUP_RESPONSE_FORMAT,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(turn_data, ensure_ascii=False)},
                ],
                **kwargs_de_decision(modelo),
            )
            seguimiento_service.registrar_uso_llm(
                client_id,
                canal,
                getattr(completion, "usage", None),
                origen="recordatorio",
                modelo=modelo,
            )
            data = json.loads(completion.choices[0].message.content or "{}")
            message = str(data.get("message") or "").strip()
            decision = FollowupDecision(send=bool(data.get("send")) and bool(message), message=message)
            _record_model_trace(
                client_id=client_id, canal=canal, agent="FOLLOWUP", model=modelo,
                request={
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(turn_data, ensure_ascii=False)},
                    ],
                    "response_format": FOLLOWUP_RESPONSE_FORMAT,
                },
                response=data, usage=getattr(completion, "usage", None),
                duration_ms=ToolCallLogger._duration_ms(started),
            )
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
            _record_model_trace(
                client_id=client_id, canal=canal, agent="FOLLOWUP", model=modelo,
                request={"turn": turn_data}, status="error", error=exc,
                duration_ms=ToolCallLogger._duration_ms(started),
            )
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
