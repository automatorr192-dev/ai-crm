import asyncio
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select

import db
from auth import hash_password
from models import Attachment, Contact, Lead, LeadEvent, Message, Note, User, seconds_until
from observability import log

ENABLED = os.environ.get("DEMO_MODE", "") == "1"
RESET_HOUR = int(os.environ.get("DEMO_RESET_HOUR", 4))

PEOPLE = {
    "owner": ("demo-owner", "Анна Котова", "admin", False),
    "irina": ("demo-irina", "Ирина Соколова", "manager", False),
    "oleg": ("demo-oleg", "Олег Миронов", "manager", True),
    "buh": ("demo-buh", "Бухгалтерия", "viewer", False),
}
ENTRANCES = {"owner": "owner", "manager": "oleg"}

UTM = {
    "ya": ("yandex", "cpc", "crm-uslugi"),
    "vk": ("vk", "social", "osen-2026"),
    "go": ("google", "cpc", "brand"),
    "tg": ("telegram", "channel", "post-crm"),
}

LEADS = [
    (
        29,
        "Полина Смирнова",
        "@polina_beauty",
        "Салон на 4 мастера, клиенты пишут в директ и вотсап, половину теряем. Нужна запись и напоминания.",
        "сайт",
        "ya",
        "запись в салон",
        "high",
        "won",
        85000,
        "irina",
        None,
        "Подписали договор, старт в понедельник",
        None,
    ),
    (
        27,
        "Олег Петров",
        "+7 916 220-14-88",
        "Автосервис на 3 поста, заявки с Авито и сайта теряются, нужен единый список и напоминания мастерам.",
        "звонок",
        None,
        "заявки автосервиса",
        "medium",
        "won",
        120000,
        "oleg",
        None,
        "Смету согласовали, оплата 50%",
        None,
    ),
    (
        26,
        "Студия «Линия»",
        "anna@studio-line.ru",
        "Мебельная студия: CRM с расчётом сметы и выгрузкой в Excel для бухгалтерии.",
        "tilda",
        "vk",
        "CRM для мебели",
        "medium",
        "lost",
        60000,
        "irina",
        "выбрали готовое решение",
        None,
        None,
    ),
    (
        24,
        "Игорь Белов",
        "@igor_fit",
        "Фитнес-клуб, нужен бот записи на пробное занятие и учёт лидов из рекламы.",
        "сайт",
        "ya",
        "бот для фитнеса",
        "low",
        "won",
        45000,
        "oleg",
        None,
        None,
        None,
    ),
    (
        22,
        "Марина Ковалёва",
        "+7 903 555-10-20",
        "Стоматология, две клиники. Есть amoCRM, хотим чтобы заявки с сайта сами создавали сделки.",
        "wordpress",
        "go",
        "интеграция amoCRM",
        "high",
        "won",
        70000,
        "irina",
        None,
        "Интеграция сдана, ждём отзыв",
        None,
    ),
    (
        20,
        "Денис Фролов",
        "@den_frolov",
        "Школа английского: заявки с сайта и из телеграма, нужно видеть, кто из менеджеров перезвонил.",
        "сайт",
        "tg",
        "контроль звонков",
        "medium",
        "lost",
        40000,
        "oleg",
        "дорого",
        None,
        None,
    ),
    (
        18,
        "Кофейня «Зерно»",
        "zerno.coffee@mail.ru",
        "Сеть из 3 кофеен, хотим собирать отзывы гостей и отвечать на них из одного места.",
        "почта",
        None,
        "отзывы гостей",
        "low",
        "waiting",
        35000,
        "irina",
        None,
        "Отправила КП, ждём решения собственника",
        -48,
    ),
    (
        17,
        "Виктория Ли",
        "+7 925 101-77-31",
        "Клининговая компания, 6 бригад. Нужно распределять заявки по районам и видеть загрузку.",
        "tilda",
        "ya",
        "распределение заявок",
        "high",
        "in_work",
        95000,
        "irina",
        None,
        "Созвонились, готовим схему районов",
        26,
    ),
    (
        15,
        "Артём Захаров",
        "@zakharov_law",
        "Юридическая фирма: заявки с сайта, нужна квалификация и запись на консультацию без звонков.",
        "сайт",
        "go",
        "запись на консультацию",
        "medium",
        "waiting",
        55000,
        "oleg",
        None,
        None,
        -30,
    ),
    (
        14,
        "Детский центр «Капитошка»",
        "+7 912 340-55-66",
        "Хотим, чтобы родители записывались на пробное занятие и получали напоминания.",
        "звонок",
        None,
        "запись на пробное",
        "medium",
        "won",
        38000,
        "irina",
        None,
        None,
        None,
    ),
    (
        13,
        "Елена Гусева",
        "@guseva_flowers",
        "Доставка цветов, заказы из инстаграма и с сайта путаются, нужен один список со статусами.",
        "сайт",
        "vk",
        "заказы цветов",
        "high",
        "in_work",
        52000,
        "oleg",
        None,
        "Показал демо, понравилась доска",
        4,
    ),
    (
        11,
        "Ветклиника «Айболит»",
        "info@aibolit-vet.ru",
        "Нужна запись к врачам и напоминания о прививках клиентам.",
        "wordpress",
        "ya",
        "напоминания о прививках",
        "medium",
        "lost",
        48000,
        "irina",
        "отложили до весны",
        None,
        None,
    ),
    (
        10,
        "Максим Орлов",
        "+7 999 120-45-67",
        "Ремонт квартир под ключ: заявки с трёх сайтов, нужен общий учёт и отчёт по рекламе.",
        "tilda",
        "ya",
        "учёт заявок ремонта",
        "high",
        "in_work",
        110000,
        "irina",
        None,
        "Нужен отчёт по кампаниям Директа",
        -5,
    ),
    (
        9,
        "Ольга Сидорова",
        "@olga_tours",
        "Турагентство, менеджеры ведут клиентов в личных телеграмах, хочу видеть всю переписку.",
        "сайт",
        "tg",
        "контроль переписки",
        "medium",
        "waiting",
        65000,
        "oleg",
        None,
        None,
        None,
    ),
    (
        8,
        "Фотостудия «Свет»",
        "svet.photo@yandex.ru",
        "Бронирование залов и предоплата, сейчас всё в гугл-таблице.",
        "почта",
        None,
        "бронь залов",
        "low",
        "in_work",
        30000,
        "oleg",
        None,
        None,
        50,
    ),
    (
        6,
        "Руслан Ахмедов",
        "+7 917 404-12-90",
        "Автошкола, заявки с сайта и звонки, нужно напоминать о занятиях и оплатах.",
        "звонок",
        None,
        "напоминания ученикам",
        "medium",
        "in_work",
        42000,
        "irina",
        None,
        None,
        -2,
    ),
    (
        5,
        "Наталья Белова",
        "@belova_buh",
        "Бухгалтерская компания: клиенты присылают документы куда попало, нужен приём заявок и файлов.",
        "сайт",
        "go",
        "приём документов",
        "medium",
        "new",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        4,
        "Студия йоги «Прана»",
        "+7 926 300-80-80",
        "Абонементы и пробные занятия, хотим автоматически дожимать тех, кто не пришёл.",
        "tilda",
        "vk",
        "дожим пробных",
        "low",
        "new",
        None,
        "oleg",
        None,
        None,
        None,
    ),
    (
        3,
        "Кирилл Новиков",
        "@kirill_shop",
        "Интернет-магазин одежды, заявки на опт приходят на почту и теряются.",
        "wordpress",
        "ya",
        "оптовые заявки",
        "high",
        "new",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        2,
        "Анастасия Романова",
        "+7 985 777-21-43",
        "Салон мебели на заказ, нужно чтобы замерщик видел свои заявки с телефона.",
        "сайт",
        "ya",
        "заявки замерщику",
        "medium",
        "in_work",
        75000,
        "oleg",
        None,
        None,
        20,
    ),
    (
        1,
        "Павел Егоров",
        "@pavel_cars",
        "Детейлинг-центр, запись через сайт и звонки, нужен календарь и напоминания.",
        "сайт",
        "go",
        "запись в детейлинг",
        "medium",
        "new",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        0,
        "Светлана Ким",
        "kim.sv@gmail.com",
        "Онлайн-школа, нужно чтобы заявки с лендингов сразу попадали менеджеру в телеграм.",
        "tilda",
        "tg",
        "заявки онлайн-школы",
        "high",
        "new",
        None,
        None,
        None,
        None,
        None,
    ),
]

