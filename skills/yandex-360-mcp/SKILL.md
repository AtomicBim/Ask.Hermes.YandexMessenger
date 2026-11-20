---
name: yandex-360-mcp
description: "Connect Yandex 360 services to AI agents via MCP."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Yandex, MCP, Yandex360, OpenWebUI, AI-agents]
    related_skills: [yandex-disk, yandex-calendar, imap-mail-search]
---

# Yandex 360 MCP

Connect Yandex 360 services (Mail, Disk, Calendar, Telemost, Tracker, Wiki, Forms, org management) to an AI agent via
Yandex's hosted MCP servers, so the agent can act on the user's office data with the user's own permissions. This is the
AI-agent protocol — distinct from the direct WebDAV/CalDAV/IMAP access in `yandex-disk` / `yandex-calendar` /
`imap-mail-search`. The MCP server is a *preview* feature: interfaces/conditions can change and break compatibility.

## When to Use
Use when the user wants an AI agent to read/act on their Yandex 360 data (read Telemost chats, work mail/disk/calendar,
manage meetings, org admin), to wire these servers into Open WebUI or another agent, or asks what Yandex 360
"extensions"/"MCP" are. Not for direct file access via WebDAV/CalDAV/IMAP — those are the yandex-disk / yandex-calendar /
imap-mail-search skills.

Docs hub: `https://yandex.ru/dev/yandex-360/mcp/ru/`

## Access requirements
- Needs a Yandex ID + an active Yandex 360 subscription (personal, or "для бизнеса"). Any tariff works.
- For business orgs MCP is **enabled by default** (orgs created after 22.10.2026); older orgs must activate it in admin
  settings. Disabling for everyone is via a support request only.
- The agent acts with the token owner's exact rights — no elevation, no more access than the user has. Consequently a token
  only ever exposes **its owner's** mail, chats, Disk and calendar; reading another person's data means that person (or an
  org admin acting under company policy) issues the token.

## Endpoints
Pattern `https://mcp.360.yandex.net/v1/<service>/mcp` (transport: streamable HTTP). Confirmed services:

| Service | Endpoint |
|---|---|
| Почта / Mail | `https://mcp.360.yandex.net/v1/mail/mcp` |
| Диск / Disk | `https://mcp.360.yandex.net/v1/disk/mcp` |
| Календарь / Calendar | `https://mcp.360.yandex.net/v1/calendar/mcp` |
| Телемост / Telemost | `https://mcp.360.yandex.net/v1/telemost/mcp` |
| Управление организацией | `https://mcp.360.yandex.net/v1/admin/mcp` |

**Tracker is a separate system:** endpoint `https://mcp.tracker.yandex.net/mcp`, and it requires an extra org header —
`X-Org-ID` (Yandex 360 for business card) or `X-Cloud-Org-ID` (Yandex Cloud Organization card) — plus `Authorization:
OAuth <token>` (or `Bearer <IAM token>`, valid ~12h). Wiki and Forms have their own help docs (`yandex.ru/support/wiki/ru/mcp`,
`yandex.ru/support/forms/ru/mcp`).

## Connect procedure
1. **Get an OAuth token per service.** Each service has its own `client_id`; the docs page for the service links
   "получить OAuth-токен" → `https://oauth.yandex.ru/authorize?client_id=<id>&response_type=token`. The user logs in,
   approves the "Внешний ИИ-агент для <сервис>" scopes, and copies the token from the resulting page.
2. **Add the server to the agent config** (standard MCP shape):
   ```json
   { "mcpServers": { "yandex_calendar": {
       "type": "http", "url": "https://mcp.360.yandex.net/v1/calendar/mcp",
       "headers": { "Authorization": "OAuth <token>" } } } }
   ```
3. **Store the token** in a `chmod 600` file under `$HERMES_HOME/.secrets/yandex-<service>-mcp.json`
   (`/opt/data/.secrets/` in the container; e.g. `{"oauth_token": "..."}`; Tracker also needs `org_id`), next to the
   yandex-mail/disk/calendar files; keep only the path in notes, never the secret in chat/logs. These file names are
   not checked by the chat onboarding (it only watches mail/calendar/disk), so connecting MCP never changes it.
4. **Verify** by asking the agent to list data (e.g. "покажи последние письма" / "список моих чатов").

