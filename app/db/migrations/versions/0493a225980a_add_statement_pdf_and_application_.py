"""add statement PDF and application tracking

Revision ID: 0493a225980a
Revises: 2bc58f52a834
"""

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "0493a225980a"
down_revision = "2bc58f52a834"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company",
        sa.Column("statement_mode", sa.String(length=16), server_default="form", nullable=False),
    )
    op.add_column("company", sa.Column("statement_pdf", sa.LargeBinary(), nullable=True))
    op.add_column(
        "company_opportunity_state",
        sa.Column("application_stage", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "company_opportunity_state", sa.Column("application_notes", sa.Text(), nullable=True)
    )
    op.add_column(
        "company_opportunity_state",
        sa.Column(
            "application_submitted_at", app.db.base.UTCDateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "company_opportunity_state",
        sa.Column("application_updated_at", app.db.base.UTCDateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_company_opportunity_state_application_stage"),
        "company_opportunity_state",
        ["application_stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_company_opportunity_state_application_stage"),
        table_name="company_opportunity_state",
    )
    op.drop_column("company_opportunity_state", "application_updated_at")
    op.drop_column("company_opportunity_state", "application_submitted_at")
    op.drop_column("company_opportunity_state", "application_notes")
    op.drop_column("company_opportunity_state", "application_stage")
    op.drop_column("company", "statement_pdf")
    op.drop_column("company", "statement_mode")
