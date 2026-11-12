# Hermes Agent + Yandex Messenger platform plugin.
#
#   docker compose build            # uses the pinned official image as the base
#   HERMES_IMAGE=nousresearch/hermes-agent:<tag> docker compose build   # try another version (or set it in .env)
#
# The default tag is the Hermes release this plugin was last tested against (see README, "Версия Hermes").
# Do not point it at `latest`: Hermes is updated often and the plugin uses its internal APIs.
#
# The base image is immutable and keeps all state in the /opt/data volume, so the plugin is baked into a
# read-only seed directory and installed into $HERMES_HOME on every container start (see
# docker/cont-init-yandex-messenger.sh). Rebuilding the image therefore upgrades the plugin without
# touching the data volume.
ARG HERMES_IMAGE=nousresearch/hermes-agent:v0.23.1
FROM ${HERMES_IMAGE}

USER root

# Seed: the plugin directory as Hermes expects it (plugin.yaml + package + vendored client).
COPY --chmod=a+rX,go-w plugins/platforms/yandex_messenger /opt/yandex-messenger-plugin/yandex_messenger

# Seed config (defaults baked in; docker-compose.yml mounts ./config over /opt/hermes-seed so edits need no rebuild).
COPY --chmod=a+rX,go-w config/ /opt/yandex-messenger-plugin/config/
COPY --chmod=a+rX,go-w docker/apply_config.py /opt/yandex-messenger-plugin/apply_config.py

# Runs after the base image's own cont-init scripts (01-hermes-setup, 015-..., 02-reconcile-profiles).
COPY --chmod=0755 docker/cont-init-yandex-messenger.sh /etc/cont-init.d/03-yandex-messenger

# A checkout on Windows may have CRLF endings, which would break the shebang of the init script.
RUN sed -i 's/\r$//' /etc/cont-init.d/03-yandex-messenger

# Fail the build early when the seed is incomplete (e.g. the vendored client was not synced).
RUN test -f /opt/yandex-messenger-plugin/yandex_messenger/plugin.yaml && \
    test -f /opt/yandex-messenger-plugin/yandex_messenger/_vendor/yandex_messenger_client/__init__.py

# Entrypoint, CMD, VOLUME and HERMES_HOME are inherited: `gateway run` starts the messaging gateway.
