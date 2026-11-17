#!/usr/bin/env python3
"""Export the user-made part of a Hermes agent (skills + MCP servers) into agents/<user>/, with secrets removed.

Normally started by scripts/export_agent.sh, which copies the data out of the container first:

    python scripts/export_agent.py <user> --data <dir with skills/ and config.yaml> [--bundled <dir>] [--out agents]

Exported:   skills/ (only skills that are NOT shipped with Hermes, or differ from the bundled copy),
            mcp_servers.yaml (secrets replaced by ${VAR}), env.required (names of the ${VAR} to define in .env).
Never exported: memories, sessions, state, .env, auth*.json, logs.

Exit code 1 means a secret-looking value survived the scrub: nothing must be committed in that case.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required (it is present in the Hermes venv: /opt/hermes/.venv/bin/python)")

# key names whose values are secrets
SECRET_KEY_RE = re.compile(r"(token|secret|key|password|passwd|authorization|oauth|credential|cookie|bearer)", re.I)
# values that look like secrets whatever the key is called
SECRET_VALUE_RES = [
    re.compile(r"\by0_[A-Za-z0-9_-]{8,}"),            # Yandex OAuth tokens
    re.compile(r"\bAQAAA[A-Za-z0-9_-]{8,}"),           # Yandex OAuth tokens (old format)
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bOAuth\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
]
# final guard run over every exported text file. Yandex token prefixes always count; "Bearer X" / "OAuth X" count
# only when X looks like a real token (contains a digit, is not a placeholder such as sk-xxxxxxxx), so prose like
# "OAuth provider" or "bearer token" in documentation is not a hit.
FINAL_GUARD_RE = re.compile(
    r"y0_[A-Za-z0-9_-]{8,}|AQAAA[A-Za-z0-9_-]{8,}"
    r"|(?:Bearer|OAuth)\s+(?![A-Za-z0-9._~+/=-]*(?:x{4,}|\*{3,}|<|\$\{))(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,}",
    re.IGNORECASE,
)
ENV_REF_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
SKIP_NAMES = {".env", "auth.json", "auth.lock", "__pycache__", ".git", ".DS_Store"}
SKIP_SUFFIXES = (".log", ".pyc", ".sqlite", ".db", ".sqlite3")


def _var_name(path: list[str], server: str) -> str:
    """Upper-case env var name for a secret found at ``path`` inside server ``server``."""
    parts = [server] + [p for p in path if p not in ("env", "headers", "args")]
    name = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_").upper()
    return name or "SECRET"


def _looks_secret(value: str) -> bool:
    return any(rx.search(value) for rx in SECRET_VALUE_RES)


class Scrubber:
    def __init__(self) -> None:
        self.required: dict[str, str] = {}   # VAR -> where it came from

    def _ref(self, var: str, where: str) -> str:
        self.required.setdefault(var, where)
        return "${" + var + "}"

    def scrub(self, node: Any, path: list[str], server: str) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                key_s = str(key)
                sub = path + [key_s]
                if isinstance(value, str) and not ENV_REF_RE.match(value) and (SECRET_KEY_RE.search(key_s) or _looks_secret(value)):
                    out[key] = self._ref(_var_name(sub, server), ".".join(sub))
                else:
                    out[key] = self.scrub(value, sub, server)
            return out
        if isinstance(node, list):
            return self._scrub_list(node, path, server)
        if isinstance(node, str) and _looks_secret(node) and not ENV_REF_RE.match(node):
            return self._ref(_var_name(path, server), ".".join(path))
        return node

    def _scrub_list(self, items: list, path: list[str], server: str) -> list:
        """CLI args: ``--token VALUE`` / ``--token=VALUE`` and bare secret-looking values."""
        out: list = []
        flag_secret = False
        for index, item in enumerate(items):
            if not isinstance(item, str):
                out.append(self.scrub(item, path + [str(index)], server))
                flag_secret = False
                continue
            if flag_secret and not item.startswith("-") and not ENV_REF_RE.match(item):
                out.append(self._ref(_var_name(path + [out[-1].lstrip("-")], server), ".".join(path)))
                flag_secret = False
                continue
            flag_secret = False
            if "=" in item and item.startswith("-"):
                flag, _, value = item.partition("=")
                if value and not ENV_REF_RE.match(value) and (SECRET_KEY_RE.search(flag) or _looks_secret(value)):
                    out.append(f"{flag}={self._ref(_var_name(path + [flag.lstrip('-')], server), '.'.join(path))}")
                    continue
            if item.startswith("-") and SECRET_KEY_RE.search(item):
                flag_secret = True
                out.append(item)
                continue
            if _looks_secret(item):
                out.append(self._ref(_var_name(path + [str(index)], server), ".".join(path)))
                continue
            out.append(item)
        return out


def scrub_mcp_servers(config: dict) -> tuple[dict, dict[str, str]]:
    servers = config.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        return {}, {}
    scrubber = Scrubber()
    cleaned = {name: scrubber.scrub(spec, [], str(name)) for name, spec in servers.items()}
    return cleaned, scrubber.required


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(file.relative_to(path)).replace("\\", "/").encode())
        h.update(file.read_bytes())
    return h.hexdigest()


def export_skills(src: Path, dst: Path, bundled: Path | None, include_modified_bundled: bool = False) -> list[str]:
    """Copy the user's own skills. Skills shipped with Hermes are skipped BY NAME: Hermes refreshes them in the
    data volume itself, so a byte difference does not mean the user wrote them. Returns exported relative names."""
    exported: list[str] = []
    if not src.is_dir():
        return exported
    bundled_digests: dict[str, str] = {}
    if bundled and bundled.is_dir():
        for skill_md in bundled.rglob("SKILL.md"):
            bundled_digests[skill_md.parent.name] = _digest(skill_md.parent)
    for skill_md in sorted(src.rglob("SKILL.md")):
        skill = skill_md.parent
        if any(part in SKIP_NAMES for part in skill.parts):
            continue
        if skill.name in bundled_digests and not (include_modified_bundled and bundled_digests[skill.name] != _digest(skill)):
            continue  # shipped with Hermes
        rel = skill.relative_to(src)
        target = dst / rel
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            skill, target,
            ignore=lambda _d, names: [n for n in names if n in SKIP_NAMES or n.endswith(SKIP_SUFFIXES)],
        )
        exported.append(str(rel).replace("\\", "/"))
    return exported


def guard(root: Path) -> list[str]:
    """Return ``file:line`` for every text line under ``root`` that still looks like a secret."""
    hits: list[str] = []
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if FINAL_GUARD_RE.search(line):
                hits.append(f"{file}:{number}")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("user")
    parser.add_argument("--data", required=True, type=Path, help="copy of $HERMES_HOME (needs skills/ and config.yaml)")
    parser.add_argument("--bundled", type=Path, default=None, help="directory with the skills shipped with Hermes")
    parser.add_argument("--out", type=Path, default=Path("agents"))
    parser.add_argument("--include-modified-bundled", action="store_true",
                        help="also export Hermes' own skills whose content differs from the image (normally noise)")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.user):
        sys.exit("user must match [A-Za-z0-9._-]+")
    out = args.out / args.user
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    skills = export_skills(args.data / "skills", out / "skills", args.bundled, args.include_modified_bundled)

    required: dict[str, str] = {}
    config_path = args.data / "config.yaml"
    servers: dict = {}
    if config_path.is_file():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        servers, required = scrub_mcp_servers(config if isinstance(config, dict) else {})
    if servers:
        (out / "mcp_servers.yaml").write_text(
            yaml.safe_dump({"mcp_servers": servers}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if required:
        lines = ["# Variables referenced as ${VAR} in mcp_servers.yaml; define them in .env (never commit values)"]
        lines += [f"{var}=    # {where}" for var, where in sorted(required.items())]
        (out / "env.required").write_text("\n".join(lines) + "\n", encoding="utf-8")

    hits = guard(out)
    if hits:
        shutil.rmtree(out)
        print("ERROR: secret-looking values remain in the export (removed, nothing to commit):", file=sys.stderr)
        for hit in hits:
            print("  " + hit, file=sys.stderr)
        return 1

    print(f"exported to {out}: {len(skills)} skill(s), {len(servers)} MCP server(s), {len(required)} secret variable(s)")
    for name in skills:
        print(f"  skill: {name}")
    for name in servers:
        print(f"  mcp:   {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
