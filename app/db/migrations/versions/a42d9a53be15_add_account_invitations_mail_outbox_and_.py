"""add account invitations mail outbox and session revocation

Revision ID: a42d9a53be15
Revises: da3413abb2ff
"""

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "a42d9a53be15"
down_revision = "da3413abb2ff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_throttle",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "account_action",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column("used_at", app.db.base.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_account_action_user_id"), "account_action", ["user_id"], unique=False)
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("encrypted_body", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column("sent_at", app.db.base.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["account_action.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_outbox_action_id"), "email_outbox", ["action_id"], unique=False)
    op.add_column(
        "user", sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "user", sa.Column("invitation_pending", sa.Boolean(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("user", "invitation_pending")
    op.drop_column("user", "auth_version")
    op.drop_index(op.f("ix_email_outbox_action_id"), table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index(op.f("ix_account_action_user_id"), table_name="account_action")
    op.drop_table("account_action")
    op.drop_table("auth_throttle")
