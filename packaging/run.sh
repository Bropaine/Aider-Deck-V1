#!/bin/sh
set -eu

bundle=$(CDPATH='' cd "$(dirname "$0")" && pwd)
export PATH="$PATH:$HOME/.local/bin"
exec "$bundle/python/bin/python3" -E -s "$bundle/launch.py" "$@"
