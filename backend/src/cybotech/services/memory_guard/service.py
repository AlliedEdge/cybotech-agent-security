from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.agents.contracts import MemoryDecision, MemoryProposal
from cybotech.infrastructure.database.models import (
    AuditEvent,
    Memory,
    MemoryProvenance,
    MemoryStatus,
)
from cybotech.infrastructure.vector.qdrant import QdrantStore

SourceContext = dict[str, Any]
ProvenanceResolver = Callable[[str, str], SourceContext | None]

_DESTINATION_CHANGE = re.compile(
    r"(?:change|update|replace|redirect).{0,50}(?:report|destination|recipient|email)|"
    r"(?:send|forward).{0,50}(?:attacker|external|new address)",
    re.I,
)
_POLICY_OVERRIDE = re.compile(
    r"(?:ignore|override|disable|bypass).{0,40}(?:policy|approval|security|gateway)", re.I
)
_ALLOWED = {
    MemoryStatus.pending: {MemoryStatus.trusted, MemoryStatus.quarantined, MemoryStatus.rejected},
    MemoryStatus.trusted: {MemoryStatus.expired, MemoryStatus.revoked},
    MemoryStatus.quarantined: {MemoryStatus.trusted, MemoryStatus.rejected, MemoryStatus.revoked},
}


class MemoryEmbedder(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]: ...


class MemoryReviewer(Protocol):
    def is_suspicious(self, proposal: MemoryProposal) -> bool: ...


def _event(
    event_type: str, actor: str, object_id: str, outcome: str, reason: str, payload: dict[str, Any]
) -> AuditEvent:
    return AuditEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        event_type=event_type,
        actor=actor,
        object_id=object_id,
        outcome=outcome,
        reason_code=reason,
        payload=payload,
    )


