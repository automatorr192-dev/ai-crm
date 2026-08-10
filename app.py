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
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

import webhook
from ai import analyze_lead
from db import add_lead, get_all_leads, get_lead, set_markup, set_status
from hub import hub
from models import STATUSES
from observability import log, setup

load_dotenv()
setup()

ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
security = HTTPBasic()

# Модель отвечает служебными словами. В интерфейсе человек читает по-русски.
URGENCY_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}


# Подбор пароля: без задержки перебор идёт со скоростью сети. Считаем неудачи по адресу
# и после порога отвечаем 429 — на живой вход это не влияет, счётчик обнуляется при успехе.
MAX_FAILURES = int(os.environ.get("ADMIN_MAX_FAILURES", 10))
LOCKOUT_SECONDS = int(os.environ.get("ADMIN_LOCKOUT_SECONDS", 300))
_failures: dict[str, list[float]] = {}


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

    ip = request.client.host if request.client else "?"
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
        "time": lead.created_local,
    }


class LeadIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    name: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=60)


app = FastAPI(title="AI-CRM", lifespan=lifespan)
# Путь от файла, а не от рабочей папки: с относительным шаблоны терялись при запуске
# uvicorn из любого другого каталога.
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
async def index(request: Request, _: str = Depends(require_admin)):
    leads = await get_all_leads()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"leads": leads, "statuses": STATUSES, "urgency_ru": URGENCY_RU},
    )


@app.post("/leads/{lead_id}/status")
async def change_status(lead_id: int, status_to: str = Form(...), _: str = Depends(require_admin)):
    if status_to not in STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный статус")
    if await set_status(lead_id, status_to) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


MAX_BODY = 20_000


@app.post("/webhook/lead")
async def incoming_lead(request: Request):
    """Заявка снаружи: форма на сайте, квиз, чужой сервис.

    Разметку моделью делаем в фоне и отвечаем сразу. Отправитель ждёт 200, а не наш поход
    к ИИ: не дождавшись, он пришлёт заявку ещё раз, и в базе появится дубль.
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

    lead = await add_lead(
        client_name=data.name,
        client_contact=data.contact,
        text=data.text,
        source=data.source,
    )
    await hub.send("lead.new", card(lead))
    asyncio.create_task(enrich(lead.id))
    log.info("lead.accepted", lead_id=lead.id, source=data.source)
    return {"id": lead.id, "status": lead.status}


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
    except Exception:
        log.exception("lead.enrich_failed", lead_id=lead_id)
        await hub.send("lead.failed", {"id": lead_id})


# --- Живой экран ---------------------------------------------------------------


# Демо открыто без пароля, а каждая заявка — оплаченный вызов модели. Лимит по адресу.
DEMO_PER_HOUR = int(os.environ.get("DEMO_PER_HOUR", 10))
_demo: dict[str, list[float]] = {}


def _demo_allowed(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _demo.get(ip, []) if now - t < 3600]
    _demo[ip] = recent
    if len(recent) >= DEMO_PER_HOUR:
        return False
    recent.append(now)
    return True


@app.get("/live")
async def live(request: Request):
    leads = await get_all_leads(limit=12)
    return templates.TemplateResponse(
        request,
        "live.html",
        {"leads": [card(lead) for lead in leads], "urgency_ru": URGENCY_RU},
    )


@app.post("/api/quiz")
async def quiz(request: Request, data: LeadIn):
    if not _demo_allowed(request.client.host if request.client else "?"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Демо принимает {DEMO_PER_HOUR} заявок в час с одного адреса. Загляните позже.",
        )

    lead = await add_lead(
        client_name=data.name,
        client_contact=data.contact,
        text=data.text,
        source=data.source or "демо",
    )
    await hub.send("lead.new", card(lead))
    asyncio.create_task(enrich(lead.id))
    log.info("lead.accepted", lead_id=lead.id, source="демо")
    return card(lead)


@app.websocket("/ws/leads")
async def leads_socket(socket: WebSocket):
    await hub.join(socket)
    try:
        await hub.keep(socket)
    finally:
        await hub.leave(socket)
