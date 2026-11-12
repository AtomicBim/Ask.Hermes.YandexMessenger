#!/usr/bin/env bash
# Run the plugin tests against the Hermes that ships in the built image (compatibility check for HERMES_IMAGE).
#
#   docker compose build && bash scripts/run_tests_docker.sh [pytest args]
#
# pytest/pytest-asyncio are pure-Python and mounted from ~/.cache/ym-plugin-test-deps (installed on demand);
# the sealed image venv is never modified and nothing is written to the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-ask-hermes-yandex:latest}"
DEPS="${YM_TEST_DEPS:-$HOME/.cache/ym-plugin-test-deps}"

if [[ ! -d "$DEPS/pytest" ]]; then
  # The image venv is sealed (no pip), so install the pure-Python test deps with the HOST's uv or pip.
  mkdir -p "$DEPS"
  UV="$(command -v uv || ls -d "$HOME"/.hermes/tools/uv-*/uv 2>/dev/null | head -n1 || true)"
  if [[ -n "$UV" ]]; then
    "$UV" pip install --quiet --target "$DEPS" pytest pytest-asyncio
  elif python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --quiet --target "$DEPS" pytest pytest-asyncio
  else
    echo "need uv or pip on the host to install pytest into $DEPS (or set YM_TEST_DEPS to a dir that has it)" >&2
    exit 2
  fi
fi

exec docker run --rm --entrypoint /opt/hermes/.venv/bin/python \
  -v "$ROOT":/work:ro -v "$DEPS":/deps:ro \
  -e PYTHONPATH=/deps:/opt/hermes -e HERMES_REPO=/opt/hermes -e HERMES_HOME=/tmp/hh \
  -e PYTHONDONTWRITEBYTECODE=1 -w /work \
  "$IMAGE" -m pytest -p no:cacheprovider "${@:--q}"
