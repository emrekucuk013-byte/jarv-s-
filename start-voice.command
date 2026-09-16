#!/usr/bin/env bash
# Mac: double-click to start Vyron in push-to-talk voice mode (hold right Ctrl, speak, release).
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine . 2>/dev/null || true
bash ./start.sh --voice
echo; read -r -p "Vyron has stopped. Press Enter to close this window."
