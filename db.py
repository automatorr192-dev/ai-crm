"""Подключение к базе и операции над данными.

Движок один, а баз две: локально sqlite-файл, в облаке Postgres. Разницу держит на себе
SQLAlchemy, поэтому в коде выше про это знать не нужно — меняется только DATABASE_URL.

Всё асинхронное: в продукте есть вебсокеты и фоновая разметка, и синхронный драйвер
блокировал бы цикл событий на каждом запросе к базе.
"""

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from auth import hash_password
from models import CLOSED_STAGES, Contact, Lead, LeadEvent, Note, User

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")

# Окно, внутри которого одинаковая заявка считается повтором, а не новой.
DEDUPE_SECONDS = int(os.environ.get("DEDUPE_SECONDS", 300))


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


async def all_contacts(search: str | None = None, limit: int = 200) -> list[Contact]:
    query = select(Contact).options(selectinload(Contact.leads)).order_by(Contact.created_at.desc())
    if search:
        query = query.where(or_(contains(Contact.name, search), contains(Contact.contact, search)))
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
    )
    lead.events.append(LeadEvent(kind="created", note=source))
    async with Session() as session:
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
    return lead, True


def _feed_query(
    stage: str | None = None,
    urgency: str | None = None,
    source: str | None = None,
    assignee_id: int | None = None,
    search: str | None = None,
    overdue: bool = False,
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


async def count_by_stage(source: str | None = None) -> dict[str, int]:
    """Счётчики стадий считает база группировкой, а не питон перебором ленты: лента
    ограничена лимитом, и счётчик по ней показывал бы «сколько влезло на экран»."""
    query = select(Lead.stage, func.count()).group_by(Lead.stage)
    if source:
        query = query.where(Lead.source == source)
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
        session.add(
            LeadEvent(
                lead_id=lead_id,
                kind="due",
                note=due_at.strftime("%d.%m %H:%M") if due_at else "снят",
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


# --- отчёт ---------------------------------------------------------------------


async def report(days: int = 30) -> dict:
    """Цифры, ради которых CRM вообще заводят: сколько пришло, сколько дошло до денег,
    где встало и как быстро отвечаем."""
    since = datetime.now(UTC) - timedelta(days=days)

    async with Session() as session:
        by_stage = {
            stage: count
            for stage, count in await session.execute(
                select(Lead.stage, func.count())
                .where(Lead.created_at >= since)
                .group_by(Lead.stage)
            )
        }
        by_source = {
            (source or "не указан"): count
            for source, count in await session.execute(
                select(Lead.source, func.count())
                .where(Lead.created_at >= since)
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
                .where(Lead.created_at >= since)
                .group_by(User.name)
                .order_by(func.count(Lead.id).desc())
            )
        ]
        in_work = (
            await session.execute(
                select(func.sum(Lead.amount)).where(Lead.stage.not_in(CLOSED_STAGES))
            )
        ).scalar_one() or 0
        won = (
            await session.execute(
                select(func.sum(Lead.amount)).where(Lead.stage == "won", Lead.created_at >= since)
            )
        ).scalar_one() or 0

        # Время до первой реакции человека: от создания заявки до первого события,
        # которое сделал сотрудник. Разметка моделью тут не считается — она не ответ.
        answered = await session.execute(
            select(Lead.created_at, func.min(LeadEvent.created_at))
            .select_from(Lead)
            .join(LeadEvent, LeadEvent.lead_id == Lead.id)
            .where(LeadEvent.user_id.is_not(None), Lead.created_at >= since)
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
        "in_work_amount": Decimal(in_work),
        "won_amount": Decimal(won),
        "conversion": round(by_stage.get("won", 0) / total * 100) if total else 0,
        "answer_minutes": round(sum(waits) / len(waits) / 60) if waits else None,
        "answered_count": len(waits),
    }
