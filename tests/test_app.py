import base64
import os

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402

app_module.ADMIN_USER = "admin"
app_module.ADMIN_PASSWORD = "secret"
client = TestClient(app_module.app)


def auth(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_health_is_open():
    assert client.get("/health").json() == {"status": "ok"}


def test_leads_are_closed_without_password():
    assert client.get("/").status_code == 401


def test_wrong_password_is_rejected():
    assert client.get("/", headers=auth("admin", "wrong")).status_code == 401


def test_admin_sees_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "get_all_leads", lambda: [])
    assert client.get("/", headers=auth("admin", "secret")).status_code == 200
