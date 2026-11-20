---
name: security-recon
description: "Use when auditing a host's exposed services and security."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [security, recon, penetration-testing, network, docker, ports]
---

# Security reconnaissance (host / VM / container audit)

## When to Use

Trigger when the user asks to test/audit the security of their own host, VM, or container
fleet ("протестируй безопасность моей виртуалки"), or asks what ports/services are exposed
and whether they are safe. Only ever probe targets the user owns.

Read-only assessment of what a host exposes and how protected it is. Enumerate before
judging, and rank every finding by severity so the report is actionable, not a raw port list.

## External exposure — never conclude "closed" from the inside

A connection from inside a box back to its OWN public IP fails even when the services ARE
exposed — outbound hairpin NAT drops it. An internal `open_port(PUBLIC_IP, p) == False`
proves nothing about the internet-facing side. Verify reachability through an independent
observer instead:

```bash
# submit probes (array of host:port), get request_id
curl -sk -H 'Accept: application/json' \
  'https://check-host.net/check-tcp?host=1.2.3.4:6379&host=1.2.3.4:9090&max_nodes=2'
# then, after a few seconds, poll the result
curl -sk -H 'Accept: application/json' \
  'https://check-host.net/check-result/<request_id>'
```

Reading the answer: `Connection timed out` on EVERY port (including 22/80/443) means a
firewall/provider NAT is DROPping inbound — not that each service is individually closed
(that would be `refused`). Cross-check with DNS: a public A record pointing at the IP with
all ports timing out means a perimeter filter, not a missing service.

## Docker bridge semantics — published ≠ same network

Ports reachable on `host.docker.internal` (usually `172.17.0.1` = the docker0 host address)
are Docker `-p` published ports, bound to `0.0.0.0`, visible to EVERY container on the host.
Reaching them from one container proves the port is published, NOT that the two containers
share a compose network. Decide which it is by DNS:

- same network → sibling container names resolve via Docker's `127.0.0.11` resolver (`cadvisor`,
  `prometheus`, …);
- different network → those names do NOT resolve, yet the host-published ports are still
  reachable through the docker0 gateway (your default route is `172.17.0.1`).

A multi-tenant host allocates one bridge per isolate (`172.17.0.0/16`, `172.18.0.0/16`, …).
Key facts for mapping it from inside one container:

- EVERY bridge gateway (`172.17.0.1`, `172.18.0.1`, `172.19.0.1`, …) is the SAME host, so any
  `0.0.0.0`-published port (e.g. Redis `:6379`) answers identically on all of them. Probing the
  gateway list and seeing identical open ports is proof of single-host, not of N services.
- The sibling CONTAINERS behind those bridges are unreachable BY IP from your container —
  inter-bridge routing is off by default, so scanning `172.X.0.2`–`.20` across every subnet
  returns 0 live hosts even on networks that clearly hold tenants (whose presence you only see
  via a leak like an unauth Prometheus). Do not read "0 hosts" as "empty host"; it means
  per-tenant L2 isolation is working.
- The only shared layer is the host's `0.0.0.0`-published surface (gateway IPs + LAN IP).

Play this back to the user: "reachable from a different container's network" means the port
is on `0.0.0.0`, which is WIDER exposure than "locked inside one compose network".

## Reading the box from a restricted container

In a `CapEff=0` container the usual recon tools are absent (`ss`, `netstat`, `lsof`, `nc`,
`nmap`). Fallbacks that always exist:

- Port scan: `timeout 1 bash -c 'echo >/dev/tcp/HOST/PORT'` in a loop (run sequentially — the
  terminal tool rejects `&` backgrounding in the same call).
- Banners / auth probes: raw Python `socket` (connect, send bytes, `recv` with a timeout).
- Own open sockets: `/proc/net/tcp` (+ `/proc/net/tcp6`), parse the state column (hex `0A` == LISTEN).
  Local address is little-endian hex — `0100007F:21C2` = `127.0.0.1:8642`. A service listening only
  on `127.0.0.x` is loopback-bound: not remotely exposed, and unreachable from sibling containers.
  Only a `0.0.0.0`/`00000000` bind is externally significant.
- Another process's env (e.g. gateway/platform vars): `tr '\0' '\n' < /proc/<pid>/environ`.
- Egress well / side channels: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` name the platform's forward
  proxy; test it with `curl -x $HTTP_PROXY http://example.com/`. A proxy you can use with NO
  credentials is a finding (open SSRF/abuse surface for any container that can route to it).

## OS fingerprint

Your own `/proc/os-release` and `uname -r` describe the CONTAINER's userspace plus the
shared kernel — not the host's distro. To identify the host OS, read what a service
leaks rather than what your box reports:

