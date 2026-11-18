#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(cd -- "$script_dir/../.." && pwd)
data_dir="$HOME/.local/share"
app_dir="$data_dir/aider-deck"
desktop_file="$data_dir/applications/aider-deck.desktop"
theme_dir="$data_dir/icons/hicolor"

mkdir -p -- "$app_dir" "$data_dir/applications"
tar --exclude=__pycache__ -C "$repo_dir" -cf - \
    app.py run.sh requirements.txt presets.json deck static templates prompts/.gitkeep \
    | tar -C "$app_dir" -xf -
chmod +x -- "$app_dir/run.sh"

for size in 16 24 32 48 64 128 256; do
    icon_dir="$theme_dir/$size"x"$size/apps"
    mkdir -p -- "$icon_dir"
    install -m 644 -- "$repo_dir/installer/icons/aider-deck-$size.png" "$icon_dir/aider-deck.png"
done

# Exec has a quoting layer in addition to desktop-entry string escapes.
exec_path=$(printf '%s' "$app_dir/run.sh" \
    | sed -e 's/\\/\\\\\\\\/g' -e 's/["$`]/\\\\&/g' -e 's/%/%%/g')
while IFS= read -r line; do
    if [[ $line == 'Exec=@EXEC@' ]]; then
        printf 'Exec="%s"\n' "$exec_path"
    else
        printf '%s\n' "$line"
    fi
done < "$script_dir/aider-deck.desktop" > "$desktop_file"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data_dir/applications" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache "$theme_dir" || true
fi

printf 'Installed Aider-Deck to %s\nLauncher: %s\nIcons: %s\n' "$app_dir" "$desktop_file" "$theme_dir"
printf 'First launch needs Python 3.11 or newer (python3 with venv support) on PATH.\n'
printf 'Install Git and Aider separately for coding sessions.\n'
