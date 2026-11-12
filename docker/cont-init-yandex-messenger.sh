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

# 3. Apply the seed config (config/hermes.yaml + optional private config/hermes.local.yaml).
#    The compose file mounts ./config at /opt/hermes-seed; without the mount the copy baked into the image is used.
#    Applied on the first start and again only when the seed content changes (manual `hermes model` / `config set`
#    changes survive restarts of an unchanged seed). HERMES_CONFIG_RESEED=1 forces a re-apply.
SEED_DIR=/opt/hermes-seed
[ -f "$SEED_DIR/hermes.yaml" ] || SEED_DIR=/opt/yandex-messenger-plugin/config
SEED_FILES="$SEED_DIR/hermes.yaml"
[ -f "$SEED_DIR/hermes.local.yaml" ] && SEED_FILES="$SEED_FILES $SEED_DIR/hermes.local.yaml"
if [ -f "$SEED_DIR/hermes.yaml" ]; then
  MARKER="$HERMES_HOME/.ym-config-seed.sha256"
  # shellcheck disable=SC2086
  SEED_HASH="$(cat $SEED_FILES | sha256sum | cut -d' ' -f1)"
  if [ "${HERMES_CONFIG_RESEED:-0}" = "1" ] || [ "$(cat "$MARKER" 2>/dev/null)" != "$SEED_HASH" ]; then
    # shellcheck disable=SC2086
    if as_hermes /opt/hermes/.venv/bin/python /opt/yandex-messenger-plugin/apply_config.py $SEED_FILES; then
      printf '%s' "$SEED_HASH" > "$MARKER" && chown hermes:hermes "$MARKER" 2>/dev/null || true
      log "seed config applied from $SEED_DIR"
    else
      log "warning: seed config was not applied (see messages above); will retry on next start"
    fi
  else
    log "seed config unchanged since the last start; not re-applied"
  fi
else
  log "no seed config found; skipping"
fi

# 4. Optional model override on EVERY start (wins over the seed; otherwise configure with `hermes model`).
if [ -n "${HERMES_MODEL:-}" ]; then
  as_hermes "$HERMES_BIN" config set model.default "$HERMES_MODEL" >/dev/null 2>&1 \
    && log "model.default = $HERMES_MODEL" || log "warning: could not set model.default"
fi
if [ -n "${HERMES_PROVIDER:-}" ]; then
  as_hermes "$HERMES_BIN" config set model.provider "$HERMES_PROVIDER" >/dev/null 2>&1 \
    && log "model.provider = $HERMES_PROVIDER" || log "warning: could not set model.provider"
fi