- SSH banner encodes the distro+release: `SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.20` →
  Ubuntu 24.04 (the OpenSSH version maps to the release, and the `-ubuntu*` package
  suffix confirms Ubuntu).
- Unauthenticated datastore metadata leaks it too: `Redis INFO` returns `os:`/`redis_version`
  and other running services expose `server:`/version headers that identify the stack.

## Raw DB auth probes (no client binaries)

```python
# Redis — open if PING answers without AUTH
s.sendall(b'*1\r\n$4\r\nPING\r\n')          # '+PONG' = no password (critical)

# PostgreSQL — read the auth method from the StartupMessage reply
s.sendall(b'\x00\x00\x00\x08\x04\xd2\x16\x2f')   # SSLRequest; 'S'/'N' reply
# then send StartupMessage: length + protocol 196608 + 'user\0postgres\0database\0postgres\0\0'
# reply 'R' auth code: 0 = trust (no password), 3 = cleartext, 5 = MD5, 10 = SCRAM-SHA-256
```

Working probes are in `scripts/db_probes.py` (Redis enumeration + a full Postgres
SCRAM-SHA-256 auth attempt, e.g. to test default `postgres/postgres`). Two things that cost
time getting them right:

- The Postgres StartupMessage is `int32 length + int32 protocol(196608 = 3.0) + params`; the
  protocol field is NOT optional — omit it and the server rejects the version with
  `unsupported frontend protocol … server supports 3.0 to 3.0` (looks like an auth error, is
  not).
- `+PONG` alone does not prove a data breach. Before calling an open Redis critical, read
  `DBSIZE` and `INFO commandstats`: an empty DB whose traffic is 99%+ `ping` is a health-check
  sink — a misconfig with an RCE *path* but no leaked data. `CONFIG GET bind`/`protected-mode`/
  `requirepass` confirm whether the exposure is actually wildcard-bind.

An ASGI app on a bare port answers `Not Found` (uvicorn/FastAPI on `/`) — probe `/docs` and
`/openapi.json` to fingerprint it. MongoDB and Elasticsearch send nothing until a correct
client handshake, so a silent port is inconclusive, not "no auth".

## Known-vulnerability check

"Audit for known vulnerabilities" is a distinct step from exposure: after fingerprinting a
version, cross-check it against advisory DBs before reporting. Read the version *ranges*,
not the headline — a fix in `3.14.4` means `3.14.4` AND later are clean, so report the
installed version as patched when it is at or past the fixed release.

- Get exact Python package versions without `pip` (`pip list` is often empty in a restricted
  venv): `python -c "import importlib.metadata as m; [print(d, m.version(d)) for d in ['aiohttp','uvicorn','fastapi','starlette','pydantic','cryptography','pyjwt']]"`.
- Query per-stack: GitHub Advisory DB + `github.com/aio-libs/aiohttp/security/advisories` for
  pip deps; `nginx.org/en/security_advisories.html` for nginx (note the `Not vulnerable: X.Y+`
  column); OSV/NVD for the rest. `web_search "<package> <version> CVE"` is usually enough to
  surface the current advisories.
- Distinguish *your box* from the *shared platform host*: an outdated nginx/service behind the
  host's reverse proxy (`172.17.0.1`, the `host.docker.internal` gateway) is platform
  infrastructure you can report but not remediate — say so, and keep it out of "your surface".

## Permission & privilege-escalation audit

When the ask is "attacker already has a shell here — what can they reach, modify, or escalate",
run a filesystem/permission pass distinct from the network pass:

- Escalation checkpoints: `/etc/sudoers.d`, `/etc/cron*`, `/var/spool/cron`, `/etc/ld.so.preload`,
  a mounted `docker.sock` or host volume, a readable `/proc/1/root`, setuid binaries, and `CapEff`
  from `/proc/self/status`. `CapEff=0` neutralizes setuid helpers (`mount`, `su`, `chsh`) — they
  need a capability no matter the mode bit, so report them as inert, not as a vector.
- Read a process's real UID from `/proc/<pid>/status` (`Uid:`), one read per PID — do not derive
  it in a shell loop with a conditionally-set variable (`[ "$pid" = 1 ] && u=$(…)`), which carries
  the value into later rows and mislabels every process as root.
- Enumerating "world-writable root binaries" must follow symlinks: `os.lstat` reports EVERY
  symlink as `0777`, so a venv's `bin/python` (a symlink to a 0755 target) shows as a
  world-writable root binary and becomes a false escalation finding. Stat the resolved target
  (`os.stat` / `readlink -f`) and report the REAL file's mode and owner.
