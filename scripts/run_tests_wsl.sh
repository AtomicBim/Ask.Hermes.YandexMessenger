#!/usr/bin/env bash
# Run the plugin test-suite inside WSL with the interpreter of the installed Hermes (real gateway.* code).
#
#   wsl -d Ubuntu-24.04 -- bash /mnt/c/.../Ask.Hermes.YandexMessenger/scripts/run_tests_wsl.sh [pytest args]
#
# - HERMES_HOME is a throw-away directory: the real ~/.hermes is never touched.
# - pytest/pytest-asyncio are installed into ~/.cache/ym-plugin-test-deps (NOT into the Hermes venv).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
PY="${HERMES_PYTHON:-$(ls -d "$HOME"/.hermes/installs/*/environments/*/venv/bin/python 2>/dev/null | head -n1)}"
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo "Hermes venv python not found; set HERMES_PYTHON" >&2
  exit 2
fi
DEPS="$HOME/.cache/ym-plugin-test-deps"
if [[ ! -d "$DEPS/pytest" ]]; then
  UV="$(ls -d "$HOME"/.hermes/tools/uv-*/uv 2>/dev/null | head -n1 || true)"
  if [[ -n "$UV" ]]; then
    "$UV" pip install --quiet --target "$DEPS" --python "$PY" pytest pytest-asyncio
  else
    "$PY" -m pip install --quiet --target "$DEPS" pytest pytest-asyncio
  fi
fi

export HERMES_HOME
HERMES_HOME="$(mktemp -d)"
trap 'rm -rf "$HERMES_HOME"' EXIT
export HERMES_REPO
export PYTHONPATH="$DEPS:$HERMES_REPO${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
exec "$PY" -m pytest -p no:cacheprovider "$@"
