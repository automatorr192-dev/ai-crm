"""Работа со сделками: воронка, клиенты, деньги, сроки, комментарии, отчёт."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import db
from models import STAGES

# --- воронка -------------------------------------------------------------------


@pytest.mark.parametrize("stage", STAGES)
async def test_every_stage_fits_the_column(database, stage):
    lead, _ = await db.add_lead(None, None, "текст")
    assert (await db.set_stage(lead.id, stage)).stage == stage


async def test_stage_change_is_recorded_with_the_author(database):
    user = await db.create_user("irina", "Ирина", "very-secret")
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_stage(lead.id, "in_work", user.id)

    saved = await db.get_lead(lead.id, full=True)
    last = saved.events[-1]
    assert last.kind == "stage"
    assert last.author.name == "Ирина"


async def test_same_stage_twice_does_not_add_noise(database):
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_stage(lead.id, "in_work")
    await db.set_stage(lead.id, "in_work")

    saved = await db.get_lead(lead.id, full=True)
    assert [e.kind for e in saved.events].count("stage") == 1


async def test_lost_reason_is_kept_only_for_refusal(database):
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_stage(lead.id, "lost", None, "дорого")
    assert (await db.get_lead(lead.id)).lost_reason == "дорого"

    # Вернули в работу — причина отказа перестаёт быть правдой и не должна висеть.
    await db.set_stage(lead.id, "in_work")
    assert (await db.get_lead(lead.id)).lost_reason is None


async def test_board_splits_leads_by_stage(database):
    first, _ = await db.add_lead(None, "@a", "первая")
    await db.add_lead(None, "@b", "вторая")
    await db.set_stage(first.id, "won")

    columns = await db.board()
    assert [lead.text for lead in columns["won"]] == ["первая"]
    assert [lead.text for lead in columns["new"]] == ["вторая"]
    assert columns["lost"] == []


async def test_drag_to_another_stage_from_the_board(database, manager):
    lead, _ = await db.add_lead(None, None, "текст")
    response = manager.post(
        f"/leads/{lead.id}/stage", data={"stage": "won"}, headers={"Accept": "application/json"}
    )
    assert response.json() == {"ok": True, "stage": "won"}


async def test_refusal_reason_comes_from_the_form(database, manager):
    """Через полгода «почему не купили» — единственное, ради чего проигранные сделки хранят."""
    lead, _ = await db.add_lead(None, None, "текст")
    manager.post(f"/leads/{lead.id}/stage", data={"stage": "lost", "lost_reason": "нашли дешевле"})
    saved = await db.get_lead(lead.id)
    assert saved.stage == "lost"
    assert saved.lost_reason == "нашли дешевле"


async def test_unknown_stage_is_refused(database, manager):
    lead, _ = await db.add_lead(None, None, "текст")
    assert manager.post(f"/leads/{lead.id}/stage", data={"stage": "придумал"}).status_code == 422
    assert (await db.get_lead(lead.id)).stage == "new"


# --- клиенты -------------------------------------------------------------------


@pytest.mark.parametrize(
    "first,second",
    [
        ("+7 999 120-45-67", "+79991204567"),
        ("8 999 120 45 67", "+7 (999) 120-45-67"),
        ("@Polina", "@polina"),
        ("Anna.K@Mail.ru", "anna.k@mail.ru"),
    ],
)
async def test_same_person_written_differently_is_one_client(database, first, second):
    """Иначе один клиент расползается по базе на несколько карточек."""
    assert db.contact_key(first) == db.contact_key(second)


async def test_repeat_requests_gather_under_one_client(database):
    await db.add_lead("Полина", "@polina", "сколько стоит бот")
    await db.add_lead(None, "@polina", "вернулась через месяц, готова начинать")

    people = await db.all_contacts()
    assert len(people) == 1
    assert len(people[0].leads) == 2
    # Имя приехало с первой заявкой и осталось за клиентом.
    assert people[0].name == "Полина"


async def test_name_arrives_with_a_later_request(database):
    await db.add_lead(None, "@polina", "первое обращение без имени")
    await db.add_lead("Полина", "@polina", "второе обращение уже с именем")

    people = await db.all_contacts()
    assert people[0].name == "Полина"


async def test_lead_without_contact_has_no_client(database):
    lead, _ = await db.add_lead("Аноним", None, "текст")
    assert lead.contact_id is None
    assert await db.all_contacts() == []


async def test_contacts_can_be_searched(database):
    await db.add_lead("Полина", "@polina", "текст")
    await db.add_lead("Тимур", "@timur", "текст")

    assert len(await db.all_contacts(search="пол")) == 1
    assert len(await db.all_contacts(search="@tim")) == 1


async def test_client_note_is_saved(database, manager):
    await db.add_lead("Полина", "@polina", "текст")
    person = (await db.all_contacts())[0]

    manager.post(f"/contacts/{person.id}/note", data={"note": "любит созвоны утром"})
    assert (await db.get_contact(person.id)).note == "любит созвоны утром"


# --- деньги и сроки ------------------------------------------------------------


async def test_amount_is_stored_exactly(database):
    """Numeric, а не float: на float сумма воронки перестала бы сходиться с суммой сделок."""
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_amount(lead.id, Decimal("45000.50"))
    assert (await db.get_lead(lead.id)).amount == Decimal("45000.50")


async def test_negative_amount_is_refused(database, manager):
    lead, _ = await db.add_lead(None, None, "текст")
    assert manager.post(f"/leads/{lead.id}/amount", data={"amount": "-100"}).status_code == 422


async def test_amount_accepts_human_typing(database, manager):
    """Человек пишет «45 000,50», а не «45000.50»."""
    lead, _ = await db.add_lead(None, None, "текст")
    manager.post(f"/leads/{lead.id}/amount", data={"amount": "45 000,50"})
    assert (await db.get_lead(lead.id)).amount == Decimal("45000.50")


async def test_garbage_amount_is_refused(database, manager):
    lead, _ = await db.add_lead(None, None, "текст")
    assert manager.post(f"/leads/{lead.id}/amount", data={"amount": "дорого"}).status_code == 422


async def test_overdue_is_visible(database):
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_due(lead.id, datetime.now(UTC) - timedelta(hours=2))
    assert (await db.get_lead(lead.id)).overdue

    # Закрытую сделку не подгоняют: срок по ней больше не задача.
    await db.set_stage(lead.id, "won")
    assert not (await db.get_lead(lead.id)).overdue


async def test_overdue_filter_finds_only_the_forgotten(database):
    late, _ = await db.add_lead(None, "@a", "забытая")
    soon, _ = await db.add_lead(None, "@b", "ещё есть время")
    await db.set_due(late.id, datetime.now(UTC) - timedelta(days=1))
    await db.set_due(soon.id, datetime.now(UTC) + timedelta(days=1))

    found = await db.get_all_leads(overdue=True)
    assert [lead.text for lead in found] == ["забытая"]


# --- комментарии и поиск -------------------------------------------------------


async def test_note_is_saved_with_its_author(database):
    user = await db.create_user("irina", "Ирина", "very-secret")
    lead, _ = await db.add_lead(None, None, "текст")
    await db.add_note(lead.id, user.id, "созвонились, ждёт смету")

    saved = await db.get_lead(lead.id, full=True)
    assert saved.notes[0].text == "созвонились, ждёт смету"
    assert saved.notes[0].author.name == "Ирина"


async def test_empty_note_is_ignored(database):
    lead, _ = await db.add_lead(None, None, "текст")
    assert await db.add_note(lead.id, None, "   ") is None
    assert (await db.get_lead(lead.id, full=True)).notes == []


async def test_search_looks_in_text_name_contact_and_topic(database):
    lead, _ = await db.add_lead("Полина", "@polina", "нужен бот для записи")
    await db.set_markup(lead.id, "хочет бота", "high", "черновик")
    await db.add_lead("Тимур", "@timur", "посчитайте смету")

    assert len(await db.get_all_leads(search="запис")) == 1
    assert len(await db.get_all_leads(search="полина")) == 1
    assert len(await db.get_all_leads(search="@tim")) == 1
    assert len(await db.get_all_leads(search="хочет бота")) == 1
    assert len(await db.get_all_leads(search="ничего такого")) == 0


async def test_assignee_filter(database):
    user = await db.create_user("irina", "Ирина", "very-secret")
    mine, _ = await db.add_lead(None, "@a", "моя")
    await db.add_lead(None, "@b", "не моя")
    await db.set_assignee(mine.id, user.id)

    assert [lead.text for lead in await db.get_all_leads(assignee_id=user.id)] == ["моя"]


# --- отчёт ---------------------------------------------------------------------


async def test_report_counts_money_and_conversion(database):
    won, _ = await db.add_lead(None, "@a", "выиграли")
    working, _ = await db.add_lead(None, "@b", "в работе")
    await db.add_lead(None, "@c", "новая")

    await db.set_amount(won.id, Decimal("100000"))
    await db.set_stage(won.id, "won")
    await db.set_amount(working.id, Decimal("50000"))
    await db.set_stage(working.id, "in_work")

    numbers = await db.report()
    assert numbers["total"] == 3
    assert numbers["by_stage"] == {"won": 1, "in_work": 1, "new": 1}
    assert numbers["won_amount"] == Decimal("100000")
    # В работе только незакрытые: выигранные деньги уже не «в работе».
    assert numbers["in_work_amount"] == Decimal("50000")
    assert numbers["conversion"] == 33


async def test_report_measures_time_to_human_answer(database):
    """Разметка моделью не считается ответом: клиенту от неё ничего не пришло."""
    user = await db.create_user("irina", "Ирина", "very-secret")
    lead, _ = await db.add_lead(None, None, "текст")
    await db.set_markup(lead.id, "тема", "low", "черновик")

    assert (await db.report())["answer_minutes"] is None

    await db.add_note(lead.id, user.id, "ответила клиенту")
    numbers = await db.report()
    assert numbers["answered_count"] == 1
    assert numbers["answer_minutes"] is not None


async def test_report_survives_an_empty_database(database):
    numbers = await db.report()
    assert numbers["total"] == 0
    assert numbers["conversion"] == 0
    assert numbers["answer_minutes"] is None


async def test_report_groups_by_source(database):
    await db.add_lead(None, "@a", "раз", source="сайт")
    await db.add_lead(None, "@b", "два", source="сайт")
    await db.add_lead(None, "@c", "три", source="интеграция")

    assert (await db.report())["by_source"] == {"сайт": 2, "интеграция": 1}


# --- экраны --------------------------------------------------------------------


async def test_screens_open(database, boss):
    lead, _ = await db.add_lead("Полина", "@polina", "нужен бот", source="сайт")
    person = (await db.all_contacts())[0]

    for path in (
        "/",
        "/leads",
        "/contacts",
        "/report",
        "/team",
        f"/leads/{lead.id}",
        f"/contacts/{person.id}",
    ):
        assert boss.get(path).status_code == 200, path


async def test_missing_lead_is_a_404_not_a_crash(database, boss):
    assert boss.get("/leads/99999").status_code == 404
    assert boss.get("/contacts/99999").status_code == 404


async def test_stage_names_reach_the_screen_in_russian(database, boss):
    await db.add_lead("Полина", "@polina", "текст", source="сайт")
    page = boss.get("/leads").text
    assert "Новые" in page
    assert ">new<" not in page
