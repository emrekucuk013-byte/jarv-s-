"""Confirmation gate for turns that arrive from a browser.

The turn streams events to the browser; a consequential tool pushes a
``confirm`` event, then waits (bounded) for the browser to answer. No answer
means no.
"""

from __future__ import annotations

import queue
import threading
import uuid
from typing import Any

from ..safety import Decision


class WebConfirmer:
    def __init__(self, timeout: float = 90.0):
        self.timeout = timeout
        self.local = threading.local()          # .queue = the current turn's event queue
        self.pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def bind(self, q: queue.Queue) -> None:
        self.local.queue = q

    def confirm(self, tool_name, args, description) -> Decision:
        q: queue.Queue | None = getattr(self.local, "queue", None)
        if q is None:
            return Decision(False, "no one to ask")
        cid = uuid.uuid4().hex[:10]
        entry = {"event": threading.Event(), "approved": None, "description": description}
        with self._lock:
            self.pending[cid] = entry
        q.put({"type": "confirm", "id": cid, "description": description})
        entry["event"].wait(self.timeout)
        with self._lock:
            self.pending.pop(cid, None)
        if entry["approved"] is True:
            return Decision(True, "user said yes")
        if entry["approved"] is False:
            return Decision(False, "user declined")
        return Decision(False, f"no answer within {int(self.timeout)}s; did nothing")

    def answer(self, cid: str, approved: bool) -> bool:
        with self._lock:
            entry = self.pending.get(cid)
        if not entry:
            return False
        entry["approved"] = bool(approved)
        entry["event"].set()
        return True
