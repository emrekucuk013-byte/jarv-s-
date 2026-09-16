"""The background loop. Separate from the conversation loop; doesn't care which machine it's on."""

from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..config import STATE_DIR
from ..tools._store import read_json, write_json
from .checks import CHECKS, CheckContext, parse_interval
from .inbox import Inbox, Notice


def in_quiet_hours(now: datetime, start: str | None, end: str | None) -> bool:
    if not start or not end:
        return False
    s = dtime.fromisoformat(start)
    e = dtime.fromisoformat(end)
    t = now.time()
    return (s <= t < e) if s <= e else (t >= s or t < e)


class Heartbeat:
    """Ticks on an interval, runs due checks, routes findings to the inbox, announces what earns it.

    ``announce(notice)`` is the interface's way of interrupting the user (print, speak, ...).
    Everything that must survive a restart (schedule, notices) is on disk.
    """

    def __init__(self, config, inbox: Inbox, announce: Callable[[Notice], None] | None = None,
                 run_agent: Callable[[str], str] | None = None, clock: Callable[[], datetime] = datetime.now,
                 audit: Callable[[str, dict], None] | None = None):
        hb = config["heartbeat"]
        self.config = config
        self.inbox = inbox
        self.announce = announce
        self.run_agent = run_agent
        self.clock = clock
        self.audit = audit or (lambda kind, data: None)
        self.tick_seconds = float(hb.get("tick_seconds", 5))
        self.quiet_start = hb.get("quiet_hours_start")
        self.quiet_end = hb.get("quiet_hours_end")
        self.state_path: Path = config.path("heartbeat", "state", STATE_DIR / "heartbeat.json")
        self.checks: list[dict[str, Any]] = [c for c in hb.get("checks", []) if c.get("enabled", True)]
        state = read_json(self.state_path, {})
        self.next_due: dict[str, datetime] = {
            k: datetime.fromisoformat(v) for k, v in state.get("next_due", {}).items()
        }
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.paused = False   # the kill switch (Tier 6) flips this
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                self.audit("heartbeat_error", {"error": repr(e)})
            self._stop.wait(self.tick_seconds)

    # -- one tick ---------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        if not self.paused:
            for spec in self.checks:
                name = spec["name"]
                due = self.next_due.get(name)
                if due is not None and due > now:
                    continue
                with self._lock:
                    if name in self._running:
                        self.audit("check_skipped_overlap", {"check": name})
                        continue
                    self._running.add(name)
                threading.Thread(target=self._run_check, args=(spec, now), daemon=True).start()
        self.deliver(now)

    def _run_check(self, spec: dict[str, Any], now: datetime) -> None:
        name = spec["name"]
        kind = spec.get("check", name)
        level = spec.get("level", "log")
        fn = CHECKS.get(kind)
        try:
            if fn is None:
                raise ValueError(f"unknown check kind {kind!r}; known: {', '.join(CHECKS)}")
            findings = fn(spec, CheckContext(self.config, now, self.run_agent))
            for text, key in findings:
                notice = self.inbox.add(name, level, text, dedupe_key=key, now=now)
                if notice:
                    self.audit("notice", {"check": name, "level": level, "text": text})
            self.audit("check_ran", {"check": name, "findings": len(findings)})
        except Exception as e:  # noqa: BLE001
            self.audit("check_failed", {"check": name, "error": f"{type(e).__name__}: {e}"})
        finally:
            with self._lock:
                self.next_due[name] = now + parse_interval(spec.get("every", "5m"))
                self._save_state()
                self._running.discard(name)

    def _save_state(self) -> None:
        """Caller holds self._lock: several checks can finish at once."""
        write_json(self.state_path, {"next_due": {k: v.isoformat(timespec="seconds") for k, v in self.next_due.items()}})

    # -- surfacing --------------------------------------------------------

    def deliver(self, now: datetime | None = None) -> list[Notice]:
        """Announce notices that earn an interruption right now. Log-level ones just wait in the inbox."""
        now = now or self.clock()
        quiet = in_quiet_hours(now, self.quiet_start, self.quiet_end)
        delivered = []
        for notice in self.inbox.unannounced():
            if notice.level == "critical" or (notice.level == "interrupt" and not quiet):
                self.inbox.mark_announced(notice)
                if self.announce:
                    self.announce(notice)
                delivered.append(notice)
        return delivered
