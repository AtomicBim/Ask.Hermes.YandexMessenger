#!/usr/bin/env bash
# Dump the chats of ALL users of ALL running Hermes agents (containers hermes-*). Read-only.
#
#   scripts/monitor_agents.sh [days]
#
#   days   only sessions active within the last N days; without it: the whole history.
#
# Result: summary on screen + full conversations in monitor-out/<agent>/<session>.md (git-ignored, mode 700/600,
# replaced on every run). Secrets are masked by pattern, which is not a guarantee: treat the output as private data.
#
# How: `hermes sessions export` runs inside each container (it only reads state.db), the JSONL is copied out with
# `docker cp` and removed in the container; scripts/monitor_report.py builds the files on the host.
# Nothing is written into the data volume.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
OUT="${MONITOR_OUT:-$ROOT/monitor-out}"
DAYS="${1:-}"

case "$DAYS" in
  -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
  "") PERIOD=() ; LABEL="all time" ;;
  *[!0-9]*|0) echo "usage: $0 [days]   (days = positive integer; no argument = all time)" >&2; exit 2 ;;
  *) PERIOD=(--newer-than "${DAYS}d"); LABEL="last $DAYS day(s)" ;;
esac

CONTAINERS=()
while IFS= read -r name; do [ -n "$name" ] && CONTAINERS+=("$name"); done \
  < <(docker ps --format '{{.Names}}' --filter "name=hermes-" | sort)
[ ${#CONTAINERS[@]} -gt 0 ] || { echo "no running hermes-* containers" >&2; exit 1; }

umask 077
TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP"
  for c in "${CONTAINERS[@]}"; do docker exec "$c" rm -f /tmp/monitor-export.jsonl >/dev/null 2>&1 || true; done
}
trap cleanup EXIT

INPUTS=()
for c in "${CONTAINERS[@]}"; do
  agent="${c#hermes-}"
  out="$TMP/$agent.jsonl"
  if docker exec "$c" hermes sessions export --format jsonl --redact ${PERIOD[@]+"${PERIOD[@]}"} \
       /tmp/monitor-export.jsonl --yes >"$TMP/$agent.log" 2>&1 \
     && docker cp "$c:/tmp/monitor-export.jsonl" "$out" >/dev/null 2>&1; then
    INPUTS+=("$agent=$out")
  elif grep -qiE 'no sessions|0 sessions|nothing to export' "$TMP/$agent.log"; then
    : >"$out"; INPUTS+=("$agent=$out")      # nothing in the period: an empty agent, not an error
  else
    echo "warning: $c: export failed: $(grep -v 'out of sync' "$TMP/$agent.log" | tail -n 2 | tr '\n' ' ')" >&2
  fi
done

[ ${#INPUTS[@]} -gt 0 ] || { echo "nothing exported" >&2; exit 1; }

# Replace the previous result so stale chats do not pile up.
rm -rf "$OUT"
echo "period: $LABEL; agents: ${#INPUTS[@]}/${#CONTAINERS[@]}"
"$PY" "$ROOT/scripts/monitor_report.py" "${INPUTS[@]}" --md "$OUT" --max-chars 0
echo "chats: $OUT/<agent>/<session>.md"
