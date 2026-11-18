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

# 3. Apply the seed config (config/hermes.yaml + optional overlay config/hermes.local.yaml).
#    The compose file mounts ./config at /opt/hermes-seed; without the mount the copy baked into the image is used.
#    Applied on the first start and again only when the seed content changes (manual `config set` changes of
#    other keys survive restarts of an unchanged seed). HERMES_CONFIG_RESEED=1 forces a re-apply.
#    The model itself is the exception: step 4 re-applies it on every start.
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

# 3b. Shared skills from the repository (skills/ baked into the image). Each skill directory is replaced as a whole on
#     every start, so a rebuilt image upgrades it; skills of the user (other directory names) are never touched.
SKILLS_SRC=/opt/yandex-messenger-plugin/skills
SKILLS_DEST="$HERMES_HOME/skills"
if [ -d "$SKILLS_SRC" ]; then
  mkdir -p "$SKILLS_DEST"
  synced=0
  for skill in "$SKILLS_SRC"/*/; do
    [ -f "${skill}SKILL.md" ] || continue
    name="$(basename "$skill")"
    rm -rf "${SKILLS_DEST:?}/$name"
    cp -a "${skill%/}" "$SKILLS_DEST/$name"
    synced=$((synced + 1))
  done
  chown -R hermes:hermes "$SKILLS_DEST" 2>/dev/null || log "warning: could not chown $SKILLS_DEST"
  log "shared skills synced: $synced (into $SKILLS_DEST)"
fi

# 4. The model is fixed by the deployment and re-applied on EVERY start, so nothing chosen at runtime
#    (`hermes model`, `/config set`) survives a restart. The chat command /model is blocked by the plugin.
#    Source of truth: HERMES_MODEL / HERMES_PROVIDER from .env; when unset, model.default / model.provider
#    from the seed config (hermes.yaml + hermes.local.yaml).
seed_model_value() {  # $1 = "default" | "provider"; prints the seed value (the overlay wins) or nothing
  [ -n "${SEED_FILES:-}" ] || return 0
  # shellcheck disable=SC2086
  /opt/hermes/.venv/bin/python - "$1" $SEED_FILES <<'PY' 2>/dev/null || true
import sys
import yaml
key, value = sys.argv[1], None
for path in sys.argv[2:]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        continue
    model = data.get("model") if isinstance(data, dict) else None
    if isinstance(model, dict) and model.get(key):
        value = str(model[key])
if value:
    print(value)
PY
}

MODEL="${HERMES_MODEL:-}"
PROVIDER="${HERMES_PROVIDER:-}"
if [ -z "$MODEL" ]; then
  MODEL="$(seed_model_value default)"
  log "warning: HERMES_MODEL is not set in .env; using model.default from the seed config (${MODEL:-none})"
fi
if [ -z "$PROVIDER" ]; then
  PROVIDER="$(seed_model_value provider)"
fi
if [ -n "$MODEL" ]; then
  as_hermes "$HERMES_BIN" config set model.default "$MODEL" >/dev/null 2>&1 \
    && log "model.default = $MODEL" || log "warning: could not set model.default"
fi
if [ -n "$PROVIDER" ]; then
  as_hermes "$HERMES_BIN" config set model.provider "$PROVIDER" >/dev/null 2>&1 \
    && log "model.provider = $PROVIDER" || log "warning: could not set model.provider"
fi
