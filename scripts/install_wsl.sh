#!/usr/bin/env bash
# Install the plugin into a Hermes home (default ~/.hermes) and enable it.
#
#   wsl -d Ubuntu-24.04 -- bash /mnt/c/.../Ask.Hermes.YandexMessenger/scripts/install_wsl.sh [--link] [--no-enable]
#
#   --link        symlink instead of copying (edits take effect after a gateway restart)
#   --no-enable   copy only; do not touch plugins.enabled
#
# Env: HERMES_HOME (default ~/.hermes), HERMES_BIN (default `hermes` on PATH).
# The bot token is NOT written anywhere: put YANDEX_MESSENGER_BOT_TOKEN into $HERMES_HOME/.env yourself.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/plugins/platforms/yandex_messenger"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST_PARENT="$HOME_DIR/plugins/platforms"
DEST="$DEST_PARENT/yandex_messenger"
KEY="platforms/yandex_messenger"
LINK=0
ENABLE=1
for arg in "$@"; do
  case "$arg" in
    --link) LINK=1 ;;
    --no-enable) ENABLE=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$SRC/_vendor/yandex_messenger_client/__init__.py" ]]; then
  echo "vendored client missing: run 'python scripts/sync_client.py' first" >&2
  exit 1
fi

mkdir -p "$DEST_PARENT"
rm -rf "$DEST"
if [[ "$LINK" == 1 ]]; then
  ln -s "$SRC" "$DEST"
  echo "linked  $DEST -> $SRC"
else
  cp -r "$SRC" "$DEST"
  find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} +
  echo "copied  $SRC -> $DEST"
fi

if [[ "$ENABLE" == 1 ]]; then
  HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"
  if [[ -z "$HERMES_BIN" ]]; then
    echo "hermes not found on PATH; enable manually: hermes plugins enable $KEY" >&2
  else
    HERMES_HOME="$HOME_DIR" "$HERMES_BIN" plugins enable "$KEY" || \
      echo "could not enable automatically; run: hermes plugins enable $KEY" >&2
  fi
fi

cat <<EOF

Next steps:
  1. echo 'YANDEX_MESSENGER_BOT_TOKEN=<token>'        >> $HOME_DIR/.env
     echo 'YANDEX_MESSENGER_ALLOWED_USERS=<login,..>' >> $HOME_DIR/.env
  2. hermes gateway restart
  3. hermes gateway status    # Yandex Messenger should be listed as connected
EOF
