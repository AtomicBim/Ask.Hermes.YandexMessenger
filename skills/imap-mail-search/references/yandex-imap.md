# Yandex Mail over IMAP: access and quirks

## Connection
- Host `imap.yandex.ru`, port `993`, SSL (`imaplib.IMAP4_SSL`). For Yandex 360 for business with a custom domain the host is
  the same.
- Login is the full mailbox address.
- IMAP access must be enabled by the user in the mail settings ("Почтовые клиенты" → access via IMAP). If `login` fails with
  an authentication error although the password is right, check that setting first, then the password type below.

## Password
- Use an **app password** created at id.yandex.ru/security/app-passwords for "Почта" (Mail / IMAP). The regular account
  password does not work when two-factor authentication is on.
- App passwords are service-scoped: a Disk or Calendar password returns an authentication error here, and a Mail password
  returns `401` against Disk and CalDAV.
- Store the access in `$HERMES_HOME/.secrets/yandex-mail.json` (`chmod 600`, directory `chmod 700`); keep only the file path in memory, never the secret.

## Folders
- Folder names are IMAP modified UTF-7. List them with `M.list()` and match by the returned name instead of typing Cyrillic.
  Typical names: `INBOX`, `Sent` (Отправленные), `Drafts`, `Trash`, `Spam`.
- Select read-only with `M.select(folder, readonly=True)` and always fetch with `BODY.PEEK[...]` so nothing is marked read.

## Known limits
- Server-side `SEARCH` with non-ASCII text may return nothing even when matches exist: verify with a control search on a
  Latin term that must match, then search the Latin transliteration or scan headers client-side.
- The server closes the connection after roughly 1000 messages fetched in a loop, and after as few as ~30 when wide
  multi-recipient headers are requested: fetch in chunks of 20-50 (wide headers) or 100-200 (`From` only), reconnect on
  `EOF`, and checkpoint the last processed id to a file.
- `From` headers mix `=?utf-8?B?...?=` and `=?koi8-r?B?...?=`: decode with `email.header.decode_header` + `make_header`.
- If the connection fails with `Network is unreachable` (IPv6), resolve an IPv4 address first or retry through `curl -4`.
