"""add versioned case dossiers checklists and final approvals

Revision ID: da3413abb2ff
Revises: 6ec1f0a43130
"""

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "da3413abb2ff"
down_revision = "6ec1f0a43130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_checklist_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("checked_by", sa.Integer(), nullable=True),
        sa.Column("checked_at", app.db.base.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["company_opportunity_state.id"],
        ),
        sa.ForeignKeyConstraint(
            ["checked_by"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_case_checklist_item_case_id"), "case_checklist_item", ["case_id"], unique=False
    )
    op.create_table(
        "case_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("document_key", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("created_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["company_opportunity_state.id"],
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "document_key", "version"),
    )
    op.create_index(op.f("ix_case_document_case_id"), "case_document", ["case_id"], unique=False)
    op.create_table(
        "case_dossier",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["company_opportunity_state.id"],
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_table(
        "dossier_approval",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("prepared_document_id", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Integer(), nullable=False),
        sa.Column("created_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["user.id"],
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["company_opportunity_state.id"],
        ),
        sa.ForeignKeyConstraint(
            ["prepared_document_id"],
            ["case_document.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "revision"),
    )
    op.create_index(
        op.f("ix_dossier_approval_case_id"), "dossier_approval", ["case_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_dossier_approval_case_id"), table_name="dossier_approval")
    op.drop_table("dossier_approval")
    op.drop_table("case_dossier")
    op.drop_index(op.f("ix_case_document_case_id"), table_name="case_document")
    op.drop_table("case_document")
    op.drop_index(op.f("ix_case_checklist_item_case_id"), table_name="case_checklist_item")
    op.drop_table("case_checklist_item")
