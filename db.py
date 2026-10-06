"""Подключение к базе и операции над данными.

Движок один, а баз две: локально sqlite-файл, в облаке Postgres. Разницу держит на себе
SQLAlchemy, поэтому в коде выше про это знать не нужно — меняется только DATABASE_URL.

Всё асинхронное: в продукте есть вебсокеты и фоновая разметка, и синхронный драйвер
блокировал бы цикл событий на каждом запросе к базе.
"""

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from auth import hash_password
from models import (
    CLOSED_STAGES,
    Attachment,
    Contact,
    Lead,
    LeadEvent,
    Message,
    Note,
    User,
    local,
)

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")

# Окно, внутри которого одинаковая заявка считается повтором, а не новой.
DEDUPE_SECONDS = int(os.environ.get("DEDUPE_SECONDS", 300))
STALE_DAYS = int(os.environ.get("STALE_DAYS", 3))


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Amvera и Postgres-хостинги отдают строку в формате postgresql://, а нам нужен
        # асинхронный драйвер. Подставляем его сами, чтобы не ловить это на деплое.
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    os.makedirs(DATA_DIR, exist_ok=True)
    return f"sqlite+aiosqlite:///{os.path.join(DATA_DIR, 'crm.db')}"


engine = create_async_engine(database_url(), future=True)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# --- сотрудники ----------------------------------------------------------------


async def get_user(user_id: int) -> User | None:
    async with Session() as session:
        return await session.get(User, user_id)


async def get_user_by_login(login: str) -> User | None:
    async with Session() as session:
        rows = await session.execute(select(User).where(User.login == login.strip().lower()))
        return rows.scalar_one_or_none()


async def all_users(active_only: bool = True) -> list[User]:
    query = select(User).order_by(User.name)
    if active_only:
        query = query.where(User.active.is_(True))
    async with Session() as session:
        return list((await session.execute(query)).scalars())


async def create_user(login: str, name: str, password: str, role: str = "manager") -> User:
    user = User(
        login=login.strip().lower(),
        name=name.strip(),
        password_hash=hash_password(password),
        role=role,
    )
    async with Session() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def set_access(user_id: int, own_only: bool, active: bool) -> User | None:
    async with Session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        user.own_only, user.active = own_only, active
        await session.commit()
        await session.refresh(user)
    return user


async def ensure_admin(login: str, password: str, name: str = "Владелец") -> User | None:
    """Первый вход в пустую систему.

    Без этого свежий контейнер оказывается запертым: пользователей нет, а завести их
    можно только войдя. Пароль берётся из окружения и в базу попадает уже хешем.
    """
    if not login or not password:
        return None
    existing = await get_user_by_login(login)
    if existing:
        return existing
    async with Session() as session:
        if (await session.execute(select(func.count()).select_from(User))).scalar_one():
            return None
    return await create_user(login, name, password, role="admin")


# --- клиенты -------------------------------------------------------------------


def contact_key(contact: str | None) -> str | None:
    """Нормализованный контакт: по нему повторные обращения склеиваются в человека.

    «+7 (999) 120-45-67» и «+79991204567» — один и тот же телефон, а «@Polina» и
    «@polina» — один и тот же телеграм. Без приведения к общему виду один клиент
    расползается по базе на несколько карточек.
    """
    if not contact:
        return None
    value = contact.strip().lower()
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    # Телефон: 8 и +7 — одна и та же российская восьмёрка.
    if len(digits) >= 10 and not re.search(r"[a-zа-я@]", value):
        tail = digits[-10:]
        return f"tel:{tail}"
    return value.lstrip("@") if value.startswith("@") else value


async def ensure_contact(name: str | None, contact: str | None) -> Contact | None:
    key = contact_key(contact)
    if key is None:
        return None
    async with Session() as session:
        rows = await session.execute(select(Contact).where(Contact.key == key))
        found = rows.scalar_one_or_none()
        if found is not None:
            # Имя могло приехать со второй заявкой: первый раз человек его не указал.
            if name and not found.name:
                found.name = name
                await session.commit()
                await session.refresh(found)
            return found

        made = Contact(key=key, name=name, contact=contact)
        session.add(made)
        await session.commit()
        await session.refresh(made)
    return made


