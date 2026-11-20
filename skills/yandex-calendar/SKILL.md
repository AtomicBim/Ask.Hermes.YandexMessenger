---
name: yandex-calendar
description: "Operate Yandex Calendar via CalDAV: list, find, edit."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Yandex, CalDAV, calendar, events, schedule]
    related_skills: [yandex-disk, imap-mail-search]
---

# Yandex Calendar (CalDAV)

Operate a Yandex Calendar over CalDAV at `caldav.yandex.ru` with stdlib `urllib` + Basic auth. No connector exists and none is needed. App passwords are service-scoped: a Mail or Disk password returns `401` here.

## When to Use

- "Connect to my calendar", list calendars, or show upcoming events.
- Find an event by title (or partial Cyrillic text) across calendars.
- Create an event, or move/reschedule an existing one.
- Create an event with attendees, or find a colleague's email in the address book to invite them (see `references/carddav-contacts.md`).

## Procedure

1. **Credentials.** CalDAV login (the Yandex account) + an app password issued for **Календарь / CalDAV**. Store it in `$HERMES_HOME/.secrets/yandex-calendar.json` (`/opt/data/.secrets/yandex-calendar.json` in the container; directory `chmod 700`, file `chmod 600`) as `{"login": ..., "app_password": ..., "caldav_url": "https://caldav.yandex.ru"}`. Keep exactly this file name: the chat bot checks for it to know whether Calendar is connected. Record only the path in memory.
2. **Discover the calendar home** (two hops):
   - `PROPFIND Depth:0` on `https://caldav.yandex.ru/` → read `current-user-principal` (e.g. `/principals/users/<login>/`).
   - `PROPFIND Depth:1` on that principal URL → read `C:calendar-home-set` (e.g. `/calendars/<login>/`).
3. **List calendars.** `PROPFIND Depth:1` on the calendar home. Each `d:response` whose `resourcetype` contains `C:calendar` is one calendar — read `displayname` (and `calendar-color` if present).
4. **Fetch / search events.** A `GET` on a calendar collection returns a plain-text LIST of per-event `.ics` hrefs (one per line), NOT the events. Read them with one `REPORT` `C:calendar-multiget` carrying every href (single response, fast + robust), or `GET` each `.ics` individually.
5. **Create.** `PUT` a `VCALENDAR`+`VEVENT` to `<calendar-home>/<slug>/<uuid>.ics` (fresh UUID filename). `201 Created` = success. Times as `TZID=Asia/Yekaterinburg` or UTC (`…Z`) — both display in the account's local time.
6. **Invite attendees.** Put `ORGANIZER;CN=…:mailto:<you>` and one `ATTENDEE;CN=…;RSVP=TRUE:mailto:<guest>` per invitee inside the `VEVENT` before `PUT`. Yandex stores each attendee as `PARTSTAT=NEEDS-ACTION` and sends the invitation email automatically. Find a colleague's address by name via the address book (`references/carddav-contacts.md`).
7. **Move / edit.** `GET` the event `.ics`, change `DTSTART`/`DTEND` (keep the original duration on a "move"), bump `SEQUENCE` by 1, `PUT` back to the same URL. Note: the stored resource name may differ from the one you PUT (see Pitfalls) — GET the href from the collection listing, not the URL you created.

## Snippets

```python
import json, base64, urllib.request, urllib.parse, xml.etree.ElementTree as ET
cfg = json.load(open(PATH_TO_600_FILE))   # {login, app_password, caldav_url}
auth = base64.b64encode(f"{cfg['login']}:{cfg['app_password']}".encode()).decode()
BASE = cfg['caldav_url'].rstrip('/')

def propfind(url, depth='1'):
    req = urllib.request.Request(url, method='PROPFIND',
        headers={'Depth': depth, 'Authorization': 'Basic '+auth})
    return urllib.request.urlopen(req, timeout=60).read().decode('utf-8','replace')
```

Multiget REPORT body (fetch N events in one call) — `POST` (method `REPORT`) to the calendar URL, then read `<C:calendar-data>` text in each `d:response`:

```xml
<?xml version="1.0"?>
<C:calendar-multiget xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><D:getetag/><C:calendar-data/></D:prop>
  <D:href>/calendars/&lt;login&gt;/&lt;slug&gt;/&lt;event&gt;.ics</D:href> …
</C:calendar-multiget>
```

## Pitfalls

- **App passwords are service-scoped.** Mail and Disk passwords return `401` against CalDAV; the user must create one selecting "Календарь".
- **A bare `^DTSTART` regex matches the VTIMEZONE first.** Yandex events carry a large `VTIMEZONE` block whose `DTSTART:1916…` sentinel lines precede the event's own `DTSTART;TZID=…`. Scope extraction to `BEGIN:VEVENT…END:VEVENT` (or match `DTSTART;TZID=`), or you'll read a 1916/1919 sentinel as the event time.
- **The collection GET returns hrefs, not events.** The `.ics` filenames are opaque UIDs, so search inside the fetched event data (`calendar-multiget`), never grep the href listing for a title.
- **Escape DESCRIPTION text once, not twice.** `\,` and `\;` are the escapes. Building the field via `re.sub` with a raw-string replacement (`r'…\\,'`) silently inserts two backslashes and stores `\\,` — assemble the value as a plain string and let Yandex (re)escape the comma on save.
- **Force IPv4 on intermittent `Network is unreachable`.** Yandex endpoints resolve to IPv4 and IPv6; if `urllib` intermittently raises `OSError [Errno 101]`, fall back to `curl -4 …` (reliable for GET/PUT/PROPFIND) or resolve `AF_INET` first and connect to that address. When monkeypatching `socket.getaddrinfo` to force AF_INET, capture the real original first and patch only once per kernel — in a persistent `execute_code` session a later cell re-captures the already-patched function and recurses to `RecursionError`.
- **Yandex renames the resource after the VEVENT `UID`, not the PUT filename.** A VEVENT whose `UID:<uuid>@hermes` is PUT to `<uuid>.ics` is stored as `<uuid>%40hermes.ics` (`@` URL-encoded): the PUT returns `201`, but a direct GET on the URL you PUT to then 404s. Confirm a create by `GET`-listing the collection and grep'ing for your UID, then GET that href — never assume the PUT URL is the stored URL.
- **Contacts live on CardDAV, behind their own app password.** The principal's `addressbook-home-set` (e.g. `/addressbook/<login>/`) lists the user's address books (Yandex 360 exposes a "Personal" book and a corporate one). A **Календарь**-scoped password cannot read the cards, so to look up an invitee's email the user must issue a separate **Контакты / CardDAV** app password. CardDAV runs on `carddav.yandex.ru` (not `caldav`), and even with the right password `REPORT addressbook-query`/`addressbook-multiget` return `403 <supported-report/>` — only PROPFIND listing + per-card GET work. See `references/carddav-contacts.md` for the full lookup procedure.

## References

- `references/carddav-contacts.md` — look up a colleague's email in the Yandex address book (CardDAV) to invite them to an event.
