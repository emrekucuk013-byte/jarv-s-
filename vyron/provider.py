"""The model provider seam.

Exactly one job: "send this conversation, get back a reply (or a request to
use a tool)". Nothing outside this module touches the provider SDK. Swapping
models, adding retries, or logging cost all happen here.

Conversation format (provider-neutral, kept deliberately close to the wire):

    {"role": "user", "content": "typed text"}
    {"role": "assistant", "content": [{"type": "text", "text": "..."},
                                      {"type": "tool_use", "id": ..., "name": ..., "input": {...}}]}
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": "...", "is_error": bool}]}

Tool specs handed to the provider are plain dicts:
    {"name": str, "description": str, "input_schema": {json schema}}
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

TextCallback = Callable[[str], None]


class ProviderError(Exception):
    """A plain-language failure the UI can show instead of a stack trace."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: list[dict[str, Any]] = field(default_factory=list)  # assistant blocks to append to history
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class ModelProvider(Protocol):
    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback | None = None,
    ) -> ModelReply: ...


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


class AnthropicProvider:
    def __init__(self, model: str, max_tokens: int = 4096, effort: str = "medium", api_key: str | None = None):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ProviderError("The 'anthropic' package is not installed. Run: pip install anthropic") from e
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set. Put it in .env (see .env.example).")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=120)
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort

    def complete(self, system, messages, tools, on_text=None) -> ModelReply:
        a = self._anthropic
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": self.effort},
        )
        if tools:
            kwargs["tools"] = tools
        try:
            with self._client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if event.type == "text" and on_text:
                        on_text(event.text)
                final = stream.get_final_message()
        except a.AuthenticationError as e:
            raise ProviderError("The model rejected the API key. Check ANTHROPIC_API_KEY in .env.") from e
        except a.RateLimitError as e:
            raise ProviderError("The model is rate-limiting us right now. Try again in a moment.") from e
        except a.APIConnectionError as e:
            raise ProviderError("Couldn't reach the model (network problem or timeout).") from e
        except a.APIStatusError as e:
            raise ProviderError(f"The model service returned an error ({e.status_code}).") from e

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        content: list[dict[str, Any]] = []
        for block in final.content:
            if block.type == "text":
                text_parts.append(block.text)
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
            elif block.type == "thinking":
                # Must be echoed back unchanged on the same model.
                content.append(block.model_dump(exclude_none=True))
        if final.stop_reason == "max_tokens" and tool_calls:
            raise ProviderError("The reply was cut off before a tool call finished. Raise model.max_tokens in config.toml.")
        if final.stop_reason == "refusal":
            tool_calls = []
        return ModelReply(
            text="".join(text_parts),
            tool_calls=tool_calls,
            content=content,
            stop_reason=final.stop_reason or "end_turn",
            usage=Usage(final.usage.input_tokens, final.usage.output_tokens),
        )


# --------------------------------------------------------------------------
# Fake (offline). Used by tests and by `provider = "fake"` for dry runs.
# --------------------------------------------------------------------------


class FakeProvider:
    """A scripted stand-in for the model.

    ``script`` is a list of replies consumed in order. Each entry is either a
    string (a plain text reply) or a ``ModelReply``. When the script runs out
    it echoes a summary of the last user message, which is enough to prove
    history is being passed back in.
    """

    def __init__(self, script: list[str | ModelReply] | None = None):
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    def complete(self, system, messages, tools, on_text=None) -> ModelReply:
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
        if self.script:
            item = self.script.pop(0)
            reply = item if isinstance(item, ModelReply) else ModelReply(text=item)
        else:
            reply = ModelReply(text=self._echo(messages))
        if not reply.content:
            reply.content = [{"type": "text", "text": reply.text}] if reply.text else []
            for tc in reply.tool_calls:
                reply.content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
        if on_text and reply.text:
            for word in reply.text.split(" "):
                on_text(word + " ")
        reply.usage = Usage(input_tokens=sum(len(str(m)) for m in messages) // 4, output_tokens=len(reply.text) // 4)
        return reply

    @staticmethod
    def _echo(messages) -> str:
        user_turns = [m for m in messages if m["role"] == "user" and isinstance(m["content"], str)]
        last = user_turns[-1]["content"] if user_turns else ""
        return f"[fake model] You said: {last!r}. That's user turn {len(user_turns)} of this conversation."


def make_provider(config) -> ModelProvider:
    section = config["model"]
    kind = os.environ.get("VYRON_PROVIDER") or section.get("provider", "anthropic")
    if kind == "fake":
        return FakeProvider()
    if kind == "anthropic":
        return AnthropicProvider(
            model=section.get("name", "claude-opus-5"),
            max_tokens=int(section.get("max_tokens", 4096)),
            effort=section.get("effort", "medium"),
        )
    raise ProviderError(f"Unknown model provider {kind!r} in config.toml.")
