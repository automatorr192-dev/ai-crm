"""Черновик коммерческого предложения от модели

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("offer_draft", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "offer_draft")
