"""Обвязка над продуктом: зовёт ту же функцию разметки, что и CRM, ничего не подправляя."""

import asyncio


async def run(case_input: dict) -> dict:
    from ai import analyze_lead_usage

    markup, usage = await asyncio.to_thread(analyze_lead_usage, case_input["text"])
    return {"output": markup.model_dump(), "usage": usage}
