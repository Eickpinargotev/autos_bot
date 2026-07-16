"""Pipeline del agente único (modelo único).

Reemplaza al grafo FSM: un solo turno = una decisión del LLM + guardrails
deterministas en código. El LLM decide QUÉ hacer (con los playbooks y el
catálogo como contexto); el código garantiza los efectos (fragmentos
literales, RAG, reporte + bloqueo, anti-bucle, estado, recordatorio).
"""

import re
from dataclasses import dataclass, field
from typing import Any

from src.application.fragment_catalog import get_fragment, resolve_variant
from src.application.rag_service import RagService
from src.application.unified_agent import RAG_TOKEN, AgentDecision, UnifiedAgent
from src.core.config import settings
from src.domain.entities import Channel, UserState
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from src.infrastructure.repositories.conversation_state_repo import ConversationState, ConversationStateRepo
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.repositories.report_repository import ReportRepository
from src.infrastructure.repositories.unanswered_question_repository import UnansweredQuestionRepository


FRAG_TOKEN_RE = re.compile(r"\[\[frag:([A-Za-z0-9_.]+)\]\]")

AGENT_FLOW = "AGENT"


@dataclass
class FlowProcessingResult:
    legacy_state: UserState
    replies: list[str] = field(default_factory=list)
    reminder: dict[str, Any] | None = None


@dataclass
class _ExpandedTurn:
    replies: list[str] = field(default_factory=list)
    history_messages: list[str] = field(default_factory=list)
    fragment_report: str = ""
    rag_missed: bool = False


