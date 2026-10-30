"""Security memory, policy and audit foundation.

Revision ID: 20261030_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "20261030_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    status = sa.Enum(
        "pending", "trusted", "quarantined", "rejected", "expired", "revoked", name="memory_status"
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text()),
        sa.Column("reviewer", sa.String(120)),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384)),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("proposal_key", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_memories_status_created", "memories", ["status", "created_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(80), nullable=False, unique=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("correlation_id", sa.String(120)),
        sa.Column("object_id", sa.String(120)),
        sa.Column("outcome", sa.String(80)),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    for name, column in [
        ("event_id", "event_id"),
        ("event_type", "event_type"),
        ("occurred_at", "occurred_at"),
        ("correlation_id", "correlation_id"),
        ("object_id", "object_id"),
    ]:
        op.create_index(
            f"ix_audit_events_{name}", "audit_events", [column], unique=name == "event_id"
        )
    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("principal", sa.String(120), nullable=False),
        sa.Column("action_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("approval_status", sa.String(20)),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True)),
        sa.Column("approval_consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_policy_decisions_action_hash", "policy_decisions", ["action_hash"])
    op.create_table(
        "memory_provenance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "memory_id", sa.Uuid(), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("trust_context", sa.String(40), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index("ix_memory_provenance_memory_id", "memory_provenance", ["memory_id"])


def downgrade() -> None:
    op.drop_table("memory_provenance")
    op.drop_index("ix_policy_decisions_action_hash", table_name="policy_decisions")
    op.drop_table("policy_decisions")
    for name in ["event_id", "event_type", "occurred_at", "correlation_id", "object_id"]:
        op.drop_index(f"ix_audit_events_{name}", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_memories_status_created", table_name="memories")
    op.drop_table("memories")
    sa.Enum(name="memory_status").drop(op.get_bind(), checkfirst=True)
