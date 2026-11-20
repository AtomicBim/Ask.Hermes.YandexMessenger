# Harvest recipes per source

Read-only everywhere. Each section says what to take, how to page, what to skip. Access code comes from the connector
skills (their snippets read the credentials path from a variable: point it at
`$HERMES_HOME/twins/<slug>/.secrets/<file>`). Corpus item header (markdown front matter or first line):

```
source: mail|calendar|disk|telemost|tracker   id: <message-id | uid | path | chat_id:message_id | issue key>
date: <ISO>   participants: [..]   kind: authored|received|context
```

`authored` = written by the subject (voice samples + decisions), `received` = written by others to the subject,
`context` = everything else (event, listing, bot chat).

## Mail (`imap-mail-search`)

1. List folders with `M.list()`; select `Sent` and `INBOX` read-only; always `BODY.PEEK`.
2. **Sent first.** Take every message in the period: subject, recipients, date, plain-text body. This is the voice and
   decision corpus. Strip quoted replies (`>` lines, "On … wrote", "От: … Отправлено:" blocks) and signatures before
   using a message as a style sample; keep the full text in the corpus.
3. **INBOX for facts.** Take threads where the subject answered, plus mail from top correspondents (rank by address,
   lowercased, from `From`/`To`/`Cc` union). Skip newsletters, notifications, automatic mail (mark them `context` or drop).
4. Page in chunks (20-50 for wide headers), checkpoint ids to disk, reconnect on `EOF`; decode ids/headers from `bytes`.
5. `html.unescape` bodies; dedupe by `Message-ID` (self-addressed mail sits in both folders).
6. Attachments: record name, size, type; download only office/PDF files that a kept thread explicitly discusses, and OCR
   scans only on request.

## Calendar (`yandex-calendar`)

1. List calendars; read events through `calendar-multiget`, scoping extraction to `BEGIN:VEVENT…END:VEVENT`.
2. For the period collect: title, start/end, organizer, attendees (names/emails), location, description, recurrence rule.
3. **Telemost links:** extract any `telemost.yandex.*` URL from `LOCATION`/`DESCRIPTION`/`URL`; keep them in the item,
   they connect the event to the Telemost data below.
4. Derive `persona/rhythm.md` input: recurring series, typical meeting load per weekday, who meets whom how often.
5. Address book (optional, separate Контакты password): resolve attendee emails to names/roles for `people.md`.

## Disk (`yandex-disk`)

1. Walk the tree with `Depth: 1` PROPFIND (skip the self entry; sleep ~1 s between requests, `429` means slow down).
   Save the tree listing as one `context` item.
2. Pick documents the subject authored or owned (their personal/project folders): text, docx, xlsx, pdf, md.
   Convert to text, keep the path as `id`. Skip media, archives, installers, files above ~50 MB.
3. Skip folders of other people that merely sit in a shared tree unless a kept thread references them.

## Telemost (`yandex-360-mcp`, see `yandex-360-mcp/references/telemost-chats.md`) — mandatory

1. `initialize` + `notifications/initialized`, then `tools/list`; read each `inputSchema` before calling.
2. `get_current_user` must be the subject; save `user.id`.
3. **Enumerate every chat** with `list_chats`: first call without arguments, then `{"cursor": next_cursor}`. Do **not**
   stop on an empty `items` page; retry the page 3-6 times, keep walking, dedupe by `chat_id`, stop only at a null
   `next_cursor`. Run the enumeration twice and compare counts; take the union.
4. **Classify each chat:** private person / private bot (`is_bot: true`, or `<Org> h-<login>`) / group / channel
   (via `search_users` + `get_chat` + `list_chat_members`). Bot chats become `context`; never persona samples.
5. **Read full history** per chat with `list_chat_messages {"chat_id", "limit": 100}` and `cursor` from `next_cursor`
   (newest-first; do not pass other arguments with a cursor). Keep `author`, `is_bot`, `created_at`, `text`,
   `attachments`, and **`forwarded_messages`** (the substance of thin captions lives there). Thread replies via
   `list_thread_messages`.
6. Messages by the subject are `authored` chat-register samples; others' messages are `received`. Keep the
   conversation order so replies stay attached to what they answered.
7. `search_messages` for targeted gaps (a project name, a person, a decision keyword) after the full pass.
8. **Meetings:** from the calendar Telemost links, record each meeting (title, time, attendees) as `context`, and attach
   the chat messages of the same day/participants. Recordings and transcripts are not available through the tool set:
   say so in the final report rather than guessing meeting content.
9. **Redact before saving:** chats routinely hold tokens (`y0__…`), passwords, Wi-Fi codes.

## Tracker (`yandex-360-mcp`, see `yandex-360-mcp/references/tracker-mcp.md`) — optional

1. `get_current_user` must be the subject; the org header (`X-Org-ID` / `X-Cloud-Org-ID`) is required.
2. `search_issues` with `assignee:"me"` (all resolutions for the period) and issues the subject created; for each
   `get_issue` + comments authored by the subject. Restricted/unavailable rows: keep only key + availability.
3. When `total` is null, page until `hasMore` is false and do not state a count.

## Completeness report (end of Step 2)

Per source: items collected, period covered, items dropped by reason (secret redaction, non-work, automatic mail, too large),
known gaps (e.g. un-CC'd mail of colleagues is invisible, no meeting recordings, chats the token could not list).
