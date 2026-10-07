import os
import ssl

import certifi
import httpx

from observability import log

API = "https://platform-api2.max.ru"
TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
CHATS = [
    int(chat)
    for chat in os.environ.get("MAX_NOTIFY_CHAT_IDS", "").replace(" ", "").split(",")
    if chat.lstrip("-").isdigit()
]
CA = os.path.join(os.path.dirname(__file__), "certs", "russian_trusted_root_ca.pem")
TRANSPORT: httpx.AsyncBaseTransport | None = None


def enabled() -> bool:
    return bool(TOKEN and CHATS)


def _trust() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(CA)
    return context


def body(text: str, button: tuple[str, str] | None = None) -> dict:
    message: dict = {"text": text[:4000], "format": "html"}
    if button:
        message["attachments"] = [
            {
                "type": "inline_keyboard",
                "payload": {"buttons": [[{"type": "link", "text": button[0], "url": button[1]}]]},
            }
        ]
    return message


async def send(chat_id: int, text: str, button: tuple[str, str] | None = None) -> bool:
    try:
        async with httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": TOKEN},
            verify=_trust() if TRANSPORT is None else True,
            transport=TRANSPORT,
            timeout=10,
        ) as client:
            response = await client.post(
                "/messages",
                params={"chat_id": chat_id, "disable_link_preview": "true"},
                json=body(text, button),
            )
        if response.status_code != 200:
            log.warning("max.refused", chat_id=chat_id, status=response.status_code)
            return False
        return True
    except httpx.HTTPError:
        log.exception("max.send_failed", chat_id=chat_id)
        return False
