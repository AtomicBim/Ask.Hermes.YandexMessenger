---
name: digital-twin
description: "Build an AI twin of a departed employee (offboarding clone)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [digital-twin, offboarding, persona, profiles, yandex, clone, knowledge-transfer]
    related_skills: [yandex-disk, yandex-calendar, imap-mail-search, yandex-360-mcp, hermes-agent]
---

# Digital twin / offboarding knowledge clone

Stand up an AI "avatar" of a (departed) employee that talks like them and answers questions from their historical data — mail, Yandex Disk, Calendar, Tracker — for a bounded transition period, then delete the whole instance cleanly.

## When to use

- "Сделай аватара/клона сотрудника", "сотрудница уволилась, надо отвечать от её имени и поднять её дела", offboarding knowledge transfer, org memory preservation.

## Prior art (buy vs build)

See `references/prior-art.md` — shipped products and open-source skills that already do variants of this, for the user to evaluate before building in-house.

## Hermes-native recipe

The clean vessel is a dedicated Hermes **profile** (isolated config/skills/memory/sessions), not a throwaway prompt — deleting the profile deletes the whole clone.

1. **Create** `hermes profile create <name> --clone` — copies config/`.env`/SOUL/skills from the active profile but deliberately drops messaging bot tokens (see the collision rule in `references/separate-bot.md`). `--clone-all` = full copy; `--no-skills` = empty profile.
2. **Target it** with `hermes -p <name> ...` (or `hermes profile use <name>`); list with `hermes profile list`.
3. **Persona**: SOUL.md + memory seeded from the person's OWN writing (their sent mail, authored docs). Capture voice/style — how they write and decide — plus a factual corpus, not just a role description.
4. **Read-only data access** — reuse the existing skills under that profile:
   - mail archive → `imap-mail-search` (IMAP read-only)
   - Yandex Disk → `yandex-disk` (WebDAV)
   - Calendar → `yandex-calendar` (CalDAV)
   - Tracker → `yandex-360-mcp` + its `references/tracker-mcp.md`
   Put the person's app-passwords in THAT profile's `.env`.
5. **Separate bot** so the clone does not live in the main agent's chat — `references/separate-bot.md`.
6. **Teardown**: `hermes profile delete <name>` removes the whole instance.

## Two data-access modes (let the user choose)

- **Live read-only** — keep their accounts alive; the clone reads mail/Disk/Calendar directly. Fast, but accounts stay provisioned.
- **Frozen archive** — one-time export of everything into a static corpus (markdown/vector DB); the clone reads the corpus and the accounts can be disabled immediately. Closer to a "portfolio".

## Rules / pitfalls

- **One Yandex Messenger bot token cannot serve two profiles** (collision) — that is exactly why `profile create --clone` drops tokens. Never reuse the main bot's token for a clone; mint a fresh bot.
- **Keep the clone read-only on mail.** Answering from the person's data is fine; *sending* mail "as them" is the risky part — a departed (or any real) person's identity should not send without explicit sign-off.
- **Confirm data ownership once** before ingesting a mailbox: work data on employer-owned accounts usually resolves, but confirm HR/InfoSec sign-off and that no personal (non-work) data is swept in.
