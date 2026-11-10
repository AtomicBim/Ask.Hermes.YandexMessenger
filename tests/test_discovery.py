"""The plugin is found, gated and loaded by the REAL Hermes plugin manager from a user plugin dir."""
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "plugins" / "platforms" / "yandex_messenger"


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / "home"
    (home / "plugins" / "platforms").mkdir(parents=True)
    shutil.copytree(SRC, home / "plugins" / "platforms" / "yandex_messenger",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return home


def run_probe(home: Path, config_yaml: str, token: str = "") -> dict:
    """Discover plugins in a fresh interpreter (the plugin manager is process-global state)."""
    (home / "config.yaml").write_text(config_yaml)
    env = {"HERMES_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}
    import os

    for key in ("PYTHONPATH", "HERMES_REPO", "HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    if token:
        env["YANDEX_MESSENGER_BOT_TOKEN"] = token
    code = textwrap.dedent(
        """
        import json
        from hermes_cli.plugins import discover_plugins, get_plugin_manager
        from gateway.platform_registry import platform_registry
        discover_plugins(force=True)
        mgr = get_plugin_manager()
        entry = platform_registry.get("yandex_messenger")
        loaded = {k: getattr(v, "error", None) for k, v in mgr._plugins.items() if "yandex" in k}
        out = {"registered": entry is not None, "plugins": loaded}
        if entry is not None:
            out.update(label=entry.label, source=entry.source, plugin_name=entry.plugin_name,
                       max_len=entry.max_message_length, check=bool(entry.check_fn()))
            from gateway.config import GatewayConfig, Platform
            cfg = GatewayConfig()
            for step in entry.env_overrides or ():
                step(cfg)
            out["enabled_from_env"] = Platform("yandex_messenger") in cfg.platforms
        print("PROBE=" + json.dumps(out))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(home))
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE=")]
    assert lines, f"probe failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr[-3000:]}"
    return json.loads(lines[-1][len("PROBE="):])


def test_enabled_plugin_registers_the_platform(hermes_home):
    out = run_probe(hermes_home, "plugins:\n  enabled:\n    - platforms/yandex_messenger\n", token="tok")
    assert out["registered"] is True, out
    assert out["label"] == "Yandex Messenger" and out["source"] == "plugin" and out["max_len"] == 6000
    assert out["check"] is True and out["enabled_from_env"] is True


def test_plugin_is_opt_in(hermes_home):
    out = run_probe(hermes_home, "plugins:\n  enabled: []\n", token="tok")
    assert out["registered"] is False, out  # user plugins load only when listed in plugins.enabled
