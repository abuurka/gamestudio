# SPDX-License-Identifier: GPL-3.0-or-later
"""A project's built-in skills: the skill folders GameStudio put in .claude/skills/, kept current.

New Project copies the skills of the project's template into .claude/skills/<name>/. Later versions
of GameStudio change those skills and add new ones (design-ui came with the game UI), while a
project keeps what it was made with. update_skills(root, builtin) brings it up to date when the
project opens, never at the expense of the user's own work. RECORD (.claude/skills/
.gamestudio-skills.json) holds, for each built-in skill, the hash of its files as GameStudio last
wrote them or found them unchanged, or null when the project had its own skill of that name:

* a built-in skill the project lacks is added, unless the record names it (the user deleted it);
* a skill whose files still hash as recorded (nobody edited it) follows the built-in version;
* a skill the user edited or wrote is never touched;
* a project without a record (made before it existed) adopts the skills identical to the built-in
  ones; a different one is the user's;
* a skill identical to a past built-in version (HISTORY, made from git by tools/skill_history.py) is
  GameStudio's unedited copy from an older version, recorded or not: it follows the built-in version.

Hashes read text line ends as \\n (git on Windows may check files out with CRLF) and skip the files
Finder and Explorer leave behind. The record belongs in version control with the skills: a clone
keeps knowing which skills the user deleted. No bpy here: claude_code.py renders the template's
skills (the builtin argument) and calls this.
"""

import hashlib
import json
import os
import pathlib
import shutil

SKILLS = ".claude/skills"
RECORD = ".gamestudio-skills.json"           # in SKILLS
SKILL_FILE = "SKILL.md"
HISTORY = pathlib.Path(__file__).with_name("skill_history.json")
IGNORED = {"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini"}
_NOTE = ("GameStudio's record of the built-in skills in this folder: the hash of each skill's files as GameStudio "
         "last wrote them (null: the project had a skill of its own by that name). A skill that still matches "
         "follows GameStudio's updates; one you edit or delete is left as it is.")


def digest(files: dict[str, bytes]) -> str:
    """A hash of a skill's files (POSIX path inside the skill folder -> contents). A text file's line
    ends count as \\n."""
    hasher = hashlib.sha256()
    for rel in sorted(files):
        data = files[rel]
        if b"\0" not in data:  # text
            data = data.replace(b"\r\n", b"\n")
        hasher.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    return hasher.hexdigest()


def folder_files(folder) -> dict[str, bytes]:
    """A skill folder's files, as digest() takes them (IGNORED aside). Raises OSError."""
    folder = pathlib.Path(folder)
    found = {}
    for path in folder.rglob("*"):
        rel = path.relative_to(folder)
        if not IGNORED & set(rel.parts) and path.is_file():
            found[rel.as_posix()] = path.read_bytes()
    return found


_history: dict | None = None


def past_versions() -> dict[str, frozenset]:
    """Skill name -> hashes of its past built-in versions (empty when HISTORY cannot be read)."""
    global _history
    if _history is None:
        try:
            data = json.loads(HISTORY.read_text(encoding="utf-8")).get("skills", {})
            _history = {str(name): frozenset(h for h in hashes if isinstance(h, str))
                        for name, hashes in data.items() if isinstance(hashes, list)}
        except (OSError, ValueError, AttributeError):
            _history = {}
    return _history


def read_record(root) -> dict | None:
    """{skill name: hash, or None for the project's own} from RECORD, or None when there is none
    (or it cannot be read: then the project is treated as one made before it existed)."""
    try:
        data = json.loads((pathlib.Path(root) / SKILLS / RECORD).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        return None
    return {str(name): value for name, value in skills.items() if value is None or isinstance(value, str)}


def _write_record(root: pathlib.Path, record: dict) -> None:
    path = root / SKILLS / RECORD
    text = json.dumps({"note": _NOTE, "skills": dict(sorted(record.items()))}, indent=2) + "\n"
    tmp = path.with_name(path.name + ".gamestudio-tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_skill(dest: pathlib.Path, files: dict[str, bytes]) -> None:
    """Write a skill folder (replacing dest, an unedited copy) through a temporary folder: a failure
    leaves dest as it was."""
    tmp = dest.with_name(f".{dest.name}.gamestudio-tmp")
    old = dest.with_name(f".{dest.name}.gamestudio-old")
    for leftover in (tmp, old):
        shutil.rmtree(leftover, ignore_errors=True)
    try:
        for rel, data in files.items():
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if dest.exists():
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if old.exists() and not dest.exists():
                os.replace(old, dest)
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)


def update_skills(root, builtin: dict[str, dict[str, bytes]], history: dict | None = None) -> dict:
    """Bring the project's built-in skills up to date (see the module docstring). builtin: the skills
    of the project's template as a new project would get them now, name -> {path in the skill folder:
    contents}. Only a project with a .claude/skills/ folder is changed. Returns {"added", "updated",
    "adopted", "kept"}: skill names; adopted: the project's copy is the built-in one (recorded as
    GameStudio's); kept: the user's own or edited, left as they are. history: past built-in versions
    (default: past_versions()). Raises OSError."""
    root = pathlib.Path(root)
    folder = root / SKILLS
    result = {"added": [], "updated": [], "adopted": [], "kept": []}
    if not folder.is_dir():
        return result
    old = read_record(root)
    record = dict(old or {})
    past = past_versions() if history is None else history
    for name in sorted(builtin):
        files = builtin[name]
        new = digest(files)
        dest = folder / name
        known = name in record
        if not dest.exists():
            if not known:  # a skill GameStudio added since the project was made
                _write_skill(dest, files)
                record[name] = new
                result["added"].append(name)
            continue  # recorded and gone: the user deleted it
        have = digest(folder_files(dest)) if dest.is_dir() else None
        if have == new:
            if record.get(name) != new:
                record[name] = new
                result["adopted"].append(name)
        elif have is not None and (have == record.get(name) or have in past.get(name, ())):
            _write_skill(dest, files)  # GameStudio's unedited copy, this version's or an older one's: the new one
            record[name] = new
            result["updated"].append(name)
        else:
            record.setdefault(name, None)  # the user's own skill of that name, or an edited one
            result["kept"].append(name)
    if record != (old or {}):
        _write_record(root, record)
    return result
