#!/bin/sh
set -eu

CDPATH='' cd "$(dirname "$0")"
export PATH="$PATH:$HOME/.local/bin"
python=.deck-venv/bin/python
if [ ! -x "$python" ]; then
    python3 -m venv .deck-venv
fi

if ! "$python" -c 'import flask, flask_sock, ruamel.yaml, dotenv, pathspec, PIL.Image' >/dev/null 2>&1; then
    "$python" -m pip install -r requirements.txt
fi
exec "$python" app.py --open "$@"
