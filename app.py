import os
import secrets
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from db import get_all_leads, init_db

load_dotenv()

ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
security = HTTPBasic()


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


def require_admin(
    request: Request, credentials: HTTPBasicCredentials = Depends(security)
) -> str:
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI-CRM", lifespan=lifespan)
# Путь от файла, а не от рабочей папки: с относительным шаблоны терялись при запуске
# uvicorn из любого другого каталога.
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "index.html", {"leads": get_all_leads()})
