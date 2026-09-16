# Vyron — build spec

Single source of truth for what this assistant is and why. Written from the
Tier 0 interview; later sessions should read this before touching code.

## Identity

- **Name:** Vyron
- **Purpose:** a personal, voice-first daily-driver assistant that can act on
  my behalf, remembers me between conversations, and can reach out first when
  something is genuinely worth my attention.
- **For:** just me (single user). Per-user state is not modelled yet.
- **Tone:** warm, plain-spoken, brief. Same voice everywhere: greetings,
  spoken replies, logs.

## First three capabilities (first tools, first test cases)

1. **Reminders and a to-do list** — add, list, complete, remove.
2. **Answer questions about my notes** — search a local folder of plain-text
   or Markdown notes and answer from them.
3. **Draft messages** — write a message for me to review. Sending is a
   separate, gated action.

## Stack

- Python 3.11+, minimal dependencies, no heavy framework.
- Brain: latest Claude via the official `anthropic` SDK, behind a thin seam
  (`vyron/provider.py`) so the provider can be swapped in one file.
- Speech-to-text: Deepgram, behind a seam. Text-to-speech: ElevenLabs, behind
  a seam. Voice name/id lives in `config.toml`, never hardcoded.
- Runs on my laptop first. The heartbeat is a separate loop so it can move to
  an always-on host later without a rewrite.

## How I talk to it

- Text first (`python -m vyron`). The text path is never removed.
- Push-to-talk voice next (`python -m vyron --voice`). Wake words later.

## Never without asking (hard confirmation gate)

Anything that **sends a message, spends money, deletes data, or changes a
setting**. Confirmation is per action and never generalises. Read-only
actions run freely.

## Proactivity

Yes, but **quiet by default**. Most checks produce nothing. Notices are held
until I'm back, respect quiet hours, are dismissible, and a background action
waiting on my approval times out to "do nothing and leave a note".

## Interview defaults assumed

Only the name and the ElevenLabs voice id (`bfGb7JTLUnZebZRiFYyq`, in
`config.toml`) were given. Everything else above is the default from the
build brief.

## Build order

Tier 1 text brain → Tier 2 tools → Tier 3 voice → Tier 4 memory →
Tier 5 heartbeat → Tier 6 rails. Each tier is runnable and verified before
the next starts. All six are built; `README.md` has the run and hand
verification steps, and `tests/` has one file per tier.

## Where things live

- Settings: `config.toml`. Secrets: `.env`. Runtime state: `state/`.
- Shared core assembly: `build_runtime` in `vyron/cli.py`.
- New capability = one tool module + one line in `default_registry`.
