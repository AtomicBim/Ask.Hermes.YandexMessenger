"""skills/: shared skills baked into the image. The repository is PUBLIC: nothing personal or secret may live here."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
EXPECTED = {"yandex-disk", "yandex-calendar", "imap-mail-search", "security-recon"}
# personal data / secret shapes (the same families the export script guards against, plus corporate identifiers)
FORBIDDEN = re.compile(
    r"y0_[A-Za-z0-9_-]{8,}|AQAAA[A-Za-z0-9_-]{8,}|(?:Bearer|OAuth)\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,}"
    r"|[A-Za-z0-9._%+-]+@(?!domain\b|example\b)[A-Za-z0-9-]+\.(?:ru|com|org|net)\b|atomsk|atomstroy"
    # only the agreed credential files of the three connectors may be named; anything else under .secrets is personal
    r"|\.secrets/(?!yandex-(?:mail|calendar|disk)\.json\b)",
    re.IGNORECASE,
)


def _skills():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no front matter"
    block = text.split("\n---", 1)[0]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.MULTILINE))


def test_expected_skills_are_present():
    assert {p.name for p in _skills()} == EXPECTED


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.name)
def test_skill_is_well_formed(skill):
    meta = _front_matter(skill / "SKILL.md")
    assert meta["name"].strip().strip('"') == skill.name  # Hermes looks the skill up by name
    assert meta.get("description", "").strip().strip('"')


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.name)
def test_skill_has_no_secrets_or_personal_data(skill):
    for file in skill.rglob("*"):
        if file.is_file():
            for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                assert not FORBIDDEN.search(line), f"{file.relative_to(ROOT)}:{number}: {line[:100]}"


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.name)
def test_skill_references_exist(skill):
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    for ref in re.findall(r"`(references/[\w./-]+)`", text):
        assert (skill / ref).is_file(), f"{skill.name}: missing {ref}"


def test_connector_skills_use_the_file_names_the_onboarding_checks(mod):
    """The welcome message decides 'connected' by the access file name: skill and plugin must agree."""
    expected = {"imap-mail-search": "yandex-mail.json", "yandex-calendar": "yandex-calendar.json",
                "yandex-disk": "yandex-disk.json"}
    for skill, filename in expected.items():
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert f".secrets/{filename}" in text, f"{skill} must tell the agent to store access in .secrets/{filename}"
    from pathlib import Path as _P
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        home = _P(tmp)
        (home / ".secrets").mkdir()
        for filename in expected.values():
            (home / ".secrets" / filename).write_text("{}x", encoding="utf-8")
        assert mod.onboarding.connection_status(home).complete


def test_image_and_init_script_ship_the_skills():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "skills/ /opt/yandex-messenger-plugin/skills/" in dockerfile
    init = (ROOT / "docker" / "cont-init-yandex-messenger.sh").read_text(encoding="utf-8")
    assert "/opt/yandex-messenger-plugin/skills" in init and 'chown -R hermes:hermes "$SKILLS_DEST"' in init
    assert "\r" not in init  # CRLF would break the shebang
