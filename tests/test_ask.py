"""«Спроси CRM»: инструменты считают на настоящей базе, модель подменена сценарием.

Проверяем то, за что отвечает код: цифры, права доступа и что таблица берётся из запроса,
а не из текста модели.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

import app as app_module
import ask
import db


def say(text):
    return NS(content=text, tool_calls=None)


def call(name, **args):
    return NS(
        content="",
        tool_calls=[NS(id=f"c-{name}", function=NS(name=name, arguments=json.dumps(args)))],
    )


def script(*steps):
    seen = []
    queue = list(steps)

    async def complete(messages):
        seen.append([dict(m) for m in messages])
        return queue.pop(0)

    complete.seen = seen
    return complete


async def deal(text, source="сайт", stage=None, amount=None, who=None, chance=None, utm=None):
    lead, _ = await db.add_lead(None, f"@{abs(hash(text))}", text, source=source, utm=utm)
    if stage:
        await db.set_stage(lead.id, stage, who)
    if amount is not None:
        await db.set_amount(lead.id, Decimal(amount))
    if who:
        await db.set_assignee(lead.id, who)
    if chance is not None:
        await db.set_chance(lead.id, chance, [], [])
    return lead


@pytest.fixture
async def team(database):
    irina = await db.create_user("ira", "Ирина", "very-secret")
    oleg = await db.create_user("oleg", "Олег", "very-secret")
    await db.set_access(oleg.id, own_only=True, active=True)
    return irina, await db.get_user(oleg.id)


async def test_deals_by_source_counts_money_and_conversion(team):
    irina, _ = team
    await deal("бот для салона", "сайт", "won", 50000, irina.id)
    await deal("бот для кофейни", "сайт", "in_work", 30000, irina.id)
    await deal("запись в клинику", "звонок")

    table = await ask.Tools(None).tool_deals_by("source")

    assert table["rows"][0] == ["сайт", 2, 1, 50000, 30000, 50]
    assert table["rows"][1][:2] == ["звонок", 1]


async def test_stage_names_are_russian(team):
    await deal("бот", stage="waiting")

    table = await ask.Tools(None).tool_deals_by("stage")

    assert table["rows"][0][0] == "Ждём ответа"


async def test_ad_campaigns_come_from_utm(team):
    await deal("бот", utm={"source": "vk", "campaign": "осень"})

    table = await ask.Tools(None).tool_deals_by("ad")

    assert ["vk", 1] == table["rows"][0][:2]


async def test_find_overdue_deals_sorted_by_money(team):
    irina, _ = team
    small = await deal("мелкая", stage="in_work", amount=10000, who=irina.id)
    big = await deal("крупная", stage="in_work", amount=90000, who=irina.id)
    await deal("в срок", stage="in_work", amount=50000, who=irina.id)
    past = datetime.now(UTC) - timedelta(days=1)
    await db.set_due(small.id, past)
    await db.set_due(big.id, past)

    table = await ask.Tools(None).tool_find_deals("overdue")

    assert table["ids"] == [big.id, small.id]
    assert [row[2] for row in table["rows"]] == [90000, 10000]


async def test_own_only_manager_never_sees_others_deals(team):
    irina, oleg = team
    await deal("сделка Ирины", stage="in_work", amount=70000, who=irina.id)
    mine = await deal("сделка Олега", stage="in_work", amount=20000, who=oleg.id)

    tools = ask.Tools(oleg)
    found = await tools.tool_find_deals("open")
    summary = await tools.tool_deals_by("manager")
    forecast = await tools.tool_forecast()

    assert found["ids"] == [mine.id]
    assert [row[0] for row in summary["rows"]] == ["Олег"]
    assert forecast["rows"][-1][2] == 20000


async def test_forecast_weights_money_by_chance(team):
    await deal("а", stage="in_work", amount=100000, chance=50)
    await deal("б", stage="in_work", amount=40000, chance=25)
    await deal("в", stage="waiting", amount=60000)

    table = await ask.Tools(None).tool_forecast()

    assert table["rows"][0] == ["В работе", 2, 140000, 60000]
    assert table["rows"][-1] == ["Итого", 3, 200000, 60000]


async def test_response_time_is_per_manager(team):
    irina, _ = team
    await deal("бот", stage="in_work", who=irina.id)

    table = await ask.Tools(None).tool_response_time()

    assert [row[0] for row in table["rows"]] == ["Ирина"]
    assert table["rows"][0][2] == 1


async def test_bad_arguments_are_an_answer_not_a_crash(team):
    tools = ask.Tools(None)

    assert "error" in await tools.call("deals_by", {"group_by": "пароли"})
    assert "error" in await tools.call("drop_table", {})
    assert "error" in await tools.call("find_deals", {"filter": "open", "sql": "1=1"})


async def test_table_comes_from_the_query_not_from_the_model(team):
    await deal("бот", "сайт", "won", 50000)
    model = script(call("deals_by", group_by="source"), say("Сайт принёс 50 000 ₽."))

    result = await ask.ask("Сколько принёс сайт?", None, complete=model)

    assert result["answer"] == "Сайт принёс 50 000 ₽."
    assert result["table"]["rows"][0][:4] == ["сайт", 1, 1, 50000]
    assert model.seen[0][1]["content"].startswith("<<DATA:")


async def test_endless_tool_calls_stop(team):
    model = script(*[call("forecast")] * ask.MAX_STEPS)

    result = await ask.ask("Прогноз?", None, complete=model)

    assert result["answer"] == ask.FALLBACK
    assert len(model.seen) == ask.MAX_STEPS


async def test_page_shows_answer_and_table(team, manager, monkeypatch):
    async def fake(question, viewer):
        return {
            "answer": "Горят две сделки.",
            "table": {
                "columns": [["Сделка", "text"], ["Сумма", "money"]],
                "rows": [["Студия «Линия»", 60000]],
                "ids": [7],
                "how": "Просроченные.",
            },
        }

    monkeypatch.setattr(app_module.asking, "ask", fake)
    page = manager.get("/ask", params={"q": "Какие сделки горят?"}).text

    assert "Горят две сделки." in page
    assert "60 000 ₽" in page
    assert 'href="/leads/7"' in page


async def test_model_failure_is_a_polite_answer(team, manager):
    page = manager.get("/ask", params={"q": "Сколько заявок?"}).text

    assert ask.FALLBACK in page


async def test_questions_per_day_are_limited(team, manager, monkeypatch):
    async def fake(question, viewer):
        return {"answer": "ответ", "table": None}

    monkeypatch.setattr(app_module.asking, "ask", fake)
    monkeypatch.setattr(app_module, "ASK_PER_DAY", 2)

    pages = [manager.get("/ask", params={"q": "вопрос"}).text for _ in range(3)]

    assert "закончились" not in pages[1]
    assert "закончились" in pages[2]


async def test_empty_page_offers_examples(team, manager):
    page = manager.get("/ask").text

    assert "Какие сделки горят?" in page
