"""Шанс сделки: оценка моделью, причины, ограничение в базе, показ на доске и в карточке.

Модель подменена: тесты проверяют, что код сохранил оценку и показал её, а не саму оценку.
Качество оценки — дело эвалов.
"""

import asyncio

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

import db
import insight
import tgbot
from ai import DealChance
from models import Lead

REAL_RETHINK = insight.rethink
CHANCE = DealChance(
    percent=64,
    plus=["бюджет назван", "созвон назначен", "отвечает быстро", "лишняя причина"],
    minus=["сравнивает с готовым сервисом"],
)


@pytest.fixture
def model(monkeypatch):
    seen = []

    def fake(deal):
        seen.append(deal)
        return CHANCE

    monkeypatch.setattr(insight, "estimate_chance", fake)
    return seen


async def new_lead(text="Хочу бота для записи в салон"):
    lead, _ = await db.add_lead("Полина", "@polina", text)
    return lead


async def test_estimate_is_saved_with_reasons(database, model):
    lead = await new_lead()

    assert await insight.estimate(lead.id)

    lead = await db.get_lead(lead.id)
    assert lead.chance == 64
    assert db.chance_why(lead) == {
        "plus": ["бюджет назван", "созвон назначен", "отвечает быстро"],
        "minus": ["сравнивает с готовым сервисом"],
    }
    assert "Хочу бота для записи в салон" in model[0]


async def test_closed_deal_is_not_estimated(database, model):
    lead = await new_lead()
    await db.set_stage(lead.id, "won")

    assert not await insight.estimate(lead.id)
    assert model == []


async def test_model_failure_keeps_the_deal_without_chance(database, monkeypatch):
    lead = await new_lead()

    def broken(deal):
        raise RuntimeError("все модели лежат")

    monkeypatch.setattr(insight, "estimate_chance", broken)

    assert not await insight.estimate(lead.id)
    assert (await db.get_lead(lead.id)).chance is None


async def test_database_refuses_impossible_chance(database):
    lead = await new_lead()

    with pytest.raises(IntegrityError):
        async with db.Session() as session:
            await session.execute(update(Lead).where(Lead.id == lead.id).values(chance=120))
            await session.commit()


@pytest.mark.parametrize(
    ("percent", "shown"), [(30, "сравнивает с готовым сервисом"), (70, "бюджет назван")]
)
async def test_board_shows_the_main_reason(database, percent, shown):
    lead = await new_lead()
    await db.set_chance(lead.id, percent, ["бюджет назван"], ["сравнивает с готовым сервисом"])

    assert insight.reason(await db.get_lead(lead.id)) == shown


async def test_card_and_board_show_the_chance(database, manager):
    lead = await new_lead()
    await db.set_chance(lead.id, 64, ["бюджет назван"], ["сравнивает с готовым сервисом"])

    card = manager.get(f"/leads/{lead.id}").text
    assert "Шанс сделки" in card
    assert "64%" in card
    assert "бюджет назван" in card and "сравнивает с готовым сервисом" in card

    board = manager.get("/").text
    assert 'data-chance="0.64"' in board


async def test_won_deal_hides_the_chance(database, manager):
    lead = await new_lead()
    await db.set_chance(lead.id, 64, ["бюджет назван"], [])
    await db.set_stage(lead.id, "won")

    assert "Шанс сделки" not in manager.get(f"/leads/{lead.id}").text


async def test_rethink_updates_step_and_chance_together(database, model, monkeypatch):
    from ai import NextStep

    lead = await new_lead()
    step = NextStep(message="Добрый день!", why="пора на созвон", remind_in_days=2)
    monkeypatch.setattr(insight, "suggest_next", lambda deal: step)

    await REAL_RETHINK(lead.id)

    lead = await db.get_lead(lead.id)
    assert lead.chance == 64
    assert db.next_step(lead)["message"] == "Добрый день!"


async def test_client_reply_to_a_human_rethinks_the_deal(database, monkeypatch):
    lead = await new_lead()
    called = []

    async def fake_reply(lead_id, text):
        return None

    async def fake_rethink(lead_id):
        called.append(lead_id)

    monkeypatch.setattr(tgbot.agent, "reply", fake_reply)
    monkeypatch.setattr(insight, "rethink", fake_rethink)

    await tgbot._answer(lead.id, 555, "Да, давайте созвонимся")
    await asyncio.sleep(0)

    assert called == [lead.id]
