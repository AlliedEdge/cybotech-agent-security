"""Genuine supervisor orchestration for the Cybotech multi-agent workflow.

``SupervisorGraph`` is NOT a subclass of ``PrimaryAssistantGraph``. It owns a
distinct compiled LangGraph that coordinates bounded, typed delegations to
specialist agents:

    intake -> primary (reason + retrieve) -> memory review -> memory enforcement
                                          -> sentinel review -> gateway -> sandbox
                                          -> finalize

The supervisor establishes a correlation id, routes by need (trivial greetings
skip retrieval), passes each agent only the context its operation requires,
collects and validates specialist outputs, and resolves any conflicting
recommendation by explicit deterministic policy. All permission checks,
execution limits and security-critical decisions are enforced by deterministic
code. The supervisor may use an LLM only for primary reasoning; it does not
authorize or execute. The deterministic Policy Enforcement Gateway remains the
sole final authorization boundary and the memory lifecycle service the sole
trusted-memory writer.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cybotech.agents.assistant import TaskResult
from cybotech.agents.contracts import (
    ActionProposal,
    ActionReviewRequest,
    DispatcherPort,
    Document,
    DocumentEvidence,
    DocumentRetrievalPort,
    EventPort,
    GatewayDecision,
    GatewayPort,
    MemoryPort,
    MemoryProposal,
    MemoryReviewRequest,
    ModelProvider,
    RetrievalPort,
    SentinelPort,
)
from cybotech.agents.graph import MemoryReviewAgent
from cybotech.agents.registry import AgentId, Handoff, Operation
from cybotech.agents.specialists import MemoryGuardAgent, PrimaryAssistant, SentinelAgent

_GREETING = (
    "^(hi|hello|hey|thanks|thank you|thankyou|ok|okay|sure|good "
    "morning|good afternoon|good evening|yo)\\b[!.\\s]*$"
)

SourceContext = dict[str, Any]
SourceResolver = Callable[[str, str], SourceContext | None]


class SupervisorState(TypedDict, total=False):
    task: str
    session_id: str
    task_id: str
    documents: list[Document]
    approval_id: str | None
    sources: dict[str, Document]
    memories: list[Any]
    retrieved_documents: list[DocumentEvidence]
    observations: list[str]
    results: list[Any]
    reply: str
    step: int
    turn: Any
    action_index: int
    action: ActionProposal
    decision: GatewayDecision
    finding: Any
    review_available: bool
    status: str
    failure_type: str
    handoff_count: int
    parent_handoff_id: str | None
    deadline: float
    trivial: bool


class SupervisorGraph:
    """A bounded, coordinated multi-agent workflow. Not a primary-assistant alias."""

    def __init__(
        self,
        model: ModelProvider,
        memory: MemoryPort,
        retrieval: RetrievalPort,
        sentinel: SentinelPort,
        gateway: GatewayPort,
        dispatcher: DispatcherPort,
        events: EventPort,
        max_steps: int = 6,
        document_retrieval: DocumentRetrievalPort | None = None,
        memory_review_agent: MemoryReviewAgent | None = None,
        max_handoffs: int = 60,
        timeout_seconds: float = 120,
        source_resolver: SourceResolver | None = None,
    ) -> None:
        if (
            not 1 <= max_steps <= 20
            or not 1 <= max_handoffs <= 200
            or not 1 <= timeout_seconds <= 300
        ):
            raise ValueError("invalid workflow budgets")
        self.model = model
        self.memory = memory
        self.retrieval = retrieval
        self.document_retrieval = document_retrieval
        self.sentinel = sentinel
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.events = events
        self.max_steps = max_steps
        self.max_handoffs = max_handoffs
        self.timeout_seconds = timeout_seconds
        self.source_resolver = source_resolver
        # Specialist agents are independently compiled LangGraph subgraphs. The
        # supervisor delegates bounded, typed tasks to them; it never reaches into
        # their internals and has no access to their private tools or data.
        self.primary = PrimaryAssistant(model, retrieval, document_retrieval, events)
        self.memory_guard = (
            MemoryGuardAgent(memory_review_agent, events)
            if memory_review_agent is not None
            else None
        )
        sentinel_reviewer = cast(Any, getattr(sentinel, "reviewer", sentinel))
        self.sentinel_agent = SentinelAgent(sentinel_reviewer, events)
        self.memory_review_agent = memory_review_agent
        self.workflow = self._compile()

    def _compile(
        self,
    ) -> CompiledStateGraph[SupervisorState, None, SupervisorState, SupervisorState]:
        graph = StateGraph(SupervisorState)
        graph.add_node("intake", self._intake)
        graph.add_node("primary", self._primary)
        graph.add_node("memory_review", self._memory_review)
        graph.add_node("sentinel_review", self._sentinel_review)
        graph.add_node("policy_gateway", self._policy_gateway)
        graph.add_node("sandbox_execution", self._sandbox_execution)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "intake")
        graph.add_edge("intake", "primary")
        graph.add_conditional_edges(
            "primary",
            self._route_after_primary,
            {
                "memory_review": "memory_review",
                "sentinel_review": "sentinel_review",
                "primary": "primary",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "memory_review",
            self._route_after_memories,
            {"sentinel_review": "sentinel_review", "primary": "primary", "finalize": "finalize"},
        )
        graph.add_edge("sentinel_review", "policy_gateway")
        graph.add_conditional_edges(
            "policy_gateway",
            self._route_after_gateway,
            {
                "sandbox_execution": "sandbox_execution",
                "next_action": "sentinel_review",
                "primary": "primary",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "sandbox_execution",
            self._route_after_action,
            {"next_action": "sentinel_review", "primary": "primary", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph.compile()

    def run(
        self,
        task: str,
        session_id: str,
        task_id: str,
        documents: list[Document],
        approval_id: str | None = None,
        parent_handoff_id: str | None = None,
    ) -> TaskResult:
        """Execute a coordinated graph; dependency failures cannot reach dispatch."""
        initial: SupervisorState = {
            "task": task,
            "session_id": session_id,
            "task_id": task_id,
            "documents": documents,
            "approval_id": approval_id,
            "sources": {},
            "memories": [],
            "retrieved_documents": [],
            "observations": [],
            "results": [],
            "reply": "",
            "step": 0,
            "action_index": 0,
            "status": "running",
            "handoff_count": 0,
            "parent_handoff_id": parent_handoff_id,
            "deadline": time.monotonic() + self.timeout_seconds,
            "trivial": False,
        }
        try:
            state = self.workflow.invoke(initial)
        except Exception:
            self.events.emit("task_failed", {"error_type": "GraphFailure"})
            return TaskResult(
                status="failed", reply="Task failed safely", steps=initial["step"], tool_results=[]
            )
        return TaskResult(
            status=state.get("status", "failed"),
            reply=state.get("reply", "Task failed safely"),
            steps=state.get("step", 0),
            tool_results=state.get("results", []),
        )

    # --- nodes ---------------------------------------------------------------

    def _intake(self, state: SupervisorState) -> dict[str, Any]:
        sources = {document.id: document for document in state["documents"]}
        if len(sources) != len(state["documents"]):
            raise ValueError("duplicate document IDs")
        trivial = self._is_trivial(state["task"], state["documents"])
        self.events.emit(
            "supervisor_intake",
            {"task_id": state["task_id"], "trivial": trivial, "document_count": len(sources)},
        )
        return {"sources": sources, "trivial": trivial}

    def _primary(self, state: SupervisorState) -> dict[str, Any]:
        self._check_deadline(state)
        step = state["step"] + 1
        outcome = self.primary.run(
            state["task"],
            state["session_id"],
            state["task_id"],
            list(state["sources"].values()),
            state["deadline"],
            state["approval_id"],
            skip_retrieval=state.get("trivial", False) and step == 1,
            observations=list(state["observations"]),
        )
        turn = outcome["turn"]
        # Merge any newly retrieved document sources into the task sources.
        sources = dict(state["sources"])
        for document in list(outcome["sources"].values()):
            if document.id in sources and sources[document.id] != document:
                raise ValueError("source identity collision")
            sources[document.id] = document
        return {
            "turn": turn,
            "reply": turn.reply,
            "step": step,
            "action_index": 0,
            "sources": sources,
            "memories": outcome["memories"],
            "retrieved_documents": outcome["retrieved_documents"],
        }

    def _route_after_primary(self, state: SupervisorState) -> str:
        turn = state["turn"]
        if turn.memories:
            return "memory_review"
        if turn.actions:
            return "sentinel_review"
        return self._next_turn_or_final(state)

    def _memory_review(self, state: SupervisorState) -> dict[str, Any]:
        observations = list(state["observations"])
        handoff_count = state["handoff_count"]
        for candidate in state["turn"].memories:
            self._check_deadline(state)
            source = state["sources"].get(candidate.source_document_id)
            if source is None:
                observations.append("Memory rejected: unknown source_document_id")
                self.events.emit("memory_proposal_invalid", {"reason": "unknown_source"})
                continue
            proposal = MemoryProposal(
                content=candidate.content,
                source_document_id=source.id,
                proposed_by=AgentId.PRIMARY.value,
                claimed_category=candidate.claimed_category,
                raw_context=source.content,
            )
            handoff_count += 1
            if handoff_count > self.max_handoffs:
                raise RuntimeError("handoff budget exceeded")
            source_trust = self._resolve_source_trust(source)
            if self.memory_guard is not None:
                handoff = Handoff(
                    handoff_id=uuid.uuid4().hex,
                    task_id=state["task_id"],
                    correlation_id=state["task_id"],
                    source=AgentId.SUPERVISOR,
                    destination=AgentId.MEMORY_GUARD,
                    initiating_agent=AgentId.PRIMARY,
                    operation=Operation.REVIEW_MEMORY,
                    source_refs=(source.id,),
                    trust_label="untrusted",
                    parent_handoff_id=state.get("parent_handoff_id"),
                    input_reference=source.id,
                )
                request = MemoryReviewRequest(
                    handoff=handoff, proposal=proposal, source_trust=source_trust
                )
                start = time.monotonic()
                review = self.memory_guard.review(request, state["deadline"])
                decision_method = getattr(self.memory, "propose_with_review", None)
                if decision_method is None:
                    raise RuntimeError("memory enforcement cannot apply agent finding")
                lifecycle = decision_method(proposal, review.model_suspicious)
                self._check_deadline(state)
                self.events.emit(
                    "agent_handoff",
                    {
                        **handoff.model_dump(mode="json"),
                        "status": "completed",
                        "model": getattr(self.memory_review_agent, "model", "unspecified"),
                        "provider": review.provider,
                        "recommendation": review.decision.lower(),
                        "duration_ms": round((time.monotonic() - start) * 1000),
                    },
                )
            else:
                lifecycle = self.memory.propose_memory(proposal)
            self.events.emit("memory_decision", {"status": lifecycle.status})
            observations.append(f"Memory decision: {lifecycle.status}")
        return {"observations": observations, "handoff_count": handoff_count}

    def _route_after_memories(self, state: SupervisorState) -> str:
        if state["turn"].actions:
            return "sentinel_review"
        return self._next_turn_or_final(state)

    def _sentinel_review(self, state: SupervisorState) -> dict[str, Any]:
        self._check_deadline(state)
        if state["handoff_count"] >= self.max_handoffs:
            return {"status": "failed", "failure_type": "HandoffBudgetExceeded"}
        try:
            candidate = state["turn"].actions[state["action_index"]]
            arguments = json.loads(candidate.arguments_json)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
            action = ActionProposal(
                tool_name=candidate.tool_name,
                arguments=arguments,
                proposed_by=AgentId.PRIMARY.value,
                session_id=state["session_id"],
                task_id=state["task_id"],
                user_task_description=state["task"],
                retrieved_memory_ids=[memory.id for memory in state["memories"]],
                approval_id=state["approval_id"],
            )
        except Exception as error:
            return {"status": "failed", "failure_type": type(error).__name__}
        action_id = self._action_id(action)
        self.events.emit(
            "action_proposed",
            {
                "action_id": action_id,
                "task_id": state["task_id"],
                "tool_name": action.tool_name,
                "arguments": action.arguments,
                "action": action.model_dump(mode="json", exclude={"proposed_at"}),
            },
        )
        # Bounded untrusted evidence only: the reviewer sees document content
        # snippets and retrieved chunks, never the full conversation or all memory.
        direct_evidence = [
            DocumentEvidence(
                chunk_id=f"source:{document.id}",
                document_id=document.id,
                content=document.content[:2000],
                title=document.id,
                source_type=document.source_type,
                content_hash=hashlib.sha256(document.content[:2000].encode()).hexdigest(),
            )
            for document in list(state["sources"].values())[:5]
        ]
        evidence = [*direct_evidence, *state["retrieved_documents"]][:10]
        handoff = Handoff(
            handoff_id=uuid.uuid4().hex,
            task_id=state["task_id"],
            correlation_id=state["task_id"],
            source=AgentId.SUPERVISOR,
            destination=AgentId.SENTINEL,
            initiating_agent=AgentId.PRIMARY,
            operation=Operation.REVIEW_ACTION,
            source_refs=tuple(
                [memory.id for memory in state["memories"]] + [doc.chunk_id for doc in evidence]
            ),
            trust_label="mixed",
            parent_handoff_id=state.get("parent_handoff_id"),
        )
        request = ActionReviewRequest(handoff=handoff, proposal=action, evidence=evidence)
        start = time.monotonic()
        output = self.sentinel_agent.review(request, state["deadline"])
        self._check_deadline(state)
        review_available = output.result.available
        self.events.emit(
            "agent_handoff",
            {
                **handoff.model_dump(mode="json"),
                "status": "completed" if review_available else "failed",
                "recommendation": output.result.security_decision.lower(),
                "provider": output.result.provider,
                "duration_ms": round((time.monotonic() - start) * 1000),
            },
        )
        self.events.emit(
            "sentinel_finding",
            {
                "action_id": action_id,
                "finding": {
                    "status": output.finding.status,
                    "risk": output.finding.risk,
                    "disposition": output.finding.disposition,
                    "provider": output.result.provider,
                    "evidence_refs": list(output.finding.evidence_refs),
                },
            },
        )
        return {
            "action": action,
            "review_available": review_available,
            "finding": output.finding,
            "handoff_count": state["handoff_count"] + 1,
        }

    def _policy_gateway(self, state: SupervisorState) -> dict[str, Any]:
        self._check_deadline(state)
        if state.get("status") == "failed":
            return {}
        action = state["action"]
        finding = state["finding"] if state["review_available"] else None
        decision = self.gateway.authorize(action, sentinel_finding=finding)
        if not state["review_available"]:
            decision = GatewayDecision(
                authorized=False,
                reason="Required reviewer unavailable",
                failed_check="review_unavailable",
            )
        self.events.emit(
            "gateway_authorized" if decision.authorized else "gateway_denied",
            {
                "action_id": self._action_id(action),
                "task_id": state["task_id"],
                "tool_name": action.tool_name,
                "approved": decision.authorized,
                "approval_id": decision.approval_id,
                "reason": decision.reason,
                "failed_check": decision.failed_check,
            },
        )
        if decision.authorized:
            return {"decision": decision}
        return {
            "decision": decision,
            "observations": [
                *state["observations"],
                f"Action denied: {decision.failed_check}",
            ],
            "action_index": state["action_index"] + 1,
        }

    def _route_after_gateway(self, state: SupervisorState) -> str:
        if state.get("status") == "failed":
            return "finalize"
        if state["decision"].authorized:
            return "sandbox_execution"
        return self._next_action_or_turn(state)

    def _sandbox_execution(self, state: SupervisorState) -> dict[str, Any]:
        self._check_deadline(state)
        result = self.dispatcher.execute(state["action"])
        results = [*state["results"], result]
        observations = [*state["observations"], result.model_dump_json()]
        self.events.emit(
            "tool_execution_completed",
            {
                "tool_name": result.tool_name,
                "action_id": self._action_id(state["action"]),
                "task_id": state["task_id"],
                "success": result.success,
                "argument_keys": sorted(result.arguments),
                "error_present": result.error is not None,
            },
        )
        sources = dict(state["sources"])
        if (
            result.success
            and result.result
            and state["action"].tool_name in {"read_email", "read_document"}
        ):
            source = Document.model_validate(result.result["document"])
            if source.id in sources and source != sources[source.id]:
                raise ValueError("source identity collision")
            sources[source.id] = source
        return {
            "results": results,
            "observations": observations,
            "sources": sources,
            "action_index": state["action_index"] + 1,
        }

    def _route_after_action(self, state: SupervisorState) -> str:
        return self._next_action_or_turn(state)

    # --- routing helpers -----------------------------------------------------

    def _next_action_or_turn(self, state: SupervisorState) -> str:
        if state["action_index"] < len(state["turn"].actions):
            return "next_action"
        return self._next_turn_or_final(state)

    def _next_turn_or_final(self, state: SupervisorState) -> str:
        if state["turn"].done:
            return "finalize"
        if state["step"] >= self.max_steps:
            return "finalize"
        return "primary"

    @staticmethod
    def _check_deadline(state: SupervisorState) -> None:
        if time.monotonic() >= state["deadline"]:
            raise TimeoutError("workflow deadline exceeded")

    @staticmethod
    def _is_trivial(task: str, documents: list[Document]) -> bool:
        import re

        text = task.strip().lower()
        if documents or "?" in text or len(text) > 40:
            return False
        return bool(re.match(_GREETING, text))

    def _resolve_source_trust(
        self, source: Document
    ) -> Literal["untrusted", "internal", "verified"]:
        if self.source_resolver is None:
            return "untrusted"
        try:
            context = self.source_resolver(source.id, source.content)
        except Exception:
            return "untrusted"
        if not isinstance(context, dict):
            return "untrusted"
        value = context.get("trust_level", "untrusted")
        return value if value in {"untrusted", "internal", "verified"} else "untrusted"

    @staticmethod
    def _action_id(action: ActionProposal) -> str:
        from cybotech.services.policy_gateway.service import PolicyGateway

        return PolicyGateway.action_hash(action)

    def _finalize(self, state: SupervisorState) -> dict[str, Any]:
        status = state.get("status", "completed")
        if status == "running":
            status = (
                "step_limit"
                if not state["turn"].done and state["step"] >= self.max_steps
                else "completed"
            )
        if status == "failed":
            self.events.emit(
                "task_failed", {"error_type": state.get("failure_type", "GraphFailure")}
            )
        return {"status": status}
