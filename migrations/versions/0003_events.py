"""История событий, отпечаток для дедупликации и ограничения на значения

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CHECK = "status in ('new', 'in_work', 'answered', 'closed')"
URGENCY_CHECK = "urgency is null or urgency in ('low', 'medium', 'high')"
EVENT_CHECK = "kind in ('created', 'marked', 'mark_failed', 'status')"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _check(table: str, name: str, condition: str) -> None:
    """Ограничение на значения колонки.

    На Postgres добавляем в два шага. Обычный ADD CONSTRAINT читает всю таблицу под
    блокировкой на запись: на живой базе с сотнями тысяч заявок приём встанет на это
    время. NOT VALID вешает правило на новые строки мгновенно, а VALIDATE проверяет
    старые уже без блокировки записи.
    """
    if _postgres():
        op.execute(f"alter table {table} add constraint {name} check ({condition}) not valid")
        op.execute(f"alter table {table} validate constraint {name}")
    else:
        with op.batch_alter_table(table) as batch:
            batch.create_check_constraint(name, condition)


def upgrade() -> None:
    # sqlite добавляет ограничения только пересозданием таблицы, а индекс по выражению
    # (created_at DESC) при этом не переносится. Снимаем его руками и вернём в конце.
    if not _postgres():
        op.drop_index("ix_leads_status_created", table_name="leads")

    with op.batch_alter_table("leads") as batch:
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("fingerprint", sa.String(length=64), nullable=True))

    # У старых заявок момента правки нет: считаем, что их не трогали с момента создания.
    op.execute("update leads set updated_at = created_at where updated_at is null")

    with op.batch_alter_table("leads") as batch:
        batch.alter_column("updated_at", nullable=False)

    _check("leads", "ck_leads_status", STATUS_CHECK)
    _check("leads", "ck_leads_urgency", URGENCY_CHECK)

    op.create_index("ix_leads_fingerprint", "leads", ["fingerprint"])
    op.create_index("ix_leads_source", "leads", ["source"])
    if not _postgres():
        op.create_index("ix_leads_status_created", "leads", ["status", sa.text("created_at DESC")])

    op.create_table(
        "lead_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        # ondelete на стороне базы: заявку могут удалить не из нашего кода.
        sa.Column(
            "lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(EVENT_CHECK, name="ck_lead_events_kind"),
    )
    op.create_index("ix_lead_events_lead_created", "lead_events", ["lead_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_lead_events_lead_created", table_name="lead_events")
    op.drop_table("lead_events")

    op.drop_index("ix_leads_source", table_name="leads")
    op.drop_index("ix_leads_fingerprint", table_name="leads")
    if not _postgres():
        op.drop_index("ix_leads_status_created", table_name="leads")

    if _postgres():
        op.execute("alter table leads drop constraint ck_leads_urgency")
        op.execute("alter table leads drop constraint ck_leads_status")
        op.drop_column("leads", "fingerprint")
        op.drop_column("leads", "updated_at")
    else:
        with op.batch_alter_table("leads") as batch:
            batch.drop_constraint("ck_leads_urgency", type_="check")
            batch.drop_constraint("ck_leads_status", type_="check")
            batch.drop_column("fingerprint")
            batch.drop_column("updated_at")
        op.create_index("ix_leads_status_created", "leads", ["status", sa.text("created_at DESC")])