class AgentPipeline:
    HANDOFF_DEFAULT_MESSAGE = "En un momento le escribirá un agente especializado para atender su caso."
    CLOSE_DEFAULT_MESSAGE = "Con gusto, quedo atento si necesita algo más."
    RAG_FALLBACK_MESSAGE = (
        "Por ahora no tengo esa información disponible. "
        "Si gusta, puedo contactar a un asesor especializado para que le ayude con esa duda."
    )
    LOOP_REPORT = (
        "Anti-bucle: el agente iba a repetir la misma respuesta que ya envió; "
        "se deriva para atención manual."
    )

    def __init__(self):
        self.agent = UnifiedAgent()
        self.rag = RagService()

    def run(
        self,
        channel: Channel | str,
        user_id: str,
        text: str,
        user_name: str = "Desconocido",
    ) -> FlowProcessingResult:
        channel_value = channel.value if isinstance(channel, Channel) else channel
        stored = ConversationStateRepo.get(channel_value, user_id)
        if user_name and user_name != "Desconocido":
            stored.user_name = user_name

        decision = self.agent.decide(text, stored, client_id=user_id, canal=channel_value)

        if decision.action == "city_invitation":
            return self._handle_city_invitation(channel_value, user_id, text, user_name, stored, decision)

        turn = self._expand_messages(decision, stored, user_id, channel_value)

        # Anti-bucle determinista: si el turno reproduce exactamente lo que el
        # bot ya dijo en un turno reciente, no lo repetimos una tercera vez;
        # lo atiende una persona.
        if decision.action == "reply" and self._is_repeating(turn.history_messages, stored):
            decision = AgentDecision(action="handoff", messages=[], report=self.LOOP_REPORT)
            turn = _ExpandedTurn(
                replies=[self.HANDOFF_DEFAULT_MESSAGE],
                history_messages=[self.HANDOFF_DEFAULT_MESSAGE],
            )

        if decision.action == "handoff":
            return self._handle_handoff(channel_value, user_id, text, user_name, stored, decision, turn)

        if decision.action == "close":
            return self._handle_close(channel_value, user_id, text, stored, turn)

        return self._handle_reply(channel_value, user_id, text, user_name, stored, decision, turn)

    # ------------------------------------------------------------------
    # Expansión de mensajes: fragmentos literales y RAG
    # ------------------------------------------------------------------

    def _expand_messages(
        self,
        decision: AgentDecision,
        stored: ConversationState,
        user_id: str,
        channel_value: str,
    ) -> _ExpandedTurn:
        turn = _ExpandedTurn()
        for message in decision.messages:
            if turn.rag_missed:
                # La duda del cliente quedó sin respaldo: no lo empujamos con
                # más pasos del proceso mientras su duda sigue abierta.
                break
            if RAG_TOKEN in message:
                self._expand_rag(message, decision, stored, user_id, channel_value, turn)
                continue
            self._expand_fragments(message, user_id, channel_value, turn)
        self._dedupe_turn(turn)
        return turn

    @staticmethod
    def _dedupe_turn(turn: _ExpandedTurn):
        """Elimina mensajes idénticos dentro del MISMO turno.

        Un duplicado exacto dentro de un turno siempre es un defecto (p. ej. el
        modelo transcribió un fragmento y además envió su etiqueta); nunca es
        contenido legítimo, así que se poda de forma determinista.
        """
        seen: set[str] = set()
        replies: list[str] = []
        for msg in turn.replies:
            key = msg.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            replies.append(msg)
        turn.replies = replies

        seen_history: set[str] = set()
        history: list[str] = []
        for msg in turn.history_messages:
            key = msg.strip().lower()
            if key in seen_history:
                continue
            seen_history.add(key)
            history.append(msg)
        turn.history_messages = history

    def _expand_fragments(self, message: str, user_id: str, channel_value: str, turn: _ExpandedTurn):
        pos = 0
        emitted_any = False
        for match in FRAG_TOKEN_RE.finditer(message):
            prefix = message[pos:match.start()].strip()
            if prefix:
                turn.replies.append(prefix)
                turn.history_messages.append(prefix)
            fragment_id = resolve_variant(match.group(1), user_id, channel_value)
            fragment = get_fragment(fragment_id)
            if fragment and fragment.messages:
                turn.replies.extend(fragment.messages)
                turn.history_messages.append(f"[[frag:{fragment_id}]]")
                if fragment.report:
                    turn.fragment_report = fragment.report
                emitted_any = True
            pos = match.end()
        suffix = message[pos:].strip()
        if suffix:
            turn.replies.append(suffix)
            turn.history_messages.append(suffix)
            emitted_any = True
        if not emitted_any and not FRAG_TOKEN_RE.search(message) and message.strip():
            turn.replies.append(message.strip())
            turn.history_messages.append(message.strip())

    def _expand_rag(
        self,
        message: str,
        decision: AgentDecision,
        stored: ConversationState,
        user_id: str,
        channel_value: str,
        turn: _ExpandedTurn,
    ):
        question = decision.rag_query or message.replace(RAG_TOKEN, "").strip()
        answer = self._answer_rag(user_id, channel_value, question, stored)
        if answer.has_answer:
            turn.replies.append(answer.answer)
            turn.history_messages.append(answer.answer)
            return
        self._create_unanswered_question(user_id, channel_value, question)
        turn.replies.append(self.RAG_FALLBACK_MESSAGE)
        turn.history_messages.append(self.RAG_FALLBACK_MESSAGE)
        turn.rag_missed = True

    def _answer_rag(self, user_id: str, channel_value: str, question: str, stored: ConversationState):
        call = lambda: self.rag.answer_question(
            question,
            context=f"{AGENT_FLOW}",
            last_question=stored.last_question,
            conversation_history=stored.conversation_history,
            client_id=user_id,
            canal=channel_value,
        )
        if not user_id or not channel_value:
            return call()
        return ToolCallLogger.record(
            client_id=user_id,
            canal=channel_value,
            tool_name="rag.answer_question",
            input_data={
                "question": question,
                "context": AGENT_FLOW,
                "last_question": stored.last_question,
                "history_turns": len(stored.conversation_history),
            },
            output_mapper=lambda answer: {
                "has_answer": bool(answer.has_answer),
                "answer": answer.answer,
                "sources": answer.sources,
            },
            text_mapper=lambda answer: f"RAG respondió: {bool(answer.has_answer)}",
            call=call,
        )

    def _create_unanswered_question(self, user_id: str, channel_value: str, question: str):
        if not user_id or not channel_value:
            UnansweredQuestionRepository.create(question)
            return
        ToolCallLogger.record(
            client_id=user_id,
            canal=channel_value,
            tool_name="unanswered_question.create",
            input_data={"question": question},
            output_mapper=lambda result: {"created": bool(result)},
            text_mapper=lambda result: f"Pregunta sin respuesta registrada: {bool(result)}",
            call=lambda: UnansweredQuestionRepository.create(question),
        )

    # ------------------------------------------------------------------
    # Guardrail anti-bucle
    # ------------------------------------------------------------------

    @staticmethod
    def _is_repeating(history_messages: list[str], stored: ConversationState) -> bool:
        if not history_messages:
            return False
        current = "\n".join(m.strip().lower() for m in history_messages if m.strip())
        if not current:
            return False
        for entry in stored.conversation_history[-2:]:
            bot_messages = entry.get("bot") or []
            previous = "\n".join(str(m).strip().lower() for m in bot_messages if str(m).strip())
            if previous and previous == current:
                return True
        return False

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    def _handle_reply(
        self,
        channel_value: str,
        user_id: str,
        text: str,
        user_name: str,
        stored: ConversationState,
        decision: AgentDecision,
        turn: _ExpandedTurn,
    ) -> FlowProcessingResult:
        # El reporte del fragmento recién enviado manda; si no hubo, se
        # conserva el que ya estaba pendiente (una duda lateral no lo borra).
        pending_report = turn.fragment_report or stored.pending_report
        # Si la duda quedó sin respaldo, no reanclamos: el "pendiente" anterior
        # se conserva pero no se agenda recordatorio inmediato agresivo.
        pending = decision.pending if not turn.rag_missed else (decision.pending or stored.last_question)

        new_state = ConversationState(
            flow=AGENT_FLOW,
            node="",
            last_question=pending,
            awaiting_reply=bool(pending or pending_report),
            pending_report=pending_report,
            last_messages=turn.replies,
            user_name=user_name if user_name != "Desconocido" else stored.user_name,
            reminder_level=0,
            conversation_history=self._append_history(
                stored.conversation_history, text, turn.history_messages, "agent_reply", pending
            ),
        )
        ConversationStateRepo.set(channel_value, user_id, new_state)

        reminder = None
        if pending and not turn.rag_missed:
            reminder = {"level": 1, "seconds": settings.FOLLOWUP_FIRST_DELAY_SECONDS}
        return FlowProcessingResult(UserState.GENERAL, replies=turn.replies, reminder=reminder)

    def _handle_handoff(
        self,
        channel_value: str,
        user_id: str,
        text: str,
        user_name: str,
        stored: ConversationState,
        decision: AgentDecision,
        turn: _ExpandedTurn,
    ) -> FlowProcessingResult:
        replies = turn.replies or [self.HANDOFF_DEFAULT_MESSAGE]
        reason = decision.report or f"El agente derivó a un asesor: {text[:240]}"
        self._create_report_and_block(channel_value, user_id, user_name, stored, reason)
        return FlowProcessingResult(UserState.GENERAL, replies=replies)

    def _handle_close(
        self,
        channel_value: str,
        user_id: str,
        text: str,
        stored: ConversationState,
        turn: _ExpandedTurn,
    ) -> FlowProcessingResult:
        replies = turn.replies or [self.CLOSE_DEFAULT_MESSAGE]
        ConversationStateRepo.clear(channel_value, user_id)
        return FlowProcessingResult(UserState.GENERAL, replies=replies)

    def _handle_city_invitation(
        self,
        channel_value: str,
        user_id: str,
        text: str,
        user_name: str,
        stored: ConversationState,
        decision: AgentDecision,
    ) -> FlowProcessingResult:
        from src.application.publicidad_service import PublicidadService

        city_text = decision.city or text
        sent = ToolCallLogger.record(
            client_id=user_id,
            canal=channel_value,
            tool_name="publicidad.handle_invitation_by_city",
            input_data={"city_text": city_text, "user_name": user_name},
            output_mapper=lambda result: {"sent": bool(result)},
            text_mapper=lambda result: f"Publicidad por ciudad enviada: {bool(result)}",
            call=lambda: PublicidadService.handle_invitation_by_city(
                user_id, city_text, user_name, Channel(channel_value)
            ),
        )
        if sent:
            ConversationStateRepo.clear(channel_value, user_id)
            return FlowProcessingResult(UserState.PUBLICIDAD, replies=[])

        reason = f"Ciudad '{city_text}' no se encontró en la lista de invitaciones."
        self._create_report_and_block(channel_value, user_id, user_name, stored, reason)
        return FlowProcessingResult(UserState.GENERAL, replies=[self.HANDOFF_DEFAULT_MESSAGE])

    def _create_report_and_block(
        self,
        channel_value: str,
        user_id: str,
        user_name: str,
        stored: ConversationState,
        reason: str,
    ):
        channel = Channel(channel_value)
        ReportRepository.create_report(
            nombre=(user_name if user_name != "Desconocido" else stored.user_name),
            numero=user_id,
            problema=f"[{channel.value}] {reason}",
            link_whatsapp=f"https://wa.me/{user_id}",
        )
        PostgresUserRepo().block_user(user_id, reason=reason, days=12, channel=channel)

        from src.application.runtime_context import clear_user_runtime_context

        clear_user_runtime_context(channel, user_id, cancel_scheduled=False, clear_reports=False)

    # ------------------------------------------------------------------
    # Historial
    # ------------------------------------------------------------------

    @staticmethod
    def _append_history(
        history: list[dict],
        user_message: str,
        bot_messages: list[str],
        turn_type: str,
        pending: str = "",
    ) -> list[dict]:
        updated = [
            *history,
            {
                "flow": AGENT_FLOW,
                "node": "",
                "type": turn_type,
                "user": user_message,
                "bot": bot_messages,
                "pending": pending,
            },
        ]
        return updated[-settings.AGENT_HISTORY_LIMIT:]
