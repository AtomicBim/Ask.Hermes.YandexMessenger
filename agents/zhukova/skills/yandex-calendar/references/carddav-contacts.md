# Yandex Address Book (CardDAV)

Look up a colleague's email to invite them to a calendar event. Separate service from CalDAV: host `carddav.yandex.ru`, and a DIFFERENT app password scoped to **Контакты / CardDAV** (a Календарь password is refused on card reads).

## Procedure

1. **Credentials.** Same Yandex login + an app password issued for **Контакты**. Store in a `chmod 600` file.
2. **Discover the address-book home.** `PROPFIND Depth:1` on `https://carddav.yandex.ru/principals/users/<login>/` → read `addressbook-home-set` (e.g. `/addressbook/<login>/`).
3. **List address books.** `PROPFIND Depth:1` on that home. Each `d:response` whose `resourcetype` contains `E:addressbook` (namespace `urn:ietf:params:xml:ns:carddav`) is one book; read `displayname` (e.g. "Personal", and a corporate book like "Атом").
4. **List cards.** `PROPFIND Depth:1` on a book → every `d:response` href except the collection itself is one card (`YAAB-…`). A corporate book can hold thousands of cards.
5. **Fetch + search.** `GET` each card href and grep the vCard text for the name; parse `FN:` and `EMAIL;TYPE=pref:`. Parallelize with a thread pool (20–25 workers) — serial GET over thousands of cards is minutes.

## Pitfalls

- **REPORT is unsupported on CardDAV.** Both `addressbook-query` and `addressbook-multiget` return `403` with `<supported-report/>` in the error body, even though the principal advertises them. Only `PROPFIND` listing and per-card `GET` work — do not waste time on REPORT.
- **App passwords are service-scoped here too.** The Календарь password `403`s on card reads and on the REPORT bodies; issue a separate Контакты password.
- **Force IPv4** as on CalDAV (see SKILL.md) — `carddav.yandex.ru` shows the same intermittent IPv6 `Network is unreachable`.
