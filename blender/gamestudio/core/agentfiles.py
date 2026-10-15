# SPDX-License-Identifier: GPL-3.0-or-later
"""A project's instructions for coding agents: one AGENTS.md for Claude Code and Codex.

GameStudio projects keep their instructions in AGENTS.md, which Codex reads, and a CLAUDE.md that
holds the line @AGENTS.md (an import), which Claude Code reads. Notes only Claude Code needs go in a
"## Claude Code" section of CLAUDE.md, under that line. Projects made before that have only a
CLAUDE.md, often edited by the user.

migrate_instructions(root, consent) brings such a project to that shape. With the user's consent
the instructions move from CLAUDE.md into AGENTS.md word for word, and CLAUDE.md becomes the import
(keeping its "## Claude Code" sections). Without it CLAUDE.md is left alone, and AGENTS.md gets a
copy of it with a note saying so, so Codex gets the same instructions. That copy is refreshed when
CLAUDE.md changes, and replaced by the move when the user agrees later, but only while nobody has
edited it (the note records a hash of what was copied). Text a user wrote is never lost: GameStudio
overwrites only its own unedited copy.

A project keeps the AGENTS.md it was made with. When the templates' AGENTS.md gains a section about
something new (the game UI), add_section_pointers(root) appends a short marked version of it to an
older project's AGENTS.md that does not cover the subject yet, once (SECTION_POINTERS).

No bpy here: the shell tests and the Codex connection use it.
"""

import hashlib
import os
import pathlib
import re
from typing import NamedTuple

AGENTS = "AGENTS.md"
CLAUDE = "CLAUDE.md"
IMPORT_LINE = "@AGENTS.md"

# instructions_state(): what the project has.
NONE = "none"  # neither file: the agent files come from the project's template
SHARED = "shared"  # AGENTS.md, and a CLAUDE.md that imports it: nothing to do
AGENTS_ONLY = "agents-only"  # AGENTS.md without CLAUDE.md: CLAUDE.md gets the import line
AGENTS_MISSING = "agents-missing"  # CLAUDE.md imports AGENTS.md, which is gone: the template brings it back
CLAUDE_ONLY = "claude-only"  # instructions only in CLAUDE.md: moved with consent, else copied
COPY = "copy"  # AGENTS.md is GameStudio's unedited copy of CLAUDE.md
SEPARATE = "separate"  # both files, written apart (or the copy was edited): both are left as they are

# migrate_instructions()["action"]
MOVED = "moved"
COPIED = "copied"
REFRESHED = "refreshed"
IMPORTED = "imported"
NOTHING = "none"

_IMPORT = re.compile(r"[ \t]*@(?:\./)?AGENTS\.md[ \t]*")  # a whole line
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_CLAUDE_SECTION = re.compile(r"claude code", re.IGNORECASE)
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_COPY_MARK = re.compile(r"^<!-- gamestudio:claude-md-copy ([0-9a-f]{64}) -->$", re.MULTILINE)
_NOTE = ("> **A copy of CLAUDE.md.** GameStudio made this file so that Codex gets the same instructions as "
         "Claude Code. Claude Code reads CLAUDE.md, not this file, so change the instructions there: GameStudio "
         "copies them here again while this copy is unchanged. To keep one file for both agents, accept "
         "GameStudio's offer to move the instructions here.")


def _read(path: pathlib.Path) -> str | None:
    """A file's text with \\n line ends and no byte order mark, or None when it does not exist.
    Raises OSError, or ValueError when it is not UTF-8 text."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None


def _write(path: pathlib.Path, text: str) -> None:
    """Write through a temporary file, so a failure leaves the old file as it was."""
    tmp = path.with_name(f".{path.name}.gamestudio-tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def imports_agents(claude: str) -> bool:
    """True when a CLAUDE.md's text has the line @AGENTS.md (or @./AGENTS.md) outside code blocks."""
    return any(_IMPORT.fullmatch(line.rstrip("\r\n")) for line, fenced in _lines(claude) if not fenced)


def _lines(text: str):
    """(line with its end, inside a fenced code block) for each line."""
    fence = None
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if match and fence is None:
            fence = match.group(1)
            yield line, True
        elif match and line.lstrip().startswith(fence):
            fence = None
            yield line, True
        else:
            yield line, fence is not None


