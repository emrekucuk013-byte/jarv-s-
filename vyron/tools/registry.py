from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], str]


@dataclass
class Tool:
    name: str
    description: str          # written for a reader: "Use this to ..."
    input_schema: dict[str, Any]
    handler: Handler
    consequential: bool = False   # sends / spends / deletes / changes a setting -> needs the Tier 6 gate

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_input(schema: dict[str, Any], data: Any) -> list[str]:
    """Minimal JSON-schema check: object shape, required keys, primitive types, enums."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["input must be an object"]
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in data:
            problems.append(f"missing required field '{key}'")
    for key, value in data.items():
        if key not in props:
            problems.append(f"unknown field '{key}'")
            continue
        expected = props[key].get("type")
        py = _TYPE_MAP.get(expected)
        if py and not isinstance(value, py) or (expected == "integer" and isinstance(value, bool)):
            problems.append(f"field '{key}' should be a {expected}")
        enum = props[key].get("enum")
        if enum and value not in enum:
            problems.append(f"field '{key}' must be one of {enum}")
    return problems


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} registered twice")
        self._tools[tool.name] = tool
        return tool

    def add(self, name: str, description: str, input_schema: dict[str, Any], consequential: bool = False):
        """Decorator form: @registry.add("name", "desc", schema)"""
        def deco(fn: Handler) -> Handler:
            self.register(Tool(name, description, input_schema, fn, consequential))
            return fn
        return deco

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def run(self, name: str, data: dict[str, Any]) -> ToolResult:
        """Run a tool. Never raises: failures come back as plain-language errors for the model."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"No tool named '{name}'. Available: {', '.join(self._tools)}", is_error=True)
        problems = validate_input(tool.input_schema, data)
        if problems:
            return ToolResult(f"Invalid input for {name}: " + "; ".join(problems), is_error=True)
        try:
            return ToolResult(str(tool.handler(data)))
        except Exception as e:  # noqa: BLE001 - the model gets to reason over any failure
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            return ToolResult(f"The tool '{name}' failed: {detail}", is_error=True)


def default_registry(config) -> ToolRegistry:
    """Build the registry with every built-in tool. New tools: add one line here."""
    from . import drafts, gmail, notes, reminders

    registry = ToolRegistry()
    reminders.register(registry, config)
    notes.register(registry, config)
    drafts.register(registry, config)
    gmail.register(registry, config)   # only registers when GMAIL_USER / GMAIL_APP_PASSWORD are set
    return registry
