"""Источник заявки и индекс под ленту админки

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Колонка nullable намеренно: в таблице уже лежат заявки, и у них источника нет.
    # NOT NULL здесь потребовал бы DEFAULT и переписывания всех существующих строк —
    # на большой таблице это блокировка на минуты.
    op.add_column("leads", sa.Column("source", sa.String(length=60), nullable=True))
    op.create_index("ix_leads_status_created", "leads", ["status", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_leads_status_created", table_name="leads")
    op.drop_column("leads", "source")
