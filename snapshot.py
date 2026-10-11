"""Копия демо для GitHub Pages: обходит экраны под владельцем и складывает их статикой.

Бесплатный сервер засыпает и может не открыться у заказчика, а статика на Pages
открывается всегда. Смотреть в копии можно всё, сохранить ничего нельзя.

    python snapshot.py            # в site/demo/
"""

import html
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).parent
LINK = re.compile(r'\b(href|src)="(/(?!/)[^"]*)"')
LIVE_ONLY = ("/backup", "/files/")


def local_name(url: str) -> str:
    """Папок нет, все страницы лежат рядом: тогда любая ссылка — просто имя файла."""
    path, _, query = url.partition("?")
    base, ext = os.path.splitext(path.strip("/"))
    slug = "-".join(filter(None, [base.replace("/", "-") or "index", query]))
    return re.sub(r"[^\w.-]+", "-", slug) + (ext or ".html")


def freeze(client: TestClient, out: Path) -> int:
    queue, seen = ["/"], set()

    def relink(match: re.Match) -> str:
        attr, url = match[1], html.unescape(match[2])
        if url.startswith("/static/"):
            return f'{attr}="{url[1:]}"'
        if url.startswith(LIVE_ONLY):
            return f'{attr}="#live"'
        queue.append(url)
        return f'{attr}="{local_name(url)}"'

    out.mkdir(parents=True, exist_ok=True)
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        response = client.get(url)
        response.raise_for_status()
        target = out / local_name(url)
        if response.headers["content-type"].startswith("text/html"):
            target.write_text(LINK.sub(relink, response.text), encoding="utf-8")
        else:
            target.write_bytes(response.content)
    return len(seen)


def main() -> None:
    # Окружение задаётся до импорта приложения: модули читают его при загрузке. Пустые
    # токены не дают локальному .env поднять ботов посреди сборки.
    os.environ.update(
        DEMO_MODE="1",
        DATA_DIR=tempfile.mkdtemp(),
        DATABASE_URL="",
        TG_BOT_TOKEN="",
        MAX_BOT_TOKEN="",
        BACKUP_KEEP="0",
        SENTRY_DSN="",
    )
    import app as app_module

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site" / "demo"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(ROOT / "static", out / "static")
    app_module.templates.env.globals["static_copy"] = True
    with TestClient(app_module.app) as client:
        client.post("/demo/owner")
        print(f"{freeze(client, out)} страниц в {out}")


if __name__ == "__main__":
    main()