def split_claude_sections(text: str) -> tuple[str, str]:
    """(the text without its "## Claude Code" sections, those sections): a section runs from a level-2
    heading "Claude Code" (any case) to the next heading of level 1 or 2."""
    shared, claude = [], []
    inside = False
    for line, fenced in _lines(text):
        heading = None if fenced else _HEADING.match(line.rstrip("\r\n"))
        if heading and len(heading.group(1)) <= 2:
            inside = len(heading.group(1)) == 2 and bool(_CLAUDE_SECTION.fullmatch(heading.group(2)))
        (claude if inside else shared).append(line)
    return "".join(shared), "".join(claude)


def _tidy(text: str) -> str:
    """Leading blank lines dropped and one line end at the end ("" stays "")."""
    text = text.lstrip("\n").rstrip()
    return text + "\n" if text else ""


def import_text(claude_sections: str = "") -> str:
    """CLAUDE.md as GameStudio writes it: the import line, then any Claude-only sections."""
    sections = _tidy(claude_sections)
    return IMPORT_LINE + "\n" + ("\n" + sections if sections else "")


def _note_block(digest: str) -> str:
    return f"<!-- gamestudio:claude-md-copy {digest} -->\n{_NOTE}\n<!-- /gamestudio:claude-md-copy -->\n"


def copy_text(claude: str) -> str:
    """AGENTS.md as a copy of a CLAUDE.md's text: the text with the note under its title line (at
    the top when it has none)."""
    block = _note_block(_digest(claude))
    if claude.startswith("# ") and "\n" in claude:
        title, _, rest = claude.partition("\n")
        return f"{title}\n\n{block}{rest}"
    return f"{block}\n{claude}"


def _copy_info(agents: str) -> tuple[str, bool] | None:
    """(the digest of the CLAUDE.md it copied, unedited) for GameStudio's copy, else None."""
    mark = _COPY_MARK.search(agents)
    if mark is None:
        return None
    digest = mark.group(1)
    block = _note_block(digest)
    at = agents.find(block)
    if at < 0:
        return digest, False  # the note itself was changed
    if at == 0:
        body = agents[len(block) + 1:] if agents[len(block):].startswith("\n") else None
    else:
        body = agents[:at - 1] + agents[at + len(block):] if agents[at - 1:at] == "\n" else None
    return digest, body is not None and _digest(body) == digest


def instructions_state(root) -> str:
    """Which instructions the project has (NONE, SHARED, AGENTS_ONLY, AGENTS_MISSING, CLAUDE_ONLY,
    COPY or SEPARATE). Raises OSError, or ValueError for a file that is not UTF-8 text."""
    root = pathlib.Path(root)
    claude, agents = _read(root / CLAUDE), _read(root / AGENTS)
    if claude is None:
        return NONE if agents is None else AGENTS_ONLY
    if imports_agents(claude):
        return SHARED if agents is not None else AGENTS_MISSING
    if agents is None:
        return CLAUDE_ONLY
    info = _copy_info(agents)
    return COPY if info is not None and info[1] else SEPARATE


def offers_move(root) -> bool:
    """True when the project's instructions are only in CLAUDE.md (or copied from it by GameStudio):
    the Connect dialog then asks whether to move them into AGENTS.md (migrate_instructions)."""
    return instructions_state(root) in (CLAUDE_ONLY, COPY)


def migrate_instructions(root, consent: bool) -> dict:
    """Bring the project's instructions to AGENTS.md plus a CLAUDE.md that imports it, as far as the
    user agreed (see the module docstring). consent: the user agreed to move CLAUDE.md's instructions
    into AGENTS.md. Returns {"state": what the project had (instructions_state), "action": MOVED,
    COPIED, REFRESHED, IMPORTED or NOTHING, "changed": the files written, in order, "message": what
    happened, in a sentence, or ""}. Raises OSError, or ValueError for a file that is not UTF-8
    text; a failure leaves each file whole (AGENTS.md is written before CLAUDE.md)."""
    root = pathlib.Path(root)
    state = instructions_state(root)
    claude = _read(root / CLAUDE)
    agents = _read(root / AGENTS)
    result = {"state": state, "action": NOTHING, "changed": [], "message": ""}

    def done(action, message, *files):
        result.update(action=action, changed=list(files), message=message)
        return result

    if state == AGENTS_ONLY:
        _write(root / CLAUDE, import_text())
        return done(IMPORTED, "Added CLAUDE.md, which points Claude Code to AGENTS.md.", CLAUDE)
    if state == SEPARATE:
        return done(NOTHING, "AGENTS.md and CLAUDE.md are separate files, so GameStudio leaves both as they are: "
                             "Codex reads AGENTS.md and Claude Code reads CLAUDE.md.")
    if state not in (CLAUDE_ONLY, COPY):
        return result
    if consent:
        shared, sections = split_claude_sections(claude)
        _write(root / AGENTS, _tidy(shared))
        _write(root / CLAUDE, import_text(sections))
        return done(MOVED, "Moved the instructions from CLAUDE.md to AGENTS.md, which Claude Code and Codex both "
                           "read; CLAUDE.md now points to it.", AGENTS, CLAUDE)
    if state == CLAUDE_ONLY:
        _write(root / AGENTS, copy_text(claude))
        return done(COPIED, "Copied CLAUDE.md to AGENTS.md for Codex. Claude Code still reads CLAUDE.md.", AGENTS)
    if _copy_info(agents)[0] != _digest(claude):
        _write(root / AGENTS, copy_text(claude))
        return done(REFRESHED, "Copied the changes in CLAUDE.md to AGENTS.md for Codex.", AGENTS)
    return result


