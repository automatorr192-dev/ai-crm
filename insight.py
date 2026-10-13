"""ИИ поверх сделки: следующий шаг и шанс дойти до оплаты.

Обе подсказки читают сделку одним и тем же текстом из deal_context и обе необязательны:
модель не ответила — подсказки нет, а сделка живёт, как жила без ИИ. Отдельный модуль,
а не app.py: пересчитывать сделку нужно и с экрана, и из бота, когда клиент пишет менеджеру.
"""

import asyncio
from datetime import UTC, datetime

import db
from ai import estimate_chance, suggest_next, write_offer
from hub import hub
from models import CLOSED_STAGES, STAGE_RU
from observability import log

KNOWN_RU = {
    "task": "задача",
    "business": "бизнес",
    "deadline": "срок",
    "budget": "бюджет",
    "call": "созвон",
}
ROLE_RU = {"client": "клиент", "agent": "ассистент", "manager": "менеджер"}


def deal_context(lead, talk, now: datetime | None = None) -> str:
    """Сделка текстом для модели: то, на что смотрит менеджер, когда решает, что дальше."""
    now = now or datetime.now(UTC)
    moments = [item.created_at for item in [*lead.events, *talk]]
    moments = [m if m.tzinfo else m.replace(tzinfo=UTC) for m in moments]
    lines = [f"Стадия: {STAGE_RU[lead.stage]}"]
    if lead.topic:
        lines.append(f"Тема: {lead.topic}")
    if lead.amount is not None:
        lines.append(f"Сумма: {lead.amount:,.0f} ₽".replace(",", " "))
    if moments:
        lines.append(f"Последнее движение: {(now - max(moments)).days} дн. назад")
    known = db.qualification(lead)
    facts = [f"{label} — {known[key]}" for key, label in KNOWN_RU.items() if known.get(key)]
    if facts:
        lines.append("Выяснено: " + "; ".join(facts))
    lines += ["", "Заявка:", lead.text]
    if talk:
        lines += ["", "Переписка, старое сверху:"]
        lines += [f"{ROLE_RU.get(m.role, m.role)}: {m.text}" for m in talk[-12:]]
    notes = sorted(lead.notes, key=lambda note: note.created_at)[-5:]
    if notes:
        lines += ["", "Комментарии команды, старое сверху:"]
        lines += [f"{n.author.name}: {n.text}" if n.author else n.text for n in notes]
    return "\n".join(lines)


async def _deal(lead_id: int) -> str | None:
    lead = await db.get_lead(lead_id, full=True)
    if lead is None or lead.stage in CLOSED_STAGES:
        return None
    return deal_context(lead, await db.messages(lead_id))


async def suggest(lead_id: int) -> bool:
    deal = await _deal(lead_id)
    if deal is None:
        return False
    try:
        step = await asyncio.to_thread(suggest_next, deal)
    except Exception:
        log.exception("lead.next_failed", lead_id=lead_id)
        return False
    await db.set_next_step(lead_id, step.model_dump())
    log.info("lead.next", lead_id=lead_id, remind_in_days=step.remind_in_days)
    return True


async def estimate(lead_id: int) -> bool:
    deal = await _deal(lead_id)
    if deal is None:
        return False
    try:
        chance = await asyncio.to_thread(estimate_chance, deal)
    except Exception:
        log.exception("lead.chance_failed", lead_id=lead_id)
        return False
    lead = await db.set_chance(lead_id, chance.percent, chance.plus[:3], chance.minus[:3])
    log.info("lead.chance", lead_id=lead_id, percent=chance.percent)
    if lead is not None:
        await hub.send(
            "lead.chance",
            {
                "id": lead_id,
                "assignee_id": lead.assignee_id,
                "chance": lead.chance,
                "reason": reason(lead),
            },
        )
    return True


async def rethink(lead_id: int) -> None:
    """Сделка сдвинулась: пересчитать и шаг, и шанс. Вызовы независимы и идут параллельно."""
    await asyncio.gather(suggest(lead_id), estimate(lead_id))


def split_price(amount, shares: list[int]) -> list[int]:
    """Сумма сделки по этапам пропорционально долям, с точностью до сотни рублей. Остаток
    округления уходит в последний этап: итог КП обязан совпасть с суммой сделки."""
    if not amount or not shares:
        return [0] * len(shares)
    total = int(amount)
    weight = sum(shares)
    prices = [round(total * share / weight, -2) for share in shares[:-1]]
    return [int(price) for price in prices] + [total - int(sum(prices))]


async def draft_offer(lead_id: int) -> bool:
    lead = await db.get_lead(lead_id, full=True)
    if lead is None:
        return False
    try:
        context = deal_context(lead, await db.messages(lead_id))
        draft = await asyncio.to_thread(write_offer, context)
    except Exception:
        log.exception("lead.offer_failed", lead_id=lead_id)
        return False
    await db.set_offer_draft(lead_id, draft.model_dump())
    log.info("lead.offer", lead_id=lead_id, lines=len(draft.lines))
    return True


def reason(lead) -> str | None:
    """Одна причина для карточки на доске: при шансе ниже половины — главная «против»,
    иначе главная «за». Полный список стоит в карточке сделки."""
    why = db.chance_why(lead)
    order = ("minus", "plus") if (lead.chance or 0) < 50 else ("plus", "minus")
    for key in order:
        if why.get(key):
            return why[key][0]
    return None
