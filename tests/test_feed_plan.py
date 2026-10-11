"""Лента сделки и отчёт: сравнение с прошлым периодом и план продаж на месяц."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import update

import app as app_module
import db
from models import Lead, LeadEvent


async def _age(model, where, days: int) -> None:
    async with db.Session() as session:
        await session.execute(
            update(model).where(where).values(created_at=datetime.now(UTC) - timedelta(days=days))
        )
        await session.commit()


async def test_previous_period_is_the_window_right_before(database):
    await db.add_lead(None, "@a", "свежая")
    old, _ = await db.add_lead(None, "@b", "прошлая")
    ancient, _ = await db.add_lead(None, "@c", "давняя")
    await _age(Lead, Lead.id == old.id, 40)
    await _age(Lead, Lead.id == ancient.id, 70)

    assert (await db.report(30))["total"] == 1
    assert (await db.report(30, offset=1))["total"] == 1
    assert (await db.report(30, offset=2))["total"] == 1


async def test_month_revenue_counts_deals_won_this_month(database):
    fresh, _ = await db.add_lead(None, "@a", "закрыли сейчас")
    await db.set_amount(fresh.id, Decimal(50000))
    await db.set_stage(fresh.id, "won")
    old, _ = await db.add_lead(None, "@b", "закрыли давно")
    await db.set_amount(old.id, Decimal(70000))
    await db.set_stage(old.id, "won")
    await _age(LeadEvent, (LeadEvent.lead_id == old.id) & (LeadEvent.kind == "stage"), 40)

    assert await db.won_this_month() == Decimal(50000)


async def test_only_owner_sets_the_month_goal(database, boss, manager):
    assert manager.post("/report/goal", data={"goal": "500 000"}).status_code == 403

    response = boss.post("/report/goal", data={"goal": "500 000"}, follow_redirects=False)
    assert response.status_code == 303
    assert await db.get_setting("month_goal") == "500000"
    assert "500 000" in boss.get("/report").text

    boss.post("/report/goal", data={"goal": ""}, follow_redirects=False)
    assert await db.get_setting("month_goal") is None


async def test_feed_is_one_line_newest_first_without_repeats(database, boss):
    lead, _ = await db.add_lead(None, "@a", "текст")
    await db.add_note(lead.id, None, "созвонились, ждёт смету")
    full = await db.get_lead(lead.id, full=True)

    items = app_module.feed(full, [])
    moments = [item["at"] for item in items]
    assert moments == sorted(moments, reverse=True)
    assert {item["kind"] for item in items} >= {"note", "event"}
    # Комментарий уже стоит в ленте целиком, строка «комментарий» из журнала была бы повтором.
    assert not any(item["kind"] == "event" and item["event"].kind == "note" for item in items)

    page = boss.get(f"/leads/{lead.id}").text
    assert "Лента" in page and "созвонились, ждёт смету" in page
