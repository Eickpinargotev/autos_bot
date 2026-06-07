from typing import Iterable
from unittest.mock import MagicMock, patch

from src.application.flow_graph import FlowGraphRunner
from src.application.rag_service import RagAnswer
from src.application.reception_agent import ReceptionDecision
from src.application.response_classifier import ReplyClassification
from src.domain.entities import Channel
from src.infrastructure.repositories.conversation_state_repo import ConversationState

from tests.conversation_evals.schemas import (
    CapturedConversationShot,
    EvalExpected,
    EvalRunResult,
    EvalState,
    ShotEvalCase,
)


class ConversationEvalRunner:
    def run_shot(
        self,
        shot: CapturedConversationShot,
        expected: EvalExpected | None = None,
        metadata: dict | None = None,
    ) -> EvalRunResult:
        return self.run_case(self.case_from_shot(shot, expected=expected, metadata=metadata))

    @staticmethod
    def case_from_shot(
        shot: CapturedConversationShot,
        expected: EvalExpected | None = None,
        metadata: dict | None = None,
    ) -> ShotEvalCase:
        meta = metadata or {}
        return ShotEvalCase(
            case_id=shot.shot_id or str(meta.get("shot_id") or "captured_shot"),
            channel=str(meta.get("chanel") or meta.get("channel") or shot.source.channel),
            user_id=str(meta.get("id_user") or meta.get("user_id") or shot.source.user_id),
            user_name=str(meta.get("user_name") or shot.source.user_name),
            initial_state=EvalState(
                flow=shot.state_before.flow,
                node=shot.state_before.node,
                last_question=shot.state_before.last_question,
                awaiting_reply=shot.state_before.awaiting_reply,
                conversation_history=shot.history,
            ),
            user_message=shot.turn.user_message,
            expected=expected or EvalExpected(),
            tags=[str(tag) for tag in shot.review.get("tags", [])] if isinstance(shot.review, dict) else [],
        )

    def run_case(self, case: ShotEvalCase) -> EvalRunResult:
        runner = FlowGraphRunner()
        stored = self._stored_state(case)
        set_mock = MagicMock()
        clear_mock = MagicMock()
        report_mock = MagicMock(return_value=(True, {}))
        block_repo = MagicMock()
        tool_calls: list[str] = []

        patches = [
            patch("src.application.flow_graph.ConversationStateRepo.get", return_value=stored),
            patch("src.application.flow_graph.ConversationStateRepo.set", set_mock),
            patch("src.application.flow_graph.ConversationStateRepo.clear", clear_mock),
            patch("src.application.flow_graph.ReportRepository.create_report", report_mock),
            patch("src.application.flow_graph.PostgresUserRepo", return_value=block_repo),
            patch("src.application.flow_graph.ToolCallLogger.record", side_effect=self._record_tool_call(tool_calls)),
            patch("src.application.reception_agent.ToolCallLogger.success"),
            patch("src.application.response_classifier.ToolCallLogger.success"),
            patch("src.application.response_classifier.ToolCallLogger.error"),
            patch("src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event"),
        ]

        if case.mocked_tools.rag_answer:
            rag = case.mocked_tools.rag_answer
            patches.append(
                patch.object(
                    runner.rag,
                    "answer_question",
                    return_value=RagAnswer(rag.has_answer, rag.answer, rag.sources),
                )
            )
        if case.mocked_tools.reply_classification:
            classification = case.mocked_tools.reply_classification
            patches.append(
                patch.object(
                    runner.classifier,
                    "classify_reply",
                    return_value=ReplyClassification(
                        classification.intent,
                        classification.value,
                        classification.has_off_flow_question,
                        classification.off_flow_question,
                    ),
                )
            )
        if case.mocked_tools.reception_decision:
            decision = case.mocked_tools.reception_decision
            patches.append(
                patch.object(
                    runner.reception,
                    "decide",
                    return_value=ReceptionDecision(
                        action=decision.action,
                        flow=decision.flow,
                        has_question=decision.has_question,
                        question=decision.question,
                        answer_source=decision.answer_source,
                        answer=decision.answer,
                        clarifying_question=decision.clarifying_question,
                        handoff_reason=decision.handoff_reason,
                        confidence=decision.confidence,
                    ),
                )
            )

        with self._patches(patches):
            result = runner.run(Channel(case.channel), case.user_id, case.user_message, case.user_name)

        final_state = set_mock.call_args.args[2] if set_mock.call_args else None
        final_flow = final_state.flow if final_state else ""
        final_node = final_state.node if final_state else ""
        final_last_question = final_state.last_question if final_state else ""

        if report_mock.called:
            tool_calls.append("report.create")
        if block_repo.block_user.called:
            tool_calls.append("user.block")
        if set_mock.called:
            tool_calls.append("state.set")
        if clear_mock.called:
            tool_calls.append("state.clear")

        return EvalRunResult(
            case_id=case.case_id,
            replies=result.replies,
            legacy_state=result.legacy_state.value,
            final_flow=final_flow,
            final_node=final_node,
            final_last_question=final_last_question,
            cleared_state=clear_mock.called,
            tool_calls=tool_calls,
            report_created=report_mock.called,
            blocked_user=block_repo.block_user.called,
        )

    @staticmethod
    def assert_expected(case: ShotEvalCase, result: EvalRunResult):
        expected = case.expected
        if expected.legacy_state:
            assert result.legacy_state == expected.legacy_state, (
                f"{case.case_id}: legacy_state expected {expected.legacy_state}, got {result.legacy_state}"
            )
        if expected.next_flow:
            assert result.final_flow == expected.next_flow, (
                f"{case.case_id}: next_flow expected {expected.next_flow}, got {result.final_flow}"
            )
        if expected.next_node:
            assert result.final_node == expected.next_node, (
                f"{case.case_id}: next_node expected {expected.next_node}, got {result.final_node}"
            )
        if expected.must_not_advance_state:
            assert result.final_flow in {"", case.initial_state.flow}, (
                f"{case.case_id}: state advanced from {case.initial_state.flow} to {result.final_flow}"
            )
            assert result.final_node in {"", case.initial_state.node}, (
                f"{case.case_id}: node advanced from {case.initial_state.node} to {result.final_node}"
            )
        for tool_name in expected.must_call_tools:
            assert tool_name in result.tool_calls, f"{case.case_id}: expected tool call {tool_name}"
        for tool_name in expected.must_not_call_tools:
            assert tool_name not in result.tool_calls, f"{case.case_id}: unexpected tool call {tool_name}"
        if expected.must_not_handoff:
            assert not result.report_created, f"{case.case_id}: unexpected report/handoff"
            assert not result.blocked_user, f"{case.case_id}: unexpected user block"
        joined = "\n".join(result.replies)
        for needle in expected.required_reply_substrings:
            assert needle in joined, f"{case.case_id}: missing reply text {needle!r}"
        for needle in expected.forbidden_reply_substrings:
            assert needle not in joined, f"{case.case_id}: forbidden reply text {needle!r}"
        if expected.must_resume_pending_question:
            needle = expected.resume_contains or case.initial_state.last_question
            assert needle and (needle in joined or needle in result.final_last_question), (
                f"{case.case_id}: did not resume pending question {needle!r}"
            )

    @staticmethod
    def _stored_state(case: ShotEvalCase) -> ConversationState:
        state = case.initial_state
        return ConversationState(
            flow=state.flow,
            node=state.node,
            last_question=state.last_question,
            awaiting_reply=state.awaiting_reply,
            user_name=case.user_name,
            conversation_history=state.conversation_history,
        )

    @staticmethod
    def _record_tool_call(tool_calls: list[str]):
        def wrapper(*args, **kwargs):
            tool_name = kwargs.get("tool_name", "")
            if tool_name:
                tool_calls.append(tool_name)
            call = kwargs.get("call")
            return call() if call else None

        return wrapper

    @staticmethod
    def _patches(patches: Iterable):
        class PatchStack:
            def __enter__(self_inner):
                self_inner.started = [p.start() for p in patches]
                return self_inner.started

            def __exit__(self_inner, exc_type, exc, tb):
                for p in reversed(list(patches)):
                    p.stop()
                return False

        return PatchStack()
