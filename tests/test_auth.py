"""Вход, сессии и права.

Раньше вход был один на всех через HTTP Basic. В CRM, где у сделки есть ответственный,
это перестаёт работать: «кто перевёл клиента в отказ» остаётся без ответа.
"""

import auth
import db


def test_password_never_stored_as_is():
    stored = auth.hash_password("very-secret")
    assert "very-secret" not in stored
    assert stored.startswith("scrypt$")


def test_password_check():
    stored = auth.hash_password("very-secret")
    assert auth.verify_password("very-secret", stored)
    assert not auth.verify_password("very-secre", stored)
    assert not auth.verify_password("", stored)


def test_same_password_gives_different_hashes():
    """Соль у каждого своя: иначе одинаковые пароли видно прямо в выгрузке базы."""
    assert auth.hash_password("одинаковый") != auth.hash_password("одинаковый")


def test_broken_hash_does_not_let_anyone_in():
    assert not auth.verify_password("что угодно", "мусор")
    assert not auth.verify_password("что угодно", "")


def test_session_survives_a_round_trip():
    assert auth.read_session(auth.make_session(42)) == 42


def test_tampered_session_is_rejected():
    """Подделать «я админ», поправив cookie в браузере, не выйдет: подпись не сойдётся."""
    cookie = auth.make_session(42)
    user_id, expires, signature = cookie.split(".")
    assert auth.read_session(f"999.{expires}.{signature}") is None
    assert auth.read_session(f"{user_id}.{expires}.подделка") is None
    assert auth.read_session("вообще не cookie") is None
    assert auth.read_session(None) is None


def test_expired_session_is_rejected(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_DAYS", -1)
    assert auth.read_session(auth.make_session(42)) is None


# --- вход ----------------------------------------------------------------------


async def test_pages_are_closed_to_strangers(database, client):
    for path in ("/", "/leads", "/contacts", "/report", "/team"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login")


async def test_login_and_see_the_board(database, boss):
    assert boss.get("/").status_code == 200


async def test_wrong_password_does_not_let_in(database, client):
    await db.create_user("boss", "Владелец", "very-secret", role="admin")
    response = client.post(
        "/login", data={"login": "boss", "password": "мимо"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert client.get("/", follow_redirects=False).status_code == 303


async def test_logout_closes_the_door(database, boss):
    boss.post("/logout")
    assert boss.get("/", follow_redirects=False).status_code == 303


async def test_brute_force_gets_locked_out(database, client, monkeypatch):
    """После порога неудач вход отвечает 429, а не продолжает принимать попытки."""
    import app as app_module

    await db.create_user("boss", "Владелец", "very-secret", role="admin")
    monkeypatch.setattr(app_module, "MAX_FAILURES", 3)

    for _ in range(3):
        client.post("/login", data={"login": "boss", "password": "мимо"}, follow_redirects=False)
    response = client.post(
        "/login", data={"login": "boss", "password": "very-secret"}, follow_redirects=False
    )
    assert response.status_code == 429


async def test_first_admin_appears_from_environment(database):
    """Свежий контейнер иначе заперт: пользователей нет, а завести их можно только войдя."""
    created = await db.ensure_admin("owner", "very-secret", "Владелец")
    assert created is not None and created.role == "admin"

    # Второй раз никого не создаём: система уже не пуста.
    assert await db.ensure_admin("someone-else", "very-secret") is None


# --- права ---------------------------------------------------------------------


async def test_watcher_sees_but_does_not_touch(database, watcher):
    lead, _ = await db.add_lead("Полина", "@polina", "текст", source="сайт")

    assert watcher.get("/leads").status_code == 200
    assert watcher.get(f"/leads/{lead.id}").status_code == 200

    blocked = watcher.post(f"/leads/{lead.id}/stage", data={"stage": "won"})
    assert blocked.status_code == 403
    assert (await db.get_lead(lead.id)).stage == "new"


async def test_manager_can_work(database, manager):
    lead, _ = await db.add_lead("Полина", "@polina", "текст", source="сайт")
    manager.post(f"/leads/{lead.id}/stage", data={"stage": "in_work"})
    assert (await db.get_lead(lead.id)).stage == "in_work"


async def test_only_owner_adds_people(database, manager):
    response = manager.post(
        "/team",
        data={"login": "new", "name": "Новый", "password": "very-secret", "role": "manager"},
    )
    assert response.status_code == 403


async def test_owner_adds_people(database, boss):
    boss.post(
        "/team",
        data={"login": "irina", "name": "Ирина", "password": "very-secret", "role": "manager"},
    )
    assert await db.get_user_by_login("irina") is not None


async def test_short_password_is_refused(database, boss):
    boss.post(
        "/team", data={"login": "weak", "name": "Слабый", "password": "1234", "role": "manager"}
    )
    assert await db.get_user_by_login("weak") is None
