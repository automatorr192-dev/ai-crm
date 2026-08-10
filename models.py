"""Схема данных.

Схема описана моделями, а не строками CREATE TABLE: alembic умеет сам сравнивать
«что должно быть» с «что в базе» и писать миграции.

Две таблицы: заявка и события по ней. История нужна не для красоты — без неё нельзя
ответить на вопрос «сколько времени заявка ждала ответа», а это главная метрика продукта.
"""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Urgency = Literal["low", "medium", "high"]
Status = Literal["new", "in_work", "answered", "closed"]

STATUSES: tuple[str, ...] = ("new", "in_work", "answered", "closed")
URGENCIES: tuple[str, ...] = ("low", "medium", "high")

# Виды событий по заявке.
EVENTS: tuple[str, ...] = ("created", "marked", "mark_failed", "status")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{v}'" for v in values) + ")"


class Base(DeclarativeBase):
    pass


class Lead(Base):
    """Заявка.

    Ограничения стоят в базе, а не только в pydantic: приложений вокруг базы со временем
    становится больше одного (админка, бот, скрипт разбора почты), и проверка в коде
    защищает ровно то приложение, где написана. CHECK защищает данные.

    CHECK, а не ENUM-тип: добавить значение в CHECK — переписать одно ограничение,
    добавить в postgres-ENUM — ALTER TYPE, который до 12-й версии вообще не откатывался.
    """

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_leads_status"),
        CheckConstraint(f"urgency is null or {_in('urgency', URGENCIES)}", name="ck_leads_urgency"),
        # Админка листает свежие сверху и фильтрует по статусу.
        Index("ix_leads_status_created", "status", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Меняется при смене статуса и при разметке. Отсюда считается время до ответа.
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

    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    # Откуда пришла заявка: сайт, квиз, бот. Нужно, чтобы считать, что работает.
    source: Mapped[str | None] = mapped_column(String(60), index=True)

    # Отпечаток текста и контакта. Человек жмёт «отправить» дважды, форма ретраится по
    # таймауту — в базе появляются близнецы. Уникальным индекс быть не может: та же
    # заявка от того же человека через месяц законна, поэтому дубль ловится по окну.
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)

    events: Mapped[list["LeadEvent"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadEvent.created_at"
    )

    def __repr__(self) -> str:
        return f"<Lead {self.id} {self.status} {self.topic!r}>"

    @property
    def created_local(self) -> str:
        moment = self.created_at or datetime.now(UTC)
        return moment.strftime("%H:%M")


class LeadEvent(Base):
    """Что происходило с заявкой.

    Статус в самой заявке отвечает на вопрос «как сейчас», события — на вопрос «как дошли».
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
    kind: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped[Lead] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<LeadEvent {self.lead_id} {self.kind}>"

    @property
    def created_local(self) -> str:
        moment = self.created_at or datetime.now(UTC)
        return moment.strftime("%H:%M")
