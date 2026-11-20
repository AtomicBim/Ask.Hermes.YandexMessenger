---
name: digital-twin
description: "Create an employee AI avatar from corporate data (mail, calendar, Disk, Telemost). Explicit request only."
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [digital-twin, avatar, persona, profiles, yandex, telemost, knowledge-transfer]
    related_skills: [imap-mail-search, yandex-calendar, yandex-disk, yandex-360-mcp, hermes-agent]
---

# Digital twin: employee avatar

An AI avatar of one employee: it writes the way that person writes and answers from that person's own work data (mail,
calendar, Disk, Telemost chats, Tracker). This skill runs on the dedicated "twin factory" instance and is loaded only
there. It is a guided pipeline, not a prompt: **Intake → Connect → Harvest → Build → Deploy → Teardown**.

## When to use

Only when the user explicitly asks for an avatar / twin / clone of an employee ("сделай аватара сотрудника",
"нужен цифровой двойник …", offboarding knowledge transfer, org memory). Never start it on your own, never from a vague
mention of a colleague. If the request is ambiguous, ask once what is wanted.

Once invoked, **do not stop after describing the plan**: run the steps below, report progress at each step boundary, and
ask the user only for what you truly cannot get yourself (answers in Step 0, credentials in Step 1).

## Working layout

All work files live in `$HERMES_HOME/twins/<slug>/` (`<slug>` = latin login of the subject):

```
manifest.json     # subject, requester, purpose, mode, authorization, sources + status, dates
.secrets/         # the SUBJECT's credentials only, chmod 700 / 600 (file names as in the connector skills)
corpus/<source>/  # harvested material, one file per item/thread, each with a provenance header
persona/          # voice.md, decisions.md, people.md, domains.md, rhythm.md  (Step 3)
eval/             # held-out questions and the graded answers (Step 3)
```

Never put a secret in `manifest.json`, the corpus, notes or chat: keep only paths.

## Step 0. Intake (one message, wait for all answers)

1. **Subject:** full name, work login/email.
2. **Requester:** is it the subject themself, their manager, or an admin?
3. **Purpose:** offboarding handover (person left or is leaving) / ongoing assistant for an active employee / other.
4. **Mode:** *live read-only* (accounts stay connected, the twin reads them on demand) or *frozen archive* (one-time
   export into `corpus/`, accounts can then be closed). Recommend frozen for a departing person.
5. **Authorization:** has HR/InfoSec (or the subject) approved ingesting this person's work mailbox/chats? Record who and when.
   No clear "yes" → stop; nothing is collected.
6. **Scope:** period to cover (default: last 24 months), folders/chats/projects to exclude, planned teardown date.

Write the answers to `manifest.json`. Personal (non-work) content found later is excluded, not "kept just in case".

## Step 1. Connect sources (hard gate)

Reuse the existing connector skills; do not reinvent access. Credentials must belong to the **subject** (issued by the
subject or by an org admin under company policy) and are stored in `$HERMES_HOME/twins/<slug>/.secrets/`. **Never use this
instance's own default `$HERMES_HOME/.secrets/`** for the harvest unless the requester is the subject: it holds the
requester's access, and a token only ever shows its owner's data.

| Source | Skill to follow | Credential file | Preflight (must succeed) | Role |
|---|---|---|---|---|
| Mail | `imap-mail-search` | `yandex-mail.json` | login, list folders incl. Sent | required |
| Calendar | `yandex-calendar` | `yandex-calendar.json` | list calendars | required |
| Disk | `yandex-disk` | `yandex-disk.json` | list the root | required |
| **Telemost** | `yandex-360-mcp` (`yandex-360-mcp/references/telemost-chats.md`) | `yandex-telemost-mcp.json` | `initialize`, `get_current_user` returns the subject, `list_chats` non-empty | **required, no skipping** |
| Tracker | `yandex-360-mcp` (`yandex-360-mcp/references/tracker-mcp.md`) | `yandex-tracker-mcp.json` | `get_current_user` returns the subject | optional |
| Address book | `yandex-calendar` (`yandex-calendar/references/carddav-contacts.md`) | separate Контакты app password | PROPFIND of the book | optional |

Rules of the gate:
- **Telemost is mandatory.** If its token is missing or fails the preflight, stop at this step, give the user the OAuth
  procedure from `yandex-360-mcp` ("Connect procedure") and wait. Do not proceed to Step 2 without it.
- Each app password is service-scoped (Mail ≠ Disk ≠ Calendar ≠ Contacts): ask for exactly the missing ones, by name.
- After every preflight, check **identity**: the account behind the credentials must be the subject. Mismatch → stop.
- Show the user a status table (source / ok / what is missing) and update `manifest.json`. Offer optional sources once, then move on.

