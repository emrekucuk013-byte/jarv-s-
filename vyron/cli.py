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
from .audit import default_audit
from .memory import default_store, register_tools as register_memory_tools
from .notify import notifier_from_env
from .safety import ConsoleConfirmer, TimeoutConfirmer, default_kill_switch
from .provider import ProviderError, make_provider
from .tools import default_registry


def print_stream(chunk: str) -> None:
    sys.stdout.write(chunk)
    sys.stdout.flush()


def print_tool_event(event: dict) -> None:
    mark = "!" if event["is_error"] else "*"
    print(f"\n  [{mark} {event['tool']} {event['input']}] -> {event['result'][:120]!r}")
    sys.stdout.write(f"{'':>2}")


HELP = """Commands:
  /inbox            show held notices        /dismiss N|all   clear notices
  /pause            kill switch: stop all proactive behaviour (heartbeat and background actions)
  /resume           release the kill switch  /audit [N]       last N audit entries
  /cost             model spend this session /quit"""


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
    elif cmd == "/pause":
        rt.kill_switch.engage()
        rt.audit.log("kill_switch", engaged=True)
        print("paused: the heartbeat and all background actions are stopped. You can still talk to me.")
    elif cmd == "/resume":
        rt.kill_switch.release()
        rt.audit.log("kill_switch", engaged=False)
        print("resumed: proactive behaviour is back on.")
    elif cmd == "/audit":
        n = int(arg) if arg.strip().isdigit() else 20
        print("\n".join(rt.audit.tail(n)) or "(audit log is empty)")
    elif cmd == "/cost":
        t = rt.audit.session_tokens
        print(f"this session: {t['input']} in / {t['output']} out tokens, about ${rt.audit.session_usd:.4f}")
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
    if rt.kill_switch.engaged:
        print("(kill switch is engaged: proactive behaviour is paused. /resume to release)")
    while True:
        count = len(rt.inbox.pending())
        badge = f" ({count} notice{'s' if count != 1 else ''}, /inbox)" if count else ""
        badge += " [paused]" if rt.kill_switch.engaged else ""
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
    parser.add_argument("--doctor", action="store_true", help="check keys, packages, APIs and audio devices, then exit")
    parser.add_argument("--serve", action="store_true", help="serve the HUD page to phones/tablets on your Wi-Fi")
    parser.add_argument("--port", type=int, default=None, help="port for --serve (default from config.toml [web])")
    parser.add_argument("--no-open", action="store_true", help="with --serve: don't open the HUD in this computer's browser")
    args = parser.parse_args(argv)
    if args.doctor:
        from .doctor import run_doctor
        return run_doctor()

    load_env()
    config = load_config()
    try:
        provider = make_provider(config)
    except ProviderError as e:
        print(f"Can't start: {e}", file=sys.stderr)
        return 2
    rt = build_runtime(config, provider)
    push = rt.heartbeat.announce

    def announce_console(n):
        print(f"\n[{rt.agent.name}] {n.text}\nyou> ", end="", flush=True)
        if push:
            push(n)
    rt.heartbeat.announce = announce_console
    if config.get("heartbeat", "enabled", True):
        rt.heartbeat.start()
    try:
        if args.serve:
            from .web.server import serve
            return serve(rt, config, port=args.port, open_browser=not args.no_open)
        if args.voice:
            return voice_mode(rt, config)
        text_loop(rt)
    finally:
        rt.heartbeat.stop()
    return 0


class Runtime:
    """Everything a front end needs: the one shared agent, the inbox, the heartbeat, and the rails."""

    def __init__(self, config, agent: Agent, inbox: Inbox, heartbeat: Heartbeat, new_agent, audit, kill_switch):
        self.config, self.agent, self.inbox, self.heartbeat = config, agent, inbox, heartbeat
        self.new_agent, self.audit, self.kill_switch = new_agent, audit, kill_switch


