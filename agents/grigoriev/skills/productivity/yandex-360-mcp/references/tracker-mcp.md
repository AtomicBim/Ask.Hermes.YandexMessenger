# Tracker MCP — access and gotchas

Endpoint `https://mcp.tracker.yandex.net/mcp` (streamable HTTP). Diverges from the `mcp.360.yandex.net` services: needs **two** auth headers — `Authorization: OAuth <token>` **and** `X-Org-ID: <org_id>` (e.g. `8292148`). Credentials live in `/opt/data/.credentials/yandex-tracker-mcp.json` (`{oauth_token, org_id, endpoint}`).

## How to call (no MCP client)

The body is **SSE**, not raw JSON: `event: message\ndata: {…}`. Parse by splitting lines and taking the line after `data:` as JSON (`json.loads(line[5:].strip())`). `requests` works directly, no proxy. Cyrillic: decode with `r.content.decode('utf-8','replace')`, never `r.text` (mojibakes to `ÐÐ`).

```python
def rpc(method, params=None, idn=1):
    p = {"jsonrpc":"2.0","id":idn,"method":method}
    if params is not None: p["params"]=params
    r = requests.post(url, json=p, headers=H, timeout=60)
    for line in r.content.decode('utf-8','replace').splitlines():
        if line.startswith('data:'):
            return json.loads(line[5:].strip())
```

Initialize first (`initialize` then the `notifications/initialized` fire-and-forget), then `tools/list`. `tools/call` returns the payload twice: `result.structuredContent` (parsed object) and `result.content[].text` (JSON strings). Some tools return MULTIPLE `content` parts (e.g. `list_queues` = a "page N" note part + the JSON part) — scan every part for JSON, never trust `content[0]`.

## Key tools

- `get_current_user` → `{login, display, email}` — run first to confirm whose token you hold.
- `search_issues` → `structuredContent.issues[]` + `total`, `hasMore`, `nextPage`. Params: `query` (Tracker query language), `assignee:"me"`, `resolution:"unresolved"`, `queue`, `sort`, `fields`. Every row carries `key`, `summary`, `status`, `assignee`, `priority`, `updatedAt`.
- `find_user` `{query}` → resolves a login/name/email fragment; ambiguous matches return a `{users:[{id,display}...], ambiguous:true}` blob inside `content` (second text part).
- `get_current_user`, `list_queues`, `list_boards`, `get_issue`, `create_issue`, `update_issue`, `transition_issue`, `add_comment`, worklog tools.

## "Today's tasks" = open issues assigned to the caller

```json
{"name":"search_issues", "arguments":{"assignee":"me", "resolution":"unresolved"}}
```
Optionally narrow by deadline: `"query": "Deadline: <= today()"` (returns 0 if nothing due today). `Assignee`/`Deadline`/`Status` are display names in `query`; open issues are `Resolution: empty()`. `assignee:"me"` is the caller-as-assignee; `involvesMe` is a broader union — use `assignee:"me"` for "my tasks".

## User email / directory lookup

`find_user` returns `id`/`display` but NOT email. To get someone's email, hit the REST API directly with the same headers:
`GET https://api.tracker.yandex.net/v2/users/{id}` → `{"email":"osipov@atomsk.ru", "login":"osipov", "display":"…", "position":"…"}`.

## Gotchas

- The token is bound to the account that authorized it; a token from a colleague's login cannot write your queues (`403 … enough permissions`). `get_current_user` reveals the holder.
- `search_issues` resolution policy: default `unresolved` only unless asked for closed issues.
- When `total` is null and `totalIsExact` false, Tracker did not count — page until `hasMore` is false and don't state a count.
- Restricted/unavailable rows come back as `{key, restricted:true}` / `{key, availability:"unavailable"}` stubs — report only the key + availability, never infer other fields.
