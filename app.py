from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from db import init_db, get_all_leads

app = FastAPI()
templates = Jinja2Templates(directory="templates")

init_db()


@app.get("/")
def index(request: Request):
    leads = get_all_leads()
    return templates.TemplateResponse(request, "index.html", {"leads": leads})
