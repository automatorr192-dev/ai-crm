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
        self._clients: dict[WebSocket, object] = {}
        self._lock = asyncio.Lock()

    async def join(self, socket: WebSocket, user=None) -> None:
        await socket.accept()
        async with self._lock:
            self._clients[socket] = user

    async def leave(self, socket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(socket, None)

    @staticmethod
    def _allowed(user, data: dict) -> bool:
        if user is None or user.sees_all or "assignee_id" not in data:
            return True
        return data["assignee_id"] in (None, user.id)

    async def send(self, event: str, data: dict) -> None:
        """Отправка всем. Мёртвые соединения выкидываем молча: закрытая вкладка это
        норма, а не ошибка, и падать из-за неё рассылка не должна."""
        async with self._lock:
            clients = list(self._clients.items())

        dead = []
        for socket, user in clients:
            if not self._allowed(user, data):
                continue
            try:
                await socket.send_json({"event": event, "data": data})
            except Exception:
                dead.append(socket)

        if dead:
            async with self._lock:
                for socket in dead:
                    self._clients.pop(socket, None)
            log.info("hub.dropped", count=len(dead))

    async def keep(self, socket: WebSocket) -> None:
        """Держим соединение открытым. Входящие сообщения не нужны: связь односторонняя."""
        with contextlib.suppress(Exception):
            while True:
                await socket.receive_text()


hub = Hub()
