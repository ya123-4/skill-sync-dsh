"""Package-shaped checks: the skill entry point, the docs, and the packed file list."""
import json
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = PKG_ROOT / "skills" / "skill-sync"
SKILL_MD = SKILL_DIR / "SKILL.md"

EXIT_CODES = ("0", "2", "3", "4", "5", "6", "7")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "SKILL.md must start with frontmatter"
    end = text.index("\n---", 4)
    fields = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def test_skill_frontmatter_is_valid():
    fields = frontmatter(read(SKILL_MD))
    assert fields["name"] == SKILL_DIR.name == "skill-sync"
    assert fields["description"]
    assert len(fields["description"]) < 1024


def test_skill_doc_documents_every_exit_code():
    text = read(SKILL_MD)
    for code in EXIT_CODES:
        assert f"`{code}`" in text, f"exit code {code} is not documented"


def test_skill_doc_names_the_commands():
    text = read(SKILL_MD)
    for command in ("check", "update", "verify"):
        assert command in text


def test_skill_doc_states_the_install_gate():
    text = read(SKILL_MD).lower()
    assert "profile" in text
    assert "install" in text


def test_readme_exists_in_both_languages_and_links_across():
    english = PKG_ROOT / "README.md"
    chinese = PKG_ROOT / "README.zh.md"
    assert english.is_file() and chinese.is_file()
    assert "README.zh.md" in read(english)
    assert "README.md" in read(chinese)


def test_files_field_lists_only_existing_paths():
    manifest = json.loads(read(PKG_ROOT / "package.json"))
    missing = [
        entry
        for entry in manifest["files"]
        if not (PKG_ROOT / entry).exists()
    ]
    assert missing == []


def test_license_is_present():
    assert (PKG_ROOT / "LICENSE").is_file()
    assert "MIT License" in read(PKG_ROOT / "LICENSE")


def test_third_party_notices_name_the_managed_upstreams():
    text = read(PKG_ROOT / "THIRD_PARTY_NOTICES.md")
    for repo in (
        "vercel-labs/agent-browser",
        "vercel-labs/skills",
        "obra/superpowers",
        "mattpocock/skills",
        "nexu-io/open-design",
    ):
        assert repo in text, repo


def test_known_deviations_documents_the_mattpocock_pin():
    text = read(PKG_ROOT / "KNOWN_DEVIATIONS.md")
    assert "49dd158d1076134a641b33efb035946536778336" in text