async def get_contact(contact_id: int) -> Contact | None:
    async with Session() as session:
        rows = await session.execute(
            select(Contact)
            .where(Contact.id == contact_id)
            .options(selectinload(Contact.leads).selectinload(Lead.assignee))
        )
        return rows.scalar_one_or_none()


def contains(column, needle: str):
    """Поиск подстроки без оглядки на регистр — включая кириллицу.

    Штатный путь (`lower(колонка) like lower(:строка)`) на Postgres работает, а на
    sqlite — нет: тамошний lower() умеет только латиницу, и «Полина» по запросу «пол»
    не находится вообще. Поэтому регистр разбирает python, а базе достаются готовые
    варианты написания.
    """
    needle = needle.strip()
    variants = {needle, needle.lower(), needle.upper(), needle.capitalize()}
    return or_(*[column.like(f"%{variant}%") for variant in variants])


def can_see(lead: Lead, viewer: User | None) -> bool:
    return viewer is None or viewer.sees_all or lead.assignee_id in (None, viewer.id)


def _mine(viewer: User | None):
    if viewer is None or viewer.sees_all:
        return None
    return or_(Lead.assignee_id == viewer.id, Lead.assignee_id.is_(None))


async def all_contacts(
    search: str | None = None, limit: int = 200, viewer: User | None = None
) -> list[Contact]:
    query = select(Contact).options(selectinload(Contact.leads)).order_by(Contact.created_at.desc())
    if search:
        query = query.where(or_(contains(Contact.name, search), contains(Contact.contact, search)))
    if (scope := _mine(viewer)) is not None:
        query = query.where(Contact.leads.any(scope))
    async with Session() as session:
        return list((await session.execute(query.limit(limit))).scalars())


async def set_contact_note(contact_id: int, note: str) -> Contact | None:
    async with Session() as session:
        contact = await session.get(Contact, contact_id)
        if contact is None:
            return None
        contact.note = note.strip() or None
        await session.commit()
        await session.refresh(contact)
    return contact


# --- заявки --------------------------------------------------------------------


def fingerprint(text: str, contact: str | None) -> str:
    """Отпечаток заявки для отлова повторов.

    Регистр и лишние пробелы схлопываем: человек, отправивший форму дважды, второй раз
    вполне мог поправить перенос строки — для нас это та же заявка.
    """
    normal = re.sub(r"\s+", " ", f"{text} {contact or ''}").strip().lower()
    return hashlib.sha256(normal.encode()).hexdigest()


