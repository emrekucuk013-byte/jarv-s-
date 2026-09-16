#!/usr/bin/env bash
# Mac: double-click this file in Finder to start Vyron.
cd "$(dirname "$0")"
./start.sh "$@"
echo; read -r -p "Vyron has stopped. Press Enter to close this window."