# --- Sections added since a project was made ----------------------------------------------------


class SectionPointer(NamedTuple):
    """A short version of a section the templates' AGENTS.md gained, for older projects."""
    key: str           # its marker: <!-- gamestudio: <key> -->
    heading: str       # an AGENTS.md with a "## <heading>" covers the subject already
    mentions: tuple    # ... and so does one that mentions any of these
    text: str          # what is appended, under the marker


SECTION_POINTERS = (
    SectionPointer("game-ui", "Game UI", ("ui/ui.blend",), (
        "## Game UI\n\n"
        "Menus and the HUD are made in Blender and drawn by the game over the 3D view: `ui/ui.blend` holds one "
        "Blender scene per screen (`Title`, `Pause`, `HUD`, ...).\n\n"
        "- Build them with the `ui_*` tools: `ui_open` (creates the file when it is missing), `ui_list` (read it "
        "first), `ui_canvas`, `ui_add` and `ui_set`.\n"
        "- No code for common parts: a volume slider is `ui_add {\"kind\": \"slider\", \"value\": \"audio.music\"}`, "
        "hearts are `{\"kind\": \"icons\", \"value\": \"lives\"}`, and `ui_canvas {\"transition\": ...}` animates menus.\n"
        "- Check a screen in the game with `game_screenshot {\"ui\": \"<Screen>\"}`.\n"
        "- The `design-ui` skill has the workflow.\n")),
)


def _marker(key: str) -> str:
    return f"<!-- gamestudio: {key} -->"


def covers(text: str, pointer: SectionPointer) -> bool:
    """True when an AGENTS.md's text has the pointer (its marker), a heading starting with the
    pointer's (any level, outside code blocks), or a mention of the subject."""
    if re.search(r"<!--\s*gamestudio:\s*" + re.escape(pointer.key) + r"\s*-->", text):
        return True
    lowered = text.lower()
    if any(mention.lower() in lowered for mention in pointer.mentions):
        return True
    title = re.compile(re.escape(pointer.heading) + r"\b", re.IGNORECASE)
    for line, fenced in _lines(text):
        heading = None if fenced else _HEADING.match(line.rstrip("\r\n"))
        if heading and title.match(heading.group(2)):
            return True
    return False


def missing_sections(root) -> list[SectionPointer]:
    """The SECTION_POINTERS the project's AGENTS.md lacks; [] unless AGENTS.md holds the instructions
    (SHARED or AGENTS_ONLY: not GameStudio's copy of CLAUDE.md, and not a project whose instructions
    are only in CLAUDE.md or written apart). Raises OSError, or ValueError when it is not UTF-8 text."""
    if instructions_state(root) not in (SHARED, AGENTS_ONLY):
        return []
    text = _read(pathlib.Path(root) / AGENTS)
    return [pointer for pointer in SECTION_POINTERS if not covers(text, pointer)]


def add_section_pointers(root) -> list[str]:
    """Append each missing section pointer (missing_sections) to the end of the project's AGENTS.md,
    under its marker. The rest of the file stays byte for byte, line ends and byte order mark
    included. Returns the headings added. Raises OSError, or ValueError when it is not UTF-8 text."""
    path = pathlib.Path(root) / AGENTS
    pointers = missing_sections(root)
    if not pointers:
        return []
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    block = "\n".join(_marker(pointer.key) + "\n" + pointer.text.rstrip("\n") + "\n" for pointer in pointers)
    if text.strip():
        block = ("" if text.endswith("\n") else "\n") + "\n" + block
    data = raw + block.replace("\n", newline).encode("utf-8")
    tmp = path.with_name(f".{path.name}.gamestudio-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return [pointer.heading for pointer in pointers]
