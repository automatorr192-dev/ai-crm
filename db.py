"""Подключение к базе и операции над заявками.

Движок один, а баз две: локально sqlite-файл, в облаке Postgres. Разницу держит на себе
SQLAlchemy, поэтому в коде выше про это знать не нужно — меняется только DATABASE_URL.

Всё асинхронное: дальше в продукте будут вебсокеты и агент, который сам ведёт переписку,
и синхронный драйвер там начал бы блокировать цикл событий на каждом запросе к базе.
"""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Lead

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")


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


async def add_lead(
    client_name: str | None,
    client_contact: str | None,
    text: str,
    topic: str | None = None,
    urgency: str | None = None,
    draft_reply: str | None = None,
    source: str | None = None,
) -> Lead:
    lead = Lead(
        client_name=client_name,
        client_contact=client_contact,
        text=text,
        topic=topic,
        urgency=urgency,
        draft_reply=draft_reply,
        source=source,
    )
    async with Session() as session:
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
    return lead


async def get_all_leads(limit: int = 200) -> list[Lead]:
    async with Session() as session:
        rows = await session.execute(select(Lead).order_by(Lead.created_at.desc()).limit(limit))
        return list(rows.scalars())


async def set_status(lead_id: int, status: str) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.status = status
        await session.commit()
        await session.refresh(lead)
    return lead


async def get_lead(lead_id: int) -> Lead | None:
    async with Session() as session:
        return await session.get(Lead, lead_id)


async def set_markup(
    lead_id: int, topic: str | None, urgency: str | None, draft_reply: str | None
) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.topic, lead.urgency, lead.draft_reply = topic, urgency, draft_reply
        await session.commit()
        await session.refresh(lead)
    return lead
