"""Уведомления команде в Telegram: новая заявка и наступивший срок по сделке."""

import asyncio
import html
import os
from datetime import datetime

import db
import maxbot
import tgbot
from models import local, seconds_until
from observability import log

CHATS = [
    int(chat)
    for chat in os.environ.get("NOTIFY_CHAT_IDS", "").replace(" ", "").split(",")
    if chat.lstrip("-").isdigit()
]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}
URGENCY = {"high": "высокая", "medium": "средняя", "low": "низкая"}
CHECK_EVERY = int(os.environ.get("REMIND_EVERY_SECONDS", 60))
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", 9))


def enabled() -> bool:
    return (bool(CHATS) and tgbot.enabled()) or maxbot.enabled()


def _button(lead_id: int | None, path: str = "") -> tuple[str, str] | None:
    if not PUBLIC_URL.startswith("https://"):
        return None
    return "Открыть в CRM", f"{PUBLIC_URL}{path or f'/leads/{lead_id}'}"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def new_lead_text(lead) -> str:
    who = html.escape(lead.client_name or "Без имени")
    source = html.escape(lead.source or "без источника")
    contact = f" · {html.escape(lead.client_contact)}" if lead.client_contact else ""
    lines = [f"{ICON.get(lead.urgency, '🆕')} <b>Новая заявка #{lead.id}</b> · {source}"]
    lines.append(f"<b>{who}</b>{contact}")
    if lead.topic:
        lines.append(f"{html.escape(lead.topic)} · срочность {URGENCY.get(lead.urgency, '—')}")
    if lead.utm_source:
        campaign = f" / {html.escape(lead.utm_campaign)}" if lead.utm_campaign else ""
        lines.append(f"Реклама: {html.escape(lead.utm_source)}{campaign}")
    lines.append("")
    lines.append(f"«{html.escape(_clip(lead.text, 400))}»")
    return "\n".join(lines)


def reminder_text(lead) -> str:
    assignee = lead.__dict__.get("assignee")
    what = lead.topic or _clip(lead.text, 80)
    lines = [
        f"⏰ <b>Срок по сделке #{lead.id}</b>",
        f"{html.escape(lead.title)} · {html.escape(what)}",
        f"Ответственный: {html.escape(assignee.name) if assignee else 'не назначен'}",
    ]
    return "\n".join(lines)


async def _broadcast(text: str, lead_id: int | None, path: str = "") -> bool:
    button = _button(lead_id, path)
    sent = False
    if tgbot.enabled():
        for chat in CHATS:
            sent = await tgbot.send(chat, text, html=True, button=button) or sent
    if maxbot.enabled():
        for chat in maxbot.CHATS:
            sent = await maxbot.send(chat, text, button) or sent
    return sent


def _money(value) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def digest_text(data: dict) -> str:
    lines = [f"☀️ <b>Сводка на {local(None):%d.%m}</b>"]
    lines.append(f"Новых за сутки: {data['fresh']}, не распределено: {data['unassigned']}")
    if data["won_count"]:
        lines.append(f"Закрыто сделок: {data['won_count']} на {_money(data['won_amount'])}")
    for key, title in (("overdue", "Просрочено"), ("stale", f"Без движения {db.STALE_DAYS}+ дня")):
        if not data[f"{key}_count"]:
            continue
        lines += ["", f"<b>{title}: {data[f'{key}_count']}</b>"]
        lines += [f"#{lead.id} {html.escape(lead.title)}" for lead in data[key]]
    if not data["overdue_count"] and not data["stale_count"]:
        lines += ["", "Просроченных и забытых сделок нет."]
    return "\n".join(lines)


async def send_digest() -> bool:
    if not enabled():
        return False
    return await _broadcast(digest_text(await db.digest()), None, "/leads?stale=true")


async def digests() -> None:
    while True:
        await asyncio.sleep(seconds_until(DIGEST_HOUR))
        try:
            await send_digest()
            log.info("notify.digest", at=datetime.now().isoformat(timespec="minutes"))
        except Exception:
            log.exception("notify.digest_failed")


async def new_lead(lead) -> None:
    if not enabled():
        return
    await _broadcast(new_lead_text(lead), lead.id)
    log.info("notify.new_lead", lead_id=lead.id)


async def remind_due() -> int:
    if not enabled():
        return 0
    count = 0
    for lead in await db.due_for_reminder():
        if await _broadcast(reminder_text(lead), lead.id):
            await db.mark_reminded(lead.id)
            count += 1
    return count


async def reminders() -> None:
    while True:
        await asyncio.sleep(CHECK_EVERY)
        try:
            sent = await remind_due()
            if sent:
                log.info("notify.reminders", count=sent)
        except Exception:
            log.exception("notify.reminders_failed")
