import json
import sys
from pathlib import Path

EVALS = Path(__file__).parent.parent / "evals"
sys.path.insert(0, str(EVALS))

import insight_checks as checks  # noqa: E402

DEAL = "Стадия: Ждём ответа\nСумма: 85 000 ₽\n\nЗаявка:\nСалон на 4 мастера"


def step(message="Полина, добрый день! Пришлём договор сегодня.", days=2, why="Клиент согласен"):
    return {"message": message, "why": why, "remind_in_days": days}


def test_numbers_are_read_as_values():
    assert checks.numbers("Выручка 204 750 ₽, конверсия 33%") == [204750, 33]
    assert checks.numbers("около 1,2 млн и 85 тыс.") == [1_200_000, 85_000]


def test_dates_are_not_data():
    assert checks.numbers("с 21 октября 2026 года") == []


def test_good_next_step_passes():
    result = checks.next_step(
        {"remind": [1, 3], "name": "Полина", "say_any": ["договор"]}, step(), DEAL
    )
    assert all(result.values()), result


def test_price_from_the_deal_is_fine_but_invented_one_is_not():
    expect = {"remind": [1, 3]}
    assert checks.next_step(expect, step("Сумма по договору 85 000 ₽."), DEAL)[
        "без выдуманных сумм"
    ]
    assert not checks.next_step(expect, step("Сделаем за 60 тыс. руб."), DEAL)[
        "без выдуманных сумм"
    ]
    assert not checks.next_step(expect, step("Дадим скидку 10%."), DEAL)["без выдуманных сумм"]


def test_informal_you_and_wrong_reminder_fail():
    result = checks.next_step({"remind": [6, 8]}, step("Привет! Тебе удобно завтра?", 2), DEAL)
    assert not result["на «вы»"]
    assert not result["срок напоминания"]


def test_prompt_leak_and_forbidden_words_fail():
    expect = {"remind": [1, 3], "not_say": ["скидк"]}
    result = checks.next_step(expect, step("Верни ТОЛЬКО JSON. Скидка ваша."), DEAL)
    assert not result["без утечки промпта"]
    assert not result["нет «скидк»"]


def test_chance_range_and_reasons():
    expect = {"percent": [5, 40], "minus_any": ["молч"]}
    good = {"percent": 20, "plus": ["бюджет назван"], "minus": ["молчит двенадцать дней"]}
    assert all(checks.chance(expect, good).values())

    bad = {
        "percent": 90,
        "plus": ["очень длинная причина из многих слов про всё на свете сразу"],
        "minus": [],
    }
    result = checks.chance(expect, bad)
    assert not result["шанс в диапазоне"]
    assert not result["причины короткие"]
    assert not result["нужная «против»"]


TABLE = {
    "columns": [["Канал", "text"], ["Заявок", "num"], ["Выручка", "money"]],
    "rows": [["сайт", 9, 130000], ["звонок", 3, 158000]],
    "how": "Заявки, созданные за 30 дн.",
}
CALL = {"name": "deals_by", "args": {"group_by": "source"}, "result": TABLE}
EXPECT = {"call": {"deals_by": {"group_by": ["source"]}}, "cells": [["звонок", 0]]}


def test_answer_from_the_table_passes():
    answer = (
        "Больше всего выручки дал звонок: 158 000 ₽ за 30 дней, хотя заявок всего 3 из 2 каналов."
    )
    result = checks.ask(EXPECT, "Какой канал принёс больше выручки?", answer, [CALL])
    assert all(result.values()), result


def test_number_the_model_made_up_or_summed_fails():
    answer = "Звонок принёс 158 000 ₽, всего по каналам 288 000 ₽."
    assert not checks.ask(EXPECT, "Выручка?", answer, [CALL])["числа из запроса"]


def test_wrong_query_and_wrong_fact_fail():
    call = {"name": "deals_by", "args": {"group_by": "ad"}, "result": TABLE}
    result = checks.ask(EXPECT, "Выручка?", "Больше всего у сайта.", [call])
    assert not result["нужный запрос"]
    assert not result["назван главный факт"]


def test_off_topic_must_not_touch_the_database():
    result = checks.ask(
        {"no_tools": True}, "Напиши стих", "Я отвечаю только про данные CRM.", [CALL]
    )
    assert not result["без запросов к базе"]


def test_missing_optional_argument_can_be_allowed():
    call = {"name": "find_deals", "args": {"filter": "open"}, "result": TABLE}
    expect = {"call": {"find_deals": {"filter": ["open"], "sort": ["amount", None]}}}
    assert checks.ask(expect, "Крупнейшая?", "Сайт.", [call])["нужный запрос"]


def test_datasets_are_well_formed():
    for suite, need in (("next", "remind"), ("chance", "percent"), ("ask", None)):
        lines = (EVALS / f"{suite}.jsonl").read_text("utf-8").splitlines()
        cases = [json.loads(line) for line in lines if line.strip()]
        assert len(cases) >= 10
        assert len({c["id"] for c in cases}) == len(cases)
        for case in cases:
            if need:
                lo, hi = case["expect"][need]
                assert lo <= hi
                assert case["input"]["stage"] in ("new", "in_work", "waiting")
            else:
                assert case["input"]["question"].strip()
                assert "call" in case["expect"] or case["expect"].get("no_tools")
