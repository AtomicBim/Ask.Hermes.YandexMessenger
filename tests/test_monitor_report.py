"""scripts/monitor_report.py: grouping of sub-agents, per-user summary, secret masking, Markdown output."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "monitor_report.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("monitor_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session(sid, user="u@example.ru", parent=None, started=1794831908.0, messages=(), **extra):
    row = {
        "id": sid, "user_id": user, "parent_session_id": parent, "chat_type": "dm", "chat_id": "c1",
        "model": "m", "title": f"title {sid}", "started_at": started, "last_activity_at": started + 60,
        "input_tokens": 100, "output_tokens": 10, "estimated_cost_usd": 0.5, "actual_cost_usd": None,
        "messages": list(messages),
    }
    row.update(extra)
    return row


def _msgs():
    return [
        {"role": "user", "content": "check y0_SECRETTOKEN1234567890 please", "timestamp": 1794831908.1},
        {"role": "session_meta", "content": None, "timestamp": 1794831908.2},
        {"role": "assistant", "content": "", "timestamp": 1794831909.0,
         "tool_calls": [{"id": "1", "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_name": "web_search", "timestamp": 1794831910.0, "content": '{"error": "timeout"}'},
        {"role": "assistant", "content": "done", "timestamp": 1794831911.0, "tool_calls": None},
    ]


def _jsonl(tmp_path, rows):
    path = tmp_path / "a.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n", encoding="utf-8")
    return path


def test_subagents_fold_under_root_and_counts(report, tmp_path):
    rows = [_session("root", messages=_msgs()), _session("sub1", parent="root", messages=_msgs()),
            _session("other", user="v@example.ru")]
    groups = report.analyse(report.load(_jsonl(tmp_path, rows)))
    assert set(groups) == {"root", "other"}
    g = groups["root"]
    assert len(g["sessions"]) == 2
    assert g["messages"] == 8          # session_meta is not counted
    assert g["tools"]["web_search"] == 2
    assert g["errors"] == 2
    assert g["in"] == 200 and abs(g["cost"] - 1.0) < 1e-9


def test_summary_per_user_and_secret_masking(report, tmp_path, capsys):
    path = _jsonl(tmp_path, [_session("root", messages=_msgs()), _session("o", user="v@example.ru")])
    assert report.main([f"alice={path}", "--tools"]) == 0
    out = capsys.readouterr().out
    assert "u@example.ru" in out and "v@example.ru" in out
    assert "web_search x1" in out

    assert report.main([f"alice={path}", "--dialog", "--user", "u@example.ru"]) == 0
    out = capsys.readouterr().out
    assert "y0_SECRETTOKEN" not in out and "[REDACTED]" in out
    assert "v@example.ru" not in out
    assert "assistant -> tools: web_search" in out


def test_markdown_output_is_masked(report, tmp_path):
    path = _jsonl(tmp_path, [_session("root", messages=_msgs())])
    dest = tmp_path / "out"
    assert report.main([f"alice={path}", "--md", str(dest), "--max-chars", "0"]) == 0
    files = list((dest / "alice" / "u@example.ru").glob("*_root.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "y0_SECRETTOKEN" not in text and "[REDACTED]" in text
    assert "assistant: done" in text


def test_missing_file_is_reported(report, tmp_path):
    assert report.main([f"alice={tmp_path / 'nope.jsonl'}"]) == 1


def test_mask_patterns(report):
    for secret in ("sk-abcdefghijklmnopqrstuv", "Bearer abcdef123456", "password: hunter2", "AQ.Ab8RN6I-yXC5Y-ig8kXEPaw"):
        assert secret not in report.mask(f"x {secret} y")
