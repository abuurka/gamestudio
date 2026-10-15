# SPDX-License-Identifier: GPL-3.0-or-later
"""The project's instructions for coding agents (core/agentfiles.py): AGENTS.md shared by Claude Code
and Codex, and moving an older project's CLAUDE.md there only with the user's consent, without losing
a word of it."""

import pathlib
import shutil
import tempfile
import unittest

from gamestudio.core import agentfiles as af

from helpers import tmp_base

OLD = ("# Gem Island\n\nCollect the gems before the timer runs out.\n\n## Claude Code\n\nAsk before deleting.\n\n"
       "## Rules\n\n```md\n## Claude Code\nnot a heading: in a code block\n```\n\nKeep it bright.\n")
SHARED = "# Gem Island\n\nCollect the gems before the timer runs out.\n\n## Rules\n\n```md\n## Claude Code\n" \
         "not a heading: in a code block\n```\n\nKeep it bright.\n"


class InstructionsCase(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_agentfiles_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, name, text, newline="\n"):
        (self.root / name).write_text(text, encoding="utf-8", newline=newline)

    def read(self, name):
        return (self.root / name).read_text(encoding="utf-8")

    def names(self):
        return sorted(p.name for p in self.root.iterdir())


class StateTests(InstructionsCase):
    def test_states(self):
        self.assertEqual(af.instructions_state(self.root), af.NONE)
        self.write("AGENTS.md", "# Game\n")
        self.assertEqual(af.instructions_state(self.root), af.AGENTS_ONLY)
        self.write("CLAUDE.md", "@AGENTS.md\n\n## Claude Code\n\nNotes.\n")
        self.assertEqual(af.instructions_state(self.root), af.SHARED)
        (self.root / "AGENTS.md").unlink()
        self.assertEqual(af.instructions_state(self.root), af.AGENTS_MISSING)
        self.write("CLAUDE.md", OLD)
        self.assertEqual(af.instructions_state(self.root), af.CLAUDE_ONLY)
        self.assertTrue(af.offers_move(self.root))
        self.write("AGENTS.md", "# Mine\n")
        self.assertEqual(af.instructions_state(self.root), af.SEPARATE)
        self.assertFalse(af.offers_move(self.root))

    def test_the_import_line(self):
        for text in ("@AGENTS.md", "@./AGENTS.md\n", "# T\n\n  @AGENTS.md  \r\n\nMore.\n"):
            with self.subTest(text=text):
                self.assertTrue(af.imports_agents(text))
        for text in ("See @AGENTS.md for more.", "```\n@AGENTS.md\n```\n", "@AGENTS.md.bak\n", "# Rules\n"):
            with self.subTest(text=text):
                self.assertFalse(af.imports_agents(text))

    def test_the_templates_have_the_shared_shape(self):
        from gamestudio import projects

        self.assertEqual(af.instructions_state(projects.TEMPLATES / "starter"), af.SHARED)
        demo = pathlib.Path(__file__).resolve().parents[3] / "examples" / "demo-game"
        self.assertEqual(af.instructions_state(demo), af.SHARED)


