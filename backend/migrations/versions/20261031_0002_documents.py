"""Untrusted document evidence and pgvector chunk retrieval."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "20261031_0002"
down_revision = "20261030_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("session_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("trust_label", sa.String(20), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "ordinal", name="ix_document_chunks_document_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("documents")
