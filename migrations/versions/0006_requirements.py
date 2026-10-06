"""Метки рекламы, согласие на ПД, напоминания, «только свои», внешняя CRM, файлы

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EVENTS = (
    "kind in ('created', 'marked', 'mark_failed', 'stage', 'assigned', 'note', 'due', 'amount')"
)
NEW_EVENTS = (
    "kind in ('created', 'marked', 'mark_failed', 'stage', 'assigned', 'note', 'due', 'amount',"
    " 'synced', 'sync_failed', 'file')"
)
LEAD_COLUMNS = (
    ("utm_source", sa.String(120)),
    ("utm_medium", sa.String(120)),
    ("utm_campaign", sa.String(120)),
    ("consent_at", sa.DateTime(timezone=True)),
    ("reminded_at", sa.DateTime(timezone=True)),
    ("external_id", sa.String(80)),
)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _events(check: str) -> sa.Table:
    meta = sa.MetaData()
    return sa.Table(
        "lead_events",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "lead_id", sa.Integer, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_id", sa.Integer),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_lead_events_user", ondelete="SET NULL"
        ),
        sa.CheckConstraint(check, name="ck_lead_events_kind"),
    )


def _swap_events_check(old: str, new: str) -> None:
    if _postgres():
        op.execute("alter table lead_events drop constraint ck_lead_events_kind")
        op.execute(
            f"alter table lead_events add constraint ck_lead_events_kind check ({new}) not valid"
        )
        op.execute("alter table lead_events validate constraint ck_lead_events_kind")
        return
    op.drop_index("ix_lead_events_lead_created", table_name="lead_events")
    with op.batch_alter_table("lead_events", copy_from=_events(old)) as batch:
        batch.drop_constraint("ck_lead_events_kind", type_="check")
        batch.create_check_constraint("ck_lead_events_kind", new)
    op.create_index("ix_lead_events_lead_created", "lead_events", ["lead_id", "created_at"])


def upgrade() -> None:
    for name, kind in LEAD_COLUMNS:
        op.add_column("leads", sa.Column(name, kind, nullable=True))
    op.create_index("ix_leads_utm_source", "leads", ["utm_source"])
    op.add_column(
        "users", sa.Column("own_only", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    _swap_events_check(OLD_EVENTS, NEW_EVENTS)
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "lead_id", sa.Integer, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_attachments_lead_id", "attachments", ["lead_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_lead_id", table_name="attachments")
    op.drop_table("attachments")
    op.execute("delete from lead_events where kind in ('synced', 'sync_failed', 'file')")
    _swap_events_check(NEW_EVENTS, OLD_EVENTS)
    op.execute("alter table users drop column own_only")
    op.drop_index("ix_leads_utm_source", table_name="leads")
    for name, _ in reversed(LEAD_COLUMNS):
        op.execute(f"alter table leads drop column {name}")