## Step 2. Harvest (read-only)

Per-source recipes (what to take, how to page, what to skip) are in `references/data-sources.md`. Summary:

- **Mail:** Sent folder first (voice + decisions), then threads from INBOX for facts. `BODY.PEEK` only.
- **Calendar:** events for the period, recurring series, attendees, and every Telemost link found in an event.
- **Disk:** tree + documents authored by the subject.
- **Telemost:** all chats (full pagination), full history, forwarded messages, plus the meetings known from the calendar.
- **Tracker:** issues and comments of the subject.

Cross-link sources: a calendar event with a Telemost link ties a meeting to its chat messages, attendees to mail
correspondents, a Disk document to the thread that mentions it. Every corpus item carries a provenance header
(`source`, `id`, `date`, `participants`) so later answers can cite it.

Everywhere: redact secrets before writing to the corpus (tokens like `y0_…`/`AQAAA…`, passwords, one-time codes),
checkpoint long scans to disk, and report counts per source when done. Mail, chats and Disk files of other people
that the subject merely received are context, not "the subject's words": label them so.

## Step 3. Build the twin

1. **Persona layers** (written to `persona/`, from the subject's OWN messages, not from a role description):
   - `voice.md`: how they write, **separately for mail (formal register) and for chats (short, informal)**: greetings,
     length, punctuation, typical phrases, how they refuse/agree/escalate; 5–10 verbatim sample messages per register.
   - `decisions.md`: recurring decisions and the criteria they apply, with examples from the corpus.
   - `people.md`: top correspondents with role, topics and tone towards each (mail + calendar + chats combined).
   - `domains.md`: projects, queues, folders and topics they owned; who to ask about what.
   - `rhythm.md`: working pattern from the calendar (recurring meetings, deadlines, quiet periods).
2. **SOUL.md** for the twin: compact persona prompt from `persona/`, plus the rules below. The twin states in its first
   reply that it is an AI avatar built from the person's work data, and never claims to be the person.
3. **Fact memory:** index `corpus/` for search (SQLite FTS or a vector DB if available) and require citations in answers.
4. **Evaluation:** hold back ~10 real questions (what was decided, who handled X, how would they reply to Y). Answer them from the
   index, grade against the held-out facts, show the user the results and the gaps. Fix and re-run once; then hand over.

## Step 4. Deploy

Dedicated Hermes profile = isolated config/skills/memory/sessions; deleting it deletes the clone.

```
hermes profile create twin-<slug> --clone      # or --no-skills for a minimal profile
hermes -p twin-<slug> ...                      # target it
```

- `--clone` drops messaging bot tokens by design but copies config/`.env`/skills: review the new profile's `.env` and
  remove everything the twin does not need. The subject's credentials go only into the twin's own `.secrets/` (live mode).
- Give the twin its own Yandex Messenger bot: `references/separate-bot.md` (one bot token cannot serve two profiles).
- Skills for the twin: *frozen* → only the corpus search; *live* → the read-only connector skills it needs.
- Hand the requester a short report: sources and counts, gaps, mode, what the twin can and cannot do, teardown date.

## Step 5. Teardown

On the planned date or on request: stop the twin's gateway, `hermes profile delete twin-<slug>`, delete
`$HERMES_HOME/twins/<slug>/`, and ask the subject/admin to revoke the app passwords and OAuth tokens that were issued
(you cannot revoke them yourself). Record the teardown in the manifest before deleting it.

## Rules and pitfalls

- **Read-only.** The twin does not send mail, create events or meetings, post to chats, or write to Disk/Tracker "as" the
  person. Answering from the data is fine; acting in the person's name needs explicit written sign-off.
- **Identity check, always.** A token reflects whose account authorized it. A colleague's token returns the colleague's
  data, not the subject's; verify with `get_current_user` / the login before harvesting.
- **Bot chats are not the person.** In Telemost, `<Org> h-<login>` style chats are the subject's own agent bot: use them as
  context at most, never as persona samples (see `yandex-360-mcp/references/telemost-chats.md`).
- **Do not trust one pass.** Chat listing and IMAP scans under-count under load (empty pages, dropped sockets): page to the
  true end, retry, dedupe, and compare counts with a second pass before saying "collected everything".
- **No secrets and no non-work data in the corpus.** Redact on write; drop personal correspondence and medical/financial
  private content; log what was dropped by category, not by content.
- **One bot token per profile.** Never reuse the main bot's token for a twin.
- **Meeting recordings/transcripts are not exposed** by the Telemost MCP tool set; do not promise them. The footprint of a
  meeting is its calendar event plus the chat messages around it.
- Existing commercial and open-source options to evaluate before building in-house: `references/prior-art.md`.
