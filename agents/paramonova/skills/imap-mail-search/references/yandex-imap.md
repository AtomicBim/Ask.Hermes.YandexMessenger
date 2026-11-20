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
- Store the password in a `chmod 600` file; keep only the file path in memory, never the secret.

## Folders
- Folder names are IMAP modified UTF-7. `M.list()` returns each name as raw ASCII bytes (e.g. `&BCEEGgQQBB0EKw-` = «СКАНЫ»);
  `M.select('СКАНЫ')` and `M.list()` of a raw Cyrillic name both raise `UnicodeEncodeError: 'ascii' codec can't encode`. To
  select a folder by its human name you MUST encode it to modified UTF-7 first (decode the `M.list()` bytes to display names).
  Typical names: `INBOX`, `Sent` (Отправленные), `Drafts`, `Trash`, `Spam`, plus custom Cyrillic ones.
  ```python
  import base64
  def m_utf7(s):  # RFC 3501 modified UTF-7 -> str
      out, buf = [], []
      def flush():
          if buf:
              b64 = base64.b64encode(''.join(buf).encode('utf-16-be')).decode().rstrip('=')
              out.append('&' + b64 + '-'); buf.clear()
      for ch in s:
          if 0x20 <= ord(ch) <= 0x7e:
              flush(); out.append('&-' if ch == '&' else ch)
          else: buf.append(ch)
      flush(); return ''.join(out)
  # select: M.select(m_utf7('СКАНЫ'), readonly=True)  — pass the encoded form.
  # decode a list() name: reverse the above (strip quotes, split on '&...-', base64-decode each seg as utf-16-be).
  ```
- Select read-only with `M.select(folder, readonly=True)` and always fetch with `BODY.PEEK[...]` so nothing is marked read.

## Known limits
- Server-side `SEARCH` with non-ASCII text may return nothing even when matches exist: verify with a control search on a
  Latin term that must match, then search the Latin transliteration or scan headers client-side.
- The server closes the connection after far fewer fetches than expected when headers are large (multi-recipient To/CC) —
  drops were seen after ~30 messages. Fetch in chunks of ~20–50, reconnect on `EOF`, and checkpoint processed ids to a file.
- `From` headers mix `=?utf-8?B?...?=` and `=?koi8-r?B?...?=`: decode with `email.header.decode_header` + `make_header`.
- If the connection fails with `Network is unreachable` (IPv6), resolve an IPv4 address first or retry through `curl -4`.

## Sending (SMTP)
- Host `smtp.yandex.ru`, port `465`, SSL (`smtplib.SMTP_SSL`). The same Mail app password works for SMTP login as for IMAP.
- Connect with the **hostname**, not a pre-resolved IPv4 address: connecting to `smtp.yandex.ru`'s resolved IP fails TLS
  verification (`certificate is not valid for '77.88.x.x'`) because the cert is bound to the hostname. (IMAP tolerates the raw
  IP; SMTP does not — reason both cert checks but for SMTP just use the hostname.)
- Compose with `email.message.EmailMessage`, attach a file with `msg.add_attachment(data, maintype=..., subtype=...,
  filename=...)`, then `s.login(login, pw); s.send_message(msg)`. `formatdate(localtime=True)` for the `Date` header.
