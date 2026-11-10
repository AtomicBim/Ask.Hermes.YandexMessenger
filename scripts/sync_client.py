#!/usr/bin/env python3
"""Copy ``yandex_messenger_client`` from the base library into the plugin's ``_vendor`` dir.

Usage:
    python scripts/sync_client.py                # copy (overwrite)
    python scripts/sync_client.py --check        # exit 1 when the copy differs from the source
    python scripts/sync_client.py --source PATH  # explicit source package dir

Default source: ``$YM_CLIENT_SOURCE`` or the sibling checkout
``../yandex_bot_example/yandex_messenger_client``.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "plugins" / "platforms" / "yandex_messenger" / "_vendor" / "yandex_messenger_client"
DEFAULT_SOURCE = ROOT.parent / "yandex_bot_example" / "yandex_messenger_client"


def package_files(directory: Path) -> dict:
    return {
        str(p.relative_to(directory)).replace("\\", "/"): p
        for p in sorted(directory.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


def tree_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for rel, path in package_files(directory).items():
        digest.update(rel.encode())
        # normalise line endings so a Windows checkout compares equal to a Linux one
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.environ.get("YM_CLIENT_SOURCE") or str(DEFAULT_SOURCE))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    if not (source / "__init__.py").is_file():
        print(f"source package not found: {source}", file=sys.stderr)
        return 2
    if args.check:
        if not TARGET.is_dir():
            print("vendored copy missing; run scripts/sync_client.py", file=sys.stderr)
            return 1
        same = tree_hash(source) == tree_hash(TARGET)
        print("vendored client is up to date" if same else "vendored client DIFFERS from source")
        return 0 if same else 1

    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for rel, path in package_files(source).items():
        dest = TARGET / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # write LF endings regardless of the checkout platform
        dest.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    print(f"synced {len(package_files(source))} files -> {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
