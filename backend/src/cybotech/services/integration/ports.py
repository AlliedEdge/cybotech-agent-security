"""
Integration ports — wires the multi-agent architecture to infrastructure adapters.

Migration note (feat/qdrant-vector-db-migration)
------------------------------------------------
``build_security_ports`` and ``build_persistent_graph`` now accept an optional
``vector_store`` argument.  When provided (production), all vector writes and
searches route through Qdrant via the VectorStore port.  When omitted (SQLite
unit tests and legacy callers), the services fall back to the original
pgvector/Python-cosine paths so no existing tests break.

The multi-agent architecture (SupervisorGraph, PrimaryAssistant, MemoryGuard,
SentinelAgent, PolicyGateway, VibeHackerGraph) is completely unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.orm import Session

from cybotech.agents.assistant import Assistant
from cybotech.agents.contracts import (
    ActionProposal,
    Document,
    DocumentEvidence,
    GatewayDecision,
    MemoryDecision,
    MemoryProposal,
    MemoryRecord,
    ModelProvider,
    RetrievalQuery,
)
from cybotech.agents.supervisor import SupervisorGraph
from cybotech.infrastructure.database.models import AuditEvent
from cybotech.infrastructure.sandbox.tools import SandboxDispatcher
from cybotech.services.documents.service import DocumentService
from cybotech.services.memory_guard.service import MemoryGuard, MemoryReviewer
from cybotech.services.policy_gateway.service import PolicyGateway
from cybotech.services.retrieval.service import Embedder, RetrievalService
from cybotech.services.sentinel.service import Sentinel, SentinelFinding

if TYPE_CHECKING:
    from cybotech.infrastructure.vector_store.interface import VectorStore
    from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher

SourceResolver = Callable[[str, str], dict[str, Any] | None]


class Reviewer(Protocol):
    def review(self, proposal: ActionProposal) -> SentinelFinding: ...


class MemoryGuardPort:
    def __init__(
        self,
        db: Session,
        source_resolver: SourceResolver,
        embedder: Embedder,
        reviewer: MemoryReviewer | None = None,
        dispatcher: VectorSyncDispatcher | None = None,
    ) -> None:
        self.guard = MemoryGuard(
            db,
            source_resolver,
            embedder,
            reviewer,
            dispatcher=dispatcher,
        )

    def propose_memory(self, proposal: MemoryProposal) -> MemoryDecision:
        return self.guard.propose_memory(proposal)

    def propose_with_review(
        self, proposal: MemoryProposal, model_suspicious: bool
    ) -> MemoryDecision:
        return self.guard.propose_memory(proposal, model_suspicious=model_suspicious)


class TrustedRetrievalPort:
    def __init__(
        self,
        db: Session,
        embedder: Embedder,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.db = db
        self.service = RetrievalService(db, embedder, vector_store=vector_store)

    def retrieve(self, query: RetrievalQuery) -> list[MemoryRecord]:
        rows = self.service.retrieve(
            query.query_text,
            max_results=query.max_results,
            principal=query.principal,
            min_similarity=query.min_similarity,
        )
        return [
            MemoryRecord(
                id=str(row.id),
                content=row.content,
                trust_label="trusted",
                source_type=row.source_type,
            )
            for row in rows
        ]


class DocumentRetrievalAdapter:
    def __init__(
        self,
        db: Session,
        embedder: Embedder,
        vector_store: VectorStore | None = None,
        dispatcher: VectorSyncDispatcher | None = None,
    ) -> None:
        self.service = DocumentService(
            db,
            embedder,
            vector_store=vector_store,
            dispatcher=dispatcher,
        )

    def retrieve(self, query: str, session_id: str) -> list[DocumentEvidence]:
        return self.service.retrieve(query, session_id)


class SentinelPortAdapter:
    def __init__(self, reviewer: Reviewer | None = None) -> None:
        self.reviewer = reviewer or Sentinel()
        self.findings: dict[str, dict[str, Any]] = {}

    def review(self, proposal: ActionProposal) -> dict[str, Any]:
        finding = self.reviewer.review(proposal)
        data = finding.model_dump()
        self.findings[PolicyGateway.action_hash(proposal)] = data
        return data

    def review_with_context(
        self, proposal: ActionProposal, evidence: list[DocumentEvidence]
    ) -> dict[str, Any]:
        method = getattr(self.reviewer, "review_with_context", None)
        finding = method(proposal, evidence) if method else self.reviewer.review(proposal)
        data = finding.model_dump()
        self.findings[PolicyGateway.action_hash(proposal)] = data
        return data


class PolicyGatewayPort:
    def __init__(self, db: Session, sentinel: SentinelPortAdapter) -> None:
        self.gateway = PolicyGateway(db)
        self.sentinel = sentinel

    def authorize(
        self, proposal: ActionProposal, *, sentinel_finding: SentinelFinding | None = None
    ) -> GatewayDecision:
        if sentinel_finding is None:
            data = self.sentinel.findings.pop(PolicyGateway.action_hash(proposal), None)
            try:
                finding = SentinelFinding.model_validate(data) if data is not None else None
            except Exception:
                finding = None
        else:
            finding = sentinel_finding
        return self.gateway.authorize(
            proposal, sentinel_finding=finding, approval_id=proposal.approval_id
        )


class PersistentEventPort:
    """Persists assistant events before returning control to the workflow."""

    def __init__(self, db: Session, correlation_id: str) -> None:
        self.db, self.correlation_id = db, correlation_id

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.db.add(
            AuditEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                event_type=event_type,
                actor="supervisor" if event_type == "agent_handoff" else "primary_assistant",
                correlation_id=self.correlation_id,
                object_id=str(payload.get("memory_id") or payload.get("action_id") or "") or None,
                outcome=(
                    str(payload.get("status"))
                    if payload.get("status") is not None
                    else str(payload.get("success"))
                    if payload.get("success") is not None
                    else None
                ),
                reason_code=payload.get("failed_check"),
                payload=payload,
            )
        )
        self.db.commit()


@dataclass
class SecurityPorts:
    memory: MemoryGuardPort
    retrieval: TrustedRetrievalPort
    sentinel: SentinelPortAdapter
    gateway: PolicyGatewayPort
    events: PersistentEventPort


def build_persistent_assistant(
    db: Session,
    model: ModelProvider,
    embedder: Embedder,
    documents: list[Document],
    source_resolver: SourceResolver,
    correlation_id: str,
    reviewer: Reviewer | None = None,
    memory_reviewer: MemoryReviewer | None = None,
    vector_store: VectorStore | None = None,
) -> Assistant:
    ports = build_security_ports(
        db,
        embedder,
        source_resolver,
        correlation_id,
        reviewer,
        memory_reviewer,
        vector_store=vector_store,
    )
    dispatcher = SandboxDispatcher(documents)
    return Assistant(
        model=model,
        memory=ports.memory,
        retrieval=ports.retrieval,
        sentinel=ports.sentinel,
        gateway=ports.gateway,
        dispatcher=dispatcher,
        events=ports.events,
    )


def build_persistent_graph(
    db: Session,
    model: ModelProvider,
    embedder: Embedder,
    documents: list[Document],
    source_resolver: SourceResolver,
    correlation_id: str,
    reviewer: Reviewer | None = None,
    memory_reviewer: MemoryReviewer | None = None,
    vector_store: VectorStore | None = None,
) -> SupervisorGraph:
    """Build the registered assistant's LangGraph with the established security ports."""
    vs_dispatcher = _make_dispatcher(db, vector_store)
    ports = build_security_ports(
        db,
        embedder,
        source_resolver,
        correlation_id,
        reviewer,
        None,
        vector_store=vector_store,
        vs_dispatcher=vs_dispatcher,
    )
    return SupervisorGraph(
        model=model,
        memory=ports.memory,
        retrieval=ports.retrieval,
        document_retrieval=DocumentRetrievalAdapter(
            db,
            embedder,
            vector_store=vector_store,
            dispatcher=vs_dispatcher,
        ),
        sentinel=ports.sentinel,
        gateway=ports.gateway,
        dispatcher=SandboxDispatcher(documents),
        events=ports.events,
        memory_review_agent=memory_reviewer,
        source_resolver=source_resolver,
    )