- Inventory credentials by NAME only, never values: when reading an agent `.env` / `config.yaml` /
  auth store, list field/var names and value LENGTHS — `credential_pool`, `providers`, `*_KEY`,
  `*_TOKEN` — and do NOT echo the secret bytes into chat or logs.
- A host-published datastore reports your connection's source as the docker gateway IP (DNAT
  rewrites it), and keeps only CURRENT clients, not history — so neither Redis `CLIENT LIST` nor
  `pg_stat_activity` can reconstruct "who else probed X". The only retrospective signal is
  accumulated `INFO commandstats`: non-`ping` commands (`dbsize`, `scan`, `config|get`) beyond
  your own run are prior/concurrent testers.

## Report shape

Group by severity with the mechanism, not just the port:

- **critical** — a datastore answering without auth (Redis `+PONG`, PG `trust`): full data
  access, and for Redis a file-write path to RCE via `CONFIG SET dir`.
- **high** — unauthenticated info disclosure: Prometheus/cAdvisor/node-exporter serving
  `/targets`, `/metrics`, host/container telemetry, internal service names. Prometheus
  `/api/v1/targets` lists every sibling by compose-service name (plus health and the scrape
  error) even when those containers are unreachable from your network — a named target that is
  `up` yet unconnectable is itself the proof they live on a separate compose network. Stronger
  still: an unauth Prometheus that also scrapes cAdvisor exposes the WHOLE host container
  registry via `container_*` metrics — `query=count by (name,image)(container_last_seen)` returns
  every container's name and image on the host (tenant/owner names, secrets-manager and DB and
  proxy images), far beyond the scrape targets. That one query is the highest-value read-only
  move for mapping a shared multi-tenant host; `scripts/prometheus_enum.py` runs the whole recipe.
- **ok** — a service that answers but requires SCRAM/basic auth; note the port is still exposed.

Always state the limiting fact up front: from inside a container you see the NETWORK surface
and can prove exposure, but cannot read host firewall rules or `docker-compose.yml` without
root — so "externally closed" is an inference, not a proof. Do not mutate anything (no
`CONFIG SET`, no writes) — audit is read-only unless the user explicitly asks for exploitation.

Deliver the audit as a structured Markdown file (headings + tables) that stays docx-convertible
(e.g. `pandoc report.md -o report.docx`): summary, scope/authorization, environment map,
per-finding severity with evidence + remediation, an operations journal, and an evidence
appendix. Update the SAME file in place as the audit deepens rather than re-summarizing in chat.

## Pitfalls

- Never trust an internal probe of the public IP for exposure (hairpin NAT) — use check-host.net.
- A wrong guess about which Docker network you are in flips the whole risk picture: verify
  via sibling-name DNS before telling the user how exposed a port is.
- An NTLM/Basic-auth site (IIS/SharePoint, `WWW-Authenticate: NTLM`) has NO login form in the
  DOM, so the credential-vault `fill` path cannot work and the browser stalls on `chrome-error`.
  There is no way to pass those creds through a normal page — ask for a different route
  (exported file, a public URL) rather than looping.
- Do not accept a password in chat, even when the user volunteers one — redirect to the vault's
  masked prompt; it only works when a login PAGE exists (see the pitfall above).
- A high-concurrency connect scan (`ThreadPoolExecutor` with dozens of workers) can return
  transient false-positive "open" ports that a later single connection reports
  `connection refused`. Re-verify every finding sequentially (one connection, no parallel load)
  before reporting a port as exposed — a port that flips open/closed under load is rate-limiter
  or SYN-cookie noise, not a service.
- Many ports accepting the TCP handshake yet answering NOTHING — even to a correct protocol
  probe (a DNS query to 53, an HTTP GET to 8080/9200) — points at a TLS/SNI-multiplexing proxy
  or a TCP sink in front of the host, not real services. Distinguish it from the normal
  "MongoDB/ES stay silent until a client handshake" case by whether the port answers AFTER the
  correct probe: a real DB responds to its handshake, a sink/proxy responds to nothing.
- A "hardened-looking" HTTPS front can be a shell: an nginx/SNI terminator serving a valid
  wildcard cert but routing NOTHING (every Host/SNI → a catch-all 404 like "nothing is
  configured for this host") while the real services sit exposed on raw ports behind it.
  SNI-enumerate the cert to discover the vhosts that front *should* exist —
  `openssl s_client -connect HOST:443 -proxy $HTTP_PROXY -servername NAME` for each candidate
  NAME, reading `subjectAltName`; each distinct certificate is a separate vhost. Do not stop
  the audit at the pretty 443 front: the raw-port services behind it are the actual surface.
