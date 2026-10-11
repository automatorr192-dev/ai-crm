"""Настройки компании: план продаж на месяц

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