def build_security_ports(
    db: Session,
    embedder: Embedder,
    source_resolver: SourceResolver,
    correlation_id: str,
    reviewer: Reviewer | None = None,
    memory_reviewer: MemoryReviewer | None = None,
    vector_store: VectorStore | None = None,
    vs_dispatcher: VectorSyncDispatcher | None = None,
) -> SecurityPorts:
    """Build security ports.  Accepts an optional VectorStore for Qdrant-backed retrieval."""
    if vs_dispatcher is None:
        vs_dispatcher = _make_dispatcher(db, vector_store)
    sentinel = SentinelPortAdapter(reviewer)
    return SecurityPorts(
        memory=MemoryGuardPort(db, source_resolver, embedder, memory_reviewer, vs_dispatcher),
        retrieval=TrustedRetrievalPort(db, embedder, vector_store=vector_store),
        sentinel=sentinel,
        gateway=PolicyGatewayPort(db, sentinel),
        events=PersistentEventPort(db, correlation_id),
    )


def _make_dispatcher(
    db: Session,
    vector_store: VectorStore | None,
) -> VectorSyncDispatcher | None:
    """Build a VectorSyncDispatcher if a VectorStore is available."""
    if vector_store is None:
        return None
    from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher

    return VectorSyncDispatcher(db, vector_store)
