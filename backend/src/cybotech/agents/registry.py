"""Application-owned identities and least-privilege handoff validation.

These identities are internal capabilities, not authenticated end-user identities.
No client request or retrieved document can register a new role.
"""

from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentId(StrEnum):
    SUPERVISOR = "supervisor"
    PRIMARY = "primary_assistant"
    MEMORY_GUARD = "memory_guard"
    SENTINEL = "sentinel"
    VIBE_HACKER = "vibe_hacker"


class Operation(StrEnum):
    REASON = "reason"
    RETRIEVE_DOCUMENTS = "retrieve_documents"
    RETRIEVE_MEMORY = "retrieve_memory"
    PROPOSE_MEMORY = "propose_memory"
    PROPOSE_ACTION = "propose_action"
    REVIEW_MEMORY = "review_memory"
    REVIEW_ACTION = "review_action"
    GENERATE_SYNTHETIC = "generate_synthetic"
    RUN_ISOLATED_EXPERIMENT = "run_isolated_experiment"
    COORDINATE = "coordinate"


PERMISSIONS = MappingProxyType(
    {
        AgentId.SUPERVISOR: frozenset({Operation.COORDINATE}),
        AgentId.PRIMARY: frozenset(
            {
                Operation.REASON,
                Operation.RETRIEVE_DOCUMENTS,
                Operation.RETRIEVE_MEMORY,
                Operation.PROPOSE_MEMORY,
                Operation.PROPOSE_ACTION,
            }
        ),
        AgentId.MEMORY_GUARD: frozenset({Operation.REVIEW_MEMORY}),
        AgentId.SENTINEL: frozenset({Operation.REVIEW_ACTION}),
        AgentId.VIBE_HACKER: frozenset(
            {Operation.GENERATE_SYNTHETIC, Operation.RUN_ISOLATED_EXPERIMENT}
        ),
    }
)


def require_permission(agent: str | AgentId, operation: Operation) -> AgentId:
    try:
        identity = AgentId(agent)
    except ValueError:
        raise PermissionError("unknown agent") from None
    if operation not in PERMISSIONS[identity]:
        raise PermissionError("agent operation not permitted")
    return identity


class AgentIdentity(BaseModel):
    """Metadata attached by trusted orchestration; permissions are never input fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: AgentId
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=120)
    parent_agent: AgentId | None = None

    @property
    def permissions(self) -> frozenset[Operation]:
        return PERMISSIONS[self.agent_id]

    @property
    def retrieval_scope(self) -> str:
        return (
            "session_documents_and_trusted_memory" if self.agent_id == AgentId.PRIMARY else "none"
        )


class Handoff(BaseModel):
    """Metadata only; source content stays within the receiving agent's typed request."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    handoff_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    source: AgentId
    destination: AgentId
    operation: Operation
    correlation_id: str = Field(min_length=1, max_length=120)
    initiating_agent: AgentId | None = None
    parent_handoff_id: str | None = Field(default=None, max_length=120)
    source_refs: tuple[str, ...] = Field(default=(), max_length=50)
    trust_label: str = Field(default="untrusted", pattern="^(untrusted|trusted|mixed)$")
    status: str = Field(default="requested", pattern="^(requested|completed|failed)$")

    @model_validator(mode="after")
    def validate_route(self) -> "Handoff":
        if self.source == self.destination:
            raise ValueError("circular handoff is not allowed")
        try:
            if self.source == AgentId.VIBE_HACKER and self.destination == AgentId.SUPERVISOR:
                require_permission(self.source, Operation.RUN_ISOLATED_EXPERIMENT)
                if self.operation != Operation.COORDINATE:
                    raise ValueError("invalid experiment handoff")
            elif self.source == AgentId.SUPERVISOR:
                require_permission(self.source, Operation.COORDINATE)
            else:
                raise ValueError("handoff source is not permitted")
            require_permission(self.destination, self.operation)
        except PermissionError as error:
            raise ValueError(str(error)) from error
        return self
