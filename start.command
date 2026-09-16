#!/usr/bin/env bash
# Mac: double-click this file in Finder to start Vyron.
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine . 2>/dev/null || true
bash ./start.sh "$@"
echo; read -r -p "Vyron has stopped. Press Enter to close this window."
