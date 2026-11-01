"""LangGraph orchestration for the registered Primary Assistant.

Security services retain their own authority: this graph only sequences their
existing ports. In particular, the sole edge into sandbox execution originates
at the deterministic gateway's successful decision.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cybotech.agents.assistant import TaskResult
from cybotech.agents.contracts import (
    ActionProposal,
    ActionReviewRequest,
    AgentTurn,
    DispatcherPort,
    Document,
    DocumentEvidence,
    DocumentRetrievalPort,
    EventPort,
    GatewayDecision,
    GatewayPort,
    MemoryPort,
    MemoryProposal,
    MemoryRecord,
    MemoryReviewRequest,
    ModelContext,
    ModelProvider,
    RetrievalPort,
    RetrievalQuery,
    SentinelPort,
    ToolResult,
)
from cybotech.agents.registry import AgentId, AgentIdentity, Handoff, Operation


class PrimaryAssistantState(TypedDict, total=False):
    task: str
    session_id: str
    task_id: str
    documents: list[Document]
    sources: dict[str, Document]
    approval_id: str | None
    memories: list[MemoryRecord]
    retrieved_documents: list[DocumentEvidence]
    observations: list[str]
    results: list[ToolResult]
    reply: str
    step: int
    turn: AgentTurn
    action_index: int
    action: ActionProposal
    decision: GatewayDecision
    review_available: bool
    status: str
    failure_type: str
    memory_findings: list[bool]
    handoff_count: int
    parent_handoff_id: str | None
    deadline: float


class MemoryReviewAgent(Protocol):
    def is_suspicious(self, proposal: MemoryProposal) -> bool: ...


class PrimaryAssistantGraph:
    """A bounded, typed StateGraph that owns assistant orchestration."""

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
        self.document_retrieval = document_retrieval or _NoDocuments()
        self.sentinel = sentinel
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.events = events
        self.max_steps = max_steps
        self.max_handoffs = max_handoffs
        self.timeout_seconds = timeout_seconds
        self.memory_review_agent = memory_review_agent
        self.workflow = self._compile()

    def _compile(
        self,
    ) -> CompiledStateGraph[
        PrimaryAssistantState, None, PrimaryAssistantState, PrimaryAssistantState
    ]:
        graph = StateGraph(PrimaryAssistantState)
        graph.add_node("prepare_request", self._prepare_request)
        graph.add_node("retrieve_trusted_memory", self._retrieve_trusted_memory)
        graph.add_node("retrieve_documents", self._retrieve_documents)
        graph.add_node("reason", self._reason)
        graph.add_node("memory_agent_review", self._memory_agent_review)
        graph.add_node("memory_guard", self._memory_guard)
        graph.add_node("sentinel_review", self._sentinel_review)
        graph.add_node("policy_gateway", self._policy_gateway)
        graph.add_node("sandbox_execution", self._sandbox_execution)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "prepare_request")
        graph.add_edge("prepare_request", "retrieve_documents")
        graph.add_edge("retrieve_documents", "retrieve_trusted_memory")
        graph.add_edge("retrieve_trusted_memory", "reason")
        graph.add_conditional_edges(
            "reason",
            self._route_after_reason,
            {
                "memory_guard": "memory_agent_review",
                "sentinel_review": "sentinel_review",
                "reason": "reason",
                "finalize": "finalize",
            },
        )
        graph.add_edge("memory_agent_review", "memory_guard")
        graph.add_conditional_edges(
            "memory_guard",
            self._route_after_memories,
            {"sentinel_review": "sentinel_review", "reason": "reason", "finalize": "finalize"},
        )
        graph.add_edge("sentinel_review", "policy_gateway")
        graph.add_conditional_edges(
            "policy_gateway",
            self._route_after_gateway,
            {
                "sandbox_execution": "sandbox_execution",
                "next_action": "sentinel_review",
                "reason": "reason",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "sandbox_execution",
            self._route_after_action,
            {"next_action": "sentinel_review", "reason": "reason", "finalize": "finalize"},
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
        """Execute a bounded graph; dependency failures cannot reach dispatch."""
        initial: PrimaryAssistantState = {
            "task": task,
            "session_id": session_id,
            "task_id": task_id,
            "documents": documents,
            "approval_id": approval_id,
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
        }
        try:
            state = self.workflow.invoke(initial)
        except Exception as error:
            self.events.emit("task_failed", {"error_type": type(error).__name__})
            return TaskResult(
                status="failed", reply="Task failed safely", steps=initial["step"], tool_results=[]
            )
        return TaskResult(
            status=state.get("status", "failed"),
            reply=state.get("reply", "Task failed safely"),
            steps=state.get("step", 0),
            tool_results=state.get("results", []),
        )

    def _prepare_request(self, state: PrimaryAssistantState) -> dict[str, Any]:
        sources = {document.id: document for document in state["documents"]}
        if len(sources) != len(state["documents"]):
            raise ValueError("duplicate document IDs")
        return {"sources": sources}

    def _retrieve_trusted_memory(self, state: PrimaryAssistantState) -> dict[str, Any]:
        memories = self.retrieval.retrieve(
            RetrievalQuery(query_text=state["task"], session_id=state["session_id"])
        )
        self.events.emit("retrieval_completed", {"count": len(memories)})
        return {"memories": memories}

    def _retrieve_documents(self, state: PrimaryAssistantState) -> dict[str, Any]:
        documents = self.document_retrieval.retrieve(state["task"], state["session_id"])
        self.events.emit("document_retrieval_completed", {"count": len(documents)})
        sources = dict(state["sources"])
        for evidence in documents:
            source = Document(
                id=evidence.chunk_id,
                content=evidence.content,
                source_type="document",
                trust_level="untrusted",
            )
            if source.id in sources and sources[source.id] != source:
                raise ValueError("source identity collision")
            sources[source.id] = source
        return {"retrieved_documents": documents, "sources": sources}

    def _reason(self, state: PrimaryAssistantState) -> dict[str, Any]:
        self._check_deadline(state)
        step = state["step"] + 1
        started = time.monotonic()
        provider, separator, model = self.model.name.partition(":")
        identity = AgentIdentity(
            agent_id=AgentId.PRIMARY,
            provider=provider[:200],
            model=(model if separator else provider)[:200],
            task_id=state["task_id"],
            parent_agent=AgentId.SUPERVISOR,
        )
        try:
            turn = self.model.generate(
                ModelContext(
                    user_task=state["task"],
                    documents=list(state["sources"].values()),
                    memories=state["memories"],
                    retrieved_documents=state["retrieved_documents"],
                    observations=state["observations"],
                    agent_identity=identity,
                )
            )
            self._check_deadline(state)
        except Exception as error:
            self.events.emit(
                "agent_invocation",
                {
                    "agent_id": AgentId.PRIMARY.value,
                    "provider": self.model.name,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise
        self.events.emit(
            "agent_invocation",
            {
                "agent_id": AgentId.PRIMARY.value,
                "provider": self.model.name,
                "status": "completed",
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return {"turn": turn, "reply": turn.reply, "step": step, "action_index": 0}

    def _route_after_reason(self, state: PrimaryAssistantState) -> str:
        turn = state["turn"]
        if turn.memories:
            return "memory_guard"
        if turn.actions:
            return "sentinel_review"
        return self._next_turn_or_final(state)

    def _memory_guard(self, state: PrimaryAssistantState) -> dict[str, Any]:
        observations = list(state["observations"])
        for index, candidate in enumerate(state["turn"].memories):
            self._check_deadline(state)
            source = state["sources"].get(candidate.source_document_id)
            if source is None:
                observations.append("Memory rejected: unknown source_document_id")
                self.events.emit("memory_proposal_invalid", {"reason": "unknown_source"})
                continue
            proposal = MemoryProposal(
                content=candidate.content,
                source_document_id=source.id,
                proposed_by="primary_assistant",
                claimed_category=candidate.claimed_category,
                raw_context=source.content,
            )
            if self.memory_review_agent is not None:
                review_method = getattr(self.memory, "propose_with_review", None)
                if review_method is None:
                    raise RuntimeError("memory enforcement cannot apply agent finding")
                decision = review_method(proposal, state["memory_findings"][index])
            else:
                decision = self.memory.propose_memory(proposal)
            self.events.emit("memory_decision", {"status": decision.status})
            observations.append(f"Memory decision: {decision.status}")
        return {"observations": observations}

    def _memory_agent_review(self, state: PrimaryAssistantState) -> dict[str, Any]:
        findings: list[bool] = []
        count = state["handoff_count"]
        for candidate in state["turn"].memories:
            self._check_deadline(state)
            source = state["sources"].get(candidate.source_document_id)
            if self.memory_review_agent is None or source is None:
                findings.append(False)
                continue
            count += 1
            if count > self.max_handoffs:
                raise RuntimeError("handoff budget exceeded")
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
            )
            proposal = MemoryProposal(
                content=candidate.content,
                source_document_id=source.id,
                proposed_by=AgentId.PRIMARY.value,
                claimed_category=candidate.claimed_category,
                raw_context=source.content,
            )
            request = MemoryReviewRequest(handoff=handoff, proposal=proposal)
            start = time.monotonic()
            status = "completed"
            error_type: str | None = None
            try:
                analyze = getattr(self.memory_review_agent, "analyze", None)
                if analyze:
                    from cybotech.services.memory_guard.model import MemoryFinding

                    finding = MemoryFinding.model_validate(analyze(request.proposal))
                    suspicious = finding.suspicious
                else:
                    suspicious = self.memory_review_agent.is_suspicious(request.proposal)
                if not isinstance(suspicious, bool):
                    raise ValueError("invalid memory review")
            except Exception as error:
                status, suspicious = "failed", True
                error_type = type(error).__name__
            self._check_deadline(state)
            self.events.emit(
                "agent_handoff",
                {
                    **handoff.model_dump(mode="json"),
                    "status": status,
                    "model": getattr(self.memory_review_agent, "model", "unspecified"),
                    "provider": getattr(
                        self.memory_review_agent,
                        "provider",
                        type(self.memory_review_agent).__name__,
                    ),
                    "recommendation": "quarantine" if suspicious else "continue_review",
                    "error_type": error_type,
                    "duration_ms": round((time.monotonic() - start) * 1000),
                },
            )
            findings.append(suspicious)
        return {"memory_findings": findings, "handoff_count": count}

    def _route_after_memories(self, state: PrimaryAssistantState) -> str:
        if state["turn"].actions:
            return "sentinel_review"
        return self._next_turn_or_final(state)

    def _sentinel_review(self, state: PrimaryAssistantState) -> dict[str, Any]:
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
                proposed_by="primary_assistant",
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
        review_available = True
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
        start = time.monotonic()
        request = ActionReviewRequest(handoff=handoff, proposal=action, evidence=evidence)
        try:
            review_with_context = getattr(self.sentinel, "review_with_context", None)
            finding = (
                review_with_context(request.proposal, request.evidence)
                if review_with_context
                else self.sentinel.review(request.proposal)
            )
            if finding.get("status") == "unavailable":
                review_available = False
        except Exception:
            review_available = False
            finding = {"recommendation": "UNAVAILABLE"}
        self._check_deadline(state)
        self.events.emit(
            "agent_handoff",
            {
                **handoff.model_dump(mode="json"),
                "status": "completed" if review_available else "failed",
                "recommendation": finding.get("disposition", "unavailable"),
                "provider": finding.get("provider", "unspecified"),
                "duration_ms": round((time.monotonic() - start) * 1000),
            },
        )
        safe_finding = {
            key: finding[key]
            for key in ("status", "risk", "disposition", "provider", "evidence_refs")
            if key in finding
        }
        self.events.emit("sentinel_finding", {"action_id": action_id, "finding": safe_finding})
        return {
            "action": action,
            "review_available": review_available,
            "handoff_count": state["handoff_count"] + 1,
        }

    def _policy_gateway(self, state: PrimaryAssistantState) -> dict[str, Any]:
        self._check_deadline(state)
        if state.get("status") == "failed":
            return {}
        action = state["action"]
        decision = self.gateway.authorize(action)
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

    def _route_after_gateway(self, state: PrimaryAssistantState) -> str:
        if state.get("status") == "failed":
            return "finalize"
        if state["decision"].authorized:
            return "sandbox_execution"
        return self._next_action_or_turn(state)

    def _sandbox_execution(self, state: PrimaryAssistantState) -> dict[str, Any]:
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

    def _route_after_action(self, state: PrimaryAssistantState) -> str:
        return self._next_action_or_turn(state)

    def _next_action_or_turn(self, state: PrimaryAssistantState) -> str:
        if state["action_index"] < len(state["turn"].actions):
            return "next_action"
        return self._next_turn_or_final(state)

    @staticmethod
    def _check_deadline(state: PrimaryAssistantState) -> None:
        if time.monotonic() >= state["deadline"]:
            raise TimeoutError("workflow deadline exceeded")

    def _next_turn_or_final(self, state: PrimaryAssistantState) -> str:
        if state["turn"].done:
            return "finalize"
        if state["step"] >= self.max_steps:
            return "finalize"
        return "reason"

    @staticmethod
    def _action_id(action: ActionProposal) -> str:
        from cybotech.services.policy_gateway.service import PolicyGateway

        return PolicyGateway.action_hash(action)

    def _finalize(self, state: PrimaryAssistantState) -> dict[str, Any]:
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


class _NoDocuments:
    def retrieve(self, query: str, session_id: str) -> list[DocumentEvidence]:
        return []