async def add_lead(
    client_name: str | None,
    client_contact: str | None,
    text: str,
    topic: str | None = None,
    urgency: str | None = None,
    draft_reply: str | None = None,
    source: str | None = None,
    utm: dict | None = None,
    consent: bool = False,
    user_id: int | None = None,
    assignee_id: int | None = None,
) -> tuple[Lead, bool]:
    """Сохранить заявку. Второе значение — новая она или повтор уже сохранённой.

    Повтор случается сам собой: человек жмёт «отправить» дважды, форма ретраится по
    таймауту, чужой сервис не дождался нашего ответа. Разметка каждого такого дубля —
    ещё один платный вызов модели и ещё одна карточка, которую человек прочитает зря.
    """
    mark = fingerprint(text, client_contact)
    async with Session() as session:
        since = datetime.now(UTC) - timedelta(seconds=DEDUPE_SECONDS)
        twin = await session.execute(
            select(Lead)
            .where(Lead.fingerprint == mark, Lead.created_at >= since)
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
        existing = twin.scalar_one_or_none()
        if existing is not None:
            return existing, False

    contact = await ensure_contact(client_name, client_contact)

    lead = Lead(
        client_name=client_name,
        client_contact=client_contact,
        text=text,
        topic=topic,
        urgency=urgency,
        draft_reply=draft_reply,
        source=source,
        fingerprint=mark,
        contact_id=contact.id if contact else None,
        assignee_id=assignee_id,
        consent_at=datetime.now(UTC) if consent else None,
        **{f"utm_{k}": (utm or {}).get(k) or None for k in ("source", "medium", "campaign")},
    )
    lead.events.append(LeadEvent(kind="created", note=source, user_id=user_id))
    async with Session() as session:
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
    return lead, True


def _stale():
    last = (
        select(func.max(LeadEvent.created_at)).where(LeadEvent.lead_id == Lead.id).scalar_subquery()
    )
    since = datetime.now(UTC) - timedelta(days=STALE_DAYS)
    return Lead.stage.not_in(CLOSED_STAGES) & (last < since)


def _feed_query(
    stage: str | None = None,
    urgency: str | None = None,
    source: str | None = None,
    assignee_id: int | None = None,
    search: str | None = None,
    overdue: bool = False,
    stale: bool = False,
    viewer: User | None = None,
):
    query = (
        select(Lead)
        .options(selectinload(Lead.assignee), selectinload(Lead.contact))
        .order_by(Lead.created_at.desc())
    )
    if stage:
        query = query.where(Lead.stage == stage)
    if urgency:
        query = query.where(Lead.urgency == urgency)
    if source:
        query = query.where(Lead.source == source)
    if assignee_id:
        query = query.where(Lead.assignee_id == assignee_id)
    if (scope := _mine(viewer)) is not None:
        query = query.where(scope)
    if stale:
        query = query.where(_stale())
    if overdue:
        query = query.where(Lead.due_at.is_not(None), Lead.due_at < datetime.now(UTC))
        query = query.where(Lead.stage.not_in(CLOSED_STAGES))
    if search:
        query = query.where(
            or_(
                contains(Lead.text, search),
                contains(Lead.client_name, search),
                contains(Lead.client_contact, search),
                contains(Lead.topic, search),
            )
        )
    return query


async def get_all_leads(limit: int = 200, **filters) -> list[Lead]:
    async with Session() as session:
        rows = await session.execute(_feed_query(**filters).limit(limit))
        return list(rows.scalars())


async def board(limit_per_stage: int = 50, **filters) -> dict[str, list[Lead]]:
    """Заявки, разложенные по стадиям воронки — то, что рисует доска."""
    from models import STAGES

    result: dict[str, list[Lead]] = {}
    async with Session() as session:
        for stage in STAGES:
            rows = await session.execute(
                _feed_query(**{**filters, "stage": stage}).limit(limit_per_stage)
            )
            result[stage] = list(rows.scalars())
    return result


async def count_by_stage(source: str | None = None, viewer: User | None = None) -> dict[str, int]:
    """Счётчики стадий считает база группировкой, а не питон перебором ленты: лента
    ограничена лимитом, и счётчик по ней показывал бы «сколько влезло на экран»."""
    query = select(Lead.stage, func.count()).group_by(Lead.stage)
    if source:
        query = query.where(Lead.source == source)
    if (scope := _mine(viewer)) is not None:
        query = query.where(scope)
    async with Session() as session:
        return {stage: count for stage, count in await session.execute(query)}


async def get_lead(lead_id: int, full: bool = False) -> Lead | None:
    async with Session() as session:
        if not full:
            return await session.get(Lead, lead_id)
        rows = await session.execute(
            select(Lead)
            .where(Lead.id == lead_id)
            .options(
                selectinload(Lead.events).selectinload(LeadEvent.author),
                selectinload(Lead.notes).selectinload(Note.author),
                selectinload(Lead.assignee),
                selectinload(Lead.contact).selectinload(Contact.leads),
                selectinload(Lead.attachments).selectinload(Attachment.author),
            )
        )
        return rows.scalar_one_or_none()


async def _touch(lead_id: int, kind: str, note: str | None, user_id: int | None) -> None:
    async with Session() as session:
        session.add(LeadEvent(lead_id=lead_id, kind=kind, note=note, user_id=user_id))
        await session.commit()


async def set_stage(
    lead_id: int, stage: str, user_id: int | None = None, lost_reason: str | None = None
) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        if lead.stage != stage:
            lead.stage = stage
            lead.lost_reason = lost_reason if stage == "lost" else None
            session.add(
                LeadEvent(
                    lead_id=lead_id,
                    kind="stage",
                    note=lost_reason or stage,
                    user_id=user_id,
                )
            )
        await session.commit()
        await session.refresh(lead)
    return lead


async def set_assignee(lead_id: int, assignee_id: int | None, user_id: int | None = None):
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.assignee_id = assignee_id
        name = None
        if assignee_id:
            assignee = await session.get(User, assignee_id)
            name = assignee.name if assignee else None
        session.add(
            LeadEvent(lead_id=lead_id, kind="assigned", note=name or "снят", user_id=user_id)
        )
        await session.commit()
        await session.refresh(lead)
    return lead


async def set_amount(lead_id: int, amount: Decimal | None, user_id: int | None = None):
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.amount = amount
        session.add(
            LeadEvent(
                lead_id=lead_id,
                kind="amount",
                note=f"{amount:.0f}" if amount is not None else "снята",
                user_id=user_id,
            )
        )
        await session.commit()
        await session.refresh(lead)
    return lead


async def set_due(lead_id: int, due_at: datetime | None, user_id: int | None = None):
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.due_at = due_at
        lead.reminded_at = None
        session.add(
            LeadEvent(
                lead_id=lead_id,
                kind="due",
                note=local(due_at).strftime("%d.%m %H:%M") if due_at else "снят",
                user_id=user_id,
            )
        )
        await session.commit()
        await session.refresh(lead)
    return lead


async def add_note(lead_id: int, user_id: int | None, text: str) -> Note | None:
    text = text.strip()
    if not text:
        return None
    note = Note(lead_id=lead_id, user_id=user_id, text=text)
    async with Session() as session:
        session.add(note)
        session.add(LeadEvent(lead_id=lead_id, kind="note", note=text[:60], user_id=user_id))
        await session.commit()
        await session.refresh(note)
    return note


async def set_markup(
    lead_id: int, topic: str | None, urgency: str | None, draft_reply: str | None
) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.topic, lead.urgency, lead.draft_reply = topic, urgency, draft_reply
        session.add(LeadEvent(lead_id=lead_id, kind="marked", note=urgency))
        await session.commit()
        await session.refresh(lead)
    return lead


async def mark_failed(lead_id: int, reason: str) -> None:
    """Модель не ответила. Заявка на месте, но в истории это должно остаться:
    иначе непонятно, почему карточка без темы."""
    await _touch(lead_id, "mark_failed", reason[:200], None)


async def due_for_reminder(now: datetime | None = None) -> list[Lead]:
    async with Session() as session:
        rows = await session.execute(
            select(Lead)
            .options(selectinload(Lead.assignee))
            .where(
                Lead.due_at.is_not(None),
                Lead.due_at <= (now or datetime.now(UTC)),
                Lead.reminded_at.is_(None),
                Lead.stage.not_in(CLOSED_STAGES),
            )
            .order_by(Lead.due_at)
            .limit(50)
        )
        return list(rows.scalars())


async def mark_reminded(lead_id: int) -> None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is not None:
            lead.reminded_at = datetime.now(UTC)
            await session.commit()


async def mark_synced(lead_id: int, external_id: str, target: str) -> None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return
        lead.external_id = external_id[:80]
        note = f"{target} #{external_id}"[:200]
        session.add(LeadEvent(lead_id=lead_id, kind="synced", note=note))
        await session.commit()


async def mark_sync_failed(lead_id: int, reason: str) -> None:
    await _touch(lead_id, "sync_failed", reason[:200], None)


async def add_attachment(lead_id: int, user_id: int | None, name: str, size: int, path: str):
    item = Attachment(lead_id=lead_id, user_id=user_id, name=name, size=size, path=path)
    async with Session() as session:
        session.add(item)
        session.add(LeadEvent(lead_id=lead_id, kind="file", note=name[:200], user_id=user_id))
        await session.commit()
        await session.refresh(item)
    return item


async def get_attachment(attachment_id: int) -> Attachment | None:
    async with Session() as session:
        rows = await session.execute(
            select(Attachment)
            .where(Attachment.id == attachment_id)
            .options(selectinload(Attachment.lead))
        )
        return rows.scalar_one_or_none()


async def digest(limit: int = 5) -> dict:
    now = datetime.now(UTC)
    day_ago = now - timedelta(days=1)
    async with Session() as session:
        fresh = (
            await session.execute(select(func.count()).where(Lead.created_at >= day_ago))
        ).scalar_one()
        unassigned = (
            await session.execute(
                select(func.count()).where(Lead.stage == "new", Lead.assignee_id.is_(None))
            )
        ).scalar_one()
        won = (
            await session.execute(
                select(func.count(), func.sum(Lead.amount)).where(
                    Lead.stage == "won", Lead.updated_at >= day_ago
                )
            )
        ).one()
        overdue = list(
            (
                await session.execute(
                    select(Lead)
                    .where(
                        Lead.due_at.is_not(None),
                        Lead.due_at < now,
                        Lead.stage.not_in(CLOSED_STAGES),
                    )
                    .order_by(Lead.due_at)
                )
            ).scalars()
        )
        stale = list(
            (
                await session.execute(select(Lead).where(_stale()).order_by(Lead.created_at))
            ).scalars()
        )
    return {
        "fresh": fresh,
        "unassigned": unassigned,
        "won_count": won[0] or 0,
        "won_amount": Decimal(won[1] or 0),
        "overdue": overdue[:limit],
        "overdue_count": len(overdue),
        "stale": stale[:limit],
        "stale_count": len(stale),
    }


async def journal(
    user_id: int | None = None, kind: str | None = None, limit: int = 300
) -> list[LeadEvent]:
    query = (
        select(LeadEvent)
        .options(selectinload(LeadEvent.author), selectinload(LeadEvent.lead))
        .order_by(LeadEvent.created_at.desc(), LeadEvent.id.desc())
        .limit(limit)
    )
    if user_id:
        query = query.where(LeadEvent.user_id == user_id)
    if kind:
        query = query.where(LeadEvent.kind == kind)
    async with Session() as session:
        return list((await session.execute(query)).scalars())


# --- переписка с клиентом -------------------------------------------------------


async def add_message(lead_id: int, role: str, text: str) -> Message:
    message = Message(lead_id=lead_id, role=role, text=text.strip()[:4000])
    async with Session() as session:
        session.add(message)
        await session.commit()
        await session.refresh(message)
    return message


async def messages(lead_id: int, limit: int = 40) -> list[Message]:
    """Последние реплики по порядку. Хвост, а не начало: в длинном разговоре модели нужны
    свежие сообщения, а то, что клиент сказал в самом начале, уже лежит в квалификации."""
    async with Session() as session:
        rows = await session.execute(
            select(Message)
            .where(Message.lead_id == lead_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(rows.scalars().all()))


async def link_chat(lead_id: int, chat_id: int) -> Lead | None:
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.chat_id = chat_id
        await session.commit()
        await session.refresh(lead)
    return lead


async def lead_by_chat(chat_id: int) -> Lead | None:
    """Самая свежая заявка этого чата: человек мог оставить вторую, и отвечать надо по ней."""
    async with Session() as session:
        rows = await session.execute(
            select(Lead).where(Lead.chat_id == chat_id).order_by(Lead.id.desc()).limit(1)
        )
        return rows.scalar_one_or_none()


def qualification(lead: Lead) -> dict:
    try:
        return json.loads(lead.qualification) if lead.qualification else {}
    except ValueError:
        return {}


async def save_qualification(lead_id: int, fields: dict) -> dict:
    """Дописать выясненное. Пустые значения не затирают уже известное: модель, не
    услышав про бюджет в этой реплике, не должна стирать бюджет из прошлой."""
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return {}
        merged = qualification(lead) | {k: v for k, v in fields.items() if v}
        lead.qualification = json.dumps(merged, ensure_ascii=False)
        await session.commit()
    return merged


async def booked_calls(start: datetime, end: datetime) -> list[datetime]:
    """Занятые слоты созвонов: сроки открытых сделок внутри окна."""
    async with Session() as session:
        rows = await session.execute(
            select(Lead.due_at).where(
                Lead.due_at >= start, Lead.due_at < end, Lead.stage.not_in(CLOSED_STAGES)
            )
        )
        return [d if d.tzinfo else d.replace(tzinfo=UTC) for d in rows.scalars().all()]


async def mark_hot(lead_id: int, summary: str) -> Lead | None:
    """Ассистент передаёт лида человеку: флаг, стадия «в работе» и выжимка разговора
    комментарием, чтобы менеджер начал с неё, а не с чтения всей переписки."""
    async with Session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return None
        lead.hot = True
        if lead.stage == "new":
            lead.stage = "in_work"
            session.add(LeadEvent(lead_id=lead_id, kind="stage", note="in_work"))
        text = f"Ассистент: {summary.strip()}"[:2000]
        session.add(Note(lead_id=lead_id, user_id=None, text=text))
        session.add(LeadEvent(lead_id=lead_id, kind="note", note=text[:60]))
        await session.commit()
        await session.refresh(lead)
    return lead


# --- отчёт ---------------------------------------------------------------------


async def report(days: int = 30, viewer: User | None = None) -> dict:
    """Цифры, ради которых CRM вообще заводят: сколько пришло, сколько дошло до денег,
    где встало и как быстро отвечаем."""
    since = datetime.now(UTC) - timedelta(days=days)
    scope = _mine(viewer)
    period = Lead.created_at >= since if scope is None else (Lead.created_at >= since) & scope

    async with Session() as session:
        by_stage = {
            stage: count
            for stage, count in await session.execute(
                select(Lead.stage, func.count()).where(period).group_by(Lead.stage)
            )
        }
        by_source = {
            (source or "не указан"): count
            for source, count in await session.execute(
                select(Lead.source, func.count())
                .where(period)
                .group_by(Lead.source)
                .order_by(func.count().desc())
            )
        }
        by_user = [
            (name or "не назначен", count, total or 0)
            for name, count, total in await session.execute(
                select(User.name, func.count(Lead.id), func.sum(Lead.amount))
                .select_from(Lead)
                .join(User, Lead.assignee_id == User.id, isouter=True)
                .where(period)
                .group_by(User.name)
                .order_by(func.count(Lead.id).desc())
            )
        ]
        open_deals = Lead.stage.not_in(CLOSED_STAGES)
        in_work = (
            await session.execute(
                select(func.sum(Lead.amount)).where(
                    open_deals if scope is None else open_deals & scope
                )
            )
        ).scalar_one() or 0
        won = (
            await session.execute(select(func.sum(Lead.amount)).where(period, Lead.stage == "won"))
        ).scalar_one() or 0
        by_campaign = [
            (source, campaign or "без кампании", count, deals or 0, money or 0)
            for source, campaign, count, deals, money in await session.execute(
                select(
                    Lead.utm_source,
                    Lead.utm_campaign,
                    func.count(),
                    func.sum(case((Lead.stage == "won", 1), else_=0)),
                    func.sum(case((Lead.stage == "won", Lead.amount), else_=None)),
                )
                .where(period, Lead.utm_source.is_not(None))
                .group_by(Lead.utm_source, Lead.utm_campaign)
                .order_by(func.count().desc())
            )
        ]

        # Время до первой реакции человека: от создания заявки до первого события,
        # которое сделал сотрудник. Разметка моделью тут не считается — она не ответ.
        answered = await session.execute(
            select(Lead.created_at, func.min(LeadEvent.created_at))
            .select_from(Lead)
            .join(LeadEvent, LeadEvent.lead_id == Lead.id)
            .where(LeadEvent.user_id.is_not(None), period)
            .group_by(Lead.id, Lead.created_at)
        )

    waits = []
    for created, first in answered:
        if created is None or first is None:
            continue
        created = created if created.tzinfo else created.replace(tzinfo=UTC)
        first = first if first.tzinfo else first.replace(tzinfo=UTC)
        waits.append((first - created).total_seconds())

    total = sum(by_stage.values())
    return {
        "days": days,
        "total": total,
        "by_stage": by_stage,
        "by_source": by_source,
        "by_user": by_user,
        "by_campaign": by_campaign,
        "in_work_amount": Decimal(in_work),
        "won_amount": Decimal(won),
        "conversion": round(by_stage.get("won", 0) / total * 100) if total else 0,
        "answer_minutes": round(sum(waits) / len(waits) / 60) if waits else None,
        "answered_count": len(waits),
    }
