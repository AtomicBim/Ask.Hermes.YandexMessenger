"""scripts/export_agent.py: secrets never reach agents/<user>/, bundled skills are skipped."""
import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # present in the Hermes image venv; optional for the host test runner

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_agent.py"


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("export_agent", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data(tmp_path, config):
    data = tmp_path / "data"
    (data / "skills" / "mine").mkdir(parents=True)
    (data / "skills" / "mine" / "SKILL.md").write_text("# mine\nuse the tracker\n", encoding="utf-8")
    (data / "skills" / "shipped").mkdir()
    (data / "skills" / "shipped" / "SKILL.md").write_text("# shipped\n", encoding="utf-8")
    (data / "memories").mkdir()
    (data / "memories" / "m.md").write_text("private y0_MEMORYSECRET123456", encoding="utf-8")
    (data / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    bundled = tmp_path / "bundled" / "shipped"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text("# shipped\n", encoding="utf-8")
    return data, tmp_path / "bundled"


CONFIG = {
    "model": {"default": "x"},
    "mcp_servers": {
        "yandex-tracker": {
            "command": "npx",
            "args": ["-y", "tracker-mcp", "--token", "y0_FAKE1234567890abcdef", "--org=123"],
            "env": {"TRACKER_TOKEN": "y0_FAKE1234567890abcdef", "TRACKER_ORG": "123", "DISK_KEY": "${ALREADY_REF}"},
        },
        "remote": {"url": "https://mcp.example/sse", "headers": {"Authorization": "Bearer abcdefgh12345678"}},
    },
}


def test_export_scrubs_secrets_and_skips_bundled(exporter, tmp_path):
    data, bundled = _data(tmp_path, CONFIG)
    out = tmp_path / "agents"
    assert exporter.main(["alice", "--data", str(data), "--bundled", str(bundled), "--out", str(out)]) == 0
    root = out / "alice"
    assert (root / "skills" / "mine" / "SKILL.md").exists()
    assert not (root / "skills" / "shipped").exists()          # shipped with Hermes
    assert not (root / "memories").exists()
    text = (root / "mcp_servers.yaml").read_text(encoding="utf-8")
    assert "y0_FAKE" not in text and "abcdefgh12345678" not in text
    servers = yaml.safe_load(text)["mcp_servers"]
    tracker = servers["yandex-tracker"]
    assert tracker["env"]["TRACKER_TOKEN"].startswith("${") and tracker["env"]["TRACKER_ORG"] == "123"
    assert tracker["env"]["DISK_KEY"] == "${ALREADY_REF}"
    assert tracker["args"][:3] == ["-y", "tracker-mcp", "--token"] and tracker["args"][3].startswith("${")
    assert tracker["args"][4] == "--org=123"
    assert servers["remote"]["headers"]["Authorization"].startswith("${")
    required = (root / "env.required").read_text(encoding="utf-8")
    assert "TRACKER_TOKEN" in required and "y0_" not in required


def test_guard_blocks_secret_inside_a_skill(exporter, tmp_path, capsys):
    data, bundled = _data(tmp_path, CONFIG)
    (data / "skills" / "mine" / "SKILL.md").write_text("curl -H 'Authorization: OAuth y0_FAKE1234567890abcdef'", encoding="utf-8")
    out = tmp_path / "agents"
    assert exporter.main(["alice", "--data", str(data), "--bundled", str(bundled), "--out", str(out)]) == 1
    assert not (out / "alice").exists()  # nothing is left to commit
    assert "SKILL.md" in capsys.readouterr().err


def test_documentation_prose_is_not_a_secret(exporter):
    prose = [
        "hermes auth - re-authenticate OAuth providers (or hermes auth add <provider>)",
        "Hermes uses as a bearer. It is not a public OAuth provider that Karakeep",
        'Authorization: "Bearer sk-xxxxxxxxxxxxxxxxxxxx"',
        "a local OpenAI API backed by whichever OAuth provider you're signed into.",
        "Authorization: Bearer ${TRACKER_TOKEN}",
    ]
    for line in prose:
        assert not exporter.FINAL_GUARD_RE.search(line), line
    for line in ("Authorization: Bearer abcd1234efgh5678ijkl", "OAuth AQAAAAAexample1234567", "token y0_FAKE1234567890abcdef"):
        assert exporter.FINAL_GUARD_RE.search(line), line


def test_modified_bundled_skill_is_not_exported(exporter, tmp_path):
    data, bundled = _data(tmp_path, CONFIG)
    (data / "skills" / "shipped" / "SKILL.md").write_text("# shipped, refreshed by Hermes in the volume\n", encoding="utf-8")
    out = tmp_path / "agents"
    assert exporter.main(["alice", "--data", str(data), "--bundled", str(bundled), "--out", str(out)]) == 0
    assert not (out / "alice" / "skills" / "shipped").exists() and (out / "alice" / "skills" / "mine").exists()


def test_user_name_is_validated(exporter, tmp_path):
    with pytest.raises(SystemExit):
        exporter.main(["../evil", "--data", str(tmp_path), "--out", str(tmp_path / "agents")])
