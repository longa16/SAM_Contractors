"""add contractor onboarding documents

Revision ID: 6ec1f0a43130
Revises: 23322ba5a2d8
"""

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "6ec1f0a43130"
down_revision = "23322ba5a2d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", app.db.base.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_company_document_company_id"), "company_document", ["company_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_company_document_company_id"), table_name="company_document")
    op.drop_table("company_document")
