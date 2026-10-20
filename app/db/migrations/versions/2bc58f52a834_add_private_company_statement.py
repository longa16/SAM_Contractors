"""add private company statement

Revision ID: 2bc58f52a834
Revises: 4253aa59a16d
"""

import sqlalchemy as sa
from alembic import op

revision = "2bc58f52a834"
down_revision = "4253aa59a16d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company", sa.Column("statement", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "statement")
