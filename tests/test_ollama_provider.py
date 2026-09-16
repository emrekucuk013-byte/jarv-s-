import json

import httpx

from vyron.agent import Agent
from vyron.config import load_config
from vyron.provider import OllamaProvider, ProviderError
from vyron.tools import ToolRegistry


def ndjson(*chunks):
    return "\n".join(json.dumps(c) for c in chunks) + "\n"


def test_streams_text_and_translates_history():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=ndjson(
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"role": "assistant", "content": "lo."}, "done": True, "prompt_eval_count": 10, "eval_count": 3},
        ))

    p = OllamaProvider("llama3.2", transport=httpx.MockTransport(handler))
    chunks = []
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": ""}, {"type": "tool_use", "id": "c1", "name": "list_reminders", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "The list is empty."}]},
    ]
    reply = p.complete("You are Vyron.", history, [{"name": "list_reminders", "description": "d", "input_schema": {"type": "object", "properties": {}}}], chunks.append)
    assert reply.text == "Hello." and "".join(chunks) == "Hello."
    assert reply.usage.input_tokens == 10 and reply.usage.output_tokens == 3
    msgs = seen["body"]["messages"]
    assert msgs[0] == {"role": "system", "content": "You are Vyron."}
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "list_reminders"
    assert msgs[3] == {"role": "tool", "content": "The list is empty.", "tool_name": "list_reminders"}
    assert seen["body"]["tools"][0]["function"]["name"] == "list_reminders"


def test_tool_call_round_trip_through_agent():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text=ndjson(
                {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "echo", "arguments": {"text": "hey"}}}]}, "done": True}))
        body = json.loads(request.content)
        assert body["messages"][-1]["role"] == "tool" and body["messages"][-1]["content"] == "echo: hey"
        return httpx.Response(200, text=ndjson({"message": {"role": "assistant", "content": "It said hey."}, "done": True}))

    reg = ToolRegistry()
    reg.add("echo", "Echo text back", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})(lambda a: f"echo: {a['text']}")
    agent = Agent(load_config(), OllamaProvider(transport=httpx.MockTransport(handler)), reg)
    result = agent.run_turn("echo hey")
    assert result.text == "It said hey." and result.tool_events[0]["tool"] == "echo"


def test_errors_are_plain_language():
    def down(request):
        raise httpx.ConnectError("refused")
    p = OllamaProvider(transport=httpx.MockTransport(down))
    try:
        p.complete("s", [{"role": "user", "content": "x"}], [])
    except ProviderError as e:
        assert "isn't running" in str(e)
    p = OllamaProvider("nope", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    try:
        p.complete("s", [{"role": "user", "content": "x"}], [])
    except ProviderError as e:
        assert "ollama pull nope" in str(e)
