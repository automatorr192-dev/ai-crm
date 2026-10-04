"""Агент-квалификатор: переписка с клиентом и отметка «горячий»

Заявка перестаёт быть одним сообщением. Клиент продолжает разговор в Telegram, ассистент
уточняет недостающее, а менеджер получает лида уже с задачей, сроком, бюджетом и
назначенным созвоном.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Новые колонки необязательные или со значением по умолчанию: ADD COLUMN без
    # пересоздания таблицы работает и на sqlite, и на Postgres, и не трогает CHECK-и leads.
    op.add_column(
        "leads", sa.Column("hot", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("leads", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("leads", sa.Column("qualification", sa.Text(), nullable=True))
    op.create_index("ix_leads_chat_id", "leads", ["chat_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "lead_id",
            sa.Integer,
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("role in ('client', 'agent', 'manager')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_lead_created", "messages", ["lead_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_lead_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_leads_chat_id", table_name="leads")
    # Не через batch: на sqlite он пересоздал бы leads по отражённой схеме, а CHECK-и
    # оттуда не читаются и потерялись бы. DROP COLUMN sqlite умеет с версии 3.35.
    for column in ("qualification", "chat_id", "hot"):
        op.execute(f"alter table leads drop column {column}")
