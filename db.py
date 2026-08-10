"""Подключение к базе и операции над заявками.

Движок один, а баз две: локально sqlite-файл, в облаке Postgres. Разницу держит на себе
SQLAlchemy, поэтому в коде выше про это знать не нужно — меняется только DATABASE_URL.

Всё асинхронное: дальше в продукте будут вебсокеты и агент, который сам ведёт переписку,
и синхронный драйвер там начал бы блокировать цикл событий на каждом запросе к базе.
"""

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from models import Lead, LeadEvent

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")

# Окно, внутри которого одинаковая заявка считается повтором, а не новой.
DEDUPE_SECONDS = int(os.environ.get("DEDUPE_SECONDS", 300))


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Amvera и Postgres-хостинги отдают строку в формате postgresql://, а нам нужен
        # асинхронный драйвер. Подставляем его сами, чтобы не ловить это на деплое.
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    os.makedirs(DATA_DIR, exist_ok=True)
    return f"sqlite+aiosqlite:///{os.path.join(DATA_DIR, 'crm.db')}"


engine = create_async_engine(database_url(), future=True)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def fingerprint(text: str, contact: str | None) -> str:
    """Отпечаток заявки для отлова повторов.

    Регистр и лишние пробелы схлопываем: человек, отправивший форму дважды, второй раз
    вполне мог поправить перенос строки — для нас это та же заявка.
    """
    normal = re.sub(r"\s+", " ", f"{text} {contact or ''}").strip().lower()
    return hashlib.sha256(normal.encode()).hexdigest()


async def add_lead(
    client_name: str | None,
    client_contact: str | None,
    text: str,
    topic: str | None = None,
    urgency: str | None = None,
    draft_reply: str | None = None,
    source: str | None = None,
) -> tuple[Lead, bool]:
    """Сохранить заявку. Второе значение — новая она или повтор уже сохранённой.

    Повтор случается сам собой: человек жмёт «отправить» дважды, форма ретраится по
    таймауту, чужой сервис не дождался нашего ответа. Разметка каждого такого дубля —
    ещё один платный вызов модели и ещё одна карточка, которую человек прочитает зря.
    """
    mark = fingerprint(text, client_contact)
    async with Session() as session:
        since = datetime.now(UTC) - timedelta(seconds=DEDUPE_SECONDS)
        twin = await session.execute(
            select(Lead)
            .where(Lead.fingerprint == mark, Lead.created_at >= since)
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
        existing = twin.scalar_one_or_none()
        if existing is not None:
            return existing, False

        lead = Lead(
            client_name=client_name,
            client_contact=client_contact,
            text=text,
            topic=topic,
            urgency=urgency,
            draft_reply=draft_reply,
            source=source,
            fingerprint=mark,
        )
        lead.events.append(LeadEvent(kind="created", note=source))
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
    return lead, True


async def get_all_leads(
    limit: int = 200,
    status: str | None = None,
    urgency: str | None = None,
    source: str | None = None,
) -> list[Lead]:
    query = select(Lead).order_by(Lead.created_at.desc()).limit(limit)
    if status:
        query = query.where(Lead.status == status)
    if urgency:
        query = query.where(Lead.urgency == urgency)
    if source:
        query = query.where(Lead.source == source)
    async with Session() as session:
        rows = await session.execute(query)
        return list(rows.scalars())


async def count_by_status(source: str | None = None) -> dict[str, int]:
    """Счётчики для фильтров админки: сколько заявок в каждом статусе.

    Считает база группировкой, а не питон перебором ленты: лента ограничена лимитом,
    и счётчик по ней показывал бы «сколько влезло на экран», а не сколько есть.
    """
    query = select(Lead.status, func.count()).group_by(Lead.status)
    if source:
        query = query.where(Lead.source == source)
    async with Session() as session:
        rows = await session.execute(query)
        return {status: count for status, count in rows}


async def get_lead(lead_id: int, with_events: bool = False) -> Lead | None:
    async with Session() as session:
        if not with_events:
            return await session.get(Lead, lead_id)
        # selectinload, а не ленивая подгрузка: за пределами сессии обращение к events
        # упало бы, а сессия закрывается на выходе из блока.
        rows = await session.execute(
            select(Lead).where(Lead.id == lead_id).options(selectinload(Lead.events))
        )
        return rows.scalar_one_or_none()


async def set_status(lead_id: int, status: str) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        if lead.status != status:
            lead.status = status
            session.add(LeadEvent(lead_id=lead_id, kind="status", note=status))
        await session.commit()
        await session.refresh(lead)
    return lead


async def set_markup(
    lead_id: int, topic: str | None, urgency: str | None, draft_reply: str | None
) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.topic, lead.urgency, lead.draft_reply = topic, urgency, draft_reply
        session.add(LeadEvent(lead_id=lead_id, kind="marked", note=urgency))
        await session.commit()
        await session.refresh(lead)
    return lead


async def mark_failed(lead_id: int, reason: str) -> None:
    """Модель не ответила. Заявка на месте, но в истории это должно остаться:
    иначе непонятно, почему карточка без темы."""
    async with Session() as session:
        session.add(LeadEvent(lead_id=lead_id, kind="mark_failed", note=reason[:200]))
        await session.commit()
