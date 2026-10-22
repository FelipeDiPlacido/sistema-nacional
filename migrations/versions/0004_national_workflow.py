"""Add account roles, inventory ownership and multi-NGO projects.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(32), server_default="NGO", nullable=False))
    op.add_column("national_resources", sa.Column("ngo_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_national_resources_ngo", "national_resources", "ngos", ["ngo_id"], ["id"]
    )
    op.create_index("ix_national_resources_ngo_id", "national_resources", ["ngo_id"])
    op.drop_constraint(
        "resource_allocations_project_reference_key", "resource_allocations", type_="unique"
    )
    op.create_unique_constraint(
        "uq_allocation_project_ngo", "resource_allocations", ["project_reference", "ngo_id"]
    )


def downgrade() -> None:
    # This intentionally fails if multiple NGOs share a project; never discard their awards.
    op.create_unique_constraint(
        "resource_allocations_project_reference_key", "resource_allocations", ["project_reference"]
    )
    op.drop_constraint("uq_allocation_project_ngo", "resource_allocations", type_="unique")
    op.drop_index("ix_national_resources_ngo_id", table_name="national_resources")
    op.drop_constraint("fk_national_resources_ngo", "national_resources", type_="foreignkey")
    op.drop_column("national_resources", "ngo_id")
    op.drop_column("users", "role")
