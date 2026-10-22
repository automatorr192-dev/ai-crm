"""Эвалы ИИ поверх сделки: следующий шаг, шанс сделки и «Спроси CRM».

    python evals/run_insight.py next
    python evals/run_insight.py chance --limit 3
    python evals/run_insight.py ask --model nvidia/nemotron-3-ultra-550b-a55b:free

Следующий шаг и шанс идут через тот же _ask и тот же промпт, что в продукте, а сделка
собирается тем же deal_context: модель видит ровно тот текст, что и в CRM. «Спроси CRM» —
настоящий цикл с инструментами на демо-базе из demo.py, той же, что видит посетитель демо.

Модель одна на прогон, без запасной: иначе непонятно, чей это результат. Бесплатный уровень
OpenRouter — 50 запросов в сутки на весь аккаунт, поэтому прогон, которому не хватит остатка,
не запускается.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).parent
RUNS = HERE / "runs"
# Запросов к модели на кейс: шаг и шанс — один ответ, «Спроси CRM» — запрос к базе и вывод.
COST = {"next": 1, "chance": 1, "ask": 2, "estimate": 1}

parser = argparse.ArgumentParser()
parser.add_argument("suite", choices=list(COST))
parser.add_argument("--model", default="nvidia/nemotron-3-super-120b-a12b:free")
parser.add_argument("--limit", type=int)
parser.add_argument("--only")
args = parser.parse_args()

os.environ["MODELS"] = os.environ["AGENT_MODELS"] = args.model
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(Path(tempfile.mkdtemp()) / "eval.db")
sys.path.insert(0, str(HERE.parent))

import insight_checks as checks  # noqa: E402
import structlog  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

import ai  # noqa: E402
import ask  # noqa: E402
import db  # noqa: E402
import demo  # noqa: E402
import insight  # noqa: E402

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))


def free_left() -> int | None:
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)["data"]["free_model_daily_requests"]["remaining"]
    except Exception:
        return None


def deal_text(case: dict, now: datetime) -> str:
    """Сделка из датасета тем же текстом, каким её видит модель в CRM."""
    at = now - timedelta(days=case.get("quiet_days", 0))
    known = case.get("known")
    lead = NS(
        stage=case["stage"],
        topic=case.get("topic"),
        amount=case.get("amount"),
        client_name=case.get("client"),
        text=case["text"],
        qualification=json.dumps(known, ensure_ascii=False) if known else None,
        events=[NS(created_at=at)],
        notes=[NS(text=t, author=NS(name=a), created_at=at) for a, t in case.get("notes", [])],
    )
    talk = [NS(role=r, text=t, created_at=at) for r, t in case.get("talk", [])]
    return insight.deal_context(lead, talk, now)


async def run_next(case: dict) -> dict:
    deal = deal_text(case["input"], datetime.now(UTC))
    step, _ = await asyncio.to_thread(ai._ask, ai.NEXT_SYSTEM, ai.fenced(deal), ai.NextStep)
    output = step.model_dump()
    return {"output": output, "checks": checks.next_step(case["expect"], output, deal)}


async def run_chance(case: dict) -> dict:
    deal = deal_text(case["input"], datetime.now(UTC))
    guess, _ = await asyncio.to_thread(ai._ask, ai.CHANCE_SYSTEM, ai.fenced(deal), ai.DealChance)
    output = guess.model_dump()
    return {"output": output, "checks": checks.chance(case["expect"], output)}


async def run_estimate(case: dict) -> dict:
    estimate, _ = await asyncio.to_thread(
        ai._ask, ai.ESTIMATE_SYSTEM, ai.fenced(case["input"]["text"]), ai.Estimate
    )
    output = estimate.model_dump()
    return {"output": output, "checks": checks.estimate(case["expect"], output)}


async def run_ask(case: dict) -> dict:
    viewer = await db.get_user_by_login(case["input"].get("viewer", "demo-owner"))
    question = case["input"]["question"]
    calls = []
    real = ask.Tools.call

    async def spy(self, name, arguments):
        result = await real(self, name, arguments)
        calls.append({"name": name, "args": arguments, "result": result})
        return result

    ask.Tools.call = spy
    try:
        reply = await ask.ask(question, viewer)
    finally:
        ask.Tools.call = real
    answer = reply["answer"] if reply["answer"] != ask.FALLBACK else ""
    return {
        "output": {"answer": reply["answer"], "calls": calls},
        "checks": checks.ask(case["expect"], question, answer, calls),
    }


async def prepare_ask() -> None:
    await asyncio.to_thread(command.upgrade, Config(str(HERE.parent / "alembic.ini")), "head")
    await demo.seed()


def previous(suite_runs: Path) -> dict | None:
    runs = sorted(suite_runs.glob("*.json"))
    return json.loads(runs[-1].read_text("utf-8")) if runs else None


async def main() -> None:
    cases = [
        json.loads(line)
        for line in (HERE / f"{args.suite}.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    cases = [c for c in cases if not args.only or c["id"] == args.only][: args.limit]

    need = len(cases) * COST[args.suite]
    left = free_left() if args.model.endswith(":free") else None
    if left is not None:
        print(f"Бесплатных запросов на сегодня: {left}, прогону нужно около {need}")
        if left < need:
            print(
                "Не хватит: прогон оборвётся на середине. Уменьшите --limit или подождите сброса."
            )
            return

    if args.suite == "ask":
        await prepare_ask()
    run = {"next": run_next, "chance": run_chance, "ask": run_ask, "estimate": run_estimate}[
        args.suite
    ]

    results = []
    for case in cases:
        try:
            result = await run(case)
        except Exception as e:
            result = {"output": None, "checks": {"ответ получен": False}, "error": repr(e)}
        result = {"id": case["id"], **result}
        results.append(result)
        failed = [name for name, ok in result["checks"].items() if not ok]
        print(f"{'FAIL' if failed else 'OK  '} {case['id']:<26} {', '.join(failed)}")

    passed = sum(all(r["checks"].values()) for r in results)
    total = {}
    for r in results:
        for name, ok in r["checks"].items():
            total.setdefault(name, []).append(ok)
    score = passed / len(results) * 100
    print(f"\nКейсов прошло: {passed} из {len(results)} ({score:.0f}%), модель {args.model}")
    for name, oks in sorted(total.items(), key=lambda item: sum(item[1]) / len(item[1])):
        if not all(oks):
            print(f"  {name}: {sum(oks)} из {len(oks)}")

    suite_runs = RUNS / args.suite
    before = previous(suite_runs)
    if before:
        delta = score - before["score"]
        print(f"Прошлый прогон: {before['score']:.0f}% ({delta:+.0f}), {before['model']}")
    suite_runs.mkdir(parents=True, exist_ok=True)
    path = suite_runs / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.json"
    payload = {
        "model": args.model,
        "score": round(score, 1),
        "cases": len(results),
        "results": results,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), "utf-8")
    print(f"Сохранено: {path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