HOT = {
    "index": 12,
    "talk": [
        (
            "agent",
            "Здравствуйте, Максим! Я ассистент студии. Уточню пару деталей, чтобы менеджер позвонил уже с решением. Сколько заявок в месяц приходит с трёх сайтов?",
        ),
        ("client", "Около 150, ещё звонки"),
        ("agent", "Понял. Кто сейчас разбирает заявки и где ведёте учёт?"),
        ("client", "Два менеджера, всё в гугл таблице, отчёт по рекламе собираю руками"),
        ("agent", "Спасибо. Когда удобно созвониться с менеджером? Есть завтра 11:00 и 15:00."),
        ("client", "Давайте в 11"),
        ("agent", "Записал на завтра 11:00. Менеджер Ирина позвонит на этот номер."),
    ],
    "known": {
        "task": "общий учёт заявок с трёх сайтов и отчёт по рекламе",
        "business": "ремонт квартир, 2 менеджера",
        "budget": "до 120 000 ₽",
        "call": "завтра 11:00",
    },
}


def _draft(name: str, topic: str) -> str:
    first = None if "«" in name else name.split()[0]
    hello = f"Здравствуйте, {first}!" if first else "Здравствуйте!"
    return f"{hello} Спасибо за заявку про {topic}. Подскажите, когда удобно созвониться на 15 минут, чтобы разобрать задачу?"


