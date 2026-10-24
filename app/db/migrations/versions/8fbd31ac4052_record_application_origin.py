"""Record who initiated an application without guessing historical origins.

Revision ID: 8fbd31ac4052
Revises: a42d9a53be15
"""

import sqlalchemy as sa
from alembic import op

revision = "8fbd31ac4052"
down_revision = "a42d9a53be15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_opportunity_state",
        sa.Column("application_origin", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_opportunity_state", "application_origin")
