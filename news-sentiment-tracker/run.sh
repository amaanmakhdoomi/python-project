#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PATH="$HOME/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
  [ -d ".venv" ] || uv venv --python python3 .venv
  uv pip install --quiet --python .venv/bin/python -r requirements.txt
else
  if [ ! -d ".venv" ]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
