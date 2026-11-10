#!/command/with-contenv sh
# s6 cont-init step: install + enable the bundled Yandex Messenger plugin, optionally seed the model.
# Idempotent; runs on every container start as root (the base image's stage2 hook already prepared $HERMES_HOME).
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
SEED=/opt/yandex-messenger-plugin/yandex_messenger
DEST="$HERMES_HOME/plugins/platforms/yandex_messenger"
HERMES_BIN=/opt/hermes/.venv/bin/hermes
KEY="platforms/yandex_messenger"

log() { echo "[yandex-messenger] $*"; }

# 1. Refresh the plugin from the image seed (a rebuilt image upgrades the plugin; user edits are not kept).
mkdir -p "$HERMES_HOME/plugins/platforms"
rm -rf "$DEST"
cp -a "$SEED" "$DEST"
chown -R hermes:hermes "$HERMES_HOME/plugins" 2>/dev/null || log "warning: could not chown $HERMES_HOME/plugins"
log "plugin installed to $DEST"

# Run hermes CLI as the unprivileged runtime user (files under $HERMES_HOME must stay hermes-owned).
as_hermes() { s6-setuidgid hermes env HERMES_HOME="$HERMES_HOME" "$@"; }

# 2. Enable (user plugins are opt-in; the command is idempotent).
if as_hermes "$HERMES_BIN" plugins enable "$KEY" >/dev/null 2>&1; then
  log "plugin enabled ($KEY)"
else
  log "warning: 'hermes plugins enable $KEY' failed; run it manually inside the container"
fi

# 3. Optional first-boot model selection (otherwise configure with `docker compose exec hermes hermes model`).
if [ -n "${HERMES_MODEL:-}" ]; then
  as_hermes "$HERMES_BIN" config set model.default "$HERMES_MODEL" >/dev/null 2>&1 \
    && log "model.default = $HERMES_MODEL" || log "warning: could not set model.default"
fi
if [ -n "${HERMES_PROVIDER:-}" ]; then
  as_hermes "$HERMES_BIN" config set model.provider "$HERMES_PROVIDER" >/dev/null 2>&1 \
    && log "model.provider = $HERMES_PROVIDER" || log "warning: could not set model.provider"
fi
