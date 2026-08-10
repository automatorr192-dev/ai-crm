import asyncio
import base64
import json
import os
import time

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
import db  # noqa: E402
import webhook  # noqa: E402

app_module.ADMIN_USER = "admin"
app_module.ADMIN_PASSWORD = "secret"
client = TestClient(app_module.app)


def auth(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_health_is_open():
    assert client.get("/health").json() == {"status": "ok"}


def test_leads_are_closed_without_password():
    assert client.get("/").status_code == 401


def test_wrong_password_is_rejected():
    assert client.get("/", headers=auth("admin", "wrong")).status_code == 401


async def test_admin_sees_leads(database):
    await db.add_lead("Иван", "@ivan", "Хочу бота для записи клиентов", source="quiz")
    page = client.get("/", headers=auth("admin", "secret"))
    assert page.status_code == 200
    assert "Иван" in page.text


async def test_brute_force_gets_locked_out(monkeypatch):
    """После порога неудач админка отвечает 429, а не продолжает принимать попытки."""
    monkeypatch.setattr(app_module, "_failures", {})
    monkeypatch.setattr(app_module, "MAX_FAILURES", 3)
    for _ in range(3):
        assert client.get("/", headers=auth("admin", "wrong")).status_code == 401
    assert client.get("/", headers=auth("admin", "secret")).status_code == 429


async def test_signed_lead_is_accepted(database, monkeypatch):
    monkeypatch.setattr(app_module, "enrich", lambda lead_id: asyncio.sleep(0))

    body = json.dumps({"text": "Хочу бота, срочно", "name": "Мария"}).encode()
    ts = str(time.time())
    response = client.post(
        "/webhook/lead",
        content=body,
        headers={"X-Timestamp": ts, "X-Signature": webhook.sign(body, ts)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "new"
    assert [lead.text for lead in await db.get_all_leads()] == ["Хочу бота, срочно"]


async def test_unsigned_lead_is_rejected(database):
    body = json.dumps({"text": "Заявка без подписи"}).encode()
    assert client.post("/webhook/lead", content=body).status_code == 401
    assert await db.get_all_leads() == []


async def test_garbage_body_with_valid_signature_is_rejected(database):
    """Подпись доказывает отправителя, но не то, что внутри осмысленная заявка."""
    body = b'{"nothing": "useful"}'
    ts = str(time.time())
    response = client.post(
        "/webhook/lead",
        content=body,
        headers={"X-Timestamp": ts, "X-Signature": webhook.sign(body, ts)},
    )
    assert response.status_code == 422
    assert await db.get_all_leads() == []


async def test_repeated_delivery_does_not_double_the_lead(database, monkeypatch):
    """Чужой сервис не дождался ответа и прислал заявку ещё раз — карточка одна."""
    monkeypatch.setattr(app_module, "enrich", lambda lead_id: asyncio.sleep(0))

    body = json.dumps({"text": "Хочу бота", "contact": "@ivan"}).encode()
    ids = set()
    for _ in range(2):
        ts = str(time.time())
        response = client.post(
            "/webhook/lead",
            content=body,
            headers={"X-Timestamp": ts, "X-Signature": webhook.sign(body, ts)},
        )
        assert response.status_code == 200
        ids.add(response.json()["id"])

    assert len(ids) == 1
    assert len(await db.get_all_leads()) == 1


async def test_status_can_be_changed_from_admin(database):
    lead, _ = await db.add_lead("Иван", "@ivan", "текст")
    response = client.post(
        f"/leads/{lead.id}/status",
        data={"status_to": "in_work"},
        headers=auth("admin", "secret"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (await db.get_lead(lead.id)).status == "in_work"


async def test_unknown_status_is_rejected(database):
    lead, _ = await db.add_lead(None, None, "текст")
    response = client.post(
        f"/leads/{lead.id}/status",
        data={"status_to": "выдуманный"},
        headers=auth("admin", "secret"),
    )
    assert response.status_code == 422
    assert (await db.get_lead(lead.id)).status == "new"


async def test_status_change_needs_password(database):
    lead, _ = await db.add_lead(None, None, "текст")
    assert client.post(f"/leads/{lead.id}/status", data={"status_to": "closed"}).status_code == 401


async def test_history_is_closed_without_password(database):
    lead, _ = await db.add_lead(None, None, "текст")
    assert client.get(f"/leads/{lead.id}/events").status_code == 401


async def test_admin_reads_history(database):
    lead, _ = await db.add_lead(None, None, "текст", source="сайт")
    response = client.get(f"/leads/{lead.id}/events", headers=auth("admin", "secret"))
    assert response.status_code == 200
    assert response.json()["events"][0]["kind"] == "created"
