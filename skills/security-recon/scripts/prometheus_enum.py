# Enumerate everything an unauthenticated Prometheus leaks. Read-only.
# Usage: python3 prometheus_enum.py <prometheus_base_url>   e.g. http://172.17.0.1:9090
import sys, json, urllib.request, urllib.parse

BASE = sys.argv[1].rstrip('/')

def api(path, **params):
    q = urllib.parse.urlencode(params)
    url = f"{BASE}{path}" + (f"?{q}" if q else "")
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())

def query(q):
    return api("/api/v1/query", query=q)

def get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode()

# 1. version + scrape config + runtime
b = api("/api/v1/status/buildinfo")["data"]
print("[buildinfo]", b.get("version"), b.get("goVersion"))
print("[config]")
print(api("/api/v1/status/config")["data"]["yaml"])
rt = api("/api/v1/status/runtimeinfo")["data"]
print("[runtime]", rt.get("hostname"), rt.get("CWD"), rt.get("storageRetention"))

# 2. scrape targets (sibling compose-service names + health)
print("[targets]")
for t in api("/api/v1/targets")["data"]["activeTargets"]:
    l = t["labels"]
    err = (t.get("lastError") or "")[:70]
    print(f"  job={l.get('job')} instance={l.get('instance')} health={t.get('health')} {err}")

# 3. FULL host container registry via cAdvisor — name + image for every container on the host
try:
    print("[containers via cAdvisor]")
    rows = []
    for r in query("count by (name, image) (container_last_seen)")["data"]["result"]:
        m = r["metric"]
        rows.append((m.get("name", "?"), m.get("image", "?")))
    for name, img in sorted(rows):
        print(f"  {name:35s} {img}")
    print(f"  TOTAL: {len(rows)}")
except Exception as e:
    print("[cAdvisor not scraped / query failed]", e)

# 4. host identity + hardware
for r in query("node_uname_info")["data"]["result"]:
    m = r["metric"]
    print("[node_uname]", m.get("nodename"), m.get("release"), m.get("version"))
for r in query("machine_memory_bytes")["data"]["result"]:
    print("[machine_memory_bytes]", r["value"][1])

# 5. full metric-name inventory (what the stack monitors)
names = api("/api/v1/label/__name__/values")["data"]
print(f"[metric names] {len(names)} total; prefixes:")
from collections import Counter
pfx = Counter(n.split('_', 1)[0] for n in names if '_' in n)
print("  ", dict(pfx.most_common(15)))
