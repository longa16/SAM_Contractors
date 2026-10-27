"""remove initial SAM bootstrap and clean expired catalogue entries

Revision ID: 65cdab8cf909
Revises: b0baeedf8e85
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "65cdab8cf909"
down_revision = "b0baeedf8e85"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    offers = sa.table(
        "opportunity",
        sa.column("notice_id", sa.String()),
        sa.column("response_deadline_utc", app.db.base.UTCDateTime()),
    )
    cases = sa.table("company_opportunity_state", sa.column("notice_id", sa.String()))
    states = sa.table("opportunity_state", sa.column("notice_id", sa.String()))
    now = datetime.now(UTC)
    expired = sa.select(offers.c.notice_id).where(
        offers.c.response_deadline_utc < now,
        ~sa.exists().where(cases.c.notice_id == offers.c.notice_id),
    )
    # Preserve candidature history and its documents; purge only catalogue-only records.
    connection.execute(sa.delete(states).where(states.c.notice_id.in_(expired)))
    connection.execute(sa.delete(offers).where(offers.c.notice_id.in_(expired)))
    runs = sa.table(
        "sync_run",
        sa.column("kind", sa.String()),
        sa.column("status", sa.String()),
        sa.column("error", sa.String()),
        sa.column("finished_at", app.db.base.UTCDateTime()),
    )
    connection.execute(
        sa.update(runs)
        .where(runs.c.kind == "initial", runs.c.status == "running")
        .values(
            status="failed",
            error="initial_load_removed",
            finished_at=now,
        )
    )
    op.drop_table("sam_bootstrap")


def downgrade() -> None:
    op.create_table(
        "sam_bootstrap",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("window_from", sa.DATE(), nullable=False),
        sa.Column("window_to", sa.DATE(), nullable=False),
        sa.Column("next_date", sa.DATE(), nullable=False),
        sa.Column("snapshot_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("completed_at", app.db.base.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
