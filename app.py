"""Сервис CRM: приём заявок и рабочие экраны.

Форма живёт не здесь. Она на сайте — своём домене, своей статике, своём деплое —
и стучится сюда по сети, как стучалась бы форма на Тильде или чужой бот. Пока форма
отдаётся тем же приложением, что и CRM, любая починка CRM требует передеплоя страницы,
на которую клиент пришёл с рекламы, а падение сервиса уносит эту страницу с собой.
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

import auth
import db
import webhook
from ai import analyze_lead
from hub import hub
from models import STAGES, URGENCIES, User
from observability import log, setup

load_dotenv()
setup()

# Модель отвечает служебными словами. В интерфейсе человек читает по-русски.
URGENCY_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}
STAGE_RU = {
    "new": "Новые",
    "in_work": "В работе",
    "waiting": "Ждём ответа",
    "won": "Сделка",
    "lost": "Отказ",
}
EVENT_RU = {
    "created": "заявка принята",
    "marked": "модель разметила",
    "mark_failed": "разметка не удалась",
    "stage": "стадия",
    "assigned": "ответственный",
    "note": "комментарий",
    "due": "срок",
    "amount": "сумма",
}


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
    # Свежая система пуста, а завести сотрудника можно только войдя. Первый вход
    # берётся из окружения и в базу попадает уже хешем.
    await db.ensure_admin(os.environ.get("ADMIN_USER", ""), os.environ.get("ADMIN_PASSWORD", ""))
    yield


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


# --- вход ----------------------------------------------------------------------

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


class NeedLogin(Exception):
    pass


async def maybe_user(request: Request) -> User | None:
    user_id = auth.read_session(request.cookies.get(auth.SESSION_COOKIE))
    if user_id is None:
        return None
    user = await db.get_user(user_id)
    return user if user and user.active else None


async def current_user(request: Request) -> User:
    """Страницы за логином. Не вошёл — уводим на форму входа, а не показываем ошибку."""
    user = await maybe_user(request)
    if user is None:
        raise NeedLogin
    return user


async def editor(request: Request) -> User:
    """Право менять. Роль наблюдателя нужна тем, кто должен видеть работу, но не
    вмешиваться в неё: стажёр, бухгалтер, гость."""
    user = await current_user(request)
    if not user.can_edit:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Только просмотр")
    return user


@app.exception_handler(NeedLogin)
async def to_login(request: Request, _: NeedLogin):
    return RedirectResponse(
        f"/login?next={request.url.path}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/login")
async def login_form(request: Request, next: str = "/", error: str | None = None):
    if await maybe_user(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": error, "hide_nav": True}
    )


@app.post("/login")
async def login(
    request: Request, login: str = Form(...), password: str = Form(...), next: str = Form("/")
):
    ip = _client_ip(request)
    if _too_many_failures(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток. Подождите пару минут."
        )

    user = await db.get_user_by_login(login)
    if user is None or not user.active or not auth.verify_password(password, user.password_hash):
        _failures.setdefault(ip, []).append(time.time())
        log.warning("login.failed", login=login[:40])
        return RedirectResponse(
            f"/login?error=1&next={next}", status_code=status.HTTP_303_SEE_OTHER
        )

    _failures.pop(ip, None)
    response = RedirectResponse(next or "/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.make_session(user.id),
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,  # javascript до сессии не дотянется даже при вставке чужого скрипта
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    log.info("login.ok", user=user.login)
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(auth.SESSION_COOKIE)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


# --- экраны --------------------------------------------------------------------


def card(lead) -> dict:
    """Одна форма заявки для вебсокета и для первой отрисовки страницы.

    Связанные объекты берём из уже загруженного, а не через обычное обращение к полю:
    у заявки, только что вынутой из закрытой сессии, такое обращение полезло бы в базу
    за ответственным и упало бы прямо посреди приёма заявки.
    """
    assignee = lead.__dict__.get("assignee")
    return {
        "id": lead.id,
        "name": lead.client_name,
        "contact": lead.client_contact,
        "text": lead.text,
        "topic": lead.topic,
        "urgency": lead.urgency,
        "draft": lead.draft_reply,
        "stage": lead.stage,
        "source": lead.source,
        "time": lead.created_local,
        "amount": float(lead.amount) if lead.amount is not None else None,
        "assignee": assignee.name if assignee else None,
        "overdue": lead.overdue,
    }


def _shell(user: User, **extra) -> dict:
    return {
        "me": user,
        "stages": STAGES,
        "stage_ru": STAGE_RU,
        "urgencies": URGENCIES,
        "urgency_ru": URGENCY_RU,
        "event_ru": EVENT_RU,
        **extra,
    }


@app.get("/")
async def board(
    request: Request, assignee_id: int | None = None, user: User = Depends(current_user)
):
    columns = await db.board(assignee_id=assignee_id)
    return templates.TemplateResponse(
        request,
        "board.html",
        _shell(
            user,
            columns={stage: [card(lead) for lead in rows] for stage, rows in columns.items()},
            counts=await db.count_by_stage(),
            people=await db.all_users(),
            assignee_id=assignee_id,
        ),
    )


@app.get("/leads")
async def leads(
    request: Request,
    stage: str | None = None,
    urgency: str | None = None,
    source: str | None = None,
    assignee_id: int | None = None,
    search: str | None = None,
    overdue: bool = False,
    user: User = Depends(current_user),
):
    rows = await db.get_all_leads(
        stage=stage,
        urgency=urgency,
        source=source,
        assignee_id=assignee_id,
        search=search,
        overdue=overdue,
    )
    return templates.TemplateResponse(
        request,
        "leads.html",
        _shell(
            user,
            leads=rows,
            counts=await db.count_by_stage(),
            people=await db.all_users(),
            filters={
                "stage": stage,
                "urgency": urgency,
                "source": source,
                "assignee_id": assignee_id,
                "search": search or "",
                "overdue": overdue,
            },
        ),
    )


@app.get("/leads/{lead_id}")
async def lead_card(request: Request, lead_id: int, user: User = Depends(current_user)):
    lead = await db.get_lead(lead_id, full=True)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return templates.TemplateResponse(
        request, "lead.html", _shell(user, lead=lead, people=await db.all_users())
    )


def _back(request: Request, lead_id: int) -> RedirectResponse:
    target = request.headers.get("referer") or f"/leads/{lead_id}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/leads/{lead_id}/stage")
async def change_stage(
    request: Request,
    lead_id: int,
    stage: str = Form(...),
    lost_reason: str = Form(""),
    user: User = Depends(editor),
):
    if stage not in STAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестная стадия")
    lead = await db.set_stage(lead_id, stage, user.id, lost_reason.strip() or None)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    await hub.send("lead.stage", card(await db.get_lead(lead_id, full=True)))
    if request.headers.get("accept", "").startswith("application/json"):
        return {"ok": True, "stage": lead.stage}
    return _back(request, lead_id)


@app.post("/leads/{lead_id}/assignee")
async def change_assignee(
    request: Request, lead_id: int, assignee_id: str = Form(""), user: User = Depends(editor)
):
    target = int(assignee_id) if assignee_id.strip() else None
    if await db.set_assignee(lead_id, target, user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return _back(request, lead_id)


@app.post("/leads/{lead_id}/amount")
async def change_amount(
    request: Request, lead_id: int, amount: str = Form(""), user: User = Depends(editor)
):
    raw = amount.replace(" ", "").replace(",", ".").strip()
    try:
        value = Decimal(raw) if raw else None
    except InvalidOperation as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Сумма не число") from e
    if value is not None and value < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Сумма не бывает отрицательной")
    if await db.set_amount(lead_id, value, user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return _back(request, lead_id)


@app.post("/leads/{lead_id}/due")
async def change_due(
    request: Request, lead_id: int, due_at: str = Form(""), user: User = Depends(editor)
):
    value = None
    if due_at.strip():
        try:
            # Браузер отдаёт местное время без зоны; считаем его временем сервера.
            value = datetime.fromisoformat(due_at).replace(tzinfo=UTC)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Не разобрал дату") from e
    if await db.set_due(lead_id, value, user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return _back(request, lead_id)


@app.post("/leads/{lead_id}/note")
async def add_note(
    request: Request, lead_id: int, text: str = Form(...), user: User = Depends(editor)
):
    if await db.get_lead(lead_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    await db.add_note(lead_id, user.id, text)
    return _back(request, lead_id)


# --- клиенты -------------------------------------------------------------------


@app.get("/contacts")
async def contacts(request: Request, search: str | None = None, user: User = Depends(current_user)):
    return templates.TemplateResponse(
        request,
        "contacts.html",
        _shell(user, contacts=await db.all_contacts(search), search=search or ""),
    )


@app.get("/contacts/{contact_id}")
async def contact_card(request: Request, contact_id: int, user: User = Depends(current_user)):
    contact = await db.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиент не найден")
    return templates.TemplateResponse(request, "contact.html", _shell(user, contact=contact))


@app.post("/contacts/{contact_id}/note")
async def contact_note(
    request: Request, contact_id: int, note: str = Form(""), user: User = Depends(editor)
):
    if await db.set_contact_note(contact_id, note) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиент не найден")
    return RedirectResponse(f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)


# --- отчёт и команда -----------------------------------------------------------


@app.get("/report")
async def report(request: Request, days: int = 30, user: User = Depends(current_user)):
    return templates.TemplateResponse(
        request, "report.html", _shell(user, report=await db.report(days), days=days)
    )


@app.get("/team")
async def team(request: Request, error: str | None = None, user: User = Depends(current_user)):
    return templates.TemplateResponse(
        request,
        "team.html",
        _shell(user, people=await db.all_users(active_only=False), error=error),
    )


@app.post("/team")
async def add_person(
    login: str = Form(...),
    name: str = Form(...),
    password: str = Form(...),
    role: str = Form("manager"),
    user: User = Depends(current_user),
):
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Сотрудников заводит владелец")
    if len(password) < 8:
        return RedirectResponse("/team?error=short", status_code=status.HTTP_303_SEE_OTHER)
    if await db.get_user_by_login(login):
        return RedirectResponse("/team?error=taken", status_code=status.HTTP_303_SEE_OTHER)
    await db.create_user(login, name, password, role)
    return RedirectResponse("/team", status_code=status.HTTP_303_SEE_OTHER)


# --- приём заявок --------------------------------------------------------------

MAX_BODY = 20_000


class LeadIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    name: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=60)


class PublicLeadIn(LeadIn):
    # Поле спрятано от человека стилями. Браузер его не покажет, а бот, заполняющий
    # форму по названиям полей, впишет туда что-нибудь — и выдаст себя.
    company: str = ""


async def accept(data: LeadIn, source: str) -> tuple[dict, bool]:
    """Общий путь для всех входов: сохранить, показать, разметить фоном."""
    lead, is_new = await db.add_lead(
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
        data = LeadIn.model_validate(json.loads(body))
    except (ValueError, ValidationError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Не разобрал заявку") from e

    shape, _ = await accept(data, data.source or "интеграция")
    return {"id": shape["id"], "status": "ok"}


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


@app.post("/api/public/lead")
async def public_lead(request: Request, data: PublicLeadIn):
    if data.company:
        log.info("lead.honeypot", ip=_client_ip(request))
        # Отвечаем как на успех: бот не должен понять, что его отсеяли, иначе автор
        # подправит скрипт. Заявка при этом никуда не сохраняется.
        return {"id": 0, "status": "ok"}

    if not _allowed(_client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"С одного адреса принимаем {PUBLIC_PER_HOUR} заявок в час. Загляните позже.",
        )

    shape, _ = await accept(data, PUBLIC_SOURCE)
    return {"id": shape["id"], "status": "ok"}


async def enrich(lead_id: int) -> None:
    """Разметка заявки моделью. Падение здесь не должно ронять приём: заявка уже в базе."""
    try:
        lead = await db.get_lead(lead_id)
        if lead is None:
            return
        markup = await asyncio.to_thread(analyze_lead, lead.text)
        updated = await db.set_markup(lead_id, markup.topic, markup.urgency, markup.draft_reply)
        log.info("lead.enriched", lead_id=lead_id, urgency=markup.urgency)
        if updated is not None:
            await hub.send("lead.marked", card(updated))
    except Exception as e:
        log.exception("lead.enrich_failed", lead_id=lead_id)
        await db.mark_failed(lead_id, str(e))
        await hub.send("lead.failed", {"id": lead_id})


@app.websocket("/ws/leads")
async def leads_socket(socket: WebSocket):
    # Через вебсокет уезжают тексты заявок с контактами: он закрыт той же сессией,
    # что и страницы, иначе он был бы дырой в обход всего входа.
    user_id = auth.read_session(socket.cookies.get(auth.SESSION_COOKIE))
    if user_id is None or await db.get_user(user_id) is None:
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await hub.join(socket)
    try:
        await hub.keep(socket)
    finally:
        await hub.leave(socket)
