"""Thread-safe pos-tick fanout for shadow wagon clients (zero cost if idle)."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any


class PosTickHub:
    """Vision threads emit ticks; asyncio pump sends to subscribed WebSockets.

    Subscription filter: set[int] machine ids, or None = all machines.
    """

    def __init__(self, max_queue: int = 4000) -> None:
        self._subs: dict[Any, set[int] | None] = {}
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._wanted: set[int] = set()
        self._want_all = False

    def _recompute_wanted(self) -> None:
        wanted: set[int] = set()
        want_all = False
        for filt in self._subs.values():
            if filt is None:
                want_all = True
                break
            wanted |= filt
        self._want_all = want_all
        self._wanted = wanted

    @property
    def has_clients(self) -> bool:
        with self._lock:
            return bool(self._subs)

    def wants(self, machine_id: int) -> bool:
        with self._lock:
            if not self._subs:
                return False
            if self._want_all:
                return True
            return int(machine_id) in self._wanted

    def add(self, ws: Any, machine_ids: set[int] | None) -> None:
        with self._lock:
            self._subs[ws] = machine_ids
            self._recompute_wanted()

    def remove(self, ws: Any) -> None:
        with self._lock:
            self._subs.pop(ws, None)
            self._recompute_wanted()

    def emit(self, tick: dict) -> None:
        mid = int(tick.get("machine_id") or -1)
        if not self.wants(mid):
            return
        try:
            self._q.put_nowait(tick)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(tick)
            except queue.Full:
                pass

    async def pump_forever(self) -> None:
        while True:
            drained = 0
            while drained < 64:
                try:
                    tick = self._q.get_nowait()
                except queue.Empty:
                    break
                drained += 1
                mid = int(tick.get("machine_id") or -1)
                text = json.dumps(tick, separators=(",", ":"))
                with self._lock:
                    clients = list(self._subs.items())
                dead: list[Any] = []
                for ws, filt in clients:
                    if filt is not None and mid not in filt:
                        continue
                    try:
                        await ws.send_text(text)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.remove(ws)
            await asyncio.sleep(0.005 if drained else 0.02)
