"""Initial ExpireGuard schema (Phase 1).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")
TRUE = sa.text("true")
FALSE = sa.text("false")


def _ts(name: str, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=NOW if default else None)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
    )
    op.create_index("ix_companies_email", "companies", ["email"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone_number", sa.String(32), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=TRUE),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_users_company_id_companies", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_company_id", "users", ["company_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_revoked_tokens_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("jti", name="pk_revoked_tokens"),
    )
    op.create_index("ix_revoked_tokens_user_id", "revoked_tokens", ["user_id"])
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])

    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_categories_company_id_companies", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("company_id", "name", name="uq_categories_company_name"),
    )
    op.create_index("ix_categories_company_id", "categories", ["company_id"])

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sku", sa.String(64), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("brand", sa.String(120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False, server_default="unit"),
        sa.Column("selling_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=TRUE),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("selling_price >= 0", name="ck_products_selling_price_non_negative"),
        sa.CheckConstraint("cost_price >= 0", name="ck_products_cost_price_non_negative"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_products_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="fk_products_category_id_categories", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("company_id", "sku", name="uq_products_company_sku"),
        sa.UniqueConstraint("company_id", "barcode", name="uq_products_company_barcode"),
    )
    op.create_index("ix_products_company_id", "products", ["company_id"])
    op.create_index("ix_products_category_id", "products", ["category_id"])
    op.create_index("ix_products_sku", "products", ["sku"])
    op.create_index("ix_products_barcode", "products", ["barcode"])
    op.create_index("ix_products_company_name", "products", ["company_id", "name"])

    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("batch_number", sa.String(64), nullable=False),
        sa.Column("manufacturing_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("initial_quantity", sa.Integer(), nullable=False),
        sa.Column("remaining_quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("initial_quantity >= 0", name="ck_batches_initial_quantity_non_negative"),
        sa.CheckConstraint(
            "manufacturing_date IS NULL OR expiry_date >= manufacturing_date",
            name="ck_batches_expiry_after_manufacturing",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_batches_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_batches_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_batches"),
        sa.UniqueConstraint("product_id", "batch_number", name="uq_batches_product_batch_number"),
    )
    op.create_index("ix_batches_company_id", "batches", ["company_id"])
    op.create_index("ix_batches_product_id", "batches", ["product_id"])
    op.create_index("ix_batches_expiry_date", "batches", ["expiry_date"])
    op.create_index("ix_batches_company_status", "batches", ["company_id", "status"])
    op.create_index("ix_batches_company_expiry", "batches", ["company_id", "expiry_date"])

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=True),
        sa.Column("reference_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_inventory_movements_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_inventory_movements_product_id_products", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], name="fk_inventory_movements_batch_id_batches", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_movements"),
    )
    op.create_index("ix_inventory_movements_company_id", "inventory_movements", ["company_id"])
    op.create_index("ix_inventory_movements_product_id", "inventory_movements", ["product_id"])
    op.create_index("ix_inventory_movements_batch_id", "inventory_movements", ["batch_id"])
    op.create_index("ix_inventory_movements_company_created", "inventory_movements", ["company_id", "created_at"])
    op.create_index("ix_inventory_movements_reference", "inventory_movements", ["reference_type", "reference_id"])

    op.create_table(
        "sales",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=False),
        _ts("created_at"),
        sa.CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_sales_unit_price_non_negative"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_sales_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_sales_product_id_products", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], name="fk_sales_batch_id_batches", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_sales"),
    )
    op.create_index("ix_sales_company_id", "sales", ["company_id"])
    op.create_index("ix_sales_batch_id", "sales", ["batch_id"])
    op.create_index("ix_sales_company_sold_at", "sales", ["company_id", "sold_at"])
    op.create_index("ix_sales_product_sold_at", "sales", ["product_id", "sold_at"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("days_remaining", sa.Integer(), nullable=True),
        sa.Column("quantity_at_risk", sa.Integer(), nullable=True),
        sa.Column("recipient_phone", sa.String(32), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("sms_sent", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("sms_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sms_error", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_alerts_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_alerts_product_id_products", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], name="fk_alerts_batch_id_batches", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
    )
    op.create_index("ix_alerts_company_id", "alerts", ["company_id"])
    op.create_index("ix_alerts_company_open", "alerts", ["company_id", "is_resolved", "created_at"])
    op.create_index(
        "uq_alerts_open_batch_type", "alerts", ["batch_id", "alert_type"], unique=True,
        postgresql_where=sa.text("is_resolved = false AND batch_id IS NOT NULL"),
    )
    op.create_index(
        "uq_alerts_open_product_type", "alerts", ["product_id", "alert_type"], unique=True,
        postgresql_where=sa.text("is_resolved = false AND batch_id IS NULL AND product_id IS NOT NULL"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_notifications_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], name="fk_notifications_alert_id_alerts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index("ix_notifications_company_id", "notifications", ["company_id"])
    op.create_index("ix_notifications_alert_id", "notifications", ["alert_id"])
    op.create_index("ix_notifications_company_status", "notifications", ["company_id", "status"])

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("created_at"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_import_jobs_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_import_jobs_created_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_import_jobs"),
    )
    op.create_index("ix_import_jobs_company_id", "import_jobs", ["company_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_audit_logs_company_id_companies", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_audit_logs_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_company_id", "audit_logs", ["company_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_company_created", "audit_logs", ["company_id", "created_at"])


def downgrade() -> None:
    for table in (
        "audit_logs", "import_jobs", "notifications", "alerts", "sales", "inventory_movements",
        "batches", "products", "categories", "revoked_tokens", "users", "companies",
    ):
        op.drop_table(table)
