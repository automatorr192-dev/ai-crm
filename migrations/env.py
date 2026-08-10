"""Точка входа Alembic.

Работает поверх того же асинхронного движка, что и приложение, и берёт адрес базы из
DATABASE_URL — чтобы миграции на любой машине ехали ровно туда же, куда ходит код.
"""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from db import database_url
from models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # sqlite не умеет ALTER TABLE почти ничего: batch пересоздаёт таблицу под капотом.
        # Без этого миграция, работающая на Postgres, падает на локальной базе.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async() -> None:
    engine = create_async_engine(database_url(), future=True)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
        await connection.commit()
    await engine.dispose()


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_async())
