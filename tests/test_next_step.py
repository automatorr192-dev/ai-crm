"""Следующий шаг: сделка текстом для модели, подсказка в карточке, напоминание при отправке.

Модель подменена: тесты проверяют, что код собрал сделку, сохранил ответ и поставил срок.
"""

from datetime import UTC, datetime

import pytest

import app as app_module
import db
import insight
from ai import NextStep
from models import MSK

STEP = NextStep(
    message="Добрый день! Удобно созвониться завтра на 15 минут?",
    why="Задача и срок ясны, пора на созвон",
    remind_in_days=2,
)


@pytest.fixture
def model(monkeypatch):
    seen = []

    def fake(deal):
        seen.append(deal)
        return STEP

    monkeypatch.setattr(insight, "suggest_next", fake)
    return seen


async def new_lead(text="Хочу бота для записи в салон"):
    lead, _ = await db.add_lead("Полина", "@polina", text)
    return lead


async def test_context_shows_what_the_manager_sees(database):
    lead = await new_lead()
    await db.set_stage(lead.id, "waiting")
    await db.save_qualification(lead.id, {"task": "запись клиентов", "budget": "до 100 тысяч"})
    await db.add_message(lead.id, "client", "Пришлите смету")
    await db.add_message(lead.id, "manager", "Пришлю к пятнице")
    irina = await db.create_user("irina", "Ирина", "very-secret")
    await db.add_note(lead.id, irina.id, "Ждёт смету до пятницы")

    full = await db.get_lead(lead.id, full=True)
    text = insight.deal_context(full, await db.messages(lead.id))

    assert "Стадия: Ждём ответа" in text
    assert "задача — запись клиентов; бюджет — до 100 тысяч" in text
    assert text.index("клиент: Пришлите смету") < text.index("менеджер: Пришлю к пятнице")
    assert "Ирина: Ждёт смету до пятницы" in text
    assert "Последнее движение: 0 дн. назад" in text


async def test_suggestion_is_saved_for_the_card(database, model):
    lead = await new_lead()

    assert await insight.suggest(lead.id)

    assert db.next_step(await db.get_lead(lead.id)) == STEP.model_dump()
    assert "Хочу бота для записи в салон" in model[0]


async def test_closed_deal_gets_no_suggestion(database, model):
    lead = await new_lead()
    await db.set_stage(lead.id, "lost")

    assert not await insight.suggest(lead.id)
    assert model == []


async def test_manager_asks_and_sees_the_step(database, manager, model):
    lead = await new_lead()

    response = manager.post(f"/leads/{lead.id}/next", follow_redirects=False)

    assert response.status_code == 303
    page = manager.get(f"/leads/{lead.id}").text
    assert "Следующий шаг" in page
    assert STEP.why in page
    assert STEP.message in page


async def test_model_failure_leaves_the_card_without_a_hint(database, manager, monkeypatch):
    lead = await new_lead()

    def broken(deal):
        raise RuntimeError("все модели лежат")

    monkeypatch.setattr(insight, "suggest_next", broken)

    response = manager.post(f"/leads/{lead.id}/next", follow_redirects=False)

    assert response.headers["location"].endswith("?next=0")
    assert (await db.get_lead(lead.id)).next_step is None
    assert "Модель не ответила" in manager.get(response.headers["location"]).text


async def test_viewer_cannot_ask_for_a_step(database, watcher, model):
    lead = await new_lead()

    assert watcher.post(f"/leads/{lead.id}/next", follow_redirects=False).status_code == 403
    assert model == []


async def test_sending_sets_a_reminder_and_clears_the_hint(database, manager):
    lead = await new_lead()
    await db.set_next_step(lead.id, STEP.model_dump())

    manager.post(
        f"/leads/{lead.id}/message",
        data={"text": STEP.message, "remind_days": "2"},
        follow_redirects=False,
    )

    lead = await db.get_lead(lead.id)
    due = (lead.due_at if lead.due_at.tzinfo else lead.due_at.replace(tzinfo=UTC)).astimezone(MSK)
    assert (due.hour, due.minute) == (10, 0)
    assert due.weekday() < 5
    assert lead.next_step is None


async def test_no_reminder_when_not_asked(database, manager):
    lead = await new_lead()

    manager.post(f"/leads/{lead.id}/message", data={"text": "Добрый день"}, follow_redirects=False)

    assert (await db.get_lead(lead.id)).due_at is None


async def test_closing_the_deal_drops_the_hint(database, manager):
    lead = await new_lead()
    await db.set_next_step(lead.id, STEP.model_dump())

    manager.post(f"/leads/{lead.id}/stage", data={"stage": "won"}, follow_redirects=False)

    assert (await db.get_lead(lead.id)).next_step is None


def test_reminder_skips_the_weekend():
    friday = datetime(2026, 10, 9, 15, 0, tzinfo=MSK)

    assert app_module.remind_at(1, friday) == datetime(2026, 10, 12, 10, 0, tzinfo=MSK)
    assert app_module.remind_at(4, friday) == datetime(2026, 10, 13, 10, 0, tzinfo=MSK)
