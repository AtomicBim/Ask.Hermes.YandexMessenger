#!/usr/bin/env bash
# Export the user-made part of a running agent (skills + MCP servers, no secrets, no memory) into agents/<user>/.
#
#   git checkout <user>                       # run on your own branch
#   scripts/export_agent.sh <user> [container=hermes-yandex]
#   git status && git add agents/<user> && git commit && git push     # review the diff first
#
# What is exported: skills created/changed by the user (those shipped with Hermes are skipped),
# mcp_servers from config.yaml with secret values replaced by ${VAR}, and env.required (names of those variables).
# NOT exported: memories/, sessions/, state*, .env, auth*.json, logs.
set -euo pipefail

USER_NAME="${1:?usage: $0 <user> [container]}"
CONTAINER="${2:-hermes-yandex}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/data" "$TMP/bundled"

# config.yaml may hold secrets: it is only read here and never copied into the repository.
docker cp "$CONTAINER:/opt/data/config.yaml" "$TMP/data/config.yaml" 2>/dev/null || echo "note: no config.yaml in the container"
docker cp "$CONTAINER:/opt/data/skills" "$TMP/data/skills" 2>/dev/null || echo "note: no skills directory in the container"

# Skills that ship with Hermes (to skip them). The location differs between versions; the first one that exists wins.
for candidate in /opt/hermes/skills /opt/hermes/hermes-agent/skills /opt/hermes/optional-skills; do
  if docker exec "$CONTAINER" test -d "$candidate" 2>/dev/null; then
    docker cp "$CONTAINER:$candidate/." "$TMP/bundled/" && echo "bundled skills: $candidate"
    break
  fi
done
[ -n "$(ls -A "$TMP/bundled" 2>/dev/null)" ] || echo "warning: bundled Hermes skills not found; ALL skills will be exported - review them" >&2

cd "$ROOT"
"$PY" scripts/export_agent.py "$USER_NAME" --data "$TMP/data" --bundled "$TMP/bundled" --out agents
echo "Review 'git diff' / 'git status agents/$USER_NAME' before committing."
