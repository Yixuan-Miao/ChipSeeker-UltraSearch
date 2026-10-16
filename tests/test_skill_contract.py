"""Check portable instruction resources, not the model's research judgment."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/chipseeker-ultra-search"


def documents():
    return [ROOT / "README.md", *SKILL.rglob("*.md")]


def test_operating_prose_is_english_while_literal_output_names_remain_supported():
    for path in documents():
        content = path.read_text(encoding="utf-8-sig")
        prose = re.sub(r"```[\s\S]*?```", "", content)
        prose = re.sub(r"`[^`]*`", "", prose)
        assert not re.search(r"[\u3400-\u9fff]", prose), path


def test_instruction_links_resolve_inside_portable_skill():
    for path in SKILL.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            assert resolved.is_relative_to(SKILL.resolve()), (path, target)
            assert resolved.is_file(), (path, target)
