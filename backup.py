import asyncio
import gzip
import json
import os
import sys
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Numeric, insert, select, text

import db
from models import Base, local, seconds_until
from observability import log

KEEP = int(os.environ.get("BACKUP_KEEP", 14))
HOUR = int(os.environ.get("BACKUP_HOUR", 3))


def folder() -> str:
    path = os.path.join(db.DATA_DIR, "backups")
    os.makedirs(path, exist_ok=True)
    return path


def _plain(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _typed(column, value):
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        moment = datetime.fromisoformat(value)
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Numeric):
        return Decimal(value)
    if isinstance(column.type, Boolean):
        return bool(value)
    return value


async def snapshot() -> bytes:
    tables = {}
    async with db.Session() as session:
        for table in Base.metadata.sorted_tables:
            rows = (await session.execute(select(table))).mappings().all()
            tables[table.name] = [{k: _plain(v) for k, v in row.items()} for row in rows]
    payload = {"made_at": datetime.now(UTC).isoformat(), "tables": tables}
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode())


def _write(data: bytes) -> str:
    path = os.path.join(folder(), f"crm-{local(None):%Y-%m-%d-%H%M}.json.gz")
    with open(path, "wb") as file:
        file.write(data)
    old = sorted(name for name in os.listdir(folder()) if name.startswith("crm-"))
    for name in old[:-KEEP]:
        os.remove(os.path.join(folder(), name))
    return path


async def save() -> str:
    data = await snapshot()
    path = await asyncio.to_thread(_write, data)
    log.info("backup.saved", path=path, size=len(data))
    return path


async def restore(data: bytes) -> dict:
    payload = json.loads(gzip.decompress(data))
    counts = {}
    async with db.Session() as session:
        for table in Base.metadata.sorted_tables:
            rows = payload["tables"].get(table.name, [])
            if rows:
                await session.execute(
                    insert(table),
                    [{c.name: _typed(c, row.get(c.name)) for c in table.columns} for row in rows],
                )
            counts[table.name] = len(rows)
        if db.engine.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                await session.execute(
                    text(
                        f"select setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"coalesce((select max(id) from {table.name}), 1))"
                    )
                )
        await session.commit()
    return counts


async def nightly() -> None:
    while True:
        await asyncio.sleep(seconds_until(HOUR))
        try:
            await save()
        except Exception:
            log.exception("backup.failed")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "restore":
        with open(sys.argv[2], "rb") as source:
            print(asyncio.run(restore(source.read())))
    else:
        print(asyncio.run(save()))
