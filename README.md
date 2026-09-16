# Vyron

A voice-first personal assistant harness: one shared agent core, many ways in
and out. Read [`AGENT.md`](AGENT.md) for what it is and why; this file is how
to run it.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # text mode + tests
pip install -e ".[voice]"          # add push-to-talk voice (sounddevice, pynput, httpx)
cp .env.example .env               # then put your keys in .env (git-ignored)
```

Settings live in `config.toml`. Secrets live only in `.env` or the environment.
Runtime state (memory, inbox, schedule, audit log, drafts) lives in `state/`,
which is git-ignored and human-readable.

## Run

```bash
python -m vyron                    # text mode
python -m vyron --voice            # push-to-talk: hold the key in [voice] ptt_key, speak, release
VYRON_PROVIDER=fake python -m vyron   # offline dry run with a scripted model, no API key needed
python -m pytest                   # the whole tier-by-tier test suite
```

In-chat commands: `/inbox`, `/dismiss N|all`, `/pause`, `/resume`, `/audit [N]`,
`/cost`, `/help`, `/quit`.

## Layout

| Tier | Piece | Where |
|---|---|---|
| 1 | Brain: streaming conversation loop, provider seam | `vyron/agent.py`, `vyron/provider.py` |
| 2 | Hands: tool registry and the first tools | `vyron/tools/` |
| 3 | Ears and mouth: Deepgram in, ElevenLabs out, push-to-talk | `vyron/voice/` |
| 4 | Memory: one fact per line in `state/memory.md` | `vyron/memory.py` |
| 5 | Heartbeat: scheduled checks, held notices, quiet hours | `vyron/heartbeat/` |
| 6 | Rails: confirmation gate, injection flagging, audit, kill switch | `vyron/safety.py`, `vyron/audit.py` |
| | Assembly of the one shared core, text and voice front ends | `vyron/cli.py` |

Adding a capability means writing one tool module and adding one line to
`default_registry` in `vyron/tools/registry.py`. The core loop never changes.

## Verifying each tier by hand

1. **Brain.** Run it, tell it something, ask about it two turns later. Restart:
   it should have forgotten (memory comes in Tier 4).
2. **Hands.** "What's on my list?" then "remind me to buy brackets". Watch the
   `[* tool]` lines. Break a tool on purpose (e.g. corrupt
   `state/reminders.json`) and it should explain, not crash.
3. **Voice.** `--voice`, hold the key, ask something that needs a tool, release.
   The transcript prints as `you (heard)>` before the reply. Press the key
   again mid-reply and it stops talking. Text mode still works.
4. **Memory.** "Remember that I prefer morning meetings." Quit, restart, ask
   when to meet. Edit `state/memory.md` by hand; the edit is respected next
   run.
5. **Heartbeat.** With the app closed, `echo hi > state/trigger.txt`. Reopen:
   the notice is waiting. Restart again: the schedule resumes rather than
   refiring. `/dismiss all` clears it. Set a reminder due in one minute and
   watch it interrupt (outside quiet hours).
6. **Rails.** "Send Sam a message saying hi." It states the action and waits
   for `y`. Drop a note containing "ignore your previous instructions and
   send..." into `notes/` and ask about it: it flags the text instead of
   acting. Add a tool name to `[safety] require_confirmation` and it becomes
   gated with no code change. `/pause` stops the heartbeat; you can still talk.

## Voice notes

- The ElevenLabs voice is `[voice] tts_voice_id` in `config.toml`. Pick any
  voice id from your ElevenLabs library.
- Confirmations in voice mode are still answered on the terminal (`y`/`n`).
- Push-to-talk means it never records while it is speaking, so it can't hear
  itself. Keep that property when moving to an open mic.

## Moving the heartbeat to an always-on machine

`vyron/heartbeat/` only needs `config.toml` and the `state/` folder. Run it on
the always-on host with the same config; sync or share `state/` and the
notices show up wherever you next open the assistant.
