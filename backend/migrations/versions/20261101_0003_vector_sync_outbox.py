"""Add VectorSyncOutbox table for PostgreSQL → Qdrant transactional outbox.

Revision ID: 20261101_0003
Revises: 20261031_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20261101_0003"
down_revision = "20261031_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enums
    sync_operation = sa.Enum("upsert", "delete", name="vector_sync_operation")
    sync_status = sa.Enum(
        "pending", "processing", "completed", "failed", name="vector_sync_status"
    )

    op.create_table(
        "vector_sync_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("collection_name", sa.String(120), nullable=False),
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("operation", sync_operation, nullable=False),
        # JSON list of floats — NULL for delete operations.
        sa.Column("vector_payload", sa.JSON(), nullable=True),
        # JSON dict of metadata — NULL for delete operations.
        sa.Column("metadata_payload", sa.JSON(), nullable=True),
        sa.Column("status", sync_status, nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_vsync_status_created",
        "vector_sync_outbox",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_vsync_record",
        "vector_sync_outbox",
        ["collection_name", "record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vsync_record", table_name="vector_sync_outbox")
    op.drop_index("ix_vsync_status_created", table_name="vector_sync_outbox")
    op.drop_table("vector_sync_outbox")
    sa.Enum(name="vector_sync_status").drop(op.get_bind())
    sa.Enum(name="vector_sync_operation").drop(op.get_bind())
