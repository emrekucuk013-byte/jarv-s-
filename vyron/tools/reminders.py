"""Capability 1: reminders and a to-do list, stored in state/reminders.json."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import STATE_DIR
from ._store import read_json, write_json


class ReminderStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[dict]:
        return read_json(self.path, [])

    def save(self, items: list[dict]) -> None:
        write_json(self.path, items)

    def add(self, text: str, due: str | None = None) -> dict:
        items = self.load()
        next_id = max((i["id"] for i in items), default=0) + 1
        item = {"id": next_id, "text": text, "due": due, "done": False, "created": datetime.now().isoformat(timespec="minutes")}
        items.append(item)
        self.save(items)
        return item

    def set_done(self, item_id: int, done: bool = True) -> dict:
        items = self.load()
        for item in items:
            if item["id"] == item_id:
                item["done"] = done
                self.save(items)
                return item
        raise KeyError(f"no reminder with id {item_id}")

    def remove(self, item_id: int) -> dict:
        items = self.load()
        for item in items:
            if item["id"] == item_id:
                items.remove(item)
                self.save(items)
                return item
        raise KeyError(f"no reminder with id {item_id}")


def _fmt(item: dict) -> str:
    box = "[x]" if item["done"] else "[ ]"
    due = f" (due {item['due']})" if item.get("due") else ""
    return f"{box} #{item['id']} {item['text']}{due}"


def register(registry, config) -> None:
    store = ReminderStore(config.path("reminders", "path", STATE_DIR / "reminders.json"))

    @registry.add(
        "list_reminders",
        "Use this to see the user's reminders and to-do items. Call it whenever they ask what's on their list, "
        "what they need to do, or before completing or removing an item so you have the right id.",
        {"type": "object", "properties": {
            "include_done": {"type": "boolean", "description": "Also show completed items. Default false."}},
         "required": []},
    )
    def list_reminders(args):
        items = store.load()
        if not args.get("include_done"):
            items = [i for i in items if not i["done"]]
        if not items:
            return "The list is empty."
        return "\n".join(_fmt(i) for i in items)

    @registry.add(
        "add_reminder",
        "Use this to add a reminder or to-do item for the user. Include a due date/time when they mention one.",
        {"type": "object", "properties": {
            "text": {"type": "string", "description": "What to remember, as a short plain statement."},
            "due": {"type": "string", "description": "Optional due date or time in ISO 8601, e.g. 2026-09-17T09:00."}},
         "required": ["text"]},
    )
    def add_reminder(args):
        return "Added: " + _fmt(store.add(args["text"], args.get("due")))

    @registry.add(
        "complete_reminder",
        "Use this to mark a reminder or to-do item as done, by its id from list_reminders.",
        {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    )
    def complete_reminder(args):
        return "Done: " + _fmt(store.set_done(args["id"]))

    @registry.add(
        "remove_reminder",
        "Use this to permanently delete a reminder or to-do item by id. This deletes data, so the user must confirm.",
        {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
        consequential=True,
    )
    def remove_reminder(args):
        return "Removed: " + _fmt(store.remove(args["id"]))
