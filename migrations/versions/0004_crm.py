"""Сотрудники, клиенты, воронка, комментарии и деньги

Заявка перестаёт быть строкой в ленте и становится сделкой: у неё есть стадия, клиент,
ответственный, сумма, срок следующего действия и переписка внутри команды.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_CHECK = "stage in ('new', 'in_work', 'waiting', 'won', 'lost')"
STATUS_CHECK = "status in ('new', 'in_work', 'answered', 'closed')"
URGENCY_CHECK = "urgency is null or urgency in ('low', 'medium', 'high')"
AMOUNT_CHECK = "amount is null or amount >= 0"
ROLE_CHECK = "role in ('admin', 'manager', 'viewer')"

OLD_EVENTS = "kind in ('created', 'marked', 'mark_failed', 'status')"
NEW_EVENTS = (
    "kind in ('created', 'marked', 'mark_failed', 'stage', 'assigned', 'note', 'due', 'amount')"
)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _old_leads() -> sa.Table:
    """Схема leads до этой миграции.

    sqlite меняет колонки только пересозданием таблицы, а отражать её из базы нельзя:
    CHECK-ограничения оттуда не читаются, и пересозданная таблица тихо потеряла бы их.
    Поэтому описываем старый вид явно и отдаём alembic через copy_from.
    """
    meta = sa.MetaData()
    return sa.Table(
        "leads",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("client_name", sa.String(200)),
        sa.Column("client_contact", sa.String(200)),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("topic", sa.String(120)),
        sa.Column("urgency", sa.String(10)),
        sa.Column("draft_reply", sa.Text),
        sa.Column("status", sa.String(20), server_default="new", nullable=False),
        sa.Column("source", sa.String(60)),
        sa.Column("fingerprint", sa.String(64)),
        sa.CheckConstraint(STATUS_CHECK, name="ck_leads_status"),
        sa.CheckConstraint(URGENCY_CHECK, name="ck_leads_urgency"),
    )


def _old_events() -> sa.Table:
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
        sa.CheckConstraint(OLD_EVENTS, name="ck_lead_events_kind"),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("login", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="manager"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(ROLE_CHECK, name="ck_users_role"),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer, primary_key=True),
        # Нормализованный контакт: по нему повторные обращения склеиваются в человека.
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("name", sa.String(200)),
        sa.Column("contact", sa.String(200)),
        sa.Column("note", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_contacts_created_at", "contacts", ["created_at"])

    # Ограничение снимаем до того, как трогаем значения: пока старый CHECK на месте,
    # он не даст записать 'waiting', которого в прежнем наборе статусов не было.
    if _postgres():
        op.execute("alter table leads drop constraint ck_leads_status")
        op.alter_column("leads", "status", new_column_name="stage")
        op.add_column("leads", sa.Column("contact_id", sa.Integer))
        op.add_column("leads", sa.Column("assignee_id", sa.Integer))
        op.add_column("leads", sa.Column("amount", sa.Numeric(12, 2)))
        op.add_column("leads", sa.Column("due_at", sa.DateTime(timezone=True)))
        op.add_column("leads", sa.Column("lost_reason", sa.String(200)))
        op.create_foreign_key(
            "fk_leads_contact", "leads", "contacts", ["contact_id"], ["id"], ondelete="SET NULL"
        )
        op.create_foreign_key(
            "fk_leads_assignee", "leads", "users", ["assignee_id"], ["id"], ondelete="SET NULL"
        )
        op.execute(
            f"alter table leads add constraint ck_leads_amount check ({AMOUNT_CHECK}) not valid"
        )
        op.execute("alter table leads validate constraint ck_leads_amount")
    else:
        with op.batch_alter_table("leads", copy_from=_old_leads()) as batch:
            batch.drop_constraint("ck_leads_status", type_="check")
            batch.alter_column("status", new_column_name="stage")
            batch.add_column(sa.Column("contact_id", sa.Integer))
            batch.add_column(sa.Column("assignee_id", sa.Integer))
            batch.add_column(sa.Column("amount", sa.Numeric(12, 2)))
            batch.add_column(sa.Column("due_at", sa.DateTime(timezone=True)))
            batch.add_column(sa.Column("lost_reason", sa.String(200)))
            batch.create_check_constraint("ck_leads_amount", AMOUNT_CHECK)
            batch.create_foreign_key(
                "fk_leads_contact", "contacts", ["contact_id"], ["id"], ondelete="SET NULL"
            )
            batch.create_foreign_key(
                "fk_leads_assignee", "users", ["assignee_id"], ["id"], ondelete="SET NULL"
            )

    # Стадии воронки вместо статусов. Отвеченная заявка ждёт ответа клиента, закрытая
    # считается выигранной: обратного соответствия нет, поэтому перенос односторонний
    # и описан здесь, а не оставлен на догадку. Значения переписываем, пока новый CHECK
    # ещё не повешен, и только потом закрываем колонку ограничением.
    op.execute("update leads set stage = 'waiting' where stage = 'answered'")
    op.execute("update leads set stage = 'won' where stage = 'closed'")
    if _postgres():
        op.execute(f"alter table leads add constraint ck_leads_stage check ({STAGE_CHECK})")
    else:
        with op.batch_alter_table("leads") as batch:
            batch.create_check_constraint("ck_leads_stage", STAGE_CHECK)

    # Индексы пересоздаём после пересборки таблицы: batch переносит не всё.
    for name in ("ix_leads_status_created", "ix_leads_fingerprint", "ix_leads_source"):
        try:
            op.drop_index(name, table_name="leads")
        except Exception:  # noqa: BLE001 — на чистой базе индекса может и не быть
            pass
    op.create_index("ix_leads_stage_created", "leads", ["stage", sa.text("created_at DESC")])
    op.create_index("ix_leads_fingerprint", "leads", ["fingerprint"])
    op.create_index("ix_leads_source", "leads", ["source"])
    op.create_index("ix_leads_contact_id", "leads", ["contact_id"])
    op.create_index("ix_leads_assignee_id", "leads", ["assignee_id"])
    op.create_index("ix_leads_due_at", "leads", ["due_at"])

    op.create_table(
        "notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "lead_id", sa.Integer, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_notes_lead_created", "notes", ["lead_id", "created_at"])

    # Событий стало больше: сменил стадию, назначил ответственного, оставил комментарий.
    # Порядок тот же: сняли ограничение, переписали значения, повесили новое.
    if _postgres():
        op.execute("alter table lead_events drop constraint ck_lead_events_kind")
        op.add_column("lead_events", sa.Column("user_id", sa.Integer))
        op.create_foreign_key(
            "fk_lead_events_user", "lead_events", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
    else:
        with op.batch_alter_table("lead_events", copy_from=_old_events()) as batch:
            batch.drop_constraint("ck_lead_events_kind", type_="check")
            batch.add_column(sa.Column("user_id", sa.Integer))
            batch.create_foreign_key(
                "fk_lead_events_user", "users", ["user_id"], ["id"], ondelete="SET NULL"
            )
        op.create_index("ix_lead_events_lead_created", "lead_events", ["lead_id", "created_at"])

    op.execute("update lead_events set kind = 'stage' where kind = 'status'")
    if _postgres():
        op.execute(
            f"alter table lead_events add constraint ck_lead_events_kind check ({NEW_EVENTS})"
        )
    else:
        with op.batch_alter_table("lead_events") as batch:
            batch.create_check_constraint("ck_lead_events_kind", NEW_EVENTS)


def _leads_after() -> sa.Table:
    """Схема leads после этой миграции — нужна откату по той же причине, что и upgrade:
    пересоздавая таблицу, sqlite должен знать про CHECK, которые предстоит снять."""
    meta = sa.MetaData()
    return sa.Table(
        "leads",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("client_name", sa.String(200)),
        sa.Column("client_contact", sa.String(200)),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("topic", sa.String(120)),
        sa.Column("urgency", sa.String(10)),
        sa.Column("draft_reply", sa.Text),
        sa.Column("stage", sa.String(20), server_default="new", nullable=False),
        sa.Column("source", sa.String(60)),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column("contact_id", sa.Integer),
        sa.Column("assignee_id", sa.Integer),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("lost_reason", sa.String(200)),
        sa.CheckConstraint(URGENCY_CHECK, name="ck_leads_urgency"),
        sa.CheckConstraint(STAGE_CHECK, name="ck_leads_stage"),
        sa.CheckConstraint(AMOUNT_CHECK, name="ck_leads_amount"),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_leads_contact", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.id"], name="fk_leads_assignee", ondelete="SET NULL"
        ),
    )


def _events_after() -> sa.Table:
    meta = sa.MetaData()
    return sa.Table(
        "lead_events",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "lead_id", sa.Integer, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.Integer),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(NEW_EVENTS, name="ck_lead_events_kind"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_lead_events_user", ondelete="SET NULL"
        ),
    )


def downgrade() -> None:
    # Комментарии некуда девать при откате: в схеме 0003 их не существует.
    op.drop_table("notes")

    for name in (
        "ix_leads_due_at",
        "ix_leads_assignee_id",
        "ix_leads_contact_id",
        "ix_leads_source",
        "ix_leads_fingerprint",
        "ix_leads_stage_created",
    ):
        op.drop_index(name, table_name="leads")

    # Сначала снимаем ограничения и только потом переписываем значения: пока CHECK на
    # месте, он не даст записать 'closed', которого в новом наборе стадий нет.
    if _postgres():
        op.drop_constraint("fk_lead_events_user", "lead_events", type_="foreignkey")
        op.drop_column("lead_events", "user_id")
        op.execute("alter table lead_events drop constraint ck_lead_events_kind")

        op.drop_constraint("fk_leads_assignee", "leads", type_="foreignkey")
        op.drop_constraint("fk_leads_contact", "leads", type_="foreignkey")
        op.execute("alter table leads drop constraint ck_leads_amount")
        op.execute("alter table leads drop constraint ck_leads_stage")
        for column in ("lost_reason", "due_at", "amount", "assignee_id", "contact_id"):
            op.drop_column("leads", column)
        op.alter_column("leads", "stage", new_column_name="status")
    else:
        with op.batch_alter_table("lead_events", copy_from=_events_after()) as batch:
            batch.drop_constraint("ck_lead_events_kind", type_="check")
            batch.drop_column("user_id")
        op.create_index("ix_lead_events_lead_created", "lead_events", ["lead_id", "created_at"])

        with op.batch_alter_table("leads", copy_from=_leads_after()) as batch:
            batch.drop_constraint("ck_leads_stage", type_="check")
            batch.drop_constraint("ck_leads_amount", type_="check")
            for column in ("lost_reason", "due_at", "amount", "assignee_id", "contact_id"):
                batch.drop_column(column)
            batch.alter_column("stage", new_column_name="status")

    # Выигранная и проигранная сделка в старой схеме обе просто «закрыта»: разницу
    # откат теряет, и это не оплошность, а свойство отката — старой колонке негде её хранить.
    op.execute("update leads set status = 'answered' where status = 'waiting'")
    op.execute("update leads set status = 'closed' where status in ('won', 'lost')")
    op.execute(
        "update lead_events set kind = 'status' "
        "where kind in ('stage', 'assigned', 'note', 'due', 'amount')"
    )

    # Ограничения возвращаем поверх уже приведённых значений.
    if _postgres():
        op.execute(f"alter table leads add constraint ck_leads_status check ({STATUS_CHECK})")
        op.execute(
            f"alter table lead_events add constraint ck_lead_events_kind check ({OLD_EVENTS})"
        )
    else:
        with op.batch_alter_table("leads") as batch:
            batch.create_check_constraint("ck_leads_status", STATUS_CHECK)
        with op.batch_alter_table("lead_events") as batch:
            batch.create_check_constraint("ck_lead_events_kind", OLD_EVENTS)

    op.create_index("ix_leads_status_created", "leads", ["status", sa.text("created_at DESC")])
    op.create_index("ix_leads_fingerprint", "leads", ["fingerprint"])
    op.create_index("ix_leads_source", "leads", ["source"])

    op.drop_table("contacts")
    op.drop_table("users")
