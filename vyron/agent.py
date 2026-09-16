"""The brain: one conversation loop shared by every way in and out.

A typed turn, a spoken turn, and a heartbeat-initiated turn all call
``Agent.run_turn``. Nothing else should ever run agent logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .provider import ModelProvider, ModelReply, ProviderError, TextCallback


def build_system_prompt(config) -> str:
    a = config["assistant"]
    return (
        f"You are {a['name']}, {a['purpose']}.\n"
        f"Your tone is {a['tone']}. Keep replies short enough to be spoken aloud comfortably; "
        f"one to three sentences unless the user asks for detail. Speak like a person, not a form."
    )


@dataclass
class TurnResult:
    text: str
    error: str | None = None
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    def __init__(self, config, provider: ModelProvider):
        self.config = config
        self.provider = provider
        self.history: list[dict[str, Any]] = []  # short-term memory: this session only
        self.name = config["assistant"]["name"]

    def system_prompt(self) -> str:
        return build_system_prompt(self.config)

    def run_turn(self, user_text: str, on_text: TextCallback | None = None) -> TurnResult:
        """Take one user turn and produce one reply, streaming text as it arrives."""
        self.history.append({"role": "user", "content": user_text})
        try:
            reply: ModelReply = self.provider.complete(self.system_prompt(), self.history, [], on_text)
        except ProviderError as e:
            # Don't leave a dangling user turn that got no answer.
            self.history.pop()
            return TurnResult(text="", error=str(e))
        self.history.append({"role": "assistant", "content": reply.content})
        return TurnResult(text=reply.text)
