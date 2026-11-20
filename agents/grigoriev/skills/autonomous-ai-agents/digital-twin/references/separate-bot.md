# Putting a second Hermes profile on its own Yandex Messenger bot

This deployment runs the custom `yandex-messenger-platform` plugin. Bot identity is read from gateway environment variables — `YANDEX_MESSENGER_BOT_TOKEN` (required) plus `YANDEX_MESSENGER_ALLOWED_USERS`, `YANDEX_MESSENGER_ADMIN_USERS`, `YANDEX_MESSENGER_HOME_CHANNEL`. See the plugin's `gateway_env.py` (`ENV_OVERRIDES`): those vars are read from the GATEWAY PROCESS environ (injected host-side at container launch), not from `config.yaml` or `/opt/data/.env`.

## Recipe: a second agent on its own bot

1. Mint a new Yandex 360 bot at `admin.yandex.ru/bot-platform` → its OAuth token (same "Атом h-*" one-bot-per-agent pattern the org already uses).
2. Give it a home either by:
   - **Per-profile gateway** (simplest for isolation + clean teardown): run a second gateway process whose environ carries the new bot's `YANDEX_MESSENGER_*` for that profile; or
   - **Multiplexed gateway**, where each profile's `.env` is its own secret scope and holds its own `YANDEX_MESSENGER_*`.

Per-profile gateway is preferable for a throwaway clone: kill the process + `hermes profile delete <name>` and the whole thing is gone.

## Collision rule

Two profiles holding the same bot token collide (the multiplexer refuses it). This is why `hermes profile create --clone` leaves messaging bot tokens and allowlists behind, and why `--clone-all` feedback never copies a live token. Never point a second profile at the main bot's token.

## Profile CLI (validated)

```
hermes profile create <name> --clone   # inherit config/skills, no bot token
hermes profile create <name> --no-skills   # empty profile, no bundled skills
hermes profile list                     # show profiles + their gateway state
hermes profile use <name>              # set sticky default
hermes -p <name> <command>            # one-off against a profile
hermes profile delete <name>          # teardown
hermes profile export / import        # move a profile between machines (no credentials by design)
```
