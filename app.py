"""Сервис CRM: приём заявок и админка.

Форма живёт не здесь. Она на сайте — своём домене, своей статике, своём деплое —
и стучится сюда по сети, как стучалась бы форма на Тильде или чужой бот. Это не
усложнение ради красоты: пока форма отдаётся тем же приложением, что и админка, любая
починка админки требует передеплоя сайта, а падение сервиса уносит с собой и страницу,
на которую клиент пришёл с рекламы.
"""

import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

import db
import webhook
from ai import analyze_lead
from db import add_lead, count_by_status, get_all_leads, get_lead, set_markup, set_status
from hub import hub
from models import STATUSES, URGENCIES
from observability import log, setup

load_dotenv()
setup()

ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
security = HTTPBasic()

# Модель отвечает служебными словами. В интерфейсе человек читает по-русски.
URGENCY_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}
STATUS_RU = {
    "new": "новая",
    "in_work": "в работе",
    "answered": "отвечено",
    "closed": "закрыта",
}


# Подбор пароля: без задержки перебор идёт со скоростью сети. Считаем неудачи по адресу
# и после порога отвечаем 429 — на живой вход это не влияет, счётчик обнуляется при успехе.
MAX_FAILURES = int(os.environ.get("ADMIN_MAX_FAILURES", 10))
LOCKOUT_SECONDS = int(os.environ.get("ADMIN_LOCKOUT_SECONDS", 300))
_failures: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _too_many_failures(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failures[ip] = recent
    return len(recent) >= MAX_FAILURES


def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """В заявках лежат имена и контакты живых людей: отдавать их без пароля нельзя ни
    в демо, ни тем более в проде. compare_digest — чтобы пароль нельзя было подобрать
    по времени ответа."""
    if not ADMIN_USER or not ADMIN_PASSWORD:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Админка не настроена")

    ip = _client_ip(request)
    if _too_many_failures(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток. Подожди пару минут."
        )

    # Сравниваем байты: compare_digest на не-ASCII строке падает TypeError, и вместо
    # честного 401 админка отвечала бы 500 на логин с кириллицей.
    ok = secrets.compare_digest(
        credentials.username.encode(), ADMIN_USER.encode()
    ) & secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not ok:
        _failures.setdefault(ip, []).append(time.time())
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    _failures.pop(ip, None)
    return credentials.username


def migrate() -> None:
    """Схему заводят миграции, а не приложение.

    alembic.command синхронный, поэтому вызывается в потоке: на старте контейнера
    цикл событий уже крутится, и блокировать его нельзя.
    """
    config = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    command.upgrade(config, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(migrate)
    yield


def card(lead) -> dict:
    """Одна форма заявки для вебсокета и для первой отрисовки страницы."""
    return {
        "id": lead.id,
        "name": lead.client_name,
        "contact": lead.client_contact,
        "text": lead.text,
        "topic": lead.topic,
        "urgency": lead.urgency,
        "draft": lead.draft_reply,
        "status": lead.status,
        "source": lead.source,
        "time": lead.created_local,
    }


class LeadIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    name: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=60)


app = FastAPI(title="AI-CRM", lifespan=lifespan)

# Сайт с формой лежит на другом домене, поэтому браузер спросит разрешения перед
# отправкой. Список доменов задаётся руками: звёздочка тут означала бы «любая страница
# в интернете может слать заявки от имени формы».
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
    )

# Путь от файла, а не от рабочей папки: с относительным шаблоны терялись при запуске
# uvicorn из любого другого каталога.
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Админка -------------------------------------------------------------------


def _screen(request: Request, leads, counts: dict[str, int], demo: bool):
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "leads": [card(lead) for lead in leads],
            "counts": counts,
            "total": sum(counts.values()),
            "statuses": STATUSES,
            "urgencies": URGENCIES,
            "urgency_ru": URGENCY_RU,
            "status_ru": STATUS_RU,
            "demo": demo,
            # Куда бить формам и запросам за историей. Демо и боевая админка — это
            # разные маршруты с разной проверкой прав, а не один с флажком в форме:
            # флажок приходит из браузера, а значит его можно подменить.
            "base": "/demo" if demo else "",
            "home": "/demo" if demo else "/",
            "public_source": PUBLIC_SOURCE,
        },
    )


async def _events_of(lead_id: int) -> dict:
    lead = await get_lead(lead_id, with_events=True)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return {
        "events": [{"kind": e.kind, "note": e.note, "time": e.created_local} for e in lead.events]
    }


