import asyncio

import app as app_module
import db
from tests.test_app import auth, client


async def test_demo_is_open_without_password(database):
    """Витрину смотрят посторонние: пароль тут был бы стеной перед демо."""
    assert client.get("/live").status_code == 200


async def test_admin_still_needs_password(database):
    assert client.get("/").status_code == 401
    assert client.get("/", headers=auth("admin", "secret")).status_code == 200


async def test_quiz_saves_lead(database, monkeypatch):
    monkeypatch.setattr(app_module, "enrich", lambda lead_id: asyncio.sleep(0))
    monkeypatch.setattr(app_module, "_demo", {})

    response = client.post(
        "/api/quiz", json={"text": "Заявки теряются в директе", "source": "демо"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "new"
    saved = await db.get_all_leads()
    assert saved[0].source == "демо"


async def test_demo_has_an_hourly_ceiling(database, monkeypatch):
    """Каждая заявка это оплаченный вызов модели, а страница открыта всем."""
    monkeypatch.setattr(app_module, "enrich", lambda lead_id: asyncio.sleep(0))
    monkeypatch.setattr(app_module, "_demo", {})
    monkeypatch.setattr(app_module, "DEMO_PER_HOUR", 2)

    for _ in range(2):
        assert client.post("/api/quiz", json={"text": "текст заявки"}).status_code == 200
    assert client.post("/api/quiz", json={"text": "текст заявки"}).status_code == 429


async def test_empty_text_is_rejected(database):
    assert client.post("/api/quiz", json={"text": ""}).status_code == 422


async def test_card_carries_everything_the_screen_draws(database):
    lead = await db.add_lead("Полина", "@p", "текст", "тема", "high", "черновик", "демо")
    shape = app_module.card(lead)
    assert set(shape) == {
        "id",
        "name",
        "contact",
        "text",
        "topic",
        "urgency",
        "draft",
        "status",
        "time",
    }
    assert shape["draft"] == "черновик"


async def test_urgency_reaches_the_page_in_russian(database):
    await db.add_lead("Полина", "@p", "текст", "тема", "high", "черновик", "демо")
    page = client.get("/live").text
    assert "срочность: высокая" in page
    assert "срочность: high" not in page
