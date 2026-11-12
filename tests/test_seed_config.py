"""docker/apply_config.py: merge + flatten of the seed files, CLI value encoding, repo seed sanity."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def apply_config():
    spec = importlib.util.spec_from_file_location("apply_config", ROOT / "docker" / "apply_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deep_merge_and_flatten(apply_config):
    base = {"stt": {"enabled": True, "provider": "groq"}, "model": {"default": "a"}}
    overlay = {"stt": {"prompt": "Иван, BIM"}, "model": {"default": "b"}}
    merged = apply_config.deep_merge(base, overlay)
    assert merged == {"stt": {"enabled": True, "provider": "groq", "prompt": "Иван, BIM"}, "model": {"default": "b"}}
    assert base["model"]["default"] == "a"  # inputs are not mutated
    assert dict(apply_config.flatten({"a": {"b": {"c": 1}}, "d": [1, 2], "e": {}})) == {"a.b.c": 1, "d": [1, 2], "e": {}}


def test_cli_value_encoding(apply_config):
    enc = apply_config.to_cli_value
    assert enc(True) == "true" and enc(False) == "false" and enc(250) == "250"
    assert enc("~deepseek/x") == "~deepseek/x" and enc("Роман, Катя") == "Роман, Катя"
    assert enc(["a", "б"]) == '["a", "б"]' and enc(None) == ""


def test_repo_seed_is_valid_and_has_no_private_data(apply_config):
    tree = apply_config.load([ROOT / "config" / "hermes.yaml"])
    leaves = dict(apply_config.flatten(tree))
    assert leaves["model.provider"] == "openrouter" and leaves["stt.enabled"] is True
    assert leaves["tts.gemini.model"] and leaves["auxiliary.vision.model"] and leaves["image_gen.model"]
    assert "stt.prompt" not in leaves  # the committed seed must not carry names/vocabulary (public repo)
    assert not any(any(s in k for s in ("key", "token", "secret")) for k in leaves)  # no credentials in the seed


def test_overlay_wins_and_unreadable_input_is_reported(apply_config, tmp_path, capsys):
    (tmp_path / "a.yaml").write_text("stt:\n  prompt: base\n  enabled: true\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("stt:\n  prompt: private\n", encoding="utf-8")
    merged = apply_config.load([tmp_path / "a.yaml", tmp_path / "b.yaml"])
    assert merged == {"stt": {"prompt": "private", "enabled": True}}
    assert apply_config.main(["--dry-run", str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")]) == 0
    out = capsys.readouterr().out
    assert "stt.prompt = private" in out and "stt.enabled = true" in out
    (tmp_path / "bad.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    assert apply_config.main(["--dry-run", str(tmp_path / "bad.yaml")]) == 2
    assert apply_config.main(["--dry-run", str(tmp_path / "missing.yaml")]) == 2
