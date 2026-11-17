---
name: yandex-disk-webdav
description: Use to access the corporate Yandex Disk over WebDAV.
---
# Yandex Disk via WebDAV

User's corporate Yandex 360 Disk is reachable over WebDAV and permanently configured on this host.

## Access
- Credentials (netrc): `/opt/data/.secrets/yandex-webdav.netrc` (chmod 600, login `v.baranova@atomsk.ru`).
- Helper script: `/opt/data/.secrets/yd.sh` — `ls`, `get`, `put`, `mkdir`, `del`.
- Base URL: `https://webdav.yandex.ru`.

## How it works
- WebDAV = plain HTTP. List with `PROPFIND` (`Depth: 1`), download with `GET`, upload with `PUT -T`, create dir with `MKCOL`, delete with `DELETE`.
- All paths are URL-encoded UTF-8. `yd.sh` handles encoding internally — always pass raw (Russian) paths, never pre-encode them or Cyrillic breaks (401/404).
- Auth via curl `--netrc-file`; never put the password in a command line (it would land in shell history/chat).

## Key gotchas
- Root of the Disk is `/`; files and folders share the namespace.
- Return codes: 401 = bad/missing auth, 404 = wrong path (check encoding), 403/405 = corporate domain disabled WebDAV (only user's IT can fix).
- Large files: WebDAV has no chat-size limit, so this is the right path for files over ~50 MB (chat drops those).
- Moving files/folders: helper has no move — use the WebDAV MOVE verb directly: `curl -X MOVE -H "Destination: <base><encoded-dest>" <base><encoded-src>` (expect 201/204). To move a whole folder tree, MOVE the folder itself; no recursive copy needed.
- HTTP 423 = resource LOCKED (usually open in Excel or held by a sync client). Blocks MOVE and DELETE but NOT GET — download still works. Ask the user to close the file, or copy (GET→PUT) and retry delete later.