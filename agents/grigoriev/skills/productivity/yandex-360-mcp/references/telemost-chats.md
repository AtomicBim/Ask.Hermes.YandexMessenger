# Telemost MCP — reading chats

Endpoint `https://mcp.360.yandex.net/v1/telemost/mcp`, `Authorization: OAuth <token>`. Chat/message access is
READ-ONLY (no send/edit/delete, no member/role/read-state changes). Meeting tools are write-capable and gated by
confirmation.

## Tool set (18 tools)
Meetings: `create_meeting`, `update_meeting`, `get_meeting_join_details`, `list_meeting_cohosts`,
`update_meeting_cohosts`, `add_meeting_cohosts`, `remove_meeting_cohosts`, `get_current_user`, `search_users`.
Chats/messages: `list_chats`, `search_chats`, `get_chat`, `list_chat_members`, `list_chat_messages`, `get_chat_message`,
`list_thread_messages`, `get_thread_message`, `search_messages`.

## Finding a person's chat
- `search_users {"query": "Фамилия"}` is the reliable first move. Each hit carries `user.id`, `display_name`, `login`
  (email) and a ready-made `chat_id` (`<your_id>_<their_id>`). Use that `chat_id` directly in `list_chat_messages`.
- `list_chats` returns EVERY chat (group / private / channel) paginated by `next_cursor`: first call no args, then
  `{"cursor": <next_cursor>}`; stop when `next_cursor` is null. A full org can be ~100–200 chats. **Pagination under-counts under load**: a single pass can silently return fewer pages (e.g. 50 vs 110 vs ~192) because the API intermittently returns an empty `items` page. Do **not** `break` the loop when `items` is empty on one page — that silently truncates the scan (a naive `if not items: break` returned ~4 of 193 chats). Instead keep walking with `cursor` (even on an empty page), retry the page 3–6×, track a capped `empty_streak`, and only stop on the true end. Dedupe by `id`/`chat_id` across pages before trusting any count.
- Private 1:1 chat_id = `<your_user_id>_<other_user_id>` (both UUIDs joined by `_`). `get_current_user` gives your own id.

## Bot vs human — do not confuse them
A private chat named `Атом h-<login>` (or any `Атом h-…`) is usually a **bot account**, not the person. The org runs many
`Атом h-<user>` messenger bots, each a separate account. Verify with `search_users`: the real person has `is_bot: false`
and `login` = their email (e.g. `lean@atomsk.ru`); a bot shows `is_bot: true` or a non-email login. The bot chat may hold
only the owner's own test messages ("привет"), not a real conversation. A freshly-created bot with **no message history at all** does not appear in `list_chats`, and `search_chats` does not index bot display names (it searches real org members) — so absence from the list means "not messaged yet", not "not created". To find or verify a bot's name, enumerate `list_chats` and match the name yourself.

**Each employee has TWO chats** in most orgs: the real person (full name, e.g. `Баранова Валерия Леонидовна`, `is_bot: false`)
and their bot (`Атом h-baranova`). When the user asks "what did X say / what's X's problem at HH:MM", read the **person**
chat — that holds their actual dialogue with the user, and the target message may be a thin forward. The bot chat is where
the employee talks to their own agent and is usually sparse (just the user's "привет" test), but it is where a forwarded
thread's inner conversation lives.

## Reading history
`list_chat_messages {"chat_id": ..., "limit": 100}` returns newest-first, paginated the same way (pass `cursor` from the
previous `next_cursor`; don't pass other args with it). `limit` max 100. Each item has `author.display_name`,
`author.is_bot`, `created_at` (ISO), `text`, `attachments`, `forwarded_messages`. `search_messages {"query": ...}` searches
across all chats (optionally scoped by `chat_id` / `thread_id`).

**Forwarded messages carry the substance.** When a person forwards a conversation ("вот с такой штукой столкнулась"), their
`text` is only a thin caption — the real content is in `forwarded_messages`, a list of `{type: "forward", message: {...}}`
with each inner `message` shaped like a normal message (`author.display_name/is_bot`, `text`, `created_at`). Always inspect
`forwarded_messages` before concluding a message is near-empty or that `attachments` is the only payload. The bot's reply
in a forwarded thread is the diagnostic the user actually wants you to read.

## Security
These chats routinely carry plaintext secrets — OAuth tokens (`y0__…`), passwords, Wi-Fi codes. When echoing or
summarising message text back to the user, redact them (e.g. `re.sub(r'y0__[A-Za-z0-9_-]+', '[TOKEN]', t)`) rather than
re-printing them. Never store conversation dumps in notes; keep only the token file path.
