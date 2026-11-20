---
name: russian-rail-tickets
description: "Use when searching Russian train tickets. Go via Tutu.ru."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [travel, trains, russia, rzd, tutu]
---

# Russian train tickets (РЖД)

## Use Tutu.ru, not ticket.rzd.ru
`ticket.rzd.ru` returns `Forbidden` to this environment's outbound (datacenter) IPs at the API level, so scraping РЖД directly is a dead end. `www.tutu.ru` is reachable through the proxy (HTTP 200) and serves the raw schedule without auth. Do the search on Tutu and report РЖД trains from its results.

## Workflow — browser (after bypassing the SSL-MITM cert error)
Every Russian https site here is SSL-MITM'd by the outbound proxy (self-signed cert), so Chrome starts on a `Privacy error` blank page. That is NOT a dead end — bypass it, then use the browser normally.

1. In `browser_exec`, clear the cert error once per session:
   `cdp('Security.setIgnoreCertificateErrors', ignore=True)`
2. Navigate to the schedule page by station NAME (no numeric codes needed):
   `goto_url("https://www.tutu.ru/poezda/Ekaterinburg/Perm/?date=30.12.2026")`
3. The page is a Next.js SPA that loads offers client-side. `wait_for_load()`, then `sleep(6)` (long enough for the offer fetch), then read the rendered list:
   `js("document.body.innerText")`
4. The rendered text contains, per train: number, name, departure/arrival times + dates, journey length, and per-berth-type availability with prices (e.g. `Купе  17 ниж, 25 верх  от 3 816 ₽`). Parse that directly — it is the live inventory; the HTML/JSON embedded in a raw `curl` fetch only holds a layout skeleton, not offers.

### Why not curl the offers API
Live offers come from a client-side call to `train-gateway.tutu.ru` (offers host) with a minified, undocumented endpoint + params. Reverse-engineering it is slow and fragile. The browser path above gets the same live prices with no endpoint digging. Keep `curl -sk` only for quick reachability probes (`curl -sk -o /dev/null -w "%{http_code}" <host>`).

### Pricing notes to report
Tutu shows prices as `от N ₽` = lowest per-berth fare PER PERSON (upper berth; lower berths cost more, fixed only at seat selection). A купе to пермь/Екб has 4 berths, so "4 people in one купе" ≈ 4 × the `от` price. State this explicitly rather than quoting `от` as a total.

## Known Tutu numeric station codes
- Ekaterinburg = 2030000
- Perm = 2030120

## Tutu MCP server
There is NO Tutu MCP in the bundled catalog (verified: `manage_connections` install for `{"name":"tutu","mcp":true}` refuses it, and the returned catalog list contains no tutu/train entry). Use the browser workflow instead.

## Pitfall
- Never guess Tutu numeric station codes. A wrong code does not error — the schedule URL silently redirects to a different station's page (e.g. `2030001` resolved to Cheptsa, not Perm). Always read the code from the page's own embedded JSON.
