#!/usr/bin/env python3
"""Build a monitoring report from `hermes sessions export --format jsonl` files (standard library only).

Normally started by scripts/monitor_agents.sh, which exports the sessions of every agent container first:

    python scripts/monitor_report.py <agent>=<file.jsonl> [<agent>=<file.jsonl> ...]
        [--user LOGIN] [--tools] [--dialog] [--md DIR] [--max-chars 400]

Without --dialog/--md: a summary per agent and user (sessions, messages, tokens, cost, top tools, errors).
--dialog prints the conversation text; --md writes one Markdown file per session into DIR/<agent>/<user>/ (mode 700/600)
and prints the summary. --max-chars 0 keeps messages whole.
Sub-agent sessions (parent_session_id) are folded under their root session. Secrets are masked in all output.
Read-only: the data comes from an export, state.db is never opened.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Same families as scripts/export_agent.py; kept local so the monitor has no import dependency on it.
MASK_RES = [
    re.compile(r"\by0_[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAQAAA[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAQ\.[A-Za-z0-9_-]{16,}"),              # Google API keys
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bOAuth\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"(?i)\b(password|passwd|pwd|пароль)\s*[:=]\s*\S+"),
]
ERROR_RE = re.compile(r"(?i)\"(error|exception|traceback)\"\s*:|^\s*(error|exception)[: ]|traceback \(most recent")


def mask(text: str) -> str:
    for rx in MASK_RES:
        text = rx.sub("[REDACTED]", text)
    return text


def load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id"):
                rows.append(row)
    return rows


def ts(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def root_of(session: dict, by_id: dict[str, dict]) -> str:
    """Id of the top-level session (sub-agents and continuations roll up into it)."""
    seen = set()
    cur = session
    while cur.get("parent_session_id") and cur["parent_session_id"] in by_id and cur["id"] not in seen:
        seen.add(cur["id"])
        cur = by_id[cur["parent_session_id"]]
    return cur["id"]


def tool_calls_of(message: dict) -> list[str]:
    names = []
    calls = message.get("tool_calls")
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except json.JSONDecodeError:
            calls = None
    for call in calls or []:
        fn = (call or {}).get("function") or {}
        if fn.get("name"):
            names.append(str(fn["name"]))
    return names


def visible_messages(session: dict) -> list[dict]:
    return [m for m in session.get("messages") or [] if m.get("role") in ("user", "assistant", "tool")]


def analyse(rows: list[dict]) -> dict[str, dict]:
    """Group sessions by root; returns root_id -> aggregate."""
    by_id = {r["id"]: r for r in rows}
    groups: dict[str, dict] = {}
    for row in rows:
        rid = root_of(row, by_id)
        root = by_id[rid]
        g = groups.setdefault(rid, {
            "root": root, "sessions": [], "tools": Counter(), "errors": 0, "messages": 0,
            "in": 0, "out": 0, "cost": 0.0, "started": None, "last": None, "user_msgs": 0,
        })
        g["sessions"].append(row)
        g["in"] += int(row.get("input_tokens") or 0)
        g["out"] += int(row.get("output_tokens") or 0)
        g["cost"] += float(row.get("actual_cost_usd") or row.get("estimated_cost_usd") or 0)
        started, last = row.get("started_at"), row.get("last_activity_at") or row.get("ended_at") or row.get("started_at")
        if started and (g["started"] is None or started < g["started"]):
            g["started"] = started
        if last and (g["last"] is None or last > g["last"]):
            g["last"] = last
        for m in row.get("messages") or []:
            role = m.get("role")
            if role in ("user", "assistant", "tool"):
                g["messages"] += 1
            if role == "user":
                g["user_msgs"] += 1
            if role == "assistant":
                g["tools"].update(tool_calls_of(m))
            if role == "tool" and isinstance(m.get("content"), str) and ERROR_RE.search(m["content"][:600]):
                g["errors"] += 1
    return groups


def user_of(group: dict) -> str:
    return str(group["root"].get("user_id") or "?")


def summary(agent: str, groups: dict[str, dict], show_tools: bool, out) -> None:
    per_user: dict[str, list[dict]] = defaultdict(list)
    for g in groups.values():
        per_user[user_of(g)].append(g)
    print(f"\n=== {agent}: {len(groups)} session(s), {sum(len(g['sessions']) for g in groups.values())} incl. sub-agents ===", file=out)
    if not groups:
        return
    for user, items in sorted(per_user.items()):
        tools = Counter()
        for g in items:
            tools.update(g["tools"])
        last = max((g["last"] or 0) for g in items)
        print(f"\n  {user}   last active {ts(last)}", file=out)
        print(f"    sessions {len(items)}, messages {sum(g['messages'] for g in items)}, "
              f"tool calls {sum(tools.values())}, tool errors {sum(g['errors'] for g in items)}, "
              f"tokens {sum(g['in'] for g in items)}/{sum(g['out'] for g in items)} (in/out), "
              f"cost ${sum(g['cost'] for g in items):.3f}", file=out)
        if show_tools and tools:
            print("    tools: " + ", ".join(f"{n} x{c}" for n, c in tools.most_common(10)), file=out)
        for g in sorted(items, key=lambda x: x["started"] or 0):
            root = g["root"]
            title = mask(str(root.get("title") or "-"))[:60]
            sub = len(g["sessions"]) - 1
            print(f"      {ts(g['started'])}  {root.get('chat_type') or '-':5}  msgs {g['messages']:4}  "
                  f"tools {sum(g['tools'].values()):3}  err {g['errors']}  {title}"
                  + (f"  (+{sub} sub-agent)" if sub else "") + f"  [{root['id']}]", file=out)


def render_dialog(group: dict, max_chars: int) -> Iterable[str]:
    root = group["root"]
    yield f"# {mask(str(root.get('title') or root['id']))}"
    yield f"user: {root.get('user_id')}  chat: {root.get('chat_type')} {root.get('chat_id')}  model: {root.get('model')}"
    yield f"started: {ts(group['started'])}  sessions: {len(group['sessions'])}  id: {root['id']}"
    yield ""
    for sess in sorted(group["sessions"], key=lambda s: s.get("started_at") or 0):
        if sess["id"] != root["id"]:
            yield f"--- sub-session {sess['id']} ({mask(str(sess.get('title') or ''))}) ---"
        for m in visible_messages(sess):
            when = ts(m.get("timestamp"))
            role = m["role"]
            content = m.get("content")
            text = mask(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            if role == "assistant":
                calls = tool_calls_of(m)
                if calls:
                    yield f"[{when}] assistant -> tools: {', '.join(calls)}"
                if not text.strip():
                    continue
            if role == "tool":
                text = f"({m.get('tool_name') or 'tool'}) {text}"
            if max_chars and len(text) > max_chars:
                text = text[:max_chars] + f"... [+{len(text) - max_chars} chars]"
            yield f"[{when}] {role}: {text}"
    yield ""


def write_md(agent: str, groups: dict[str, dict], dest: Path, max_chars: int) -> int:
    base = dest / agent
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
        os.chmod(base, 0o700)
    except OSError:
        pass
    count = 0
    for rid, g in groups.items():
        # <user>/ keeps one person's chats together; the id keeps the name unique and greppable.
        user_dir = base / re.sub(r"[^A-Za-z0-9._@-]+", "_", user_of(g))
        user_dir.mkdir(exist_ok=True)
        try:
            os.chmod(user_dir, 0o700)
        except OSError:
            pass
        stamp = (ts(g["started"]) or "-").replace(" ", "_").replace(":", "")
        path = user_dir / f"{stamp}_{rid}.md"
        path.write_text("\n".join(render_dialog(g, max_chars)), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", metavar="AGENT=FILE", help="agent name and its exported JSONL file")
    ap.add_argument("--user", help="only this user_id (login)")
    ap.add_argument("--tools", action="store_true", help="list the most used tools per user")
    ap.add_argument("--dialog", action="store_true", help="print the conversations")
    ap.add_argument("--md", metavar="DIR", help="write one Markdown file per session into DIR/<agent>/")
    ap.add_argument("--max-chars", type=int, default=400, help="truncate one message in --dialog/--md (0 = no limit)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    exit_code = 0
    for item in args.inputs:
        agent, _, file = item.partition("=")
        if not file:
            print(f"bad argument '{item}', expected AGENT=FILE", file=sys.stderr)
            return 2
        path = Path(file)
        if not path.is_file():
            print(f"warning: {agent}: no export file {path}", file=sys.stderr)
            exit_code = 1
            continue
        groups = analyse(load(path))
        if args.user:
            groups = {k: g for k, g in groups.items() if user_of(g) == args.user}
        if args.md:
            summary(agent, groups, True, sys.stdout)
            print(f"\n{agent}: wrote {write_md(agent, groups, Path(args.md), args.max_chars)} chat file(s) "
                  f"to {Path(args.md) / agent}/<user>/")
            continue
        if args.dialog:
            print(f"\n=== {agent} ===")
            for g in sorted(groups.values(), key=lambda x: x["started"] or 0):
                print("\n".join(render_dialog(g, args.max_chars)))
            continue
        summary(agent, groups, args.tools, sys.stdout)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
