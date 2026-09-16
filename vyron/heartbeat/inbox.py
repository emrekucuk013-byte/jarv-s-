"""Notices the heartbeat wants the user to see. Held on disk until dismissed."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..tools._store import read_json, write_json

LEVELS = ("log", "interrupt", "critical")  # log: glance later. interrupt: tell me now (respects quiet hours). critical: always now.


@dataclass
class Notice:
    id: int
    check: str
    level: str
    text: str
    created: str
    dedupe_key: str = ""
    dismissed: bool = False
    announced: bool = False   # has it been spoken/printed to the user yet? (in-memory only: reset on restart -> catch-up)

    def line(self) -> str:
        when = self.created[:16].replace("T", " ")
        return f"#{self.id} [{self.level}] {when} {self.check}: {self.text}"


class Inbox:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._notices: list[Notice] = [Notice(**{**n, "announced": False}) for n in read_json(path, [])]

    def _save(self) -> None:
        write_json(self.path, [{k: v for k, v in asdict(n).items() if k != "announced"} for n in self._notices])

    def add(self, check: str, level: str, text: str, dedupe_key: str = "", now: datetime | None = None) -> Notice | None:
        """Add a notice. Returns None if an identical undismissed notice already exists."""
        if level not in LEVELS:
            level = "log"
        with self._lock:
            key = dedupe_key or f"{check}:{text}"
            if any(n.dedupe_key == key and not n.dismissed for n in self._notices):
                return None
            notice = Notice(
                id=max((n.id for n in self._notices), default=0) + 1,
                check=check, level=level, text=text,
                created=(now or datetime.now()).isoformat(timespec="seconds"), dedupe_key=key,
            )
            self._notices.append(notice)
            self._save()
            return notice

    def pending(self) -> list[Notice]:
        with self._lock:
            return [n for n in self._notices if not n.dismissed]

    def unannounced(self) -> list[Notice]:
        with self._lock:
            return [n for n in self._notices if not n.dismissed and not n.announced]

    def mark_announced(self, notice: Notice) -> None:
        with self._lock:
            notice.announced = True

    def dismiss(self, notice_id: int) -> bool:
        with self._lock:
            for n in self._notices:
                if n.id == notice_id and not n.dismissed:
                    n.dismissed = True
                    self._save()
                    return True
            return False

    def dismiss_all(self) -> int:
        with self._lock:
            count = 0
            for n in self._notices:
                if not n.dismissed:
                    n.dismissed = True
                    count += 1
            if count:
                self._save()
            return count
