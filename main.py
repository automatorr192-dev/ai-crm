"""Консольный прогон приёма заявки — тот же путь, что и у вебхука, только без сети.

Нужен, чтобы посмотреть на разметку живой моделью, не поднимая форму и не подписывая
запросы. Боевой вход — POST /webhook/lead.
"""

import asyncio

from ai import analyze_lead
from db import add_lead, get_all_leads, set_markup
from observability import log, setup


async def intake(client_name: str, client_contact: str, text: str, source: str = "консоль"):
    """Заявка сохраняется в любом случае: разметка — приятное дополнение, а потерянный
    лид — потерянные деньги."""
    lead = await add_lead(client_name, client_contact, text, source=source)
    try:
        markup = await asyncio.to_thread(analyze_lead, text)
    except RuntimeError as e:
        log.warning("lead.enrich_failed", lead_id=lead.id, error=str(e))
        return lead
    await set_markup(lead.id, markup.topic, markup.urgency, markup.draft_reply)
    log.info("lead.enriched", lead_id=lead.id, urgency=markup.urgency)
    return lead


async def demo():
    await intake("Иван", "@ivan", "Здравствуйте, хочу бота для записи клиентов, срочно надо")
    await intake("Мария", "@maria", "а сколько стоит и есть ли рассрочка?")
    print("\n--- Все заявки в базе ---")
    for lead in await get_all_leads():
        print(f"[{lead.status}] {lead.client_name}: {lead.topic} ({lead.urgency})")


if __name__ == "__main__":
    setup()
    asyncio.run(demo())
