from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from src.application.flow_router import FlowRouter
from src.application.message_catalog import get_node_data
from src.application.rag_service import RagService
from src.application.response_classifier import ResponseClassifier
from src.core.config import settings
from src.domain.entities import Channel, UserState
from src.infrastructure.repositories.conversation_state_repo import ConversationState, ConversationStateRepo
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.repositories.report_repository import ReportRepository
from src.infrastructure.repositories.unanswered_question_repository import UnansweredQuestionRepository


class FlowGraphState(TypedDict, total=False):
    channel: str
    user_id: str
    user_name: str
    text: str
    stored: ConversationState
    flow: str
    node: str
    next_flow: str
    next_node: str
    replies: list[str]
    should_report: bool
    report_reason: str
    should_block: bool
    reminder: dict[str, Any]
    legacy_state: UserState
    off_flow_answered: bool
    off_flow_question: str


@dataclass
class FlowProcessingResult:
    legacy_state: UserState
    replies: list[str] = field(default_factory=list)
    reminder: dict[str, Any] | None = None


class FlowGraphRunner:
    CITY_INVITATION_FLOW = "PUBLICIDAD"
    CITY_INVITATION_NODE = "CITY_INVITATION"
    COMPLAINT_HANDOFF_MESSAGE = "En un momento le escribirá un agente especializado para atender su caso."

    def __init__(self):
        self.router = FlowRouter()
        self.classifier = ResponseClassifier()
        self.rag = RagService()
        self.graph = self._build_graph()

    def run(self, channel: Channel | str, user_id: str, text: str, user_name: str = "Desconocido") -> FlowProcessingResult:
        channel_value = channel.value if isinstance(channel, Channel) else channel
        result = self.graph.invoke({
            "channel": channel_value,
            "user_id": user_id,
            "user_name": user_name,
            "text": text,
            "replies": [],
            "should_report": False,
            "should_block": False,
            "off_flow_answered": False,
        })
        return FlowProcessingResult(
            legacy_state=result.get("legacy_state", UserState.GENERAL),
            replies=result.get("replies", []),
            reminder=result.get("reminder"),
        )

    def _build_graph(self):
        graph = StateGraph(FlowGraphState)
        graph.add_node("load_current_node", self._load_current_node)
        graph.add_node("classify_initial_intent", self._classify_initial_intent)
        graph.add_node("evaluate_reply", self._evaluate_reply)
        graph.add_node("answer_off_flow_question", self._answer_off_flow_question)
        graph.add_node("send_node_messages", self._send_node_messages)
        graph.add_node("create_report_and_block", self._create_report_and_block)

        graph.set_entry_point("load_current_node")
        graph.add_conditional_edges(
            "load_current_node",
            self._after_load,
            {
                "initial": "classify_initial_intent",
                "existing": "evaluate_reply",
                "report": "create_report_and_block",
            },
        )
        graph.add_edge("classify_initial_intent", "send_node_messages")
        graph.add_conditional_edges(
            "evaluate_reply",
            self._after_evaluate,
            {
                "send": "send_node_messages",
                "question": "answer_off_flow_question",
                "report": "create_report_and_block",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "answer_off_flow_question",
            self._after_off_flow,
            {
                "report": "create_report_and_block",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "send_node_messages",
            self._after_send,
            {
                "report": "create_report_and_block",
                "end": END,
            },
        )
        graph.add_edge("create_report_and_block", END)
        return graph.compile()

    def _load_current_node(self, state: FlowGraphState) -> FlowGraphState:
        stored = ConversationStateRepo.get(state["channel"], state["user_id"])
        state["stored"] = stored
        state["flow"] = stored.flow
        state["node"] = stored.node
        return state

    def _after_load(self, state: FlowGraphState) -> str:
        if not state.get("node"):
            return "initial"
        return "existing"

    def _classify_initial_intent(self, state: FlowGraphState) -> FlowGraphState:
        flow = self._initial_flow(state["text"])
        node = self.router.initial_node(flow, Channel(state["channel"]), state["user_id"])
        state["next_flow"] = flow
        state["next_node"] = node
        state["legacy_state"] = self._legacy_state(flow)
        return state

    def _evaluate_reply(self, state: FlowGraphState) -> FlowGraphState:
        stored = state["stored"]
        classification = self.classifier.classify_reply(state["text"], stored.flow, stored.node, stored.last_question)
        if classification.intent == "complaint":
            if stored.flow == "QUEJA" and stored.pending_report:
                state["should_report"] = True
                state["report_reason"] = stored.pending_report
                return state
            state["should_report"] = True
            state["replies"] = [self.COMPLAINT_HANDOFF_MESSAGE]
            state["report_reason"] = self._complaint_report_reason(state["text"], stored)
            return state
        if self.classifier.asks_for_human_help(state["text"]):
            state["should_report"] = True
            state["replies"] = [self.COMPLAINT_HANDOFF_MESSAGE]
            state["report_reason"] = self._human_help_report_reason(state["text"], stored)
            return state

        next_flow, next_node = self.router.next_node(stored.flow, stored.node, classification, state["user_id"], state["channel"])
        if next_node:
            state["next_flow"] = next_flow
            state["next_node"] = next_node
            if classification.has_off_flow_question:
                state["off_flow_question"] = classification.off_flow_question or state["text"]
            state["legacy_state"] = self._legacy_state(next_flow)
            return state

        if classification.intent == "question":
            return state

        if stored.pending_report:
            state["should_report"] = True
            state["report_reason"] = stored.pending_report
            return state

        state["off_flow_answered"] = True
        state["replies"] = [self._retake_message(stored)]
        stored.conversation_history = self._append_history(
            stored.conversation_history,
            state["text"],
            state["replies"],
            stored.flow,
            stored.node,
            "retake",
        )
        ConversationStateRepo.set(state["channel"], state["user_id"], stored)
        return state

    def _after_evaluate(self, state: FlowGraphState) -> str:
        if state.get("should_report"):
            return "report"
        if state.get("next_node"):
            return "send"
        if not state.get("off_flow_answered"):
            return "question"
        return "end"

    def _answer_off_flow_question(self, state: FlowGraphState) -> FlowGraphState:
        stored = state["stored"]
        if self.classifier.asks_for_human_help(state["text"]):
            state["should_report"] = True
            state["replies"] = [self.COMPLAINT_HANDOFF_MESSAGE]
            state["report_reason"] = self._human_help_report_reason(state["text"], stored)
            return state

        state["replies"], turn_type = self._off_flow_replies(state["text"], stored, include_retake=True)
        stored.conversation_history = self._append_history(
            stored.conversation_history,
            state["text"],
            state["replies"],
            stored.flow,
            stored.node,
            turn_type,
        )
        ConversationStateRepo.set(state["channel"], state["user_id"], stored)
        return state

    def _off_flow_replies(self, question: str, stored: ConversationState, include_retake: bool) -> tuple[list[str], str]:
        if not question:
            return [], ""

        answer = self.rag.answer_question(
            question,
            context=f"{stored.flow}.{stored.node}",
            last_question=stored.last_question,
            conversation_history=stored.conversation_history,
        )
        if answer.has_answer:
            replies = [answer.answer]
            turn_type = "rag_answer"
        else:
            UnansweredQuestionRepository.create(question)
            replies = [
                "Por ahora no tengo esa información disponible. Si gusta, puedo contactar a un asesor especializado para que le ayude con esa duda.",
            ]
            turn_type = "rag_fallback"

        if include_retake:
            replies.append(self._retake_message(stored))
        return replies, turn_type

    def _after_off_flow(self, state: FlowGraphState) -> str:
        return "report" if state.get("should_report") else "end"

    def _send_node_messages(self, state: FlowGraphState) -> FlowGraphState:
        flow = state["next_flow"]
        node = state["next_node"]
        if flow == self.CITY_INVITATION_FLOW and node == self.CITY_INVITATION_NODE:
            return self._send_city_invitation(state)

        node_data = get_node_data(flow, node)
        messages = node_data.get("mensajes", [])
        last_question = self._extract_last_question(messages)
        pending_report = node_data.get("reporte", "")

        stored = state.get("stored", ConversationState())
        off_flow_replies, off_flow_turn_type = self._off_flow_replies(
            state.get("off_flow_question", ""),
            stored,
            include_retake=False,
        )
        replies = [*off_flow_replies, *messages]
        state["replies"] = replies
        new_state = ConversationState(
            flow=flow,
            node=node,
            last_question=last_question,
            awaiting_reply=bool(last_question or pending_report),
            pending_report=pending_report,
            last_messages=messages,
            user_name=state["user_name"],
            reminder_level=0,
            conversation_history=self._append_history(
                stored.conversation_history,
                state["text"],
                replies,
                flow,
                node,
                "answer_and_flow_message" if off_flow_turn_type else "flow_message",
            ),
        )
        ConversationStateRepo.set(state["channel"], state["user_id"], new_state)
        state["legacy_state"] = self._legacy_state(flow)

        reminder = node_data.get("recordatorio")
        if reminder:
            state["reminder"] = {"flow": flow, "node": node, "level": 1, "seconds": reminder.get("segundos", 7200)}

        if flow == "WIN" and pending_report:
            state["should_report"] = True
            state["report_reason"] = pending_report
        return state

    def _send_city_invitation(self, state: FlowGraphState) -> FlowGraphState:
        stored = state.get("stored", ConversationState())
        from src.application.publicidad_service import PublicidadService

        sent = PublicidadService.handle_invitation_by_city(
            state["user_id"],
            state["text"],
            state["user_name"],
            Channel(state["channel"]),
        )
        state["legacy_state"] = UserState.PUBLICIDAD if sent else self._legacy_state(stored.flow)
        if sent:
            return state

        state["replies"] = [self._retake_message(stored)]
        stored.conversation_history = self._append_history(
            stored.conversation_history,
            state["text"],
            state["replies"],
            stored.flow,
            stored.node,
            "city_not_found",
        )
        ConversationStateRepo.set(state["channel"], state["user_id"], stored)
        return state

    def _after_send(self, state: FlowGraphState) -> str:
        return "report" if state.get("should_report") else "end"

    def _create_report_and_block(self, state: FlowGraphState) -> FlowGraphState:
        channel = Channel(state["channel"])
        user_id = state["user_id"]
        reason = state.get("report_reason") or "Se generó reporte por respuesta del usuario"
        ReportRepository.create_report(
            nombre=state.get("user_name") or state.get("stored", ConversationState()).user_name,
            numero=user_id,
            problema=f"[{channel.value}] {reason}",
            link_whatsapp=f"https://wa.me/{user_id}",
        )
        PostgresUserRepo().block_user(user_id, reason=reason, days=12, channel=channel)

        from src.application.runtime_context import clear_user_runtime_context

        clear_user_runtime_context(channel, user_id, cancel_scheduled=False, clear_reports=False)
        state["legacy_state"] = UserState.GENERAL
        return state

    def _initial_flow(self, text: str) -> str:
        return self.classifier.classify_initial_flow(text)

    def _legacy_state(self, flow: str) -> UserState:
        mapping = {
            "GENERAL": UserState.GENERAL,
            "Alquiler": UserState.ALQUILER,
            "CLASES": UserState.CLASES,
            "DICTAMEN": UserState.DICTAMEN,
            "QUEJA": UserState.QUEJAS,
            "WIN": UserState.WIN,
            "PUBLICIDAD": UserState.PUBLICIDAD,
        }
        return mapping.get(flow, UserState.GENERAL)

    def _extract_last_question(self, messages: list[str]) -> str:
        for msg in reversed(messages):
            for line in reversed([line.strip() for line in msg.splitlines() if line.strip()]):
                if "?" in line or "¿" in line:
                    return line
        return messages[-1] if messages else ""

    def _retake_message(self, stored: ConversationState) -> str:
        if stored.last_question:
            return f"Para continuar, retomemos la última pregunta:\n\n{self._extract_last_question([stored.last_question])}"
        return "Para continuar, cuénteme cómo desea seguir con su proceso."

    def _complaint_report_reason(self, text: str, stored: ConversationState) -> str:
        context = self._recent_complaint_context(stored.conversation_history)
        reason = f"El usuario manifestó queja o enojo en {stored.flow}.{stored.node}: {text}"
        if context:
            reason += f" | Contexto reciente: {context}"
        return reason[:1000]

    def _human_help_report_reason(self, text: str, stored: ConversationState) -> str:
        summary = self.classifier.summarize_for_report(text, stored.flow, stored.node)
        context = self._recent_complaint_context(stored.conversation_history)
        if context:
            summary = f"{summary} | El historial reciente muestra molestia/queja: {context}"
        return summary[:1000]

    def _recent_complaint_context(self, history: list[dict]) -> str:
        snippets = []
        for turn in history[-settings.RAG_CONVERSATION_HISTORY_LIMIT:]:
            user_text = str(turn.get("user") or "").strip()
            if user_text and self.classifier.is_angry_or_complaint(user_text):
                flow = turn.get("flow") or ""
                node = turn.get("node") or ""
                snippets.append(f"{flow}.{node}: {user_text[:180]}")
        return " / ".join(snippets[-2:])

    def _append_history(
        self,
        history: list[dict],
        user_message: str,
        bot_messages: list[str],
        flow: str,
        node: str,
        turn_type: str,
    ) -> list[dict]:
        updated = [
            *history,
            {
                "flow": flow,
                "node": node,
                "type": turn_type,
                "user": user_message,
                "bot": bot_messages,
            },
        ]
        return updated[-settings.RAG_CONVERSATION_HISTORY_LIMIT:]
