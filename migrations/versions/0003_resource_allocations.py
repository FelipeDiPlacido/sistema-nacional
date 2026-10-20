"""Add transactional resource allocations and closure.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_allocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("project_reference", sa.String(length=160), nullable=False),
        sa.Column("ngo_id", sa.Uuid(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ngo_id"], ["ngos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_reference"),
        sa.UniqueConstraint("request_key"),
    )
    op.create_index("ix_resource_allocations_ngo_id", "resource_allocations", ["ngo_id"])
    op.create_table(
        "resource_allocation_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_resource_allocation_items_quantity_positive"),
        sa.ForeignKeyConstraint(["allocation_id"], ["resource_allocations.id"]),
        sa.ForeignKeyConstraint(["resource_id"], ["national_resources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "allocation_id",
            "resource_id",
            name="uq_resource_allocation_items_allocation_resource",
        ),
    )
    op.create_index(
        "ix_resource_allocation_items_allocation_id",
        "resource_allocation_items",
        ["allocation_id"],
    )
    op.create_index(
        "ix_resource_allocation_items_resource_id",
        "resource_allocation_items",
        ["resource_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_allocation_items_resource_id",
        table_name="resource_allocation_items",
    )
    op.drop_index(
        "ix_resource_allocation_items_allocation_id",
        table_name="resource_allocation_items",
    )
    op.drop_table("resource_allocation_items")
    op.drop_index("ix_resource_allocations_ngo_id", table_name="resource_allocations")
    op.drop_table("resource_allocations")
