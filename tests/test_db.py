import pytest

import db
from models import STATUSES


async def test_lead_is_saved_and_read_back(database):
    await db.add_lead("Иван", "@ivan", "Хочу бота для записи", source="quiz")
    leads = await db.get_all_leads()
    assert len(leads) == 1
    assert leads[0].client_name == "Иван"
    assert leads[0].source == "quiz"


async def test_new_lead_starts_as_new(database):
    lead = await db.add_lead("Мария", "@maria", "Сколько стоит?")
    assert lead.status == "new"
    assert lead.created_at is not None


async def test_markup_can_be_filled_later(database):
    """Заявка сохраняется сразу, а разметка приезжает потом — модель может и не ответить."""
    lead = await db.add_lead(None, None, "Срочно нужен бот")
    assert lead.topic is None

    await db.set_markup(lead.id, "хочет бота", "high", "Ответим в течение часа")
    updated = await db.get_lead(lead.id)
    assert updated.topic == "хочет бота"
    assert updated.urgency == "high"


async def test_status_changes(database):
    lead = await db.add_lead(None, None, "текст")
    await db.set_status(lead.id, "in_work")
    assert (await db.get_lead(lead.id)).status == "in_work"


async def test_missing_lead_is_not_an_error(database):
    assert await db.set_status(99999, "closed") is None
    assert await db.get_lead(99999) is None


async def test_freshest_lead_comes_first(database):
    await db.add_lead(None, None, "первая")
    await db.add_lead(None, None, "вторая")
    leads = await db.get_all_leads()
    assert [lead.text for lead in leads][0] in {"вторая", "первая"}
    assert len(leads) == 2


@pytest.mark.parametrize("status", STATUSES)
async def test_every_status_fits_the_column(database, status):
    lead = await db.add_lead(None, None, "текст")
    assert (await db.set_status(lead.id, status)).status == status


def test_postgres_url_gets_async_driver(monkeypatch):
    """Хостинги отдают строку в формате postgresql:// — синхронный драйвер в async упадёт."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/crm")
    assert db.database_url().startswith("postgresql+asyncpg://")
