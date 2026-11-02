"""
Memory Guard — proposal, lifecycle and trust management for long-term memory.

Migration note (feat/qdrant-vector-db-migration)
------------------------------------------------
Memory vector writes now go through the transactional outbox so that the
PostgreSQL ``Memory`` record and the Qdrant vector point are always consistent:

  propose_memory():
    - PostgreSQL writes the canonical Memory record (unchanged).
    - If an embedder is available, the embedding is stored in
      Memory.embedding (kept for the migration window).
    - A ``VectorSyncOutbox`` row is enqueued in the SAME transaction.
    - After commit the outbox dispatcher is called; Qdrant downtime leaves
      the row in "pending" state for retry — it does NOT roll back the memory.

  transition() → trusted:
    - PostgreSQL updates the Memory record (unchanged).
    - If the embedding was generated at propose time, an outbox upsert is
      enqueued.  If not (lazy case), the embedding is generated here and then
      enqueued.
    - Qdrant write is dispatched after commit.

  transition() → revoked | rejected:
    - A Qdrant delete is enqueued in the same transaction.

Security invariants preserved
------------------------------
- PostgreSQL is the sole authority on memory trust.
- A vector's presence in Qdrant never implies the memory is trusted.
- is_eligible() still runs against live PostgreSQL state on every retrieval.
- MemoryGuard lifecycle transitions, human-review requirements, provenance
  verification, and regex checks are completely unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.agents.contracts import MemoryDecision, MemoryProposal
from cybotech.agents.registry import Operation, require_permission
from cybotech.infrastructure.database.models import (
    AuditEvent,
    Memory,
    MemoryProvenance,
    MemoryStatus,
)

if TYPE_CHECKING:
    from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher

SourceContext = dict[str, Any]
ProvenanceResolver = Callable[[str, str], SourceContext | None]

logger = logging.getLogger(__name__)

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

# Embedding version tag for the Qdrant payload.
_EMBEDDING_VERSION = "1"


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


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class MemoryGuard:
    """Uses the application's SQLAlchemy session; caller owns transaction boundary."""

    def __init__(
        self,
        db: Session,
        provenance_resolver: ProvenanceResolver | None = None,
        embedder: MemoryEmbedder | None = None,
        reviewer: MemoryReviewer | None = None,
        dispatcher: VectorSyncDispatcher | None = None,
    ) -> None:
        self.db = db
        self.provenance_resolver = provenance_resolver
        self.embedder = embedder
        self.reviewer = reviewer
        self._dispatcher = dispatcher

    def propose_memory(
        self, proposal: MemoryProposal, *, model_suspicious: bool = False
    ) -> MemoryDecision:
        require_permission(proposal.proposed_by, Operation.PROPOSE_MEMORY)
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
            if model_suspicious and existing.status in {MemoryStatus.pending, MemoryStatus.trusted}:
                target = (
                    MemoryStatus.quarantined
                    if existing.status == MemoryStatus.pending
                    else MemoryStatus.revoked
                )
                existing = self.transition(
                    existing.id, target, "memory_guard", "semantic_review_flagged"
                )
            return MemoryDecision(
                proposal_id=str(existing.id),
                status=existing.status.value,
                reason=existing.decision_reason or "duplicate_proposal",
            )

        content = proposal.content.strip()
        # The source trust is supplied by the application provenance resolver, never by content.
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

        if state == MemoryStatus.pending and model_suspicious:
            state, reason = MemoryStatus.quarantined, "semantic_review_flagged"
        if state == MemoryStatus.pending and self.reviewer is not None:
            try:
                if self.reviewer.is_suspicious(proposal):
                    state, reason = MemoryStatus.quarantined, "semantic_review_flagged"
            except Exception:
                state, reason = MemoryStatus.quarantined, "semantic_review_unavailable"

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
        )
        vector: list[float] | None = None
        if self.embedder is not None:
            vector = self.embedder.embed(content)
            if len(vector) != 384:
                raise ValueError("embedding dimension must be 384")
            record.embedding = vector
            record.embedding_model = self.embedder.model_name

        self.db.add(record)
        self.db.flush()  # assign record.id before outbox entries

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

        # Enqueue Qdrant upsert in the same transaction if embedding is available.
        # Non-trusted (quarantined/rejected) memories are still indexed so that
        # the reconciliation command can verify them; trust is enforced by
        # PostgreSQL at retrieval time, never by Qdrant metadata.
        if self._dispatcher is not None and vector is not None:
            self._dispatcher.enqueue_upsert_memory(
                self.db,
                memory_id=str(record.id),
                session_id="",  # Memory has no session scope in Qdrant
                provenance_reference=proposal.source_document_id,
                content_hash=_content_hash(content),
                embedding_model=self.embedder.model_name,  # type: ignore[union-attr]
                embedding_version=_EMBEDDING_VERSION,
                vector=vector,
            )

        self.db.commit()
        self._try_dispatch()
        return MemoryDecision(proposal_id=str(record.id), status=state.value, reason=reason)

    def _source_context(self, proposal: MemoryProposal) -> SourceContext | None:
        # Assistant contract carries source id and raw text, not authority claims. Only recognize
        # explicit source ids from the documented synthetic demo namespace; API proposals can
        # supply a provenance entry through the adapter's source resolver.
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
        new_vector: list[float] | None = None

        if target == MemoryStatus.trusted:
            if row.embedding is None:
                if self.embedder is None:
                    raise ValueError("embedding provider required before memory approval")
                new_vector = self.embedder.embed(row.content)
                if len(new_vector) != 384:
                    raise ValueError("embedding dimension must be 384")
                row.embedding = new_vector
                row.embedding_model = self.embedder.model_name
            row.provenance = {**row.provenance, "review_verified": True, "reviewed_by": actor}

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

        # Enqueue Qdrant operation in the same transaction.
        if self._dispatcher is not None:
            if target in {MemoryStatus.revoked, MemoryStatus.rejected}:
                # Memory is no longer eligible — delete from Qdrant.
                self._dispatcher.enqueue_delete_memory(self.db, str(row.id))
            elif target == MemoryStatus.trusted:
                # Upsert so Qdrant has the current vector if it was generated lazily.
                vector_to_sync = new_vector if new_vector is not None else row.embedding
                if vector_to_sync is not None and self.embedder is not None:
                    self._dispatcher.enqueue_upsert_memory(
                        self.db,
                        memory_id=str(row.id),
                        session_id="",
                        provenance_reference=row.source_id,
                        content_hash=_content_hash(row.content),
                        embedding_model=row.embedding_model or self.embedder.model_name,
                        embedding_version=_EMBEDDING_VERSION,
                        vector=list(vector_to_sync),
                    )

        self.db.commit()
        self._try_dispatch()
        return row

    def _try_dispatch(self) -> None:
        """Dispatch pending outbox events after a PostgreSQL commit.

        Failures are logged by the dispatcher and left in "pending" state for
        retry.  They must never roll back the PostgreSQL write.
        """
        if self._dispatcher is not None:
            try:
                self._dispatcher.dispatch_pending()
            except Exception:
                logger.debug("Outbox dispatch failed after memory operation", exc_info=True)
