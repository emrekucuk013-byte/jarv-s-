"""Text interface. Never removed: it is how every later layer gets debugged."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .agent import Agent
from .config import load_config, load_env
from .provider import ProviderError, make_provider
from .tools import default_registry


def print_stream(chunk: str) -> None:
    sys.stdout.write(chunk)
    sys.stdout.flush()


def print_tool_event(event: dict) -> None:
    mark = "!" if event["is_error"] else "*"
    print(f"\n  [{mark} {event['tool']} {event['input']}] -> {event['result'][:120]!r}")
    sys.stdout.write(f"{'':>2}")


def text_loop(agent: Agent) -> None:
    print(f"{agent.name} (v{__version__}) is listening. Type /quit to exit.")
    while True:
        try:
            user = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in {"/quit", "/exit"}:
            break
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
    agent = Agent(config, provider, default_registry(config))
    if args.voice:
        return voice_mode(agent, config)
    text_loop(agent)
    return 0


def voice_mode(agent: Agent, config) -> int:
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
    try:
        run_push_to_talk(session, v.get("ptt_key", "ctrl_r"))
    except RuntimeError as e:
        print(f"Can't start voice: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
