"""Provisional ports matching two-person-workflow.md; Dev B owns canonical domain types.

DECISION: keep these in the agent layer until Dev B supplies domain models. Adapters
must resolve source metadata and canonical memory state on the service side.
"""

from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cybotech.agents.registry import AgentId, AgentIdentity, Handoff, Operation

MemoryState = Literal["trusted", "quarantined", "rejected", "pending", "expired", "revoked"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Document(Record):
    id: str
    content: str = Field(max_length=16000)
    source_type: Literal["email", "document", "synthetic", "internal"]
    trust_level: Literal["untrusted", "internal", "verified"] = "untrusted"


class MemoryProposal(Record):
    content: str
    source_document_id: str
    proposed_by: str
    proposed_at: datetime = Field(default_factory=utc_now)
    claimed_category: str
    raw_context: str


class MemoryDecision(Record):
    proposal_id: str
    status: MemoryState
    reason: str
    decided_at: datetime = Field(default_factory=utc_now)


class RetrievalQuery(Record):
    query_text: str
    session_id: str
    max_results: int = 5
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    principal: AgentId = AgentId.PRIMARY


class MemoryRecord(Record):
    id: str
    content: str
    trust_label: str
    source_type: str
    retrieved_at: datetime = Field(default_factory=utc_now)


class DocumentEvidence(Record):
    """Retrieved document text is untrusted evidence, never authorization."""

    chunk_id: str
    document_id: str
    content: str
    title: str
    source_type: str
    content_hash: str
    trust_label: Literal["untrusted"] = "untrusted"


class ActionProposal(Record):
    tool_name: str
    arguments: dict[str, Any]
    proposed_by: str
    session_id: str
    task_id: str
    user_task_description: str
    retrieved_memory_ids: list[str]
    approval_id: str | None = None
    proposed_at: datetime = Field(default_factory=utc_now)


class MemoryReviewRequest(Record):
    handoff: Handoff
    proposal: MemoryProposal

    @model_validator(mode="after")
    def validate_request(self) -> "MemoryReviewRequest":
        if (
            self.handoff.destination != AgentId.MEMORY_GUARD
            or self.handoff.operation != Operation.REVIEW_MEMORY
            or self.proposal.source_document_id not in self.handoff.source_refs
            or self.proposal.proposed_by != AgentId.PRIMARY
        ):
            raise ValueError("invalid memory review handoff")
        return self


class ActionReviewRequest(Record):
    handoff: Handoff
    proposal: ActionProposal
    evidence: list[DocumentEvidence] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_request(self) -> "ActionReviewRequest":
        if (
            self.handoff.destination != AgentId.SENTINEL
            or self.handoff.operation != Operation.REVIEW_ACTION
            or self.handoff.task_id != self.proposal.task_id
            or self.proposal.proposed_by != AgentId.PRIMARY
        ):
            raise ValueError("invalid action review handoff")
        return self


class GatewayDecision(Record):
    authorized: bool
    reason: str
    failed_check: str | None = None
    approval_id: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)


class ToolResult(Record):
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    executed_at: datetime = Field(default_factory=utc_now)


class ModelMemory(Record):
    content: str = Field(max_length=2000)
    source_document_id: str
    claimed_category: str


class ModelAction(Record):
    tool_name: Literal["read_email", "read_document", "search_customers", "send_report"]
    # A JSON string keeps the provider schema strict without an open-ended JSON object.
    arguments_json: str


class AgentTurn(Record):
    reply: str = Field(max_length=8000)
    memories: list[ModelMemory] = Field(max_length=5)
    actions: list[ModelAction] = Field(max_length=5)
    done: bool


class ModelContext(Record):
    user_task: str
    documents: list[Document]
    memories: list[MemoryRecord]
    retrieved_documents: list[DocumentEvidence] = Field(default_factory=list)
    observations: list[str]
    agent_id: Literal[AgentId.PRIMARY] = AgentId.PRIMARY
    agent_identity: AgentIdentity | None = None


class ModelProvider(Protocol):
    name: str

    def generate(self, context: ModelContext) -> AgentTurn: ...


class MemoryPort(Protocol):
    def propose_memory(self, proposal: MemoryProposal) -> MemoryDecision: ...


class RetrievalPort(Protocol):
    def retrieve(self, query: RetrievalQuery) -> list[MemoryRecord]: ...


class DocumentRetrievalPort(Protocol):
    def retrieve(self, query: str, session_id: str) -> list[DocumentEvidence]: ...


class SentinelPort(Protocol):
    # DECISION: Dev B owns the eventual finding schema; never treat this as authorization.
    def review(self, proposal: ActionProposal) -> dict[str, Any]: ...


class GatewayPort(Protocol):
    def authorize(self, proposal: ActionProposal) -> GatewayDecision: ...


class ToolPort(Protocol):
    def execute(self, arguments: dict[str, Any]) -> ToolResult: ...


class DispatcherPort(Protocol):
    def execute(self, proposal: ActionProposal) -> ToolResult: ...


class EventPort(Protocol):
    # Dev B's adapter attaches persistent IDs, timestamp, run ID and sequence number.
    def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...