async def _apply_status(lead_id: int, status_to: str, back: str) -> RedirectResponse:
    if status_to not in STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный статус")
    lead = await set_status(lead_id, status_to)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    await hub.send("lead.status", card(lead))
    return RedirectResponse(back, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/")
async def admin(
    request: Request,
    status_filter: str | None = None,
    urgency: str | None = None,
    _: str = Depends(require_admin),
):
    leads = await get_all_leads(status=status_filter, urgency=urgency)
    return _screen(request, leads, await count_by_status(), demo=False)


@app.get("/leads/{lead_id}/events")
async def lead_events(lead_id: int, _: str = Depends(require_admin)):
    """История одной заявки. Отдельным запросом по клику, а не вместе с лентой:
    иначе на каждой отрисовке страницы база тянула бы события всех двухсот карточек."""
    return await _events_of(lead_id)


@app.post("/leads/{lead_id}/status")
async def change_status(lead_id: int, status_to: str = Form(...), _: str = Depends(require_admin)):
    return await _apply_status(lead_id, status_to, "/")


# --- Приём заявок --------------------------------------------------------------

MAX_BODY = 20_000


async def accept(data: LeadIn, source: str) -> tuple[dict, bool]:
    """Общий путь для всех входов: сохранить, показать, разметить фоном."""
    lead, is_new = await add_lead(
        client_name=data.name,
        client_contact=data.contact,
        text=data.text,
        source=source,
    )
    if not is_new:
        # Повтор той же заявки. Отвечаем тем же id, как будто приняли: отправитель
        # получает ожидаемый ответ, а второй карточки и второго вызова модели нет.
        log.info("lead.duplicate", lead_id=lead.id, source=source)
        return card(lead), False

    await hub.send("lead.new", card(lead))
    asyncio.create_task(enrich(lead.id))
    log.info("lead.accepted", lead_id=lead.id, source=source)
    return card(lead), True


@app.post("/webhook/lead")
async def incoming_lead(request: Request):
    """Заявка от чужого сервиса: бот, интеграция, форма на CMS с поддержкой вебхуков.

    Разметку моделью делаем в фоне и отвечаем сразу. Отправитель ждёт 200, а не наш поход
    к ИИ: не дождавшись, он пришлёт заявку ещё раз.
    """
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Слишком большое тело")

    try:
        webhook.check(
            body,
            request.headers.get("X-Timestamp"),
            request.headers.get("X-Signature"),
        )
    except webhook.BadSignature as e:
        log.warning("webhook.rejected", reason=str(e))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Подпись не сходится") from e

    try:
        payload = json.loads(body)
        data = LeadIn.model_validate(payload)
    except (ValueError, ValidationError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Не разобрал заявку") from e

    shape, _ = await accept(data, data.source or "интеграция")
    return {"id": shape["id"], "status": shape["status"]}


# Публичная форма не может ничего подписать: любой ключ, положенный в статику, лежит в
# исходном коде страницы. Поэтому здесь другой набор защит — потолок с адреса, ловушка
# для ботов и лимит длины. Это не капча и не заменяет её на потоке, но отсекает перебор.
PUBLIC_SOURCE = os.environ.get("PUBLIC_SOURCE", "сайт")
PUBLIC_PER_HOUR = int(os.environ.get("PUBLIC_PER_HOUR", 10))
_seen: dict[str, list[float]] = {}


def _allowed(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _seen.get(ip, []) if now - t < 3600]
    _seen[ip] = recent
    if len(recent) >= PUBLIC_PER_HOUR:
        return False
    recent.append(now)
    return True


class PublicLeadIn(LeadIn):
    # Поле спрятано от человека стилями. Браузер его не покажет, а бот, заполняющий
    # форму по названиям полей, впишет туда что-нибудь — и выдаст себя.
    company: str = ""


@app.post("/api/public/lead")
async def public_lead(request: Request, data: PublicLeadIn):
    if data.company:
        log.info("lead.honeypot", ip=_client_ip(request))
        # Отвечаем как на успех: бот не должен понять, что его отсеяли, иначе автор
        # подправит скрипт. Заявка при этом никуда не сохраняется.
        return {"id": 0, "status": "new"}

    if not _allowed(_client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"С одного адреса принимаем {PUBLIC_PER_HOUR} заявок в час. Загляните позже.",
        )

    shape, _ = await accept(data, PUBLIC_SOURCE)
    return shape


async def enrich(lead_id: int) -> None:
    """Разметка заявки моделью. Падение здесь не должно ронять приём: заявка уже в базе."""
    try:
        lead = await get_lead(lead_id)
        if lead is None:
            return
        markup = await asyncio.to_thread(analyze_lead, lead.text)
        updated = await set_markup(lead_id, markup.topic, markup.urgency, markup.draft_reply)
        log.info("lead.enriched", lead_id=lead_id, urgency=markup.urgency)
        if updated is not None:
            await hub.send("lead.marked", card(updated))
    except Exception as e:
        log.exception("lead.enrich_failed", lead_id=lead_id)
        await db.mark_failed(lead_id, str(e))
        await hub.send("lead.failed", {"id": lead_id})


# --- Демо ----------------------------------------------------------------------

# Витрина для портфолио: тот же экран админки, но открытый и видящий только заявки,
# пришедшие с публичной формы. Боевые заявки в демо не попадают никогда — их отбирает
# запрос к базе по источнику, а не фильтр на странице.
DEMO_ENABLED = os.environ.get("DEMO_ENABLED", "1") == "1"


async def _demo_lead_or_403(lead_id: int) -> None:
    """Статусы в демо трогать можно, но только у демонстрационных заявок."""
    lead = await get_lead(lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    if lead.source != PUBLIC_SOURCE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Эта заявка не из демо")


def _demo_on() -> None:
    if not DEMO_ENABLED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Демо выключено")


@app.get("/demo")
async def demo(request: Request, status_filter: str | None = None, urgency: str | None = None):
    _demo_on()
    leads = await get_all_leads(
        limit=30, status=status_filter, urgency=urgency, source=PUBLIC_SOURCE
    )
    return _screen(request, leads, await count_by_status(source=PUBLIC_SOURCE), demo=True)


@app.get("/demo/leads/{lead_id}/events")
async def demo_events(lead_id: int):
    _demo_on()
    await _demo_lead_or_403(lead_id)
    return await _events_of(lead_id)


@app.post("/demo/leads/{lead_id}/status")
async def demo_status(lead_id: int, status_to: str = Form(...)):
    _demo_on()
    await _demo_lead_or_403(lead_id)
    return await _apply_status(lead_id, status_to, "/demo")


@app.websocket("/ws/leads")
async def leads_socket(socket: WebSocket):
    await hub.join(socket)
    try:
        await hub.keep(socket)
    finally:
        await hub.leave(socket)
