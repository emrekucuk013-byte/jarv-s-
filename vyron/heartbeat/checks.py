"""Scheduled checks. Each is a small unit: when to run, what to look at, whether it's worth surfacing.

A check function receives (settings, ctx) and returns a list of (text, dedupe_key)
findings. Returning an empty list is the normal, quiet outcome. ``ctx`` carries
``config``, ``now`` and ``run_agent`` (a callable that runs one fresh agent turn).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..config import ROOT, STATE_DIR
from ..tools._store import read_json

Finding = tuple[str, str]
CheckFn = Callable[[dict[str, Any], "CheckContext"], list[Finding]]

CHECKS: dict[str, CheckFn] = {}


def check(name: str):
    def deco(fn: CheckFn) -> CheckFn:
        CHECKS[name] = fn
        return fn
    return deco


class CheckContext:
    def __init__(self, config, now: datetime, run_agent: Callable[[str], str] | None = None):
        self.config = config
        self.now = now
        self.run_agent = run_agent


def parse_interval(text: str | int | float) -> timedelta:
    """'30s', '5m', '2h', '1d' or a bare number of seconds."""
    if isinstance(text, (int, float)):
        return timedelta(seconds=float(text))
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(text))
    if not m:
        raise ValueError(f"bad interval {text!r}; use e.g. 30s, 5m, 2h, 1d")
    n, unit = float(m.group(1)), m.group(2) or "s"
    return timedelta(seconds=n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


@check("due_reminders")
def due_reminders(settings, ctx) -> list[Finding]:
    """Surface reminders whose due time has arrived."""
    path = ctx.config.path("reminders", "path", STATE_DIR / "reminders.json")
    out = []
    for item in read_json(path, []):
        if item.get("done") or not item.get("due"):
            continue
        try:
            due = datetime.fromisoformat(item["due"])
        except ValueError:
            continue
        if due <= ctx.now:
            out.append((f"Reminder due: {item['text']}", f"reminder:{item['id']}:{item['due']}"))
    return out


@check("file_watch")
def file_watch(settings, ctx) -> list[Finding]:
    """Notice when a file appears or changes. Handy for wiring in anything that can touch a file."""
    raw = settings.get("path")
    if not raw:
        raise ValueError("file_watch needs a 'path' setting")
    path = Path(raw)
    path = path if path.is_absolute() else ROOT / path
    if not path.exists():
        return []
    stamp = int(path.stat().st_mtime)
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    text = settings.get("message") or f"{path.name} changed" + (f": {content[:200]}" if content else "")
    return [(text, f"file:{path}:{stamp}")]


@check("ask_agent")
def ask_agent(settings, ctx) -> list[Finding]:
    """Let the brain judge: run one fresh agent turn with a prompt from config.

    The prompt should describe what to look at. The model replies NOTHING when
    there is nothing worth the user's attention; anything else becomes a notice.
    """
    prompt = settings.get("prompt")
    if not prompt:
        raise ValueError("ask_agent needs a 'prompt' setting")
    if ctx.run_agent is None:
        raise RuntimeError("no agent available to this heartbeat")
    reply = ctx.run_agent(
        prompt + "\n\nThis is a background check, not a conversation. Reply with exactly NOTHING if there is "
        "nothing worth interrupting the user for; otherwise reply with a one- or two-sentence notice."
    ).strip()
    if not reply or reply.upper().startswith("NOTHING"):
        return []
    return [(reply, f"agent:{settings.get('name')}:{ctx.now.date()}:{reply[:60]}")]