class MemoryGuard:
    """Uses the application's SQLAlchemy session; caller owns transaction boundary."""

    def __init__(
        self,
        db: Session,
        provenance_resolver: ProvenanceResolver | None = None,
        embedder: MemoryEmbedder | None = None,
        reviewer: MemoryReviewer | None = None,
        vector_store: QdrantStore | None = None,
    ) -> None:
        self.db = db
        self.provenance_resolver = provenance_resolver
        self.embedder = embedder
        self.reviewer = reviewer
        self.vector_store = vector_store

    def propose_memory(self, proposal: MemoryProposal) -> MemoryDecision:
        if (
            not proposal.content.strip()
            or not proposal.source_document_id.strip()
            or not proposal.proposed_by.strip()
        ):
            raise ValueError("content, source_document_id and proposed_by are required")
        identity = proposal.model_dump(mode="json", exclude={"proposed_at"})
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        existing = self.db.scalar(select(Memory).where(Memory.proposal_key == key))
        if existing:
            return MemoryDecision(
                proposal_id=str(existing.id),
                status=existing.status.value,
                reason=existing.decision_reason or "duplicate_proposal",
            )

        content = proposal.content.strip()
        source = self._source_context(proposal)
        if source is None:
            state, reason, trust = MemoryStatus.quarantined, "provenance_missing", "unverified"
        elif _DESTINATION_CHANGE.search(content):
            state, reason, trust = (
                MemoryStatus.quarantined,
                "trusted_destination_change",
                source["trust_level"],
            )
        elif _POLICY_OVERRIDE.search(content):
            state, reason, trust = (
                MemoryStatus.quarantined,
                "embedded_policy_instruction",
                source["trust_level"],
            )
        elif source["trust_level"] not in {"verified", "internal"}:
            state, reason, trust = (
                MemoryStatus.quarantined,
                "untrusted_source",
                source["trust_level"],
            )
        else:
            state, reason, trust = MemoryStatus.pending, "review_required", source["trust_level"]

        if state == MemoryStatus.pending and self.reviewer is not None:
            try:
                if self.reviewer.is_suspicious(proposal):
                    state, reason = MemoryStatus.quarantined, "semantic_review_flagged"
            except Exception:
                state, reason = MemoryStatus.quarantined, "semantic_review_unavailable"

        # Compute embedding (stored in Qdrant, model name kept in Postgres for audit).
        vector: list[float] | None = None
        embedding_model: str | None = None
        if self.embedder is not None:
            vector = self.embedder.embed(content)
            if len(vector) != 384:
                raise ValueError("embedding dimension must be 384")
            embedding_model = self.embedder.model_name

        record = Memory(
            content=content,
            category=proposal.claimed_category,
            status=state,
            source_type=source.get("source_type", "unknown") if source else "unknown",
            source_id=proposal.source_document_id,
            provenance=source or {},
            created_by=proposal.proposed_by,
            proposal_key=key,
            decision_reason=reason,
            embedding_model=embedding_model,
        )
        self.db.add(record)
        self.db.flush()  # assigns record.id

        self.db.add(
            MemoryProvenance(
                memory_id=record.id,
                source_type=record.source_type,
                source_id=record.source_id,
                trust_context=trust,
                details={
                    "provenance": source or {},
                    "proposed_at": proposal.proposed_at.isoformat(),
                },
            )
        )
        self.db.add(
            _event(
                "memory_guard_decision",
                "memory_guard",
                str(record.id),
                state.value,
                reason,
                {"category": record.category, "source_id": record.source_id},
            )
        )
        self.db.commit()

        # Upsert vector into Qdrant only for pending/trusted memories that will be retrieved.
        # Quarantined memories are intentionally excluded from the vector index.
        if (
            vector is not None
            and self.vector_store is not None
            and state != MemoryStatus.quarantined
        ):
            self.vector_store.upsert_memory(
                memory_id=str(record.id),
                vector=vector,
                payload={
                    "embedding_model": embedding_model,
                    "category": record.category,
                    "source_type": record.source_type,
                    "status": state.value,
                },
            )

        return MemoryDecision(proposal_id=str(record.id), status=state.value, reason=reason)

    def _source_context(self, proposal: MemoryProposal) -> SourceContext | None:
        resolver = self.provenance_resolver
        if resolver:
            return resolver(proposal.source_document_id, proposal.raw_context)
        return None

    def transition(
        self, memory_id: uuid.UUID, target: MemoryStatus, actor: str, reason: str
    ) -> Memory:
        row = self.db.get(Memory, memory_id)
        if row is None:
            raise LookupError("memory not found")
        if target not in _ALLOWED.get(row.status, set()):
            raise ValueError(f"invalid memory transition: {row.status.value} -> {target.value}")
        if target == MemoryStatus.trusted and row.status not in {
            MemoryStatus.pending,
            MemoryStatus.quarantined,
        }:
            raise ValueError(
                "only pending or explicitly quarantined records can be reviewed as trusted"
            )
        previous = row.status.value

        if target == MemoryStatus.trusted:
            # Ensure a vector exists in Qdrant when approving a memory.
            if self.embedder is None:
                raise ValueError("embedding provider required before memory approval")
            if self.vector_store is None:
                raise ValueError("vector store required before memory approval")
            vector = self.embedder.embed(row.content)
            if len(vector) != 384:
                raise ValueError("embedding dimension must be 384")
            row.embedding_model = self.embedder.model_name
            row.provenance = {**row.provenance, "review_verified": True, "reviewed_by": actor}
            self.vector_store.upsert_memory(
                memory_id=str(row.id),
                vector=vector,
                payload={
                    "embedding_model": self.embedder.model_name,
                    "category": row.category,
                    "source_type": row.source_type,
                    "status": MemoryStatus.trusted.value,
                },
            )

        if target in {MemoryStatus.revoked, MemoryStatus.rejected, MemoryStatus.quarantined}:
            # Remove from vector index so the memory can never be retrieved after revocation.
            if self.vector_store is not None:
                self.vector_store.delete_memory(str(row.id))

        row.status, row.reviewer, row.decision_reason = target, actor, reason
        if target == MemoryStatus.revoked:
            row.revoked_at, row.revoked_reason = datetime.now(UTC), reason
        self.db.add(
            _event(
                "memory_lifecycle_transition",
                actor,
                str(row.id),
                target.value,
                reason,
                {"from": previous},
            )
        )
        self.db.commit()
        return row
