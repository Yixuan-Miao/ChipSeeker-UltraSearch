#!/usr/bin/env python3
"""Install the remote ChipSeeker Skill with Python's standard library."""

import argparse
import os
import shutil
import uuid
from pathlib import Path


SKILL_NAME = "chipseeker-ultra-search"
SOURCE = Path(__file__).resolve().parent / "skills" / SKILL_NAME


def install_skill(client="codex", skills_dir=None):
    if skills_dir:
        root = Path(skills_dir).expanduser().resolve()
    elif client == "claude-code":
        root = Path.home() / ".claude" / "skills"
    else:
        root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "skills"
    if not (SOURCE / "SKILL.md").is_file() or not (SOURCE / "scripts" / "chipseeker_remote.py").is_file():
        raise ValueError("The downloaded repository is incomplete. Download the full repository before installing.")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / SKILL_NAME
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError(f"The target is not a regular Skill directory: {destination}")
    staged = root / f".{SKILL_NAME}.install-{uuid.uuid4().hex}"
    shutil.copytree(SOURCE, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    backup = None
    if destination.exists():
        backup = root / f".{SKILL_NAME}.backup-{uuid.uuid4().hex}"
        destination.rename(backup)
    try:
        staged.rename(destination)
    except OSError:
        if backup and not destination.exists():
            backup.rename(destination)
        raise
    return destination, backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("codex", "claude-code"), default="codex")
    parser.add_argument("--skills-dir", default="")
    args = parser.parse_args()
    try:
        destination, backup = install_skill(args.client, args.skills_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")
    print(f"Installed: {destination}")
    if backup:
        print(f"Previous version backed up: {backup}")
    print(f"Read and follow: {destination / 'SKILL.md'}")


if __name__ == "__main__":
    main()
