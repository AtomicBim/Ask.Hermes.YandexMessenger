#!/usr/bin/env python3
"""Apply seed YAML files to Hermes' config through its public CLI (`hermes config set <dotted.key> <value>`).

Usage (inside the container):  apply_config.py [--dry-run] SEED.yaml [OVERLAY.yaml ...]

* Later files win; dicts are merged recursively, every other value replaces.
* Only the listed leaves are written; the rest of config.yaml (and its comments) stays as Hermes keeps it.
* Exit code is 0 even when single keys fail (they are reported); 2 on unreadable input.
Runs with the Hermes venv python (parses with Hermes' own ``hermes_yaml``).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator, Tuple

try:  # Hermes' own wrapper (the sealed image venv ships ruamel, not PyYAML)
    import hermes_yaml as yaml
except ImportError:  # outside Hermes (tests on a plain interpreter)
    import yaml


def deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def flatten(tree: dict, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    for key, value in tree.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            yield from flatten(value, dotted)
        else:
            yield dotted, value


def to_cli_value(value: Any) -> str:
    """The string `hermes config set` parses back into the same YAML scalar/list."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def load(paths: list) -> dict:
    merged: dict = {}
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level must be a mapping")
        merged = deep_merge(merged, data)
    return merged


def main(argv: list) -> int:
    dry = "--dry-run" in argv
    paths = [a for a in argv if a != "--dry-run"]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        tree = load(paths)
    except Exception as exc:  # unreadable file, invalid YAML, wrong shape
        print(f"[apply-config] cannot read seed: {exc}", file=sys.stderr)
        return 2
    failed = 0
    for key, value in flatten(tree):
        shown = "<hidden>" if any(s in key.lower() for s in ("key", "token", "secret", "password")) else to_cli_value(value)
        if dry:
            print(f"{key} = {shown}")
            continue
        proc = subprocess.run(["hermes", "config", "set", key, to_cli_value(value)], capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"[apply-config] {key} = {shown}")
        else:
            failed += 1
            print(f"[apply-config] FAILED {key}: {(proc.stderr or proc.stdout).strip()[:200]}", file=sys.stderr)
    if failed:
        print(f"[apply-config] {failed} key(s) were not applied", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
