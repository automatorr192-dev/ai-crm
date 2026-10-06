"""Telegram-канал ассистента.

Клиент оставляет заявку на сайте и получает кнопку «Продолжить в Telegram». В ссылке
лежит id заявки с подписью: без подписи любой мог бы подставить чужой номер и читать
чужую переписку с ассистентом. Дальше бот живёт фоновой задачей в lifespan приложения,
как и в остальных продуктах портфолио: отдельный контейнер под него на Amvera — лишние
деньги каждый месяц.
"""

import asyncio
import hashlib
import hmac
import os

import agent
import db
from hub import hub
from observability import log

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
USERNAME = os.environ.get("TG_BOT_USERNAME", "").lstrip("@")
_SECRET = (os.environ.get("SESSION_SECRET") or os.environ.get("WEBHOOK_SECRET") or "").encode()

_bot = None


def enabled() -> bool:
    return bool(TOKEN and USERNAME and _SECRET)


def _sign(lead_id: int) -> str:
    return hmac.new(_SECRET, f"lead:{lead_id}".encode(), hashlib.sha256).hexdigest()[:16]


def start_payload(lead_id: int) -> str:
    return f"l{lead_id}_{_sign(lead_id)}"


def read_payload(payload: str | None) -> int | None:
    """id заявки из параметра /start или None, если подпись не сходится."""
    if not payload or not payload.startswith("l") or "_" not in payload:
        return None
    raw_id, sig = payload[1:].split("_", 1)
    if not raw_id.isdigit():
        return None
    lead_id = int(raw_id)
    return lead_id if hmac.compare_digest(sig, _sign(lead_id)) else None


def start_link(lead_id: int) -> str | None:
    if not enabled():
        return None
    return f"https://t.me/{USERNAME}?start={start_payload(lead_id)}"


async def send(
    chat_id: int, text: str, html: bool = False, button: tuple[str, str] | None = None
) -> bool:
    if _bot is None:
        return False
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

    markup = None
    if button:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=button[0], url=button[1])]]
        )
    try:
        await _bot.send_message(
            chat_id,
            text,
            parse_mode="HTML" if html else None,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return True
    except Exception:
        log.exception("tg.send_failed", chat_id=chat_id)
        return False


async def _answer(lead_id: int, chat_id: int, text: str | None) -> None:
    if _bot is not None:
        await _bot.send_chat_action(chat_id, "typing")
    try:
        answer = await agent.reply(lead_id, text)
    except Exception:
        log.exception("agent.failed", lead_id=lead_id)
        answer = agent.FALLBACK
    lead = await db.get_lead(lead_id)
    await hub.send("lead.message", {"id": lead_id, "hot": bool(lead and lead.hot)})
    if answer:
        await send(chat_id, answer)


def _router():
    from aiogram import F, Router
    from aiogram.filters import Command, CommandObject, CommandStart
    from aiogram.types import Message

    team = Router()
    router = Router()
    router.message.filter(F.chat.type == "private")

    @team.message(Command("id"))
    async def chat_id(message: Message):
        await message.answer(f"ID этого чата: {message.chat.id}. Впишите его в NOTIFY_CHAT_IDS.")

    @router.message(CommandStart(deep_link=True))
    async def start_with_lead(message: Message, command: CommandObject):
        lead_id = read_payload(command.args)
        if lead_id is None or await db.link_chat(lead_id, message.chat.id) is None:
            await message.answer(
                "Не нашёл вашу заявку. Оставьте её на сайте, и вернитесь по кнопке."
            )
            return
        log.info("tg.linked", lead_id=lead_id)
        await _answer(lead_id, message.chat.id, None)

    @router.message(CommandStart())
    async def start_plain(message: Message):
        await message.answer("Здравствуйте! Оставьте заявку на сайте, и я продолжу разговор здесь.")

    @router.message(F.text)
    async def text(message: Message):
        lead = await db.lead_by_chat(message.chat.id)
        if lead is None:
            await message.answer("Сначала оставьте заявку на сайте, по ней я и продолжу.")
            return
        await _answer(lead.id, message.chat.id, message.text)

    team.include_router(router)
    return team


async def run() -> None:
    """Polling, а не вебхук: про вебхуки Telegram у Amvera документации нет, а polling
    работает из любого контейнера без публичного адреса."""
    global _bot
    from aiogram import Bot, Dispatcher

    _bot = Bot(TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(_router())
    try:
        await dispatcher.start_polling(_bot, handle_signals=False)
    except asyncio.CancelledError:
        pass
    finally:
        await _bot.session.close()
        _bot = None