async def seed() -> int:
    now = datetime.now(UTC)
    async with db.Session() as session:
        people = {}
        for key, (login, name, role, own_only) in PEOPLE.items():
            people[key] = User(
                login=login,
                name=name,
                role=role,
                own_only=own_only,
                password_hash=hash_password(secrets.token_hex(12)),
                created_at=now - timedelta(days=40),
            )
        session.add_all(people.values())
        await session.flush()
        owner = people["owner"]
        contacts: dict[str, Contact] = {}

        for index, row in enumerate(LEADS):
            (
                days,
                name,
                contact,
                text,
                source,
                utm,
                topic,
                urgency,
                stage,
                amount,
                who,
                lost,
                note,
                due,
            ) = row
            created = now - timedelta(days=days, hours=(index * 7) % 9 + 1, minutes=index * 13 % 50)
            key = db.contact_key(contact)
            if key not in contacts:
                contacts[key] = Contact(key=key, name=name, contact=contact, created_at=created)
                session.add(contacts[key])
                await session.flush()
            person = people.get(who) if who else None
            source_utm = UTM.get(utm, (None, None, None))
            lead = Lead(
                created_at=created,
                updated_at=created + timedelta(hours=6),
                client_name=name,
                client_contact=contact,
                text=text,
                topic=topic,
                urgency=urgency,
                draft_reply=_draft(name, topic),
                stage=stage,
                source=source,
                fingerprint=db.fingerprint(text, contact),
                contact_id=contacts[key].id,
                assignee_id=person.id if person else None,
                amount=Decimal(amount) if amount else None,
                due_at=now + timedelta(hours=due) if due is not None else None,
                lost_reason=lost,
                utm_source=source_utm[0],
                utm_medium=source_utm[1],
                utm_campaign=source_utm[2],
                consent_at=created if source in ("сайт", "tilda", "wordpress") else None,
            )
            session.add(lead)
            await session.flush()

            step = timedelta(minutes=4 + index % 11)
            events = [
                LeadEvent(lead_id=lead.id, kind="created", note=source, created_at=created),
                LeadEvent(
                    lead_id=lead.id,
                    kind="marked",
                    note=urgency,
                    created_at=created + timedelta(seconds=20),
                ),
            ]
            if person:
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="assigned",
                        note=person.name,
                        user_id=owner.id,
                        created_at=created + step,
                    )
                )
            actor = person or owner
            if stage != "new":
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="stage",
                        note="in_work",
                        user_id=actor.id,
                        created_at=created + step * 3,
                    )
                )
            if amount:
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="amount",
                        note=str(amount),
                        user_id=actor.id,
                        created_at=created + timedelta(days=1),
                    )
                )
            if stage in ("waiting", "won", "lost"):
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="stage",
                        note="waiting",
                        user_id=actor.id,
                        created_at=created + timedelta(days=2),
                    )
                )
            if stage in ("won", "lost"):
                final = lost or stage
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="stage",
                        note=final,
                        user_id=actor.id,
                        created_at=created + timedelta(days=min(days, 5)),
                    )
                )
            if note:
                moment = created + timedelta(days=1, hours=2)
                session.add(Note(lead_id=lead.id, user_id=actor.id, text=note, created_at=moment))
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="note",
                        note=note[:60],
                        user_id=actor.id,
                        created_at=moment,
                    )
                )
            if due is not None:
                events.append(
                    LeadEvent(
                        lead_id=lead.id,
                        kind="due",
                        note="напомнить",
                        user_id=actor.id,
                        created_at=created + timedelta(hours=3),
                    )
                )
            session.add_all(events)

            if index == HOT["index"]:
                lead.hot = True
                lead.qualification = json.dumps(HOT["known"], ensure_ascii=False)
                for offset, (role, said) in enumerate(HOT["talk"]):
                    session.add(
                        Message(
                            lead_id=lead.id,
                            role=role,
                            text=said,
                            created_at=created + timedelta(minutes=3 + offset),
                        )
                    )
                session.add(
                    Note(
                        lead_id=lead.id,
                        user_id=None,
                        text="Ассистент: 150 заявок в месяц с трёх сайтов, учёт в таблице, созвон завтра в 11:00.",
                        created_at=created + timedelta(minutes=12),
                    )
                )

        await session.commit()
    log.info("demo.seeded", leads=len(LEADS))
    return len(LEADS)


async def reset() -> int:
    async with db.Session() as session:
        for model in (Attachment, Message, Note, LeadEvent, Lead, Contact):
            await session.execute(delete(model))
        await session.execute(delete(User).where(User.login.like("demo-%")))
        await session.commit()
    return await seed()


async def ensure() -> None:
    async with db.Session() as session:
        has_leads = (await session.execute(select(func.count()).select_from(Lead))).scalar_one()
        has_people = (
            await session.execute(select(func.count()).where(User.login.like("demo-%")))
        ).scalar_one()
    if not has_leads or not has_people:
        await reset()


async def nightly() -> None:
    while True:
        await asyncio.sleep(seconds_until(RESET_HOUR))
        try:
            await reset()
        except Exception:
            log.exception("demo.reset_failed")


async def entrance(who: str) -> User | None:
    key = ENTRANCES.get(who)
    if key is None:
        return None
    return await db.get_user_by_login(PEOPLE[key][0])
