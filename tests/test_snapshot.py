import re

import app as app_module
import demo
import snapshot


def test_local_name_keeps_every_link_in_one_folder():
    assert snapshot.local_name("/") == "index.html"
    assert snapshot.local_name("/?assignee_id=3") == "index-assignee_id-3.html"
    assert snapshot.local_name("/leads/7/offer") == "leads-7-offer.html"
    assert snapshot.local_name("/export/leads.xlsx?stage=won") == "export-leads-stage-won.xlsx"


async def test_static_copy_has_no_broken_links(boss, tmp_path, monkeypatch):
    await demo.seed()
    monkeypatch.setitem(app_module.templates.env.globals, "static_copy", True)

    pages = snapshot.freeze(boss, tmp_path)

    assert pages > len(demo.LEADS)
    for page in tmp_path.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r'\b(href|src)="/(?!/)', text), page.name
        for target in re.findall(r'\bhref="([^"#:]+)"', text):
            assert target.startswith("static/") or (tmp_path / target).exists(), target
