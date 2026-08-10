"""Рассылка событий открытым вкладкам.

Заявка появляется в ленте сразу, а разметка приезжает через несколько секунд — двумя
разными событиями. Без этого пришлось бы опрашивать сервер по таймеру и показывать
карточку уже готовой, а весь смысл демо в том, что работу видно по шагам.
"""

import asyncio
import contextlib

from fastapi import WebSocket

from observability import log


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def join(self, socket: WebSocket) -> None:
        await socket.accept()
        async with self._lock:
            self._clients.add(socket)

    async def leave(self, socket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(socket)

    async def send(self, event: str, data: dict) -> None:
        """Отправка всем. Мёртвые соединения выкидываем молча: закрытая вкладка это
        норма, а не ошибка, и падать из-за неё рассылка не должна."""
        async with self._lock:
            clients = list(self._clients)

        dead = []
        for socket in clients:
            try:
                await socket.send_json({"event": event, "data": data})
            except Exception:
                dead.append(socket)

        if dead:
            async with self._lock:
                self._clients.difference_update(dead)
            log.info("hub.dropped", count=len(dead))

    async def keep(self, socket: WebSocket) -> None:
        """Держим соединение открытым. Входящие сообщения не нужны: связь односторонняя."""
        with contextlib.suppress(Exception):
            while True:
                await socket.receive_text()


hub = Hub()
