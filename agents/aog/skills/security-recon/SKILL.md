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

Play this back to the user: "reachable from a different container's network" means the port
is on `0.0.0.0`, which is WIDER exposure than "locked inside one compose network".

## Reading the box from a restricted container

In a `CapEff=0` container the usual recon tools are absent (`ss`, `netstat`, `lsof`, `nc`,
`nmap`). Fallbacks that always exist:

- Port scan: `timeout 1 bash -c 'echo >/dev/tcp/HOST/PORT'` in a loop (run sequentially — the
  terminal tool rejects `&` backgrounding in the same call).
- Banners / auth probes: raw Python `socket` (connect, send bytes, `recv` with a timeout).
- Own open sockets: `/proc/net/tcp` (+ `/proc/net/tcp6`), parse the state column (hex `0A` == LISTEN).
- Another process's env (e.g. gateway/platform vars): `tr '\0' '\n' < /proc/<pid>/environ`.

## Raw DB auth probes (no client binaries)

```python
# Redis — open if PING answers without AUTH
s.sendall(b'*1\r\n$4\r\nPING\r\n')          # '+PONG' = no password (critical)

# PostgreSQL — read the auth method from the StartupMessage reply
s.sendall(b'\x00\x00\x00\x08\x04\xd2\x16\x2f')   # SSLRequest; 'S'/'N' reply
# then send StartupMessage: length + protocol 196608 + 'user\0postgres\0database\0postgres\0\0'
# reply 'R' auth code: 0 = trust (no password), 3 = cleartext, 5 = MD5, 10 = SCRAM-SHA-256
```

An ASGI app on a bare port answers `Not Found` (uvicorn/FastAPI on `/`) — probe `/docs` and
`/openapi.json` to fingerprint it. MongoDB and Elasticsearch send nothing until a correct
client handshake, so a silent port is inconclusive, not "no auth".

## Report shape

Group by severity with the mechanism, not just the port:

- **critical** — a datastore answering without auth (Redis `+PONG`, PG `trust`): full data
  access, and for Redis a file-write path to RCE via `CONFIG SET dir`.
- **high** — unauthenticated info disclosure: Prometheus/cAdvisor/node-exporter serving
  `/targets`, `/metrics`, host/container telemetry, internal service names.
- **ok** — a service that answers but requires SCRAM/basic auth; note the port is still exposed.

Always state the limiting fact up front: from inside a container you see the NETWORK surface
and can prove exposure, but cannot read host firewall rules or `docker-compose.yml` without
root — so "externally closed" is an inference, not a proof. Do not mutate anything (no
`CONFIG SET`, no writes) — audit is read-only unless the user explicitly asks for exploitation.

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
