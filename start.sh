#!/usr/bin/env bash
# Easy start for Mac/Linux: sets everything up on first run, then starts Vyron in text mode.
# Usage: ./start.sh            (text)      ./start.sh --voice   (once you have a mic and the voice keys)
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ then run this again."
  command -v open >/dev/null 2>&1 && open "https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: setting things up (about a minute)..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if [ ! -f .venv/.installed ] || [ pyproject.toml -nt .venv/.installed ]; then
  if [ "$1" = "--voice" ]; then pip install -q -e ".[voice]"; else pip install -q -e "."; fi
  touch .venv/.installed
fi

if [ ! -f .env ] || ! grep -q '^ANTHROPIC_API_KEY=sk' .env 2>/dev/null; then
  echo
  echo "Vyron needs your Anthropic API key (get one at https://console.anthropic.com)."
  read -r -p "Paste it here and press Enter: " KEY
  [ -f .env ] || cp .env.example .env
  if grep -q '^ANTHROPIC_API_KEY=' .env; then
    sed -i.bak "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$KEY|" .env && rm -f .env.bak
  else
    echo "ANTHROPIC_API_KEY=$KEY" >> .env
  fi
  echo "Saved to .env (kept only on this computer)."
fi

if [ "$1" = "--voice" ]; then
  for VAR in DEEPGRAM_API_KEY ELEVENLABS_API_KEY; do
    if ! grep -q "^$VAR=.\+" .env 2>/dev/null; then
      echo
      case $VAR in
        DEEPGRAM_API_KEY) echo "Voice needs your Deepgram key (https://console.deepgram.com, API Keys).";;
        ELEVENLABS_API_KEY) echo "Voice needs your ElevenLabs key (https://elevenlabs.io, profile menu, API Keys).";;
      esac
      read -r -p "Paste it here and press Enter: " KEY
      if grep -q "^$VAR=" .env; then
        sed -i.bak "s|^$VAR=.*|$VAR=$KEY|" .env && rm -f .env.bak
      else
        echo "$VAR=$KEY" >> .env
      fi
    fi
  done
fi

exec python -m vyron "$@"
