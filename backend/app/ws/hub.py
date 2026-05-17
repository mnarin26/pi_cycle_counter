from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.websockets import WebSocket


class Hub:
    def __init__(self) -> None:
        self._clients: set = set()
        self._lock = asyncio.Lock()

    def add(self, ws) -> None:
        self._clients.add(ws)

    def remove(self, ws) -> None:
        self._clients.discard(ws)

    async def broadcast(self, text: str) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)
