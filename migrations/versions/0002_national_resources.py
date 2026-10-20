"""Add national resource inventory.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "national_resources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=80), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column("committed_quantity", sa.Integer(), nullable=False),
        sa.Column("required_certification_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total_quantity >= 0", name="ck_national_resources_total_nonnegative"),
        sa.CheckConstraint(
            "committed_quantity >= 0",
            name="ck_national_resources_committed_nonnegative",
        ),
        sa.CheckConstraint(
            "committed_quantity <= total_quantity",
            name="ck_national_resources_commitment_within_total",
        ),
        sa.ForeignKeyConstraint(["required_certification_id"], ["certifications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(
        "ix_national_resources_is_active", "national_resources", ["is_active"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_national_resources_is_active", table_name="national_resources")
    op.drop_table("national_resources")
