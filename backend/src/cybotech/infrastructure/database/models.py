from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MemoryStatus(enum.StrEnum):
    pending = "pending"
    trusted = "trusted"
    quarantined = "quarantined"
    rejected = "rejected"
    expired = "expired"
    revoked = "revoked"


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, name="memory_status"), nullable=False, default=MemoryStatus.pending
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    reviewer: Mapped[Any] = mapped_column(String(120), nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embedding: Mapped[Any] = mapped_column(Vector(384).with_variant(JSON, "sqlite"), nullable=True)
    embedding_model: Mapped[Any] = mapped_column(String(200), nullable=True)
    proposal_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    correlation_id: Mapped[Any] = mapped_column(String(120), index=True, nullable=True)
    object_id: Mapped[Any] = mapped_column(String(120), index=True, nullable=True)
    outcome: Mapped[Any] = mapped_column(String(80), nullable=True)
    reason_code: Mapped[Any] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    principal: Mapped[str] = mapped_column(String(120), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    approval_status: Mapped[Any] = mapped_column(String(20), nullable=True)
    approval_expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_consumed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MemoryProvenance(Base):
    __tablename__ = "memory_provenance"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    trust_context: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
