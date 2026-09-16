import json

import pytest

from vyron.agent import Agent
from vyron.config import Config, DEFAULTS
from vyron.provider import FakeProvider, ModelReply, ToolCall
from vyron.tools import ToolRegistry, default_registry
from vyron.tools.registry import validate_input


@pytest.fixture
def config(tmp_path):
    data = json.loads(json.dumps(DEFAULTS))
    data["reminders"] = {"path": str(tmp_path / "reminders.json")}
    data["notes"] = {"dir": str(tmp_path / "notes")}
    data["drafts"] = {"dir": str(tmp_path / "drafts"), "outbox": str(tmp_path / "outbox")}
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "projects.md").write_text("# Projects\nThe garage shelving needs 8 brackets.\n")
    return Config(data)


def test_registry_lists_the_first_tools(config):
    reg = default_registry(config)
    assert {"list_reminders", "add_reminder", "search_notes", "save_draft", "send_message"} <= set(reg.names())
    assert reg.get("send_message").consequential and reg.get("remove_reminder").consequential
    assert not reg.get("list_reminders").consequential


def test_validation_rejects_bad_input():
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}
    assert validate_input(schema, {}) == ["missing required field 'id'"]
    assert validate_input(schema, {"id": "3"}) == ["field 'id' should be a integer"]
    assert validate_input(schema, {"id": 3}) == []


def test_model_calls_tool_then_answers(config):
    script = [
        ModelReply(text="", tool_calls=[ToolCall("t1", "add_reminder", {"text": "buy brackets"})]),
        ModelReply(text="", tool_calls=[ToolCall("t2", "list_reminders", {})]),
        "You have one thing: buy brackets.",
    ]
    provider = FakeProvider(script)
    agent = Agent(config, provider, default_registry(config))
    events = []
    result = agent.run_turn("remind me to buy brackets, then what's on my list?", on_tool=events.append)
    assert result.error is None
    assert [e["tool"] for e in events] == ["add_reminder", "list_reminders"]
    assert "buy brackets" in events[1]["result"]
    # tool specs were handed to the model, and results went back into history
    assert any(t["name"] == "add_reminder" for t in provider.calls[0]["tools"])
    assert provider.calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert result.text.startswith("You have one thing")


def test_failed_tool_is_reported_to_model_not_raised(config):
    script = [
        ModelReply(text="", tool_calls=[ToolCall("t1", "complete_reminder", {"id": 99})]),
        ModelReply(text="", tool_calls=[ToolCall("t2", "no_such_tool", {})]),
        "Sorry, there's no reminder 99.",
    ]
    provider = FakeProvider(script)
    agent = Agent(config, provider, default_registry(config))
    result = agent.run_turn("finish task 99")
    sent = provider.calls[1]["messages"][-1]["content"][0]
    assert sent["is_error"] and "no reminder with id 99" in sent["content"]
    sent2 = provider.calls[2]["messages"][-1]["content"][0]
    assert sent2["is_error"] and "No tool named" in sent2["content"]
    assert result.text.startswith("Sorry")


def test_notes_search_and_read(config):
    reg = default_registry(config)
    out = reg.run("search_notes", {"query": "garage brackets"})
    assert "projects.md:2" in out.content and not out.is_error
    assert "8 brackets" in reg.run("read_note", {"name": "projects.md"}).content
    assert reg.run("read_note", {"name": "../secret"}).is_error


def test_tool_round_cap(config):
    provider = FakeProvider([ModelReply(text="", tool_calls=[ToolCall(f"t{i}", "list_reminders", {})]) for i in range(20)])
    agent = Agent(config, provider, default_registry(config))
    agent.max_tool_rounds = 3
    result = agent.run_turn("loop forever")
    assert "too many tool calls" in result.error
