#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write blender/gamestudio/core/skill_history.json: the hash of every past version of every built-in
skill (project_templates/*/.claude/skills/<name>/), from this repository's git history. Hashes already
in the file are kept, so a shallow clone or a squashed history never drops a version.

    python tools/skill_history.py           # regenerate (tools/build_extension.py runs it too)
    python tools/skill_history.py --check   # exit 1 when the file is out of date

A project made with an older GameStudio holds those versions. core/skills.py recognises them as
GameStudio's own, unedited copies, so they follow the new built-in version instead of counting as the
user's. Hashes are core/skills.digest of the skill's files, as git stores them.
"""

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = "blender/gamestudio/project_templates"
OUT = REPO / "blender" / "gamestudio" / "core" / "skill_history.json"
NOTE = ("Hashes of every past version of GameStudio's built-in skills (tools/skill_history.py). A project's "
        "skill that matches one is GameStudio's unedited copy and follows the current version.")


def _skills_module():
    spec = importlib.util.spec_from_file_location("gs_skills", REPO / "blender" / "gamestudio" / "core" / "skills.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=True).stdout


def history() -> dict[str, list[str]]:
    """Skill name -> sorted hashes of its past versions (every template that ships it)."""
    digest = _skills_module().digest
    found: dict[str, set[str]] = {}
    for commit in _git("log", "--format=%H", "--", TEMPLATES).decode().split():
        listing = _git("ls-tree", "-r", "--name-only", commit, "--", TEMPLATES).decode().splitlines()
        skills: dict[tuple[str, str], dict[str, str]] = {}
        for path in listing:
            parts = path[len(TEMPLATES) + 1:].split("/")
            # <template>/.claude/skills/<name>/<file...>
            if len(parts) >= 5 and parts[1:3] == [".claude", "skills"]:
                skills.setdefault((parts[0], parts[3]), {})["/".join(parts[4:])] = path
        for (_template, name), files in skills.items():
            if "SKILL.md" not in files:
                continue
            data = {rel: _git("show", f"{commit}:{path}") for rel, path in files.items()}
            found.setdefault(name, set()).add(digest(data))
    return {name: sorted(hashes) for name, hashes in sorted(found.items())}


def recorded(text: str) -> dict[str, list[str]]:
    """The hashes the file already holds (none when it is missing or unreadable)."""
    try:
        skills = json.loads(text).get("skills", {}) if text else {}
    except ValueError:
        return {}
    return skills if isinstance(skills, dict) else {}


def merge(*sources: dict[str, list[str]]) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for source in sources:
        for name, hashes in source.items():
            found.setdefault(name, set()).update(hashes)
    return {name: sorted(hashes) for name, hashes in sorted(found.items())}


def render(data: dict[str, list[str]]) -> str:
    return json.dumps({"note": NOTE, "skills": data}, indent=1) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether the file is current")
    args = parser.parse_args(argv)
    try:
        found = history()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[skill_history] git history unavailable ({exc}); {OUT.name} left as it is")
        return 1 if args.check else 0
    current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
    text = render(merge(recorded(current), found))
    if args.check:
        if current != text:
            print(f"[skill_history] {OUT.relative_to(REPO)} is out of date: run python tools/skill_history.py")
            return 1
        return 0
    if current != text:
        OUT.write_bytes(text.encode("utf-8"))
        print(f"[skill_history] wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
