"""Capability 3: draft messages. Drafting is free; sending is consequential.

There is no real mail/chat transport yet: send_message writes to
state/outbox/ so the gate and the audit trail can be exercised end to end.
Wire a real transport by replacing ``deliver`` below.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..config import STATE_DIR


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "draft"


def deliver(to: str, subject: str, body: str, outbox: Path) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outbox / f"{stamp}-{_slug(to)}.txt"
    path.write_text(f"To: {to}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
    return path


def register(registry, config) -> None:
    drafts_dir = config.path("drafts", "dir", STATE_DIR / "drafts")
    outbox = config.path("drafts", "outbox", STATE_DIR / "outbox")

    @registry.add(
        "save_draft",
        "Use this to save a message you've drafted for the user (email, text, note to a colleague) so they can review it. "
        "Drafting never sends anything.",
        {"type": "object", "properties": {
            "to": {"type": "string", "description": "Recipient name or address."},
            "subject": {"type": "string", "description": "Subject or one-line summary."},
            "body": {"type": "string", "description": "The full message text."}},
         "required": ["to", "subject", "body"]},
    )
    def save_draft(args):
        drafts_dir.mkdir(parents=True, exist_ok=True)
        path = drafts_dir / f"{_slug(args['subject'])}.txt"
        path.write_text(f"To: {args['to']}\nSubject: {args['subject']}\n\n{args['body']}\n", encoding="utf-8")
        return f"Draft saved to {path}."

    @registry.add(
        "send_message",
        "Use this only when the user explicitly asks to send a message. Sending is irreversible and always requires "
        "the user's confirmation first.",
        {"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "subject", "body"]},
        consequential=True,
    )
    def send_message(args):
        path = deliver(args["to"], args["subject"], args["body"], outbox)
        return f"Sent to {args['to']} (recorded at {path})."
