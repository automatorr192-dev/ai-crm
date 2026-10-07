import app as app_module
import db
import demo


async def test_demo_seed_fills_every_screen(database):
    count = await demo.seed()
    assert count == len(demo.LEADS)
    report = await db.report(30)
    assert report["total"] == len(demo.LEADS)
    assert report["by_stage"]["won"] >= 4
    assert report["by_campaign"]
    assert report["answer_minutes"] is not None
    assert await db.get_all_leads(stale=True)
    hot = [lead for lead in await db.get_all_leads() if lead.hot]
    assert len(hot) == 1
    assert len(await db.messages(hot[0].id)) == len(demo.HOT["talk"])


async def test_reset_keeps_real_accounts(database):
    real = await db.create_user("grisha", "Гриша", "very-secret", role="admin")
    await demo.seed()
    lead, _ = await db.add_lead("Лишний", "@x", "добавил посетитель демо")
    await demo.reset()
    assert await db.get_user(real.id) is not None
    names = {item.client_name for item in await db.get_all_leads()}
    assert "Лишний" not in names
    assert len(names) > 10


async def test_demo_entrance_only_in_demo_mode(database, client, monkeypatch):
    await demo.seed()
    assert client.post("/demo/owner", follow_redirects=False).status_code == 404

    monkeypatch.setattr(demo, "ENABLED", True)
    response = client.post("/demo/manager", follow_redirects=False)
    assert response.status_code == 303
    listing = client.get("/leads").text
    assert "Максим Орлов" not in listing
    assert client.post("/demo/root", follow_redirects=False).status_code == 404


async def test_login_page_shows_demo_buttons_only_in_demo(database, client, monkeypatch):
    assert "Войти как владелец" not in client.get("/login").text
    monkeypatch.setitem(app_module.templates.env.globals, "demo", True)
    assert "Войти как владелец" in client.get("/login").text


async def test_offer_is_a_printable_page_for_editors(database, manager, watcher):
    lead, _ = await db.add_lead("Полина", "@polina", "нужен бот записи")
    await db.set_markup(lead.id, "бот записи", "high", "черновик")
    await db.set_amount(lead.id, 45000)
    page = manager.get(f"/leads/{lead.id}/offer")
    assert page.status_code == 200
    assert "Коммерческое предложение № " in page.text
    assert "45 000" in page.text and "Бот записи" in page.text
    assert watcher.get(f"/leads/{lead.id}/offer").status_code == 403
