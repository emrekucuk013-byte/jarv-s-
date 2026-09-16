from vyron.agent import Agent
from vyron.config import load_config
from vyron.provider import FakeProvider, ModelReply, ProviderError


def make_agent(script=None, provider=None):
    return Agent(load_config(), provider or FakeProvider(script))


def test_reply_streams_and_is_recorded():
    agent = make_agent(["Hello there."])
    chunks = []
    result = agent.run_turn("hi", on_text=chunks.append)
    assert result.text == "Hello there."
    assert "".join(chunks).strip() == "Hello there."
    assert [m["role"] for m in agent.history] == ["user", "assistant"]


def test_history_is_passed_back_every_turn():
    provider = FakeProvider()
    agent = make_agent(provider=provider)
    agent.run_turn("my favourite colour is teal")
    agent.run_turn("what did I just say?")
    third = agent.run_turn("and again?")
    sent = provider.calls[-1]["messages"]
    assert sent[0]["content"] == "my favourite colour is teal"
    assert "turn 3" in third.text
    assert provider.calls[-1]["system"].startswith("You are Vyron")


def test_provider_failure_does_not_crash_or_corrupt_history():
    class Flaky:
        def complete(self, *a, **k):
            raise ProviderError("Couldn't reach the model.")

    agent = make_agent(provider=Flaky())
    result = agent.run_turn("hello?")
    assert result.error == "Couldn't reach the model."
    assert agent.history == []  # the unanswered turn is dropped so the next one is clean
