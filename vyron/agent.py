"""The brain: one conversation loop shared by every way in and out.

A typed turn, a spoken turn, and a heartbeat-initiated turn all call
``Agent.run_turn``. Nothing else should ever run agent logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .provider import ModelProvider, ModelReply, ProviderError, TextCallback
from .tools import ToolRegistry

ToolEventCallback = Callable[[dict[str, Any]], None]


def build_system_prompt(config) -> str:
    a = config["assistant"]
    return (
        f"You are {a['name']}, {a['purpose']}.\n"
        f"Your tone is {a['tone']}. Keep replies short enough to be spoken aloud comfortably; "
        f"one to three sentences unless the user asks for detail. Speak like a person, not a form.\n"
        f"You have tools. Use one whenever it would make your answer accurate instead of guessing. "
        f"If a tool fails, say what went wrong in plain words and suggest what to do next."
    )


@dataclass
class TurnResult:
    text: str
    error: str | None = None
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    def __init__(self, config, provider: ModelProvider, tools: ToolRegistry | None = None):
        self.config = config
        self.provider = provider
        self.tools = tools or ToolRegistry()
        self.history: list[dict[str, Any]] = []  # short-term memory: this session only
        self.name = config["assistant"]["name"]
        self.max_tool_rounds = int(config.get("model", "max_tool_rounds", 10))

    def system_prompt(self) -> str:
        return build_system_prompt(self.config)

    def run_turn(
        self,
        user_text: str,
        on_text: TextCallback | None = None,
        on_tool: ToolEventCallback | None = None,
    ) -> TurnResult:
        """Take one user turn and produce one reply, streaming text as it arrives.

        The model may call several tools in a row before answering; each round
        runs the requested tools, feeds the results back, and asks again.
        """
        self.history.append({"role": "user", "content": user_text})
        checkpoint = len(self.history) - 1
        events: list[dict[str, Any]] = []
        text = ""
        for _ in range(self.max_tool_rounds + 1):
            try:
                reply: ModelReply = self.provider.complete(
                    self.system_prompt(), self.history, self.tools.specs(), on_text
                )
            except ProviderError as e:
                # Roll back to before this turn so the next one starts clean.
                del self.history[checkpoint:]
                return TurnResult(text="", error=str(e), tool_events=events)
            self.history.append({"role": "assistant", "content": reply.content})
            text = reply.text
            if not reply.wants_tools:
                return TurnResult(text=text, tool_events=events)
            results = []
            for call in reply.tool_calls:
                outcome = self.tools.run(call.name, call.input)
                event = {"tool": call.name, "input": call.input, "result": outcome.content, "is_error": outcome.is_error}
                events.append(event)
                if on_tool:
                    on_tool(event)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": outcome.content, "is_error": outcome.is_error})
            self.history.append({"role": "user", "content": results})
        return TurnResult(text=text, error="Stopped after too many tool calls in one turn.", tool_events=events)
