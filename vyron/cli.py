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
    parser.parse_args(argv)

    load_env()
    config = load_config()
    try:
        provider = make_provider(config)
    except ProviderError as e:
        print(f"Can't start: {e}", file=sys.stderr)
        return 2
    agent = Agent(config, provider, default_registry(config))
    text_loop(agent)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
