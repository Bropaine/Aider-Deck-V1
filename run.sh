#!/bin/sh
set -eu

cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
. .venv/bin/activate

if ! python -c 'import flask, flask_sock, ruamel.yaml, dotenv, pathspec, PIL.Image' >/dev/null 2>&1; then
    python -m pip install -r requirements.txt
fi
if ! python -c 'import pytest' >/dev/null 2>&1; then
    python -m pip install pytest
fi
if [ ! -x .venv/bin/aider ]; then
    python -m pip install aider-chat
fi

exec python app.py --open "$@"
