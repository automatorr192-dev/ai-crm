import os

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"
os.environ["WEBHOOK_SECRET"] = "shhh"

import asyncio  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

import db  # noqa: E402


@pytest.fixture
async def database(tmp_path, monkeypatch):
    """Свежая база на каждый тест, поднятая теми же миграциями, что и прод.

    create_all по моделям был бы быстрее, но тогда тесты проверяли бы схему из кода, а не
    ту, которую реально накатывает alembic — и сломанная миграция доезжала бы до прода.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url, future=True)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "Session", async_sessionmaker(engine, expire_on_commit=False))

    config = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    # env.py поднимает свой asyncio.run — из уже работающего цикла это запрещено,
    # поэтому миграции едут в потоке. Ровно так же они запускаются на старте приложения.
    await asyncio.to_thread(command.upgrade, config, "head")

    yield
    await engine.dispose()
