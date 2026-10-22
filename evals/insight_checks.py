"""Проверки ответов ИИ поверх сделки. На вход — ожидание из датасета и ответ модели, на выход —
{название проверки: прошла ли}. Названия общие для всех кейсов: прогон показывает не только,
какой кейс упал, но и какая проверка проваливается чаще всего.

Дословно ответ не сравнивается: сообщение клиенту и вывод аналитика — свободный текст.
Проверяется то, от чего зависят деньги и доверие: нет ли выдуманной цены или цифры, тот ли
диапазон шанса, тот ли запрос к базе, нет ли утечки промпта.
"""

import re

LEAKS = ("<<DATA", "<</DATA", "remind_in_days", "JSON", "Граница данных", "аналитик внутри CRM")
MONTHS = "январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"
NUMBER = re.compile(rf"(\d[\d   ]*(?:[.,]\d+)?)\s*(тыс|млн|{MONTHS})?", re.IGNORECASE)
MONEY = re.compile(r"(\d[\d   ]*(?:[.,]\d+)?)\s*(тыс|млн)?\.?\s*(?:₽|руб|%|процент)", re.IGNORECASE)
INFORMAL = re.compile(r"\b(ты|тебе|тебя|тобой|твой|твоя|твоё|твое|твои|твоих|твоему)\b")


def _value(raw: str, unit: str | None) -> float:
    value = float(re.sub(r"[   ]", "", raw).replace(",", "."))
    return value * {"тыс": 1_000, "млн": 1_000_000}.get((unit or "").lower(), 1)


def numbers(text: str) -> list[float]:
    """Числа как значения: «204 750 ₽» → 204750, «1,2 млн» → 1200000. Даты («21 октября»,
    «2026») пропускаются: это не данные из базы."""
    found = []
    for raw, unit in NUMBER.findall(text):
        if unit and unit.lower() not in ("тыс", "млн"):
            continue
        value = _value(raw.strip(), unit)
        if value.is_integer() and 2000 <= value <= 2100:
            continue
        found.append(value)
    return found


def money(text: str) -> list[float]:
    """Суммы и проценты: числа, за которыми стоит ₽, «руб», «тыс» или %."""
    return [_value(raw.strip(), unit) for raw, unit in MONEY.findall(text)]


def _known(value: float, allowed: list[float]) -> bool:
    return any(abs(value - a) <= max(0.5, a * 0.005) for a in allowed)


def _values(data) -> list[float]:
    """Все числа из результата запроса: ячейки таблицы и числа внутри подписей."""
    if isinstance(data, bool) or data is None:
        return []
    if isinstance(data, int | float):
        return [float(data)]
    if isinstance(data, str):
        return numbers(data)
    if isinstance(data, dict):
        return [v for item in data.values() for v in _values(item)]
    return [v for item in data for v in _values(item)]


def _says(text: str, words: list[str]) -> bool:
    return any(w.lower() in text.lower() for w in words)


def _common(text: str, expect: dict) -> dict:
    checks = {"без утечки промпта": not _says(text, list(LEAKS))}
    for bad in expect.get("not_say", []):
        checks[f"нет «{bad}»"] = bad.lower() not in text.lower()
    return checks


def next_step(expect: dict, output: dict, deal: str) -> dict:
    message = output["message"]
    lo, hi = expect["remind"]
    checks = {
        "по-русски": bool(re.search("[а-яё]", message, re.IGNORECASE)),
        "на «вы»": not INFORMAL.search(message.lower()),
        "коротко": len(message) <= 450,
        "без выдуманных сумм": all(_known(v, numbers(deal)) for v in money(message)),
        "срок напоминания": lo <= output["remind_in_days"] <= hi,
        "есть «почему»": bool(output["why"].strip()),
    }
    if expect.get("name"):
        checks["по имени"] = expect["name"].lower() in message.lower()
    if expect.get("say_any"):
        checks["нужный шаг"] = _says(message, expect["say_any"])
    return checks | _common(message, expect)


def chance(expect: dict, output: dict) -> dict:
    lo, hi = expect["percent"]
    reasons = output["plus"] + output["minus"]
    checks = {
        "шанс в диапазоне": lo <= output["percent"] <= hi,
        "причины короткие": all(0 < len(r.split()) <= 8 for r in reasons),
    }
    if expect.get("plus_any"):
        checks["нужная «за»"] = _says(" ".join(output["plus"]), expect["plus_any"])
    if expect.get("minus_any"):
        checks["нужная «против»"] = _says(" ".join(output["minus"]), expect["minus_any"])
    return checks | _common(" ".join(reasons), expect)


def _matches(call: dict, wanted: dict) -> bool:
    return all(call["args"].get(arg) in allowed for arg, allowed in wanted.items())


def _cell(result: dict, row, col: int):
    rows = result.get("rows") or []
    if isinstance(row, str):
        rows = [r for r in rows if r[0] == row]
        row = 0
    return rows[row][col] if rows and -len(rows) <= row < len(rows) else None


def ask(expect: dict, question: str, answer: str, calls: list[dict]) -> dict:
    """calls — вызовы инструментов по порядку: {"name", "args", "result"}. Число в ответе,
    которого нет в результате запроса, считается выдуманным, даже если модель его посчитала
    сама: считать должна база. Исключение — сколько строк вернул запрос."""
    results = [c["result"] for c in calls]
    allowed = numbers(question) + _values(results) + [len(r.get("rows") or []) for r in results]
    checks = {
        "ответ дан": bool(answer.strip()),
        "числа из запроса": all(_known(v, allowed) for v in numbers(answer)),
    }
    if expect.get("no_tools"):
        checks["без запросов к базе"] = not calls
    if "call" in expect:
        hit = next(
            (
                c
                for c in calls
                if c["name"] in expect["call"] and _matches(c, expect["call"][c["name"]])
            ),
            None,
        )
        checks["нужный запрос"] = hit is not None
        for row, col in expect.get("cells", []):
            value = _cell(hit["result"], row, col) if hit else None
            if isinstance(value, str):
                ok = value.split()[0].lower() in answer.lower()
            else:
                ok = value is not None and any(_known(v, [value]) for v in numbers(answer))
            checks["назван главный факт"] = checks.get("назван главный факт", True) and ok
    return checks | _common(answer, expect)


def estimate(expect: dict, output: dict) -> dict:
    """Поля калькулятора: каждое, что есть в ожидании, должно совпасть. null — модель не должна
    угадывать то, чего в описании нет."""
    checks = {}
    for field, wanted in expect.items():
        if field == "unclear_given":
            checks["сказала, чего не хватает"] = bool(output.get("unclear"))
            continue
        allowed = wanted if isinstance(wanted, list) else [wanted]
        checks[f"поле {field}"] = output.get(field) in allowed
    return checks
