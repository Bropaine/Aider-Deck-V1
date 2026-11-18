#!/usr/bin/env bash
set -euo pipefail

if [[ $(uname -s) != Darwin ]]; then
    printf 'Build this disk image on macOS with hdiutil.\n' >&2
    exit 1
fi

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(cd -- "$script_dir/../.." && pwd)
dist="$repo_dir/dist"
mkdir -p -- "$dist"
staging=$(mktemp -d "$dist/.macos-stage.XXXXXXXX")
trap 'rm -rf -- "$staging"' EXIT

bundle="$staging/Aider Deck.app"
contents="$bundle/Contents"
app_dir="$contents/Resources/app"
mkdir -p -- "$contents/MacOS" "$app_dir"
cp -- "$script_dir/Info.plist" "$contents/Info.plist"
cp -- "$repo_dir/installer/aider-deck.icns" "$contents/Resources/AiderDeck.icns"
cp -- "$script_dir/aider-deck" "$contents/Resources/aider-deck.sh"
# Launch Services treats script-only app bundles as Intel apps on Apple silicon.
xcrun clang -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=12.0 \
    -o "$contents/MacOS/aider-deck" "$script_dir/launcher.c"
xcrun lipo -verify_arch arm64 x86_64 "$contents/MacOS/aider-deck"
tar --exclude=__pycache__ -C "$repo_dir" -cf - \
    app.py run.sh requirements.txt presets.json deck static templates prompts \
    | tar -C "$app_dir" -xf -

ln -s /Applications "$staging/Applications"
hdiutil create -volname "Aider-Deck" -srcfolder "$staging" -ov -format UDZO \
    "$dist/AiderDeck-0.1.0.dmg"
rm -rf -- "$dist/Aider Deck.app"
mv -- "$bundle" "$dist/Aider Deck.app"
printf 'Built %s and %s\n' "$dist/Aider Deck.app" "$dist/AiderDeck-0.1.0.dmg"
