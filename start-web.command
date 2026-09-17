#!/usr/bin/env bash
# Mac: double-click to serve Vyron to your phone/tablet on the same Wi-Fi (the address is printed).
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine . 2>/dev/null || true
bash ./start.sh --serve
echo; read -r -p "Vyron has stopped. Press Enter to close this window."