def build_runtime(config, provider, confirmer=None) -> Runtime:
    """The one place the shared agent core is assembled: tools, memory, notices, heartbeat, rails."""
    memory = default_store(config)
    inbox = Inbox(config.path("heartbeat", "inbox", STATE_DIR / "inbox.json"))
    audit = default_audit(config)
    kill_switch = default_kill_switch(config)
    limit = int(config.get("memory", "max_facts_in_prompt", 40))
    confirmer = confirmer or ConsoleConfirmer()

    def new_agent(source: str = "chat", who_confirms=None) -> Agent:
        registry = default_registry(config)
        register_memory_tools(registry, memory)
        register_inbox_tools(registry, inbox)
        agent = Agent(config, provider, registry, confirmer=who_confirms or confirmer, audit=audit, source=source)
        agent.context_providers.append(lambda user_text: memory.prompt_block(user_text, limit))
        agent.context_providers.append(notices_context(inbox))
        return agent

    # Unattended turns never block forever: wait a bounded time, then do nothing and leave a note.
    background_confirmer = TimeoutConfirmer(
        timeout=float(config.get("safety", "background_confirm_timeout", 0)),
        note=lambda text: inbox.add("approval", "log", text),
    )

    def run_background_turn(prompt: str) -> str:
        if kill_switch.engaged:
            return ""
        result = new_agent("heartbeat", background_confirmer).run_turn(prompt)
        return result.text if not result.error else ""

    from .config import secret
    if not ((secret("GMAIL_USER") or config.get("gmail", "user")) and secret("GMAIL_APP_PASSWORD")):
        # No Gmail credentials: drop the gmail check so it doesn't fail every run.
        config["heartbeat"]["checks"] = [c for c in config["heartbeat"].get("checks", []) if c.get("check") != "gmail"]
    heartbeat = Heartbeat(config, inbox, run_agent=run_background_turn, is_paused=lambda: kill_switch.engaged,
                          audit=lambda kind, data: audit.log(kind, source="heartbeat", **data))
    # Phone notifications: interrupt-level notices also go to ntfy when a topic is set.
    notifier = notifier_from_env(config)
    if notifier:
        def announce(n: Notice):
            ok = notifier.send(config["assistant"]["name"], n.text, "high" if n.level == "critical" else "default")
            audit.log("push", source="heartbeat", ok=ok, text=n.text)
        heartbeat.announce = announce
    rt = Runtime(config, new_agent(), inbox, heartbeat, new_agent, audit, kill_switch)
    rt.notifier = notifier
    return rt


def voice_mode(rt: Runtime, config) -> int:
    agent = rt.agent
    agent.source = "voice"
    from .config import secret
    from .voice.audio import Recorder, SoundDevicePlayer
    from .voice.session import VoiceSession, run_push_to_talk
    from .voice.stt import DeepgramTranscriber
    from .voice.tts import ElevenLabsSpeaker, SpeechQueue, SystemSpeaker

    v = config["voice"]
    dg, el = secret("DEEPGRAM_API_KEY"), secret("ELEVENLABS_API_KEY")
    if not dg:
        print("Voice mode needs DEEPGRAM_API_KEY in .env (free at https://console.deepgram.com).", file=sys.stderr)
        return 2
    rate = int(v.get("sample_rate", 16000))
    player = SoundDevicePlayer(rate)
    if el and v.get("tts_provider", "elevenlabs") == "elevenlabs":
        speaker = ElevenLabsSpeaker(el, v["tts_voice_id"], player, v.get("tts_model_id", "eleven_turbo_v2_5"))
        print("Voice: ElevenLabs.")
    else:
        speaker = SystemSpeaker(v.get("system_voice") or None, int(v["system_rate"]) if v.get("system_rate") else None)
        print("Voice: this computer's built-in voice (no ElevenLabs key set; add one to .env for a nicer voice).")
    speech = SpeechQueue(speaker, on_error=lambda e: print(f"\n(speech failed: {e})"))
    transcriber = DeepgramTranscriber(dg, v.get("stt_model", "nova-3"), v.get("stt_language", "en"))
    session = VoiceSession(agent, transcriber, speech, Recorder(rate))
    rt.heartbeat.announce = lambda n: (print(f"\n[{agent.name}] {n.text}"), speech.say(n.text), push and push(n))
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
