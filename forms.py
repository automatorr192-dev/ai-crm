"""Заявка из чужой формы: Тильда, Contact Form 7, конструкторы и n8n."""

import re
from urllib.parse import unquote

NAME_KEYS = ("name", "your-name", "fio", "имя", "фио", "client_name")
PHONE_KEYS = ("phone", "tel", "your-phone", "телефон", "phone_number")
EMAIL_KEYS = ("email", "e-mail", "your-email", "почта", "mail")
TEXT_KEYS = ("comments", "comment", "message", "your-message", "text", "textarea", "сообщение")
CONSENT_KEYS = ("consent", "agree", "agreement", "soglasie", "согласие", "checkbox")
SERVICE_KEYS = {"tranid", "formid", "formname", "cookies", "test", "your-subject", "api_key", "key"}
UTM = ("source", "medium", "campaign")


def _pick(data: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _utm_from_cookies(raw: str) -> dict:
    text = raw
    for _ in range(3):
        text = unquote(text)
    found = {}
    for key in UTM:
        match = re.search(rf"utm_{key}=([^|&;]+)", text)
        if match:
            found[key] = match.group(1).strip()[:120]
    return found


def parse(raw: dict, source: str | None = None) -> dict | None:
    data = {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if v is not None}
    if data.get("test") == "test" and len(data) <= 2:
        return None

    name = _pick(data, NAME_KEYS)
    phone, email = _pick(data, PHONE_KEYS), _pick(data, EMAIL_KEYS)
    contact = phone or email
    text = _pick(data, TEXT_KEYS)
    used = set(NAME_KEYS + PHONE_KEYS + EMAIL_KEYS + TEXT_KEYS + CONSENT_KEYS) | SERVICE_KEYS
    extra = [
        f"{key}: {value}"
        for key, value in data.items()
        if key not in used and not key.startswith("utm_") and value
    ]
    if phone and email:
        extra.append(f"почта: {email}")
    fallback = f"Заявка с формы {data.get('formid', '')}".strip()
    body = "\n".join(filter(None, [text, *extra])) or fallback

    utm = {key: data[f"utm_{key}"][:120] for key in UTM if data.get(f"utm_{key}")}
    if not utm and data.get("cookies"):
        utm = _utm_from_cookies(data["cookies"])

    if not source:
        if "tranid" in data or "formid" in data:
            source = "tilda"
        elif any(key.startswith("your-") for key in data):
            source = "wordpress"
        else:
            source = "форма"

    consent = _pick(data, CONSENT_KEYS)
    return {
        "text": body[:5000],
        "name": name[:200] if name else None,
        "contact": contact[:200] if contact else None,
        "source": source[:60],
        "utm_source": utm.get("source"),
        "utm_medium": utm.get("medium"),
        "utm_campaign": utm.get("campaign"),
        "consent": bool(consent) and consent.lower() not in ("0", "no", "false", "нет", "off"),
    }
