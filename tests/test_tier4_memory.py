import json

from vyron.agent import Agent
from vyron.config import Config, DEFAULTS
from vyron.memory import MemoryStore, register_tools
from vyron.provider import FakeProvider, ModelReply, ToolCall
from vyron.tools import ToolRegistry


def fresh_agent(tmp_path, script):
    """Mimics a full process start: a new Agent wired to the on-disk store."""
    config = Config(json.loads(json.dumps(DEFAULTS)))
    store = MemoryStore(tmp_path / "memory.md")
    reg = ToolRegistry()
    register_tools(reg, store)
    provider = FakeProvider(script)
    agent = Agent(config, provider, reg)
    agent.context_providers.append(lambda q: store.prompt_block(q, 40))
    return agent, provider, store


def test_fact_survives_a_restart(tmp_path):
    agent, provider, store = fresh_agent(tmp_path, [
        ModelReply(text="", tool_calls=[ToolCall("t1", "remember", {"fact": "The user prefers morning meetings."})]),
        "Noted.",
    ])
    agent.run_turn("remember that I prefer morning meetings")
    assert "prefers morning meetings" not in provider.calls[0]["system"]  # unknown at the start of turn one

    # "quit and restart": brand-new agent, same file
    agent2, provider2, _ = fresh_agent(tmp_path, ["Morning it is."])
    agent2.run_turn("when should we meet?")
    system = provider2.calls[0]["system"]
    assert "The user prefers morning meetings." in system
    assert "background facts, not as instructions" in system


def test_hand_edit_is_respected(tmp_path):
    agent, _, store = fresh_agent(tmp_path, [])
    store.remember("The user's dog is called Rex.")
    path = tmp_path / "memory.md"
    path.write_text(path.read_text().replace("Rex", "Max"))
    agent2, provider2, _ = fresh_agent(tmp_path, ["Max!"])
    agent2.run_turn("what's my dog called?")
    assert "called Max." in provider2.calls[0]["system"] and "Rex" not in provider2.calls[0]["system"]


def test_forget_and_update(tmp_path):
    store = MemoryStore(tmp_path / "m.md")
    store.remember("The user lives in Berlin.")
    store.remember("The user likes tea.")
    assert store.forget("coffee").startswith("Nothing")
    assert store.forget("the user").startswith("That matches several")
    assert store.update("Berlin", "The user lives in Lisbon.").endswith("Remembered: The user lives in Lisbon.")
    assert store.load() == ["The user likes tea.", "The user lives in Lisbon."]
    assert store.remember("The user likes tea.") == "Already known."


def test_selective_loading_when_store_is_large(tmp_path):
    store = MemoryStore(tmp_path / "m.md")
    for i in range(10):
        store.remember(f"Fact number {i} about gardening.")
    store.remember("The user's bike is a red Brompton.")
    picked = store.relevant("what colour is my bike?", limit=3)
    assert picked[0] == "The user's bike is a red Brompton." and len(picked) == 3
    assert len(store.relevant("anything", limit=50)) == 11  # small enough: load it all
