"""persist admin validated application milestones

Revision ID: 23322ba5a2d8
Revises: 0493a225980a
"""

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "23322ba5a2d8"
down_revision = "0493a225980a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_milestone",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("from_stage", sa.String(length=32), nullable=False),
        sa.Column("to_stage", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("validated_by", sa.Integer(), nullable=False),
        sa.Column("created_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["company_opportunity_state.id"],
        ),
        sa.ForeignKeyConstraint(
            ["validated_by"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_application_milestone_case_id"), "application_milestone", ["case_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_application_milestone_case_id"), table_name="application_milestone")
    op.drop_table("application_milestone")
