import os
import secrets
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


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """В заявках лежат имена и контакты живых людей: отдавать их без пароля нельзя ни
    в демо, ни тем более в проде. compare_digest — чтобы пароль нельзя было подобрать
    по времени ответа."""
    if not ADMIN_USER or not ADMIN_PASSWORD:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Админка не настроена")
    ok = secrets.compare_digest(credentials.username, ADMIN_USER) & secrets.compare_digest(
        credentials.password, ADMIN_PASSWORD
    )
    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI-CRM", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "index.html", {"leads": get_all_leads()})
