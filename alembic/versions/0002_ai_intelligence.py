"""Phase 2: AI insights and conversations.

Revision ID: 0002_ai
Revises: 0001_initial
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_ai"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "insights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("supporting_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ai_provider", sa.String(32), nullable=False),
        sa.Column("ai_model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_insights_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_insights_product_id_products", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], name="fk_insights_batch_id_batches", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_insights"),
    )
    op.create_index("ix_insights_company_id", "insights", ["company_id"])
    op.create_index("ix_insights_company_created", "insights", ["company_id", "created_at"])
    op.create_index("ix_insights_company_category", "insights", ["company_id", "category"])

    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_ai_conversations_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ai_conversations_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_conversations"),
    )
    op.create_index("ix_ai_conversations_company_id", "ai_conversations", ["company_id"])
    op.create_index("ix_ai_conversations_company_user", "ai_conversations", ["company_id", "user_id", "updated_at"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], name="fk_ai_messages_conversation_id_ai_conversations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_messages"),
    )
    op.create_index("ix_ai_messages_conversation_created", "ai_messages", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_table("ai_messages")
    op.drop_table("ai_conversations")
    op.drop_table("insights")
