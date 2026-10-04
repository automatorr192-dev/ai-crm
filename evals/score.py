"""Балл за одну заявку, от 0 до 1. Тема заявки — свободный текст, поэтому сравниваем не её,
а то, от чего зависит работа менеджера:

  срочность   0.5   попала ли в допустимый набор (у пограничных заявок он шире одного)
  спам        0.3   пометила ли модель спам там, где он есть, и только там
  черновик    0.2   есть, на русском, короткий, без утечки промпта и без запрещённых обещаний

null в разметке спама значит «не проверяем»: заявка не по профилю или пустая, и спорить,
спам это или нет, бессмысленно.
"""

import re

LEAKS = ("<<DATA", "<</DATA", "draft_reply", '"urgency"', "Верни ТОЛЬКО JSON")


def is_spam(output: dict) -> bool:
    return "спам" in output.get("topic", "").lower()


def reply_ok(reply: str, forbid: list[str]) -> bool:
    if not reply or len(reply) > 500:
        return False
    if not re.search("[а-яё]", reply, re.IGNORECASE):
        return False
    low = reply.lower()
    return not any(x.lower() in low for x in (*LEAKS, *forbid))


def score(expected: dict, output: dict) -> float:
    urgency = 0.5 if output.get("urgency") in expected["urgency"] else 0.0
    want = expected.get("spam")
    spam = 0.3 if want is None or is_spam(output) == want else 0.0
    draft = (
        0.2 if reply_ok(output.get("draft_reply", ""), expected.get("reply_forbid", [])) else 0.0
    )
    return round(urgency + spam + draft, 2)
