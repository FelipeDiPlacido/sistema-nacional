"""Track consumed stock when closing allocations.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resource_allocation_items",
        sa.Column("consumed_quantity", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_resource_allocation_items_consumed_within_quantity",
        "resource_allocation_items",
        "consumed_quantity >= 0 AND consumed_quantity <= quantity",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_resource_allocation_items_consumed_within_quantity",
        "resource_allocation_items",
        type_="check",
    )
    op.drop_column("resource_allocation_items", "consumed_quantity")
