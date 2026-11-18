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

1. **Resolve credentials.** WebDAV login (the Yandex account, e.g. `user@domain`) + an app password issued for **Disk / WebDAV** — a Mail app password will not work (see Pitfalls). Store it in a `chmod 600` file; record only the path in memory, never the secret.
2. **Endpoint.** `https://webdav.yandex.ru/` — same for every account and folder; paths are appended below this root.
3. **List a folder** with a `PROPFIND` request at `Depth: 1` and parse the `DAV:` multistatus. The `href` is percent-encoded; the human name (including Cyrillic) is in `displayname`.
4. **Download** with `GET`, **upload** with `PUT`, both on the URL-encoded path.

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
        if not name: continue                       # skip the self/root entry
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
- **`Depth: 1` on the root returns the root itself as a child**; skip the empty/self `href` or you'll list the root as a fake entry.
- **Force IPv4 on intermittent `Network is unreachable`.** Yandex endpoints (webdav/caldav) resolve to IPv4 and IPv6; if `urllib` intermittently raises `OSError [Errno 101]`, fall back to `curl -4 …` (reliable for GET/PUT/PROPFIND) or resolve `AF_INET` first and connect to that address.
