#!/usr/bin/env python3
"""Raw protocol probes for Redis and PostgreSQL with no client binaries installed.
Read-only by design.

Redis:  PING / CONFIG GET (requirepass, protected-mode, bind) / DBSIZE / INFO commandstats.
        Sizes the REAL risk rather than stopping at "+PONG == critical": an empty DB
        (DBSIZE=0, commandstats showing only `ping` calls) is a misconfig, not a data breach.
Postgres: full SCRAM-SHA-256 client handshake to test one credential pair
        (e.g. default postgres/postgres) WITHOUT mutating anything or running SQL.

Usage:
    python3 db_probes.py redis HOST [PORT]
    python3 db_probes.py pg HOST PORT USER PASSWORD [DATABASE]
"""
import socket, struct, os, base64, hashlib, hmac, sys


def _recv(s, n=65536, maxb=20000, timeout=5):
    s.settimeout(timeout); data = b""
    while True:
        try:
            c = s.recv(n)
            if not c: break
            data += c
            if len(data) > maxb: break
        except socket.timeout:
            break
    return data


def redis_probe(host, port=6379):
    s = socket.socket(); s.settimeout(5); s.connect((host, port))
    def cmd(*a):
        parts = [str(x).encode() for x in a]
        s.sendall(b"*%d\n" % len(parts) + b"".join(b"$%d\r\n%s\r\n" % (len(p), p) for p in parts))
        return _recv(s).decode("latin1", "replace")
    print("PING           ->", cmd("PING").strip())
    print("requirepass    ->", cmd("CONFIG", "GET", "requirepass").replace("\r\n", " ")[:80])
    print("protected-mode ->", cmd("CONFIG", "GET", "protected-mode").replace("\r\n", " ")[:80])
    print("bind           ->", cmd("CONFIG", "GET", "bind").replace("\r\n", " ")[:80])
    print("DBSIZE         ->", cmd("DBSIZE").strip())
    print("commandstats   ->", cmd("INFO", "commandstats")[:400])
    s.close()


def pg_scram_auth(host, port, user, password, database="postgres"):
    s = socket.socket(); s.settimeout(8); s.connect((host, port))
    # StartupMessage = int32 length + int32 protocol(196608 = 3.0) + "user\0..\0database\0..\0\0".
    # Omitting the protocol field makes the server reject the version as garbage.
    body = b"user\x00" + user.encode() + b"\x00database\x00" + database.encode() + b"\x00\x00"
    s.sendall(struct.pack("!I", len(body) + 8) + struct.pack("!I", 196608) + body)

    def rd():
        t = s.recv(1)
        if not t: return None, b""
        ln = struct.unpack("!I", s.recv(4))[0]
        return t, s.recv(ln - 4)

    mechanisms = server_first = None
    while True:
        t, data = rd()
        if t is None: return "connection closed"
        if t == b"R":
            code = struct.unpack("!I", data[:4])[0]
            if code == 10: mechanisms = data[4:].split(b"\x00")[0].decode(); break
            if code == 0: return "trust (no password)"
            if code in (3, 5, 7): return f"auth code {code} (cleartext/md5/gss)"
        elif t == b"E":
            return "error: " + data.decode("latin1", "replace")[:120]

    nonce = base64.b64encode(os.urandom(18)).decode()
    cfb = f"n={user},r={nonce}"
    init = mechanisms.encode() + b"\x00" + struct.pack("!i", len("n,," + cfb)) + ("n,," + cfb).encode()
    s.sendall(b"p" + struct.pack("!I", len(init) + 4) + init)

    while True:
        t, data = rd()
        if t == b"R" and struct.unpack("!I", data[:4])[0] == 11:
            server_first = data[4:].decode(); break
        if t == b"E": return "error: " + data.decode("latin1", "replace")[:120]

    p = dict(x.split("=", 1) for x in server_first.split(",") if "=" in x)
    nonce = p["r"]; salt = base64.b64decode(p["s"]); iters = int(p["i"])
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    ckey = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    skey = hashlib.sha256(ckey).digest()
    cfin = f"c=biws,r={nonce}"
    amsg = f"{cfb},{server_first},{cfin}".encode()
    proof = bytes(a ^ b for a, b in zip(ckey, hmac.new(skey, amsg, hashlib.sha256).digest()))
    final = f"{cfin},p={base64.b64encode(proof).decode()}"
    s.sendall(b"p" + struct.pack("!I", len(final) + 4) + final.encode())

    while True:
        t, data = rd()
        if t == b"R" and struct.unpack("!I", data[:4])[0] == 0:
            s.close(); return "AUTH OK"
        if t == b"E":
            s.close(); return "AUTH FAILED: " + data.decode("latin1", "replace")[:150]


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "redis":
        redis_probe(a[1], int(a[2]) if len(a) > 2 else 6379)
    elif a and a[0] == "pg":
        _, host, port, user, pw = a[:5]
        print(f"{user}/{pw} ->", pg_scram_auth(host, int(port), user, pw))
    else:
        print(__doc__)
