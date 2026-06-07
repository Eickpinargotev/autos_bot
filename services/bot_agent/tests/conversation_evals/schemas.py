from typing import Any

from pydantic import BaseModel, Field


class EvalState(BaseModel):
    flow: str = "INICIO"
    node: str = ""
    last_question: str = ""
    awaiting_reply: bool = False
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class RagAnswerMock(BaseModel):
    has_answer: bool = False
    answer: str = ""
    sources: list[Any] = Field(default_factory=list)


class ReplyClassificationMock(BaseModel):
    intent: str
    value: str = ""
    has_off_flow_question: bool = False
    off_flow_question: str = ""


class ReceptionDecisionMock(BaseModel):
    action: str
    flow: str = ""
    has_question: bool = False
    question: str = ""
    answer_source: str = "none"
    answer: str = ""
    clarifying_question: str = ""
    handoff_reason: str = ""
    confidence: float = 0.0


class MockedTools(BaseModel):
    rag_answer: RagAnswerMock | None = None
    reply_classification: ReplyClassificationMock | None = None
    reception_decision: ReceptionDecisionMock | None = None


class EvalExpected(BaseModel):
    turn_type: str = ""
    legacy_state: str = ""
    next_flow: str = ""
    next_node: str = ""
    must_call_tools: list[str] = Field(default_factory=list)
    must_not_call_tools: list[str] = Field(default_factory=list)
    must_not_advance_state: bool = False
    must_resume_pending_question: bool = False
    must_not_handoff: bool = False
    required_reply_substrings: list[str] = Field(default_factory=list)
    forbidden_reply_substrings: list[str] = Field(default_factory=list)
    resume_contains: str = ""


class JudgeSpec(BaseModel):
    enabled: bool = False
    must_answer_user_question: bool = False
    must_be_grounded: bool = False
    must_resume_naturally: bool = False


class ShotEvalCase(BaseModel):
    case_id: str
    channel: str = "whatsapp"
    user_id: str = "eval-user"
    user_name: str = "Eval"
    initial_state: EvalState = Field(default_factory=EvalState)
    user_message: str
    mocked_tools: MockedTools = Field(default_factory=MockedTools)
    expected: EvalExpected
    judge: JudgeSpec = Field(default_factory=JudgeSpec)
    tags: list[str] = Field(default_factory=list)


class EvalRunResult(BaseModel):
    case_id: str
    replies: list[str]
    legacy_state: str
    final_flow: str = ""
    final_node: str = ""
    final_last_question: str = ""
    cleared_state: bool = False
    tool_calls: list[str] = Field(default_factory=list)
    report_created: bool = False
    blocked_user: bool = False


class CapturedShotState(BaseModel):
    flow: str = "INICIO"
    node: str = ""
    last_question: str = ""
    awaiting_reply: bool = False


class CapturedShotTurn(BaseModel):
    user_message: str = ""
    bot_replies: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class CapturedShotSource(BaseModel):
    channel: str = "whatsapp"
    user_id: str = "eval-user"
    user_name: str = "Eval"


class CapturedConversationShot(BaseModel):
    shot_id: str = ""
    source: CapturedShotSource = Field(default_factory=CapturedShotSource)
    state_before: CapturedShotState = Field(default_factory=CapturedShotState)
    history: list[dict[str, Any]] = Field(default_factory=list)
    turn: CapturedShotTurn = Field(default_factory=CapturedShotTurn)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    state_after: CapturedShotState = Field(default_factory=CapturedShotState)
    review: dict[str, Any] = Field(default_factory=dict)
