---
name: yandex-disk
description: "Operate Yandex Disk via WebDAV: list, download, upload."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Yandex, WebDAV, cloud, disk, files]
    related_skills: [imap-mail-search]
---

# Yandex Disk (WebDAV)

Connect to and operate a Yandex Disk account over the WebDAV protocol (stdlib `urllib`, Basic auth — no connector exists for Yandex Disk, and none is needed).

## When to Use

- The user asks to "connect to my Yandex Disk" or hands you a Disk app password.
- Reading files the user has parked on Disk (e.g. a shared "для бота" folder), or writing results back.
- Any WebDAV operation against `webdav.yandex.ru` (list / download / upload / delete).

## Procedure

1. **Resolve credentials.** WebDAV login (the Yandex account, e.g. `user@domain`) + an app password issued for **Disk / WebDAV** — a Mail app password will not work (see Pitfalls). Store it in `$HERMES_HOME/.secrets/yandex-disk.json` (`/opt/data/.secrets/yandex-disk.json` in the container; create the directory with `chmod 700`, the file with `chmod 600`) as `{"login": ..., "app_password": ..., "webdav_url": "https://webdav.yandex.ru"}`. Keep exactly this file name: the chat bot checks for it to know whether Disk is connected. Record only the path in memory, never the secret.
2. **Endpoint.** `https://webdav.yandex.ru/` — same for every account and folder; paths are appended below this root.
3. **List a folder** with a `PROPFIND` request at `Depth: 1` and parse the `DAV:` multistatus. The `href` is percent-encoded; the human name (including Cyrillic) is in `displayname`.
4. **Download** with `GET`, **upload** with `PUT`, both on the URL-encoded path.
5. **Other verbs.** `MKCOL` creates a folder, `DELETE` removes, and `MOVE` renames/moves: `Destination: <BASE><encoded-dest>` header on a request to the encoded source (expect `201`/`204`). To move a whole folder tree, MOVE the folder itself — no recursive copy needed.

## Reusable snippet (stdlib only)

```python
import json, base64, urllib.request, urllib.parse, xml.etree.ElementTree as ET
cfg = json.load(open(PATH_TO_600_FILE))          # {login, app_password, webdav_url}
auth = base64.b64encode(f"{cfg['login']}:{cfg['app_password']}".encode()).decode()
BASE = cfg['webdav_url'].rstrip('/')

# LIST a folder (depth 1)
def list_dir(path):
    url = BASE + '/' + urllib.parse.quote(path.strip('/')) + '/'
    req = urllib.request.Request(url, method='PROPFIND',
        headers={'Depth':'1', 'Authorization':'Basic '+auth})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=60).read())
    ns = {'d':'DAV:'}
    for r in root.findall('d:response', ns):
        href = r.findtext('d:href','',ns)
        name = urllib.parse.unquote(href.rstrip('/').rsplit('/',1)[-1])
        if urllib.parse.unquote(href).strip('/') == path.strip('/'): continue   # skip the self entry (root or the queried folder)
        isdir = r.find('.//d:resourcetype/d:collection', ns) is not None
        size  = r.findtext('.//d:getcontentlength','',ns)
        print(('DIR ' if isdir else 'FILE'), name, size)

# DOWNLOAD a file
def download(path, out):
    req = urllib.request.Request(BASE + '/' + urllib.parse.quote(path),
        headers={'Authorization':'Basic '+auth})
    open(out,'wb').write(urllib.request.urlopen(req, timeout=120).read())

# UPLOAD a file (PUT — 201 Created = success)
def upload(local, path):
    req = urllib.request.Request(BASE + '/' + urllib.parse.quote(path),
        data=open(local,'rb').read(), method='PUT',
        headers={'Authorization':'Basic '+auth})
    urllib.request.urlopen(req, timeout=120)
```

`PROPFIND` returns HTTP `207 Multi-Status` on success — that is the success code here, not an error.

## Pitfalls

- **Yandex app passwords are service-scoped.** A password issued for Mail returns `401` against Disk WebDAV. Have the user create a separate one at id.yandex.ru/security/app-passwords choosing "Диск" / WebDAV.
- **URL-encode every path segment** with `urllib.parse.quote` — Cyrillic folder/file names must be percent-encoded or the request 404s. Read names back from `displayname` (already decoded), not from the percent-encoded `href`.
- **PROPFIND returns the requested collection itself as one of the responses.** The self `href` equals the path you queried (with a trailing slash). `if not name: continue` only filters the root case (`href='/'` → empty name); for a subfolder the self `href` is e.g. `/Reports/`, whose last segment is non-empty, so a naive recursive walker re-lists the same folder forever (Reports → Reports → Reports …). Skip the response whose decoded `href` (slashes stripped) equals the path you requested, not just empty names.
- **Recursive PROPFIND scans hit HTTP 429 (rate limit).** Rapid `Depth: 1` probes across many folders get throttled. Target the folder you actually need by name, and add a ~1s sleep between requests when walking more than a handful of directories.
- **Status codes.** `401` = bad/missing auth or wrong-service app password; `404` = wrong path (check encoding); `403`/`405` = WebDAV disabled for the corporate domain (only the org's IT can fix); `423` = resource LOCKED (open in an editor or held by a sync client) — blocks `MOVE` and `DELETE` but NOT `GET`, so ask the user to close the file, or copy (GET → PUT) and retry the delete later.
- **Large files and batches.** WebDAV has no chat-size limit, so it is the right path for files over ~50 MB (chat drops those). Uploads can stall past a tool's default timeout even for a 1–2 MB file: earlier `PUT`s in a batch land, later ones silently don't. Upload one file at a time, re-list the target folder to confirm each landed, and retry a missing one with a higher timeout.
- **Never put the password on a command line** (it would land in shell history/chat). With `curl`, use a `chmod 600` netrc file via `--netrc-file`; with Python, read the credentials from the `.secrets` JSON.
- **Force IPv4 on intermittent `Network is unreachable`.** Yandex endpoints (webdav/caldav) resolve to IPv4 and IPv6; if `urllib` intermittently raises `OSError [Errno 101]`, fall back to `curl -4 …` (reliable for GET/PUT/PROPFIND) or resolve `AF_INET` first and connect to that address.
