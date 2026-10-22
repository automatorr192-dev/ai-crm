"""Два входа для заявок и разная защита у каждого.

Вебхук подписывается общим секретом: его адрес попадает в настройки чужой формы и рано
или поздно становится известен. Публичная форма подписать не может ничего — любой ключ,
положенный в статику, лежит в исходном коде страницы, поэтому там другой набор защит.
"""

import json
import time

import app as app_module
import db
import webhook

SITE = app_module.PUBLIC_SOURCE


def _signed(client, payload: dict):
    body = json.dumps(payload).encode()
    ts = str(time.time())
    return client.post(
        "/webhook/lead",
        content=body,
        headers={"X-Timestamp": ts, "X-Signature": webhook.sign(body, ts)},
    )


# --- вебхук --------------------------------------------------------------------


async def test_signed_lead_is_accepted(database, client):
    response = _signed(client, {"text": "Хочу бота, срочно", "name": "Мария"})

    assert response.status_code == 200
    assert [lead.text for lead in await db.get_all_leads()] == ["Хочу бота, срочно"]


async def test_unsigned_lead_is_rejected(database, client):
    body = json.dumps({"text": "Заявка без подписи"}).encode()
    assert client.post("/webhook/lead", content=body).status_code == 401
    assert await db.get_all_leads() == []


async def test_garbage_body_with_valid_signature_is_rejected(database, client):
    """Подпись доказывает отправителя, но не то, что внутри осмысленная заявка."""
    assert _signed(client, {"nothing": "useful"}).status_code == 422
    assert await db.get_all_leads() == []


async def test_repeated_delivery_does_not_double_the_lead(database, client):
    """Чужой сервис не дождался ответа и прислал заявку ещё раз — карточка одна."""
    payload = {"text": "Хочу бота", "contact": "@ivan"}
    ids = {_signed(client, payload).json()["id"] for _ in range(2)}

    assert len(ids) == 1
    assert len(await db.get_all_leads()) == 1


async def test_oversized_body_is_refused(database, client):
    body = json.dumps({"text": "x" * 30000}).encode()
    ts = str(time.time())
    response = client.post(
        "/webhook/lead",
        content=body,
        headers={"X-Timestamp": ts, "X-Signature": webhook.sign(body, ts)},
    )
    assert response.status_code == 413


# --- публичная форма -----------------------------------------------------------


async def test_form_lead_is_accepted_without_signature(database, client):
    response = client.post(
        "/api/public/lead", json={"consent": True, "text": "Заявки теряются в директе"}
    )

    assert response.status_code == 200
    saved = await db.get_all_leads()
    assert saved[0].source == SITE


async def test_empty_text_is_rejected(database, client):
    assert client.post("/api/public/lead", json={"consent": True, "text": ""}).status_code == 422


async def test_honeypot_looks_like_success_but_saves_nothing(database, client):
    """Бот не должен понять, что его отсеяли: иначе автор поправит скрипт."""
    response = client.post(
        "/api/public/lead", json={"consent": True, "text": "куплю ссылки", "company": "ООО Рога"}
    )

    assert response.status_code == 200
    assert await db.get_all_leads() == []


async def test_public_form_has_an_hourly_ceiling(database, client, monkeypatch):
    """Каждая заявка — оплаченный вызов модели, а форма открыта всему интернету."""
    monkeypatch.setattr(app_module, "PUBLIC_PER_HOUR", 2)

    for i in range(2):
        assert (
            client.post(
                "/api/public/lead", json={"consent": True, "text": f"заявка {i}"}
            ).status_code
            == 200
        )
    assert (
        client.post("/api/public/lead", json={"consent": True, "text": "третья"}).status_code == 429
    )


async def test_form_lead_creates_a_client_card(database, client):
    client.post(
        "/api/public/lead",
        json={"consent": True, "text": "нужен бот", "contact": "@polina", "name": "Полина"},
    )

    people = await db.all_contacts()
    assert len(people) == 1
    assert people[0].title == "Полина"


# --- живое обновление ----------------------------------------------------------


async def test_websocket_is_closed_to_strangers(database, client):
    """Через него уезжают тексты заявок с контактами: открытым он был бы дырой
    в обход всего входа."""
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/leads") as socket:
            socket.receive_text()


async def test_websocket_opens_for_a_logged_in_person(database, boss):
    with boss.websocket_connect("/ws/leads") as socket:
        assert socket is not None


# --- ИИ-смета для сайта ремонта ----------------------------------------------------


def test_estimate_returns_calculator_fields(client, monkeypatch):
    from ai import Estimate

    seen = []

    def fake(text):
        seen.append(text)
        return Estimate(area=54, home="old", level="comfort", design=None, unclear="Нет санузлов")

    monkeypatch.setattr(app_module, "read_estimate", fake)
    response = client.post("/api/public/estimate", json={"text": "Двушка 54 метра во вторичке"})

    assert response.json() == {
        "area": 54,
        "home": "old",
        "level": "comfort",
        "design": None,
        "unclear": "Нет санузлов",
    }
    assert seen == ["Двушка 54 метра во вторичке"]


def test_estimate_needs_a_real_description(client):
    assert client.post("/api/public/estimate", json={"text": "ремонт"}).status_code == 422


def test_model_failure_tells_to_set_fields_by_hand(client, monkeypatch):
    def broken(text):
        raise RuntimeError("все модели лежат")

    monkeypatch.setattr(app_module, "read_estimate", broken)
    response = client.post("/api/public/estimate", json={"text": "Студия 30 метров, бюджетно"})

    assert response.status_code == 503
    assert "вручную" in response.json()["detail"]


def test_estimate_shares_the_hourly_ceiling_with_the_form(client, monkeypatch):
    from ai import Estimate

    monkeypatch.setattr(app_module, "PUBLIC_PER_HOUR", 1)
    monkeypatch.setattr(app_module, "read_estimate", lambda text: Estimate())
    assert client.post("/api/public/estimate", json={"text": "Студия 30 метров"}).status_code == 200
    assert client.post("/api/public/estimate", json={"text": "Студия 31 метр"}).status_code == 429
