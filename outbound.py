"""Передача заявки во внешнюю CRM клиента: Битрикс24 или amoCRM."""

import asyncio
import os
import re

import httpx

import db
from observability import log

BITRIX_WEBHOOK = os.environ.get("BITRIX24_WEBHOOK", "").rstrip("/")
AMO_SUBDOMAIN = os.environ.get("AMOCRM_SUBDOMAIN", "").strip()
AMO_TOKEN = os.environ.get("AMOCRM_TOKEN", "").strip()
AMO_PIPELINE = os.environ.get("AMOCRM_PIPELINE_ID", "").strip()
DELAYS = (1.0, 3.0)
RETRY_CODES = {429, 500, 502, 503, 504}
TRANSPORT: httpx.AsyncBaseTransport | None = None


class SyncError(Exception):
    pass


def targets() -> list[str]:
    found = []
    if BITRIX_WEBHOOK:
        found.append("Битрикс24")
    if AMO_SUBDOMAIN and AMO_TOKEN:
        found.append("amoCRM")
    return found


def split_contact(contact: str | None) -> dict:
    value = (contact or "").strip()
    if not value:
        return {}
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 10 and not re.search(r"[a-zа-я@]", value.lower()):
        return {"phone": "+7" + digits[-10:] if len(digits) in (10, 11) else "+" + digits}
    if "@" in value and "." in value.split("@")[-1] and not value.startswith("@"):
        return {"email": value}
    return {"messenger": value}


def comment(lead) -> str:
    lines = [lead.text]
    meta = []
    if lead.topic:
        meta.append(f"Тема: {lead.topic}")
    if lead.urgency:
        meta.append(f"Срочность: {lead.urgency}")
    if lead.source:
        meta.append(f"Источник: {lead.source}")
    if lead.utm_source:
        utm = " / ".join([lead.utm_source, lead.utm_medium or "-", lead.utm_campaign or "-"])
        meta.append(f"UTM: {utm}")
    if lead.client_contact and "messenger" in split_contact(lead.client_contact):
        meta.append(f"Контакт: {lead.client_contact}")
    if meta:
        lines += ["", *meta]
    if lead.draft_reply:
        lines += ["", f"Черновик ответа от ИИ: {lead.draft_reply}"]
    return "\n".join(lines)


def title(lead) -> str:
    who = lead.client_name or lead.client_contact or "без имени"
    return f"{lead.topic or 'Заявка'} · {who}"[:250]


def bitrix_body(lead) -> dict:
    contact = split_contact(lead.client_contact)
    fields = {
        "TITLE": title(lead),
        "NAME": lead.client_name or "",
        "COMMENTS": comment(lead),
        "SOURCE_ID": "WEB",
        "SOURCE_DESCRIPTION": lead.source or "",
    }
    if "phone" in contact:
        fields["PHONE"] = [{"VALUE": contact["phone"], "VALUE_TYPE": "WORK"}]
    if "email" in contact:
        fields["EMAIL"] = [{"VALUE": contact["email"], "VALUE_TYPE": "WORK"}]
    for key in ("source", "medium", "campaign"):
        if value := getattr(lead, f"utm_{key}"):
            fields[f"UTM_{key.upper()}"] = value
    if lead.amount is not None:
        fields["OPPORTUNITY"] = float(lead.amount)
        fields["CURRENCY_ID"] = "RUB"
    return {"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}}


def amo_body(lead) -> list:
    contact = split_contact(lead.client_contact)
    person: dict = {"first_name": lead.client_name or lead.client_contact or "Без имени"}
    values = [
        {"field_code": kind.upper(), "values": [{"enum_code": "WORK", "value": contact[kind]}]}
        for kind in ("phone", "email")
        if kind in contact
    ]
    if values:
        person["custom_fields_values"] = values
    deal: dict = {"name": title(lead), "_embedded": {"contacts": [person]}}
    if lead.source:
        deal["_embedded"]["tags"] = [{"name": lead.source[:50]}]
    if lead.amount is not None:
        deal["price"] = int(lead.amount)
    if AMO_PIPELINE.isdigit():
        deal["pipeline_id"] = int(AMO_PIPELINE)
    return [deal]


async def _post(client: httpx.AsyncClient, url: str, payload, headers: dict | None = None):
    for attempt in range(len(DELAYS) + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.TransportError as e:
            if attempt == len(DELAYS):
                raise SyncError(f"нет связи: {e.__class__.__name__}") from e
        else:
            if response.status_code not in RETRY_CODES or attempt == len(DELAYS):
                return response
        await asyncio.sleep(DELAYS[attempt])
    raise SyncError("исчерпаны попытки")


async def to_bitrix(client: httpx.AsyncClient, lead) -> str:
    response = await _post(client, f"{BITRIX_WEBHOOK}/crm.lead.add.json", bitrix_body(lead))
    data = response.json() if response.content else {}
    if response.status_code != 200 or "result" not in data:
        reason = data.get("error_description") or data.get("error") or ""
        raise SyncError(f"{response.status_code} {reason}".strip())
    return str(data["result"])


async def to_amo(client: httpx.AsyncClient, lead) -> str:
    base = f"https://{AMO_SUBDOMAIN}.amocrm.ru/api/v4"
    headers = {"Authorization": f"Bearer {AMO_TOKEN}"}
    response = await _post(client, f"{base}/leads/complex", amo_body(lead), headers)
    if response.status_code != 200:
        raise SyncError(f"{response.status_code} {response.text[:120]}")
    deal_id = response.json()[0]["id"]
    note = [{"entity_id": deal_id, "note_type": "common", "params": {"text": comment(lead)}}]
    await _post(client, f"{base}/leads/notes", note, headers)
    return str(deal_id)


async def push(lead) -> None:
    if not targets() or lead.external_id:
        return
    async with httpx.AsyncClient(timeout=10, transport=TRANSPORT) as client:
        for target, send in (("Битрикс24", to_bitrix), ("amoCRM", to_amo)):
            if target not in targets():
                continue
            try:
                external_id = await send(client, lead)
            except (SyncError, httpx.HTTPError, ValueError, KeyError, IndexError) as e:
                log.warning("sync.failed", lead_id=lead.id, target=target, reason=str(e))
                await db.mark_sync_failed(lead.id, f"{target}: {e}")
                continue
            log.info("sync.ok", lead_id=lead.id, target=target, external_id=external_id)
            await db.mark_synced(lead.id, external_id, target)