class MigrationTests(InstructionsCase):
    def test_moving_with_consent(self):
        self.write("CLAUDE.md", OLD)
        result = af.migrate_instructions(self.root, consent=True)
        self.assertEqual((result["state"], result["action"], result["changed"]),
                         (af.CLAUDE_ONLY, af.MOVED, ["AGENTS.md", "CLAUDE.md"]))
        self.assertIn("AGENTS.md", result["message"])
        self.assertEqual(self.read("AGENTS.md"), SHARED, "word for word, without the Claude Code section")
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n\n## Claude Code\n\nAsk before deleting.\n")
        self.assertEqual(af.instructions_state(self.root), af.SHARED)
        self.assertEqual(self.names(), ["AGENTS.md", "CLAUDE.md"], "no temporary files left")
        again = af.migrate_instructions(self.root, consent=True)
        self.assertEqual((again["action"], again["changed"]), (af.NOTHING, []))

    def test_no_word_is_lost(self):
        """Whatever CLAUDE.md held is in AGENTS.md or CLAUDE.md after the move."""
        texts = (OLD, "No title, one line", "", "# Only a title", "## Claude Code\n\nAll of it is Claude's.\n",
                 "# T\r\n\r\nWindows line ends.\r\n", "﻿# BOM\n\nText.\n")
        for text in texts:
            with self.subTest(text=text):
                self.write("CLAUDE.md", text, newline="")
                (self.root / "AGENTS.md").unlink(missing_ok=True)
                af.migrate_instructions(self.root, consent=True)
                agents, claude = self.read("AGENTS.md"), self.read("CLAUDE.md")
                self.assertTrue(claude.startswith("@AGENTS.md\n"))
                words = text.replace("﻿", "").split()
                self.assertEqual(sorted(agents.split() + claude.split()[1:]), sorted(words))

    def test_declining_copies_with_a_note(self):
        self.write("CLAUDE.md", OLD)
        result = af.migrate_instructions(self.root, consent=False)
        self.assertEqual((result["action"], result["changed"]), (af.COPIED, ["AGENTS.md"]))
        self.assertEqual(self.read("CLAUDE.md"), OLD, "CLAUDE.md is not touched")
        copy = self.read("AGENTS.md")
        self.assertTrue(copy.startswith("# Gem Island\n\n<!-- gamestudio:claude-md-copy "), "the title stays first")
        self.assertIn("A copy of CLAUDE.md", copy)
        self.assertIn(OLD.split("\n", 1)[1].strip(), copy)
        self.assertEqual(af.instructions_state(self.root), af.COPY)
        self.assertTrue(af.offers_move(self.root), "the move can still be offered later")
        self.assertEqual(af.migrate_instructions(self.root, consent=False)["action"], af.NOTHING, "up to date")

        # CLAUDE.md changes: the unedited copy follows.
        self.write("CLAUDE.md", OLD + "\nMore rules.\n")
        result = af.migrate_instructions(self.root, consent=False)
        self.assertEqual((result["state"], result["action"]), (af.COPY, af.REFRESHED))
        self.assertIn("More rules.", self.read("AGENTS.md"))

        # The user agrees later: the copy is replaced by the move.
        result = af.migrate_instructions(self.root, consent=True)
        self.assertEqual((result["state"], result["action"]), (af.COPY, af.MOVED))
        self.assertEqual(self.read("AGENTS.md"), SHARED + "\nMore rules.\n")
        self.assertNotIn("gamestudio:claude-md-copy", self.read("AGENTS.md"))

    def test_a_copy_without_a_title(self):
        self.write("CLAUDE.md", "Rules first.\n")
        af.migrate_instructions(self.root, consent=False)
        self.assertTrue(self.read("AGENTS.md").startswith("<!-- gamestudio:claude-md-copy "))
        self.assertTrue(self.read("AGENTS.md").endswith("\n\nRules first.\n"))
        self.assertEqual(af.instructions_state(self.root), af.COPY)

    def test_an_edited_copy_is_the_users(self):
        self.write("CLAUDE.md", OLD)
        af.migrate_instructions(self.root, consent=False)
        edited = self.read("AGENTS.md") + "\nCodex only: run the tests twice.\n"
        self.write("AGENTS.md", edited)
        self.write("CLAUDE.md", OLD + "\nChanged.\n")
        self.assertEqual(af.instructions_state(self.root), af.SEPARATE)
        for consent in (False, True):
            with self.subTest(consent=consent):
                result = af.migrate_instructions(self.root, consent=consent)
                self.assertEqual((result["action"], result["changed"]), (af.NOTHING, []))
                self.assertIn("separate", result["message"])
                self.assertEqual(self.read("AGENTS.md"), edited)
                self.assertEqual(self.read("CLAUDE.md"), OLD + "\nChanged.\n")
        # An edited note counts as an edit too.
        self.write("AGENTS.md", af.copy_text(OLD).replace("**A copy of CLAUDE.md.**", "My note."))
        self.assertEqual(af.instructions_state(self.root), af.SEPARATE)

    def test_agents_md_alone_gets_the_import(self):
        self.write("AGENTS.md", "# Mine\n")
        result = af.migrate_instructions(self.root, consent=False)
        self.assertEqual((result["action"], result["changed"]), (af.IMPORTED, ["CLAUDE.md"]))
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n")
        self.assertEqual(self.read("AGENTS.md"), "# Mine\n")

    def test_nothing_to_do(self):
        self.assertEqual(af.migrate_instructions(self.root, consent=True)["action"], af.NOTHING)
        self.assertEqual(self.names(), [], "no files without instructions")
        self.write("CLAUDE.md", "@AGENTS.md\n")
        result = af.migrate_instructions(self.root, consent=True)
        self.assertEqual((result["state"], result["action"]), (af.AGENTS_MISSING, af.NOTHING))
        self.assertEqual(self.names(), ["CLAUDE.md"])

    def test_text_that_is_not_utf8_is_refused(self):
        (self.root / "CLAUDE.md").write_bytes(b"# Caf\xe9\n")
        with self.assertRaises(ValueError):
            af.migrate_instructions(self.root, consent=True)
        self.assertEqual(self.names(), ["CLAUDE.md"])
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), b"# Caf\xe9\n")


GAME_UI = af.SECTION_POINTERS[0]
MARKER = "<!-- gamestudio: game-ui -->"
OLDER = "# Gem Island\n\nA 3D game made with GameStudio.\n\n## The loop\n\n1. Build.\n2. Play.\n"


