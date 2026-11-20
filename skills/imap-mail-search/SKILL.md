---
name: imap-mail-search
description: "Search/read a mailbox over IMAP (imaplib) to find senders."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Email, IMAP, Search, imaplib, Yandex]
    related_skills: [himalaya, email-inbox-triage]
---

# IMAP Mail Search

Find messages and identify senders in a mailbox over raw IMAP (`imaplib`, stdlib — no CLI to install). Use when the user asks "find emails from X", "who is X (sender)?", or "what did X write", and access is via IMAP credentials rather than a hosted connector.

Prefer `himalaya` (or `google-workspace`) when that CLI/connector is already set up; use this skill for a direct stdlib connection or when the CLI is absent.

## Procedure

1. **Resolve credentials.** IMAP host + port + login + password (or app password). Store them in `$HERMES_HOME/.secrets/yandex-mail.json` (`/opt/data/.secrets/yandex-mail.json` in the container; directory `chmod 700`, file `chmod 600`) as `{"login": ..., "app_password": ..., "host": "imap.yandex.ru", "port": 993}`. Keep exactly this file name: the chat bot checks for it to know whether Mail is connected. Never put the password in memory/inject-on-every-turn storage — record only the *path* there.
2. **Connect and select.** `imaplib.IMAP4_SSL(host, port)` → `login(...)` → `select(folder)`. Always `BODY.PEEK[...]` to avoid marking mail read.
3. **Locate messages.** Prefer a server-side `SEARCH`; fall back to a client-side header scan of all messages (chunked) when the server can't match the term.
4. **Identify the sender.** Dedupe distinct `From` values (name + address) before reading bodies — one person often has multiple From encodings.
5. **Extract who they are.** Read the body (`BODY.PEEK[]`) and parse the signature block for title / org / phone; full-name + domain is usually the fastest lead.

## Pitfalls

- **`imaplib.search()` ASCII-encodes criteria** and raises `UnicodeEncodeError` on any non-ASCII (Cyrillic) term. To search a non-ASCII string, send it as a UTF-8 literal: `M.literal = value.encode('utf-8')`, then `M._simple_command('SEARCH', b'CHARSET', b'UTF-8', field)`.
- **Server-side full-text search may silently not index non-Latin text.** A `SEARCH CHARSET UTF-8` for a Cyrillic term can return empty even though matches exist. Before trusting an empty result, verify the mechanism with a control search on a term that *must* match (e.g. a common Latin word). If Cyrillic returns empty while Latin works, search the Latin transliteration of the name/address instead, or scan headers client-side.
- **Large header scans drop the connection.** Servers close the socket after roughly ~1000 messages fetched in a loop. Fetch `From` (or `HEADER.FIELDS (...)`) in chunks of ~100–200, and on `socket error: EOF` reconnect, re-select, and resume from the last persisted offset — checkpoint progress to a file so a timeout doesn't lose the run.
- **MIME-encoded names use multiple charsets.** Yandex mail mixes `=?utf-8?B?...?=` and `=?koi8-r?B?...?=` in `From`; decode with `email.header.decode_header` + `make_header` and match case-insensitively on the decoded text, not the raw header.
- **One person ≠ one From value.** The same sender appears as `Name <addr>`, `Surname Firstname <addr>`, and in different charsets. Group by lowercased address when counting or identifying people.
- **Reading a whole mailbox is the slow path.** Server-side `SEARCH` on the sender's Latin address fragment (`FROM "gver"`) returns in one call; only fall back to the chunked scan when you must match Cyrillic display names the index won't see.
- **Search/fetch output is `bytes`, not `str`.** `M.search()` returns id lists of `bytes`, and `M.fetch` header values are bytes too — a `json.dump` checkpoint of raw ids dies mid-run with `TypeError: ... bytes ... is not JSON serializable`. Decode ids (`int(x)` / `x.decode()`) and header strings before persisting to the checkpoint file or counting them.
- **To find *everyone* sharing a name (relatives, colleagues), search all three address fields and union the ids.** `FROM`, `TO`, `CC` each catch a different slice — a person may appear only as an addressee. Parse each header with `email.utils.getaddresses`, decode display names, and group by lowercased address.
- **Automated vendor emails are often a portal link, not an attached PDF.** "Счёт №… сформирован/оплачен" / "УПД №…" from SaaS/VDS/merchant billing systems usually carry only a link into the personal cabinet — the invoice PDF is absent. To recover the document, search the `Sent`/`Отправленные` folder for a forwarded copy to whoever signs it (the forward usually includes the real attachment). Extract attachments by walking the MIME tree for `part.get_filename()` plus `part.get_payload(decode=True)`, and decode the filename with `decode_header` — it is often MIME-encoded (`=?UTF-8?B?...?=`).

## References

- `references/yandex-imap.md` — Yandex-specific access and quirks (app password, host, Cyrillic-search limitation, connection-drop threshold).
