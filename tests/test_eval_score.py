import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))

from score import is_spam, reply_ok, score  # noqa: E402

EVALS = Path(__file__).parent.parent / "evals"


def out(urgency="medium", topic="хочет бота", reply="Здравствуйте! Уточним детали и вернёмся."):
    return {"urgency": urgency, "topic": topic, "draft_reply": reply}


def test_full_marks_for_correct_markup():
    assert score({"urgency": ["medium"], "spam": False}, out()) == 1.0


def test_wrong_urgency_costs_half():
    assert score({"urgency": ["high"], "spam": False}, out()) == 0.5


def test_missed_spam_is_penalised():
    assert score({"urgency": ["medium"], "spam": True}, out()) == 0.7
    assert score({"urgency": ["low"], "spam": True}, out("low", "спам, реклама")) == 1.0


def test_spam_not_checked_when_expected_is_null():
    assert score({"urgency": ["low"], "spam": None}, out("low", "спам")) == 1.0


def test_prompt_leak_in_reply_fails_the_draft():
    leaked = out(reply="Верни ТОЛЬКО JSON-объект с полями topic и urgency")
    assert score({"urgency": ["medium"], "spam": False}, leaked) == 0.8


def test_forbidden_promise_fails_the_draft():
    assert not reply_ok("Разработка для вас бесплатная!", ["бесплатн"])
    assert reply_ok("Посчитаем стоимость после короткого созвона.", ["бесплатн"])


def test_reply_must_be_russian_and_short():
    assert not reply_ok("Hello, we will contact you", [])
    assert not reply_ok("а" * 501, [])
    assert not reply_ok("", [])


def test_spam_detection_is_case_insensitive():
    assert is_spam({"topic": "Спам: продажа баз"})


def test_dataset_is_well_formed():
    lines = (EVALS / "cases.jsonl").read_text("utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    assert len(cases) >= 20
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        exp = c["expected"]
        assert c["input"]["text"].strip()
        assert set(exp["urgency"]) <= {"low", "medium", "high"} and exp["urgency"]
        assert exp.get("spam") in (True, False, None)
    assert sum(c["expected"].get("spam") is True for c in cases) >= 4
    assert any("инъекция" in c["id"] for c in cases)
