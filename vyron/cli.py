"""Text interface. Never removed: it is how every later layer gets debugged."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .agent import Agent
from .config import load_config, load_env
from .config import STATE_DIR
from .heartbeat import Heartbeat, Inbox, Notice
from .heartbeat.tools import notices_context, register as register_inbox_tools
from .memory import default_store, register_tools as register_memory_tools
from .provider import ProviderError, make_provider
from .tools import default_registry


def print_stream(chunk: str) -> None:
    sys.stdout.write(chunk)
    sys.stdout.flush()


def print_tool_event(event: dict) -> None:
    mark = "!" if event["is_error"] else "*"
    print(f"\n  [{mark} {event['tool']} {event['input']}] -> {event['result'][:120]!r}")
    sys.stdout.write(f"{'':>2}")


HELP = """Commands: /inbox  show held notices   /dismiss N|all  clear notices   /quit"""


def show_inbox(inbox: Inbox) -> None:
    pending = inbox.pending()
    if not pending:
        print("(no notices)")
    for n in pending:
        print("  " + n.line())


def handle_command(user: str, rt: "Runtime") -> bool:
    """Returns True if the input was a slash command (handled here, not sent to the brain)."""
    if not user.startswith("/"):
        return False
    cmd, _, arg = user.partition(" ")
    if cmd == "/inbox":
        show_inbox(rt.inbox)
    elif cmd == "/dismiss":
        if arg.strip() == "all":
            print(f"cleared {rt.inbox.dismiss_all()} notices")
        elif arg.strip().isdigit():
            print("dismissed" if rt.inbox.dismiss(int(arg)) else "no such notice")
        else:
            print("usage: /dismiss N | /dismiss all")
    elif cmd == "/help":
        print(HELP)
    else:
        print(f"unknown command {cmd}; {HELP}")
    return True


def text_loop(rt: "Runtime") -> None:
    agent = rt.agent
    print(f"{agent.name} (v{__version__}) is listening. Type /quit to exit, /help for commands.")
    pending = rt.inbox.pending()
    if pending:
        print(f"\nWhile you were away ({len(pending)} notice{'s' if len(pending) != 1 else ''}):")
        show_inbox(rt.inbox)
        for n in pending:
            rt.inbox.mark_announced(n)   # catch-up shown; nothing here gets announced twice
    while True:
        count = len(rt.inbox.pending())
        badge = f" ({count} notice{'s' if count != 1 else ''}, /inbox)" if count else ""
        try:
            user = input(f"\nyou{badge}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in {"/quit", "/exit"}:
            break
        if handle_command(user, rt):
            continue
        sys.stdout.write(f"{agent.name.lower()}> ")
        result = agent.run_turn(user, on_text=print_stream, on_tool=print_tool_event)
        if result.error:
            print(f"(couldn't get a reply: {result.error})")
        else:
            print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vyron", description="Vyron, a voice-first personal assistant.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--voice", action="store_true", help="push-to-talk voice mode (needs the voice extras)")
    args = parser.parse_args(argv)

    load_env()
    config = load_config()
    try:
        provider = make_provider(config)
    except ProviderError as e:
        print(f"Can't start: {e}", file=sys.stderr)
        return 2
    rt = build_runtime(config, provider)
    rt.heartbeat.announce = lambda n: print(f"\n[{rt.agent.name}] {n.text}\nyou> ", end="", flush=True)
    if config.get("heartbeat", "enabled", True):
        rt.heartbeat.start()
    try:
        if args.voice:
            return voice_mode(rt, config)
        text_loop(rt)
    finally:
        rt.heartbeat.stop()
    return 0


class Runtime:
    """Everything a front end needs: the one shared agent, the inbox, and the heartbeat."""

    def __init__(self, config, agent: Agent, inbox: Inbox, heartbeat: Heartbeat, new_agent):
        self.config, self.agent, self.inbox, self.heartbeat, self.new_agent = config, agent, inbox, heartbeat, new_agent


def build_runtime(config, provider) -> Runtime:
    """The one place the shared agent core is assembled: tools, memory, notices, heartbeat."""
    memory = default_store(config)
    inbox = Inbox(config.path("heartbeat", "inbox", STATE_DIR / "inbox.json"))
    limit = int(config.get("memory", "max_facts_in_prompt", 40))

    def new_agent() -> Agent:
        registry = default_registry(config)
        register_memory_tools(registry, memory)
        register_inbox_tools(registry, inbox)
        agent = Agent(config, provider, registry)
        agent.context_providers.append(lambda user_text: memory.prompt_block(user_text, limit))
        agent.context_providers.append(notices_context(inbox))
        return agent

    def run_background_turn(prompt: str) -> str:
        # A heartbeat-initiated turn: same brain and tools, fresh short-term history.
        result = new_agent().run_turn(prompt)
        return result.text if not result.error else ""

    heartbeat = Heartbeat(config, inbox, run_agent=run_background_turn)
    return Runtime(config, new_agent(), inbox, heartbeat, new_agent)


def voice_mode(rt: Runtime, config) -> int:
    agent = rt.agent
    from .config import secret
    from .voice.audio import Recorder, SoundDevicePlayer
    from .voice.session import VoiceSession, run_push_to_talk
    from .voice.stt import DeepgramTranscriber
    from .voice.tts import ElevenLabsSpeaker, SpeechQueue

    v = config["voice"]
    dg, el = secret("DEEPGRAM_API_KEY"), secret("ELEVENLABS_API_KEY")
    if not dg or not el:
        print("Voice mode needs DEEPGRAM_API_KEY and ELEVENLABS_API_KEY in .env.", file=sys.stderr)
        return 2
    rate = int(v.get("sample_rate", 16000))
    player = SoundDevicePlayer(rate)
    speaker = ElevenLabsSpeaker(el, v["tts_voice_id"], player, v.get("tts_model_id", "eleven_turbo_v2_5"))
    speech = SpeechQueue(speaker, on_error=lambda e: print(f"\n(speech failed: {e})"))
    transcriber = DeepgramTranscriber(dg, v.get("stt_model", "nova-3"), v.get("stt_language", "en"))
    session = VoiceSession(agent, transcriber, speech, Recorder(rate))
    rt.heartbeat.announce = lambda n: (print(f"\n[{agent.name}] {n.text}"), speech.say(n.text))
    for n in rt.inbox.pending():
        print("  " + n.line())
        rt.inbox.mark_announced(n)
    try:
        run_push_to_talk(session, v.get("ptt_key", "ctrl_r"))
    except RuntimeError as e:
        print(f"Can't start voice: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
