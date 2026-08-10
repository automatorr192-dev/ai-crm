"""Публичная форма и открытая витрина.

Форма живёт на другом домене и ничего подписать не может, поэтому здесь проверяется
не подпись, а то, что открытый вход не превращается в дыру: потолок с адреса, ловушка
для ботов и полная изоляция боевых заявок от витрины.
"""

import asyncio

import app as app_module
import db
from tests.test_app import auth, client

SITE = app_module.PUBLIC_SOURCE


def _no_ai(monkeypatch):
    monkeypatch.setattr(app_module, "enrich", lambda lead_id: asyncio.sleep(0))
    monkeypatch.setattr(app_module, "_seen", {})


async def test_form_lead_is_accepted_without_signature(database, monkeypatch):
    _no_ai(monkeypatch)
    response = client.post("/api/public/lead", json={"text": "Заявки теряются в директе"})

    assert response.status_code == 200
    assert response.json()["status"] == "new"
    saved = await db.get_all_leads()
    assert saved[0].source == SITE


async def test_empty_text_is_rejected(database):
    assert client.post("/api/public/lead", json={"text": ""}).status_code == 422


async def test_honeypot_looks_like_success_but_saves_nothing(database, monkeypatch):
    """Бот не должен понять, что его отсеяли: иначе автор поправит скрипт."""
    _no_ai(monkeypatch)
    response = client.post("/api/public/lead", json={"text": "куплю ссылки", "company": "ООО Рога"})

    assert response.status_code == 200
    assert await db.get_all_leads() == []


async def test_public_form_has_an_hourly_ceiling(database, monkeypatch):
    """Каждая заявка — оплаченный вызов модели, а форма открыта всему интернету."""
    _no_ai(monkeypatch)
    monkeypatch.setattr(app_module, "PUBLIC_PER_HOUR", 2)

    for i in range(2):
        assert client.post("/api/public/lead", json={"text": f"заявка {i}"}).status_code == 200
    assert client.post("/api/public/lead", json={"text": "третья"}).status_code == 429


# --- витрина -------------------------------------------------------------------


async def test_demo_is_open_without_password(database):
    assert client.get("/demo").status_code == 200


async def test_admin_still_needs_password(database):
    assert client.get("/").status_code == 401
    assert client.get("/", headers=auth("admin", "secret")).status_code == 200


async def test_demo_never_shows_real_leads(database):
    """Витрину смотрят посторонние, а в боевых заявках лежат живые люди."""
    await db.add_lead("Полина", "+79990000000", "боевая заявка", source="интеграция")
    await db.add_lead("Гость", "@guest", "заявка с формы", source=SITE)

    page = client.get("/demo").text
    assert "заявка с формы" in page
    assert "боевая заявка" not in page
    assert "+79990000000" not in page


async def test_demo_cannot_touch_a_real_lead(database):
    lead, _ = await db.add_lead(None, None, "боевая", source="интеграция")
    response = client.post(f"/demo/leads/{lead.id}/status", data={"status_to": "closed"})

    assert response.status_code == 403
    assert (await db.get_lead(lead.id)).status == "new"


async def test_demo_lead_status_can_be_changed(database):
    lead, _ = await db.add_lead(None, None, "с формы", source=SITE)
    response = client.post(
        f"/demo/leads/{lead.id}/status", data={"status_to": "in_work"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert (await db.get_lead(lead.id)).status == "in_work"


async def test_demo_history_is_open_only_for_demo_leads(database):
    mine, _ = await db.add_lead(None, None, "с формы", source=SITE)
    theirs, _ = await db.add_lead(None, None, "боевая", source="интеграция")

    assert client.get(f"/demo/leads/{mine.id}/events").status_code == 200
    assert client.get(f"/demo/leads/{theirs.id}/events").status_code == 403


async def test_demo_can_be_switched_off(database, monkeypatch):
    """У клиента на своём сервере витрина не нужна: заявки там не показывают никому."""
    monkeypatch.setattr(app_module, "DEMO_ENABLED", False)
    assert client.get("/demo").status_code == 404


async def test_urgency_reaches_the_page_in_russian(database):
    lead, _ = await db.add_lead("Полина", "@p", "текст", source=SITE)
    await db.set_markup(lead.id, "хочет бота", "high", "черновик")

    page = client.get("/demo").text
    assert "высокая" in page
    assert ">high<" not in page


async def test_card_carries_everything_the_screen_draws(database):
    lead, _ = await db.add_lead("Полина", "@p", "текст", "тема", "high", "черновик", SITE)
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
        "source",
        "time",
    }
    assert shape["draft"] == "черновик"
