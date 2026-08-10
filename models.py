"""Схема данных.

Одна таблица, но описанная моделью, а не строкой CREATE TABLE: со схемой в коде
alembic умеет сам сравнивать «что должно быть» с «что в базе» и писать миграции.
"""

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Urgency = Literal["low", "medium", "high"]
Status = Literal["new", "in_work", "answered", "closed"]

STATUSES: tuple[str, ...] = ("new", "in_work", "answered", "closed")


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    client_name: Mapped[str | None] = mapped_column(String(200))
    client_contact: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)

    # Заполняется моделью и может остаться пустым: заявку сохраняем, даже когда ИИ лежит.
    topic: Mapped[str | None] = mapped_column(String(120))
    urgency: Mapped[str | None] = mapped_column(String(10))
    draft_reply: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    # Откуда пришла заявка: квиз, форма на сайте, бот. Нужно, чтобы считать, что работает.
    source: Mapped[str | None] = mapped_column(String(60))

    def __repr__(self) -> str:
        return f"<Lead {self.id} {self.status} {self.topic!r}>"

    @property
    def created_local(self) -> str:
        moment = self.created_at or datetime.now(timezone.utc)
        return moment.strftime("%H:%M")


# Админка всегда листает свежие сверху и часто фильтрует по статусу.
Index("ix_leads_status_created", Lead.status, Lead.created_at.desc())
