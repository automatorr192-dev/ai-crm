"""Схема данных.

Схема описана моделями, а не строками CREATE TABLE: alembic умеет сам сравнивать
«что должно быть» с «что в базе» и писать миграции.

Пять таблиц. Заявка — центр, вокруг неё клиент (чтобы повторные обращения одного
человека собирались вместе), сотрудник (кто ведёт), комментарии (что решили) и события
(как дошли до нынешнего состояния).
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Urgency = Literal["low", "medium", "high"]
Stage = Literal["new", "in_work", "waiting", "won", "lost"]
Role = Literal["admin", "manager", "viewer"]

# Воронка. Порядок здесь — порядок колонок на доске, менять его значит менять экран.
STAGES: tuple[str, ...] = ("new", "in_work", "waiting", "won", "lost")
# Стадии, после которых заявка больше не в работе: их не считают в «активных деньгах».
CLOSED_STAGES: tuple[str, ...] = ("won", "lost")

URGENCIES: tuple[str, ...] = ("low", "medium", "high")
ROLES: tuple[str, ...] = ("admin", "manager", "viewer")

# Виды событий по заявке.
EVENTS: tuple[str, ...] = (
    "created",
    "marked",
    "mark_failed",
    "stage",
    "assigned",
    "note",
    "due",
    "amount",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{v}'" for v in values) + ")"


class Base(DeclarativeBase):
    pass


class User(Base):
    """Сотрудник.

    Пароль лежит хешем: базу выгружают для бэкапа, отдают подрядчику, теряют вместе
    с ноутбуком — и открытый пароль в ней означал бы, что вместе с базой уходят и все
    остальные сервисы, где человек его повторил.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="manager", server_default="manager")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<User {self.login} {self.role}>"

    @property
    def can_edit(self) -> bool:
        return self.role in ("admin", "manager")


class Contact(Base):
    """Клиент.

    Заявка приходит от человека, а человек обращается не один раз: сначала спросил цену,
    через месяц вернулся. Пока заявки лежали плоским списком, эта связь терялась, и
    менеджер каждый раз начинал разговор с нуля.
    """

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    # Нормализованный контакт: по нему заявки и склеиваются в одного человека.
    key: Mapped[str] = mapped_column(String(200), unique=True)
    contact: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    leads: Mapped[list["Lead"]] = relationship(back_populates="contact")

    def __repr__(self) -> str:
        return f"<Contact {self.id} {self.key}>"

    @property
    def title(self) -> str:
        return self.name or self.contact or "Без имени"


class Lead(Base):
    """Заявка, она же сделка в воронке.

    Ограничения стоят в базе, а не только в pydantic: приложений вокруг базы со временем
    становится больше одного (панель, бот, скрипт разбора почты), и проверка в коде
    защищает ровно то приложение, где написана. CHECK защищает данные.

    CHECK, а не ENUM-тип: добавить значение в CHECK — переписать одно ограничение,
    добавить в postgres-ENUM — ALTER TYPE, который до 12-й версии вообще не откатывался.
    """

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(_in("stage", STAGES), name="ck_leads_stage"),
        CheckConstraint(f"urgency is null or {_in('urgency', URGENCIES)}", name="ck_leads_urgency"),
        # Деньги не бывают отрицательными, а минус в сумме ломает любой отчёт молча.
        CheckConstraint("amount is null or amount >= 0", name="ck_leads_amount"),
        # Доска и список читают свежие сверху внутри стадии.
        Index("ix_leads_stage_created", "stage", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client_name: Mapped[str | None] = mapped_column(String(200))
    client_contact: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)

    # Заполняется моделью и может остаться пустым: заявку сохраняем, даже когда ИИ лежит.
    topic: Mapped[str | None] = mapped_column(String(120))
    urgency: Mapped[str | None] = mapped_column(String(10))
    draft_reply: Mapped[str | None] = mapped_column(Text)

    stage: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    source: Mapped[str | None] = mapped_column(String(60), index=True)

    # Отпечаток текста и контакта. Человек жмёт «отправить» дважды, форма ретраится по
    # таймауту — в базе появляются близнецы. Уникальным индекс быть не может: та же
    # заявка от того же человека через месяц законна, поэтому дубль ловится по окну.
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)

    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    # Ответственный уходит в отпуск и увольняется, а сделки остаются: SET NULL, чтобы
    # удаление сотрудника не уносило с собой историю работы.
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # Numeric, а не float: деньги в двоичной дробной арифметике теряют копейки, и сумма
    # воронки перестаёт сходиться с суммой сделок.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Ближайшее действие: перезвонить, отправить смету. Просроченное подсвечивается.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lost_reason: Mapped[str | None] = mapped_column(String(200))

    contact: Mapped[Contact | None] = relationship(back_populates="leads")
    assignee: Mapped[User | None] = relationship()
    events: Mapped[list["LeadEvent"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadEvent.created_at"
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Note.created_at.desc()"
    )

    def __repr__(self) -> str:
        return f"<Lead {self.id} {self.stage} {self.topic!r}>"

    @property
    def created_local(self) -> str:
        moment = self.created_at or datetime.now(UTC)
        return moment.strftime("%H:%M")

    @property
    def title(self) -> str:
        return self.client_name or self.client_contact or "Без имени"

    @property
    def overdue(self) -> bool:
        if self.due_at is None or self.stage in CLOSED_STAGES:
            return False
        due = self.due_at if self.due_at.tzinfo else self.due_at.replace(tzinfo=UTC)
        return due < datetime.now(UTC)


class Note(Base):
    """Комментарий сотрудника к заявке: «созвонились, ждёт смету до пятницы»."""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_lead_created", "lead_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped[Lead] = relationship(back_populates="notes")
    author: Mapped[User | None] = relationship()

    @property
    def created_local(self) -> str:
        moment = self.created_at or datetime.now(UTC)
        return moment.strftime("%d.%m %H:%M")


class LeadEvent(Base):
    """Что происходило с заявкой.

    Стадия отвечает на вопрос «как сейчас», события — на вопрос «как дошли».
    Без них нельзя ни разобрать спор с клиентом, ни посчитать скорость ответа.
    """

    __tablename__ = "lead_events"
    __table_args__ = (
        CheckConstraint(_in("kind", EVENTS), name="ck_lead_events_kind"),
        Index("ix_lead_events_lead_created", "lead_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # ondelete на стороне базы: удалять заявку и оставлять её события — мусор,
    # а полагаться на то, что чистить придёт python, нельзя.
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped[Lead] = relationship(back_populates="events")
    author: Mapped[User | None] = relationship()

    def __repr__(self) -> str:
        return f"<LeadEvent {self.lead_id} {self.kind}>"

    @property
    def created_local(self) -> str:
        moment = self.created_at or datetime.now(UTC)
        return moment.strftime("%d.%m %H:%M")
