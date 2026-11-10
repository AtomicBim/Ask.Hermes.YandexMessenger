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
  mkdir -p "$DEPS"
  docker run --rm --user "$(id -u):$(id -g)" -v "$DEPS":/deps --entrypoint /usr/bin/env "$IMAGE" \
    sh -c 'python3 -m pip install --quiet --target /deps pytest pytest-asyncio 2>&1 | tail -3' || {
      echo "could not install pytest into $DEPS; install it there manually (pip install --target)" >&2; exit 2; }
fi

exec docker run --rm --entrypoint /opt/hermes/.venv/bin/python \
  -v "$ROOT":/work:ro -v "$DEPS":/deps:ro \
  -e PYTHONPATH=/deps:/opt/hermes -e HERMES_REPO=/opt/hermes -e HERMES_HOME=/tmp/hh \
  -e PYTHONDONTWRITEBYTECODE=1 -w /work \
  "$IMAGE" -m pytest -p no:cacheprovider "${@:--q}"
