"""Прогон эвалов: датасет -> метрика в процентах -> сравнение с прошлым прогоном.

    python evals/run_eval.py
    python evals/run_eval.py --limit 5 --concurrency 2

Проектное живёт в target.py (как позвать продукт) и score.py (как считать балл).
Стоимость берётся фактическая, из ответа OpenRouter, и переводится в рубли по USD_RUB.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import score as scoring  # noqa: E402
import target  # noqa: E402

CASES = HERE / "cases.jsonl"
RUNS = HERE / "runs"
USD_RUB = float(os.environ.get("USD_RUB", "90"))


def load_cases(limit: int | None) -> list[dict]:
    cases = [json.loads(line) for line in CASES.read_text("utf-8").splitlines() if line.strip()]
    return cases[:limit] if limit else cases


async def run_case(case: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        started = time.perf_counter()
        try:
            result = await target.run(case["input"])
            output, usage, error = result["output"], result.get("usage", {}), None
        except Exception as exc:
            output, usage, error = None, {}, f"{type(exc).__name__}: {exc}"
        return {
            "id": case["id"],
            "expected": case["expected"],
            "score": 0.0 if error else scoring.score(case["expected"], output),
            "output": output,
            "usage": usage,
            "error": error,
            "seconds": round(time.perf_counter() - started, 2),
        }


def cost_rub(results: list[dict]) -> float:
    return round(sum((r["usage"].get("usd") or 0) for r in results) * USD_RUB, 2)


def breakdown(results: list[dict]) -> dict:
    ok = [r for r in results if r["output"]]
    if not ok:
        return {}
    urg = sum(r["output"]["urgency"] in r["expected"]["urgency"] for r in ok) / len(ok)
    spam_cases = [r for r in ok if r["expected"].get("spam") is not None]
    spam = sum(scoring.is_spam(r["output"]) == r["expected"]["spam"] for r in spam_cases)
    return {"urgency": round(urg * 100, 1), "spam": round(spam / max(1, len(spam_cases)) * 100, 1)}


def previous_run() -> dict | None:
    runs = sorted(RUNS.glob("*.json"))
    return json.loads(runs[-1].read_text("utf-8")) if runs else None


def save(results: list[dict], accuracy: float, cost: float, parts: dict) -> Path:
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.json"
    payload = {
        "accuracy": accuracy,
        "parts": parts,
        "cost_rub": cost,
        "cases": len(results),
        "results": results,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    cases = load_cases(args.limit)
    previous = previous_run()

    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*(run_case(c, sem) for c in cases))

    for r in results:
        mark = "OK  " if r["score"] >= 0.999 else "FAIL"
        note = r["error"] or f"{r['score']:.2f}"
        print(f"{mark} {r['id']:<24} {note}  {r['seconds']}s")

    accuracy = sum(r["score"] for r in results) / len(results) * 100
    parts = breakdown(results)
    cost = cost_rub(results)
    path = save(results, round(accuracy, 1), cost, parts)

    print(f"\nТочность: {accuracy:.1f}%  ({len(results)} кейсов)")
    if parts:
        print(f"Срочность угадана: {parts['urgency']}%, спам распознан: {parts['spam']}%")
    if previous:
        delta = accuracy - previous["accuracy"]
        print(f"Прошлый прогон: {previous['accuracy']:.1f}%  ({delta:+.1f})")
    if cost:
        print(
            f"Стоимость прогона: {cost} ₽  ({cost / len(results):.2f} ₽ за заявку, курс {USD_RUB})"
        )
    print(f"Сохранено: {path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
