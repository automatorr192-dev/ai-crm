import os

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret-secret"
os.environ["WEBHOOK_SECRET"] = "shhh"
os.environ["SESSION_SECRET"] = "test-session-secret"

import asyncio  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
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


@pytest.fixture
def client():
    """Гость: не вошёл, ничего не видит."""
    return TestClient(app_module.app)


async def _sign_in(login: str, password: str) -> TestClient:
    session = TestClient(app_module.app)
    response = session.post(
        "/login", data={"login": login, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, "вход не удался"
    return session


@pytest.fixture
async def boss(database):
    """Владелец: может всё."""
    await db.create_user("boss", "Владелец", "very-secret", role="admin")
    return await _sign_in("boss", "very-secret")


@pytest.fixture
async def manager(database):
    await db.create_user("irina", "Ирина", "very-secret", role="manager")
    return await _sign_in("irina", "very-secret")


@pytest.fixture
async def watcher(database):
    """Наблюдатель: видит работу, но не вмешивается."""
    await db.create_user("guest", "Гость", "very-secret", role="viewer")
    return await _sign_in("guest", "very-secret")


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    """Ни один тест не должен ходить в модель за деньги."""
    monkeypatch.setattr(app_module, "enrich", lambda lead_id: asyncio.sleep(0))
    monkeypatch.setattr(app_module, "_seen", {})
    monkeypatch.setattr(app_module, "_failures", {})