## Drive the server directly (no MCP client)
Hermes can call these hosted servers itself over raw JSON-RPC (streamable HTTP) — no MCP client library needed. This is the
fastest path when the goal is "read my X now", not "wire it into OUI". Python `requests` (2.34.2 in the image) reaches the
endpoint directly with no proxy.

```python
url = "https://mcp.360.yandex.net/v1/<service>/mcp"
H = {"Authorization": f"OAuth {token}", "Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}
def rpc(method, params=None, idn=1):
    p = {"jsonrpc":"2.0","id":idn,"method":method}
    if params is not None: p["params"] = params
    return requests.post(url, json=p, headers=H, timeout=60).json()

rpc("initialize", {"protocolVersion":"2025-03-26","capabilities":{},
    "clientInfo":{"name":"hermes","version":"1.0"}})      # returns protocolVersion
requests.post(url, json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)  # no id
rpc("tools/list")                                           # returns every tool + inputSchema
rpc("tools/call", {"name":"<tool>","arguments":{...}})      # result in result.content[0].text (JSON string)
```
Notes: the server is stateless — no `Mcp-Session-Id` to echo. `tools/call` returns the payload twice:
`result.content[].text` (JSON-encoded strings) and `result.structuredContent` (the parsed object). The `content` array can hold
MULTIPLE text parts — e.g. Tracker `list_queues` returns `"Showing 25 of 36 queues (page 1). Call again with page=2."` as one
part and the actual JSON as the next — so scan every text part for a JSON object instead of trusting `content[0]`. Decode the
SSE body with `r.content.decode('utf-8','replace')`, never `r.text`: `requests` guesses the charset from the header and mojibakes
Cyrillic into `ÐÐ¢`-style garbage when the charset is absent. Pull each tool's `inputSchema` from `tools/list` before calling it —
argument shapes differ (e.g. `search_chats` needs `query`, `list_chats` pages via `cursor`; Tracker `list_queues` pages via `page`).

See `references/telemost-chats.md` for the Telemost-specific tool set plus the chat-id, person-vs-bot triage ("what did X say / what's X's problem?"), and forwarded-message gotchas. See `references/tracker-mcp.md` for Tracker-specific access (OAuth auth-flow, queue/issue tooling, identity-and-permissions gotchas).

## Telemost chats are READ-ONLY
Reading chats/messages is supported, mutating is not: `telemost.list_chats`, `search_chats`, `get_chat`,
`list_chat_members`, `list_chat_messages`, `get_chat_message`, `list_thread_messages`, `get_thread_message`,
`search_messages` all work; sending/editing/deleting messages, changing members/roles/read-state are not. Meeting tools
(create/update meeting, manage cohosts) are write-capable and gated by user confirmation. A channel is just a chat type —
use the chat tools. So "read my chats with X" is achievable via MCP, but "reply in that chat" is not.

## Extensions vs MCP vs Alisa — do not conflate
- **Расширения (extensions/plugins)** customise the Yandex 360 office services themselves (Почта, Трекер, Календарь, Диск,
  Вики, Формы): ready plugins from a catalog (50+), or custom solutions via the unified API. Admin decides per-group access;
  plugins pass moderation before publishing; any org with domain mail can build its own. Catalog:
  `https://360.yandex.ru/business/extensions/`.
- **MCP** is the AI-agent layer (this skill).
- **Alisa AI для бизнеса** is a *separate* product (a corporate assistant) with its *own* plugin catalog — not the same thing.

## Pitfalls
- The docs site (`yandex.ru/dev/yandex-360/...`) is JS-rendered: `web_extract` returns a "браузер устарел" stub. Read it
  with the browser instead (`browser_exec`, ignore cert errors) and pull text via `document.body.innerText`; grab the
  per-service OAuth link from the anchor `href` (`oauth.yandex.ru/authorize?client_id=...`).
- Don't hard-code a client_id across services — each MCP service has its own; pull it from the service's own docs page.
- Tracker MCP needs the org id header or calls fail — it is the one service that diverges from the `mcp.360.yandex.net` pattern.
- **An OAuth token is bound to the account that authorized it, not to anyone's queue access.** A Tracker token issued under a
  colleague's login cannot create issues in your queues (`403 ... enough permissions in the '<queue>' queue`) even though the
  org id is shared. To write to your own queues, get a token for *your* account; don't reuse a colleague's token. `get_current_user`
  (or the REST `/v3/myself`) reveals whose token you are actually holding before you build on it.