class SectionPointerTests(InstructionsCase):
    """An older project's AGENTS.md gets a short marked note for each section the templates gained
    (agentfiles.SECTION_POINTERS), once, and only where AGENTS.md holds the instructions."""

    def test_game_ui_note_is_appended_once(self):
        self.write("AGENTS.md", OLDER)
        self.write("CLAUDE.md", "@AGENTS.md\n")
        self.assertEqual(af.missing_sections(self.root), [GAME_UI])
        self.assertEqual(af.add_section_pointers(self.root), ["Game UI"])
        text = self.read("AGENTS.md")
        self.assertTrue(text.startswith(OLDER + "\n" + MARKER + "\n## Game UI\n\n"), text)
        block = text[len(OLDER) + 1:]
        lines = [line for line in block.splitlines() if line.strip()]
        self.assertTrue(4 <= len(lines) <= 7, lines)
        for needle in ("`ui/ui.blend`", "one Blender scene per screen", "`ui_open`", "`ui_list`", "`ui_canvas`",
                       "`ui_add`", "`ui_set`", 'game_screenshot {"ui": "<Screen>"}', "`design-ui` skill"):
            self.assertIn(needle, block)
        self.assertTrue(text.endswith("workflow.\n"))
        self.assertEqual(af.add_section_pointers(self.root), [], "once")
        self.assertEqual(self.read("AGENTS.md"), text)
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n")
        # The note moved or reworded by an agent, its marker kept: still covered.
        self.write("AGENTS.md", MARKER + "\n" + OLDER)
        self.assertEqual(af.add_section_pointers(self.root), [])

    def test_only_when_agents_md_does_not_cover_it(self):
        self.write("CLAUDE.md", "@AGENTS.md\n")
        for text in (OLDER + "\n## Game UI\n\nMenus.\n", OLDER + "\n### game ui and HUD\n",
                     OLDER + "\nThe menus are in `ui/UI.blend`.\n"):
            with self.subTest(text=text):
                self.write("AGENTS.md", text)
                self.assertEqual(af.add_section_pointers(self.root), [])
                self.assertEqual(self.read("AGENTS.md"), text)
        fenced = OLDER + "\n```md\n## Game UI\n```\n"
        self.write("AGENTS.md", fenced)
        self.assertEqual(af.add_section_pointers(self.root), ["Game UI"], "a heading in a code block is no section")
        self.write("AGENTS.md", OLDER + "\n## Game UIs of other engines\n")
        self.assertEqual(af.add_section_pointers(self.root), ["Game UI"])

    def test_the_rest_of_the_file_stays_byte_for_byte(self):
        original = ("﻿" + OLDER.rstrip("\n")).replace("\n", "\r\n").encode("utf-8")  # BOM, CRLF, no last line end
        (self.root / "AGENTS.md").write_bytes(original)
        self.assertEqual(af.add_section_pointers(self.root), ["Game UI"])
        raw = (self.root / "AGENTS.md").read_bytes()
        self.assertTrue(raw.startswith(original + b"\r\n\r\n" + MARKER.encode() + b"\r\n## Game UI\r\n"), raw[-400:])
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "the file's own line ends")
        self.assertEqual(af.instructions_state(self.root), af.AGENTS_ONLY)

    def test_only_where_agents_md_holds_the_instructions(self):
        self.write("CLAUDE.md", OLDER)
        self.assertEqual(af.instructions_state(self.root), af.CLAUDE_ONLY)
        self.assertEqual(af.add_section_pointers(self.root), [])
        self.assertEqual(self.names(), ["CLAUDE.md"], "no AGENTS.md for a project whose instructions are in CLAUDE.md")
        self.assertEqual(self.read("CLAUDE.md"), OLDER)
        af.migrate_instructions(self.root, consent=False)  # GameStudio's copy of CLAUDE.md for Codex
        copy = self.read("AGENTS.md")
        self.assertEqual(af.add_section_pointers(self.root), [])
        self.assertEqual(self.read("AGENTS.md"), copy, "the copy stays a copy")
        self.write("AGENTS.md", "# Codex rules\n")
        self.assertEqual(af.instructions_state(self.root), af.SEPARATE)
        self.assertEqual(af.add_section_pointers(self.root), [], "written apart: left as they are")
        self.assertEqual(self.read("AGENTS.md"), "# Codex rules\n")
        (self.root / "CLAUDE.md").unlink()
        self.assertEqual(af.add_section_pointers(self.root), ["Game UI"], "AGENTS.md alone holds them")
        (self.root / "AGENTS.md").unlink()
        self.assertEqual(af.add_section_pointers(self.root), [])
        self.assertEqual(self.names(), [])

    def test_new_projects_never_need_a_pointer(self):
        """Every built-in template's AGENTS.md covers each pointer's subject already."""
        from gamestudio import projects

        for info in projects.templates():
            if info["user"]:
                continue
            with self.subTest(template=info["id"]):
                text = projects.render_text(projects.template_files(info["id"])["AGENTS.md"], {"name": "Gem Island"})
                for pointer in af.SECTION_POINTERS:
                    self.assertTrue(af.covers(text, pointer), pointer.key)


if __name__ == "__main__":
    unittest.main()
