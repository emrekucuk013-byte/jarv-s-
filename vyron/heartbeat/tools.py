"""Tools that let the assistant read and clear the inbox during a conversation."""

from __future__ import annotations

from .inbox import Inbox


def register(registry, inbox: Inbox) -> None:
    @registry.add(
        "list_notices",
        "Use this to see what the background heartbeat has noticed and is holding for the user: due reminders, "
        "watched changes, anything it flagged. Use it when the user asks what's new or what they missed.",
        {"type": "object", "properties": {}, "required": []},
    )
    def _list(args):
        pending = inbox.pending()
        return "\n".join(n.line() for n in pending) if pending else "No pending notices."

    @registry.add(
        "dismiss_notice",
        "Use this to clear a notice the user has acknowledged, by id from list_notices. Use id 0 to clear all.",
        {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    )
    def _dismiss(args):
        if args["id"] == 0:
            return f"Cleared {inbox.dismiss_all()} notices."
        return "Dismissed." if inbox.dismiss(args["id"]) else f"No pending notice #{args['id']}."


def notices_context(inbox: Inbox):
    """Context provider: tells the brain what's pending so it can mention it naturally."""
    def provide(user_text: str) -> str:
        pending = inbox.pending()
        if not pending:
            return ""
        lines = "\n".join(n.line() for n in pending[:10])
        return ("Notices from your background checks the user has not dismissed yet (mention them if relevant, "
                "and offer to clear them):\n" + lines)
    return provide
