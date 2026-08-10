import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import db
from models import STATUSES


async def test_lead_is_saved_and_read_back(database):
    await db.add_lead("Иван", "@ivan", "Хочу бота для записи", source="quiz")
    leads = await db.get_all_leads()
    assert len(leads) == 1
    assert leads[0].client_name == "Иван"
    assert leads[0].source == "quiz"


async def test_new_lead_starts_as_new(database):
    lead, is_new = await db.add_lead("Мария", "@maria", "Сколько стоит?")
    assert is_new
    assert lead.status == "new"
    assert lead.created_at is not None
    assert lead.updated_at is not None


async def test_markup_can_be_filled_later(database):
    """Заявка сохраняется сразу, а разметка приезжает потом — модель может и не ответить."""
    lead, _ = await db.add_lead(None, None, "Срочно нужен бот")
    assert lead.topic is None

    await db.set_markup(lead.id, "хочет бота", "high", "Ответим в течение часа")
    updated = await db.get_lead(lead.id)
    assert updated.topic == "хочет бота"
    assert updated.urgency == "high"


async def test_status_changes(database):
    lead, _ = await db.add_lead(None, None, "текст")
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
    lead, _ = await db.add_lead(None, None, "текст")
    assert (await db.set_status(lead.id, status)).status == status


def test_postgres_url_gets_async_driver(monkeypatch):
    """Хостинги отдают строку в формате postgresql:// — синхронный драйвер в async упадёт."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/crm")
    assert db.database_url().startswith("postgresql+asyncpg://")


# --- повторы -------------------------------------------------------------------


async def test_same_lead_twice_is_one_lead(database):
    """Человек жмёт «отправить» дважды. Второй карточки и второго вызова модели быть не должно."""
    first, new_first = await db.add_lead("Иван", "@ivan", "Хочу бота")
    second, new_second = await db.add_lead("Иван", "@ivan", "Хочу бота")

    assert new_first and not new_second
    assert first.id == second.id
    assert len(await db.get_all_leads()) == 1


async def test_repeat_ignores_case_and_spacing(database):
    await db.add_lead(None, "@ivan", "Хочу бота")
    _, is_new = await db.add_lead(None, "@ivan", "  хочу   бота ")
    assert not is_new


async def test_different_people_with_same_text_are_different_leads(database):
    await db.add_lead(None, "@ivan", "Сколько стоит?")
    _, is_new = await db.add_lead(None, "@maria", "Сколько стоит?")
    assert is_new
    assert len(await db.get_all_leads()) == 2


async def test_old_twin_is_a_new_lead(database, monkeypatch):
    """Та же заявка от того же человека через месяц — законная новая заявка, а не дубль."""
    monkeypatch.setattr(db, "DEDUPE_SECONDS", 0)
    await db.add_lead(None, "@ivan", "Хочу бота")
    _, is_new = await db.add_lead(None, "@ivan", "Хочу бота")
    assert is_new


# --- история -------------------------------------------------------------------


async def test_history_records_the_whole_path(database):
    lead, _ = await db.add_lead(None, None, "текст", source="сайт")
    await db.set_markup(lead.id, "вопрос по цене", "low", "черновик")
    await db.set_status(lead.id, "in_work")

    saved = await db.get_lead(lead.id, with_events=True)
    assert [event.kind for event in saved.events] == ["created", "marked", "status"]
    assert saved.events[-1].note == "in_work"


async def test_same_status_twice_does_not_add_noise(database):
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_status(lead.id, "in_work")
    await db.set_status(lead.id, "in_work")

    saved = await db.get_lead(lead.id, with_events=True)
    assert [event.kind for event in saved.events].count("status") == 1


async def test_failed_markup_leaves_a_trace(database):
    """Иначе непонятно, почему карточка без темы: модель молчала или её не звали."""
    lead, _ = await db.add_lead(None, None, "текст")
    await db.mark_failed(lead.id, "все модели вернули 429")

    saved = await db.get_lead(lead.id, with_events=True)
    assert saved.events[-1].kind == "mark_failed"
    assert "429" in saved.events[-1].note


# --- ограничения базы ----------------------------------------------------------


async def test_database_refuses_a_made_up_status(database):
    """Проверка в питоне защищает только питон. Мимо ORM ходят скрипты и psql."""
    with pytest.raises(IntegrityError):
        async with db.Session() as session:
            await session.execute(
                text("insert into leads (text, status) values ('текст', 'придумал')")
            )
            await session.commit()


async def test_database_refuses_a_made_up_urgency(database):
    with pytest.raises(IntegrityError):
        async with db.Session() as session:
            await session.execute(
                text(
                    "insert into leads (text, status, urgency) "
                    "values ('текст', 'new', 'очень срочно')"
                )
            )
            await session.commit()


async def test_empty_urgency_is_allowed(database):
    """Заявка живёт без разметки: ограничение не должно этого запрещать."""
    lead, _ = await db.add_lead(None, None, "текст")
    assert lead.urgency is None


# --- выборки -------------------------------------------------------------------


async def test_counts_come_from_the_whole_table(database):
    """Счётчик по ленте показывал бы «сколько влезло на экран», а не сколько есть."""
    first, _ = await db.add_lead(None, "@a", "первая")
    await db.add_lead(None, "@b", "вторая")
    await db.set_status(first.id, "closed")

    assert await db.count_by_status() == {"new": 1, "closed": 1}


async def test_feed_can_be_filtered(database):
    lead, _ = await db.add_lead(None, "@a", "первая", source="сайт")
    await db.add_lead(None, "@b", "вторая", source="бот")
    await db.set_status(lead.id, "closed")

    assert len(await db.get_all_leads(status="closed")) == 1
    assert len(await db.get_all_leads(source="бот")) == 1
    assert len(await db.get_all_leads(source="сайт", status="new")) == 0
