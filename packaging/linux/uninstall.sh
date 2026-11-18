#!/usr/bin/env bash
set -euo pipefail

data_dir="$HOME/.local/share"
rm -rf -- "$data_dir/aider-deck"
rm -f -- "$data_dir/applications/aider-deck.desktop"
for size in 16 24 32 48 64 128 256; do
    rm -f -- "$data_dir/icons/hicolor/$size"x"$size/apps/aider-deck.png"
done
printf 'Removed Aider-Deck from %s\n' "$data_dir"
