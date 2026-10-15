# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeping an older project's built-in skills and AGENTS.md current when it opens (core/skills.py,
claude_code.refresh_agent_files): new built-in skills are added, unedited ones follow GameStudio's
version, the user's own, edited and deleted skills are left as they are, Codex's copy follows, and
AGENTS.md gets a Game UI note once."""

import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from gamestudio import claude_code as cc
from gamestudio import codex, projects
from gamestudio.core import agentfiles, project, skills

from helpers import tmp_base

MARKER = "<!-- gamestudio: game-ui -->"
OLDER_AGENTS = "# Gem Island\n\nA 3D game made with GameStudio.\n\n## The loop\n\n1. Build.\n2. Play.\n"


def skill(name: str, body: str = "Steps.\n", **extra) -> dict:
    files = {"SKILL.md": f"---\nname: {name}\ndescription: Does {name}.\n---\n\n{body}".encode("utf-8")}
    files.update({rel: text.encode("utf-8") for rel, text in extra.items()})
    return files


class _Project(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_skills_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.skills = self.root / ".claude" / "skills"

    def put(self, name: str, files: dict) -> pathlib.Path:
        folder = self.skills / name
        for rel, data in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(data)
        return folder

    def files(self, name: str) -> dict:
        return skills.folder_files(self.skills / name)

    def record(self) -> dict:
        return json.loads((self.skills / skills.RECORD).read_text(encoding="utf-8"))["skills"]


class UpdateSkillsTests(_Project):
    """core/skills.update_skills with made-up built-in skills."""

    def setUp(self):
        super().setUp()
        self.skills.mkdir(parents=True)
        self.builtin = {"make-prop": skill("make-prop", **{"MODELING.md": "How to model.\n"}),
                        "design-ui": skill("design-ui")}

    def test_adds_missing_skills_and_adopts_identical_ones(self):
        self.put("make-prop", self.builtin["make-prop"])
        result = skills.update_skills(self.root, self.builtin)
        self.assertEqual(result, {"added": ["design-ui"], "updated": [], "adopted": ["make-prop"], "kept": []})
        self.assertEqual(self.files("design-ui"), self.builtin["design-ui"])
        self.assertEqual(self.record(), {name: skills.digest(files) for name, files in self.builtin.items()})
        text = (self.skills / skills.RECORD).read_text(encoding="utf-8")
        self.assertIn('"note"', text)
        self.assertEqual(sorted(p.name for p in self.skills.iterdir()), [skills.RECORD, "design-ui", "make-prop"],
                         "no temporary folders left")
        stamp = (self.skills / skills.RECORD).stat().st_mtime_ns
        self.assertEqual(skills.update_skills(self.root, self.builtin),
                         {"added": [], "updated": [], "adopted": [], "kept": []})
        self.assertEqual((self.skills / skills.RECORD).stat().st_mtime_ns, stamp, "written only when it changes")

    def test_refreshes_skills_nobody_edited(self):
        skills.update_skills(self.root, self.builtin)
        newer = dict(self.builtin, **{"make-prop": skill("make-prop", "Better steps.\n")})
        result = skills.update_skills(self.root, newer)
        self.assertEqual(result["updated"], ["make-prop"])
        self.assertEqual(self.files("make-prop"), newer["make-prop"], "MODELING.md, gone from the new version, too")
        self.assertEqual(self.record()["make-prop"], skills.digest(newer["make-prop"]))

    def test_never_touches_edited_or_own_skills(self):
        own = skill("design-ui", "My own way of making menus.\n")
        self.put("design-ui", own)  # the project's own skill of that name, before GameStudio had one
        skills.update_skills(self.root, self.builtin)
        edited = dict(self.builtin["make-prop"], **{"SKILL.md": b"---\nname: make-prop\n---\n\nMy steps.\n"})
        self.put("make-prop", edited)
        (self.skills / "make-prop" / "NOTES.md").write_bytes(b"Mine.\n")
        newer = {name: skill(name, "Newer.\n") for name in self.builtin}
        for _ in range(2):
            result = skills.update_skills(self.root, newer)
            self.assertEqual(result, {"added": [], "updated": [], "adopted": [], "kept": ["design-ui", "make-prop"]})
        self.assertEqual(self.files("design-ui"), own)
        self.assertEqual(self.files("make-prop"), dict(edited, **{"NOTES.md": b"Mine.\n"}))
        self.assertIsNone(self.record()["design-ui"], "the project's own")
        self.assertEqual(self.record()["make-prop"], skills.digest(self.builtin["make-prop"]),
                         "what GameStudio wrote: the skill is GameStudio's again if the edit is undone")

    def test_copies_of_past_builtin_versions_follow_the_new_one(self):
        old = skill("make-prop", "Old steps.\n")  # what an older GameStudio wrote, before the record
        self.put("make-prop", old)
        self.put("design-ui", skill("design-ui", "Old menu steps.\n"))
        history = {"make-prop": frozenset({skills.digest(old)})}
        result = skills.update_skills(self.root, self.builtin, history)
        self.assertEqual(result["updated"], ["make-prop"])
        self.assertEqual(result["kept"], ["design-ui"], "not a known version: the user's")
        self.assertEqual(self.files("make-prop"), self.builtin["make-prop"])
        # Recorded as the user's by a GameStudio without the history: a known version still follows.
        shutil.rmtree(self.skills / "make-prop")
        self.put("make-prop", old)
        skills._write_record(self.root, dict(self.record(), **{"make-prop": None}))
        self.assertEqual(skills.update_skills(self.root, self.builtin, history)["updated"], ["make-prop"])

    def test_the_shipped_history_knows_every_builtin_skill(self):
        past = skills.past_versions()
        templates = pathlib.Path(skills.__file__).parents[1] / "project_templates"
        template_skills = {p.name for p in templates.glob("*/.claude/skills/*") if (p / skills.SKILL_FILE).is_file()}
        self.assertTrue(template_skills)
        self.assertEqual(template_skills - set(past), set(), "run python tools/skill_history.py")

    def test_deleted_skills_stay_deleted(self):
        self.put("design-ui", skill("design-ui", "Mine.\n"))
        skills.update_skills(self.root, self.builtin)
        shutil.rmtree(self.skills / "make-prop")  # GameStudio's
        shutil.rmtree(self.skills / "design-ui")  # the user's own
        newer = {name: skill(name, "Newer.\n") for name in self.builtin}
        self.assertEqual(skills.update_skills(self.root, newer),
                         {"added": [], "updated": [], "adopted": [], "kept": []})
        self.assertEqual(sorted(p.name for p in self.skills.iterdir()), [skills.RECORD])
        self.assertEqual(set(self.record()), {"make-prop", "design-ui"}, "remembered")

    def test_line_ends_and_folder_litter_are_no_edit(self):
        skills.update_skills(self.root, self.builtin)
        path = self.skills / "make-prop" / "SKILL.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # git's autocrlf on Windows
        (self.skills / "make-prop" / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
        newer = dict(self.builtin, **{"make-prop": skill("make-prop", "Newer.\n")})
        self.assertEqual(skills.update_skills(self.root, newer)["updated"], ["make-prop"])
        self.assertEqual(path.read_bytes(), newer["make-prop"]["SKILL.md"])

    def test_a_project_without_a_skills_folder_is_left_alone(self):
        shutil.rmtree(self.root / ".claude")
        self.assertEqual(skills.update_skills(self.root, self.builtin),
                         {"added": [], "updated": [], "adopted": [], "kept": []})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_an_unreadable_record_counts_as_none(self):
        self.put("make-prop", skill("make-prop", "Mine.\n"))
        (self.skills / skills.RECORD).write_text("{ not json", encoding="utf-8")
        self.assertIsNone(skills.read_record(self.root))
        result = skills.update_skills(self.root, self.builtin)
        self.assertEqual((result["added"], result["kept"]), (["design-ui"], ["make-prop"]))
        self.assertEqual(self.record()["make-prop"], None)


class RefreshAgentFilesTests(_Project):
    """claude_code.refresh_agent_files with the real templates: what an older project gets on open."""

    def make(self, template: str | None = None, agents: str = OLDER_AGENTS) -> pathlib.Path:
        """A project connected to Claude Code by an older GameStudio: no design-ui, no Game UI section."""
        config = {"name": "Gem Island", **({"template": template} if template else {})}
        (self.root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
        cc.add_agent_files(self.root)
        shutil.rmtree(self.skills / "design-ui")
        (self.root / "AGENTS.md").write_text(agents, encoding="utf-8")
        return self.root

    def builtin(self, name: str, template: str = "starter") -> dict:
        return cc.builtin_skills(self.root, template)[name]

    def test_an_older_project_gets_the_new_skill_and_the_note(self):
        self.make()
        prop = self.skills / "make-prop" / "SKILL.md"
        prop.write_text(prop.read_text(encoding="utf-8") + "\nAlways use the blue palette.\n", encoding="utf-8")
        claude = (self.root / "CLAUDE.md").read_bytes()
        note = cc.refresh_agent_files(self.root)
        self.assertEqual(note, "Added the design-ui skill and a Game UI note to AGENTS.md")
        self.assertEqual(self.files("design-ui"), self.builtin("design-ui"))
        self.assertIn("Always use the blue palette.", prop.read_text(encoding="utf-8"), "an edited skill is the user's")
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith(OLDER_AGENTS + "\n" + MARKER + "\n## Game UI\n"))
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), claude)
        record = self.record()
        self.assertIsNone(record["make-prop"])
        self.assertEqual(record["design-ui"], skills.digest(self.builtin("design-ui")))
        self.assertEqual(record["playtest-and-fix"], skills.digest(self.builtin("playtest-and-fix")), "adopted")
        self.assertEqual(cc.refresh_agent_files(self.root), "", "once")
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), agents)

    def test_unedited_skills_follow_gamestudios_version(self):
        self.make()
        cc.refresh_agent_files(self.root)
        old = skill("playtest-and-fix", "The version an older GameStudio wrote.\n")
        shutil.rmtree(self.skills / "playtest-and-fix")
        self.put("playtest-and-fix", old)
        record = self.record()
        record["playtest-and-fix"] = skills.digest(old)
        (self.skills / skills.RECORD).write_text(json.dumps({"skills": record}), encoding="utf-8")
        self.assertEqual(cc.refresh_agent_files(self.root),
                         "Updated the playtest-and-fix skill to this GameStudio's version")
        self.assertEqual(self.files("playtest-and-fix"), self.builtin("playtest-and-fix"))
        shutil.rmtree(self.skills / "design-ui")
        self.assertEqual(cc.refresh_agent_files(self.root), "", "deleted by the user: not added again")
        self.assertFalse((self.skills / "design-ui").exists())

    def test_codex_copy_follows(self):
        self.make()
        codex.mirror_skills(self.root)  # connected to Codex by an older GameStudio
        self.assertFalse((self.root / codex.SKILLS / "design-ui").exists())
        cc.refresh_agent_files(self.root)
        copy = self.root / codex.SKILLS / "design-ui"
        self.assertEqual((copy / "SKILL.md").read_bytes(), self.builtin("design-ui")["SKILL.md"])
        self.assertIsNotNone(codex.copy_info(copy))
        self.assertFalse((self.root / codex.SKILLS / skills.RECORD).exists(), "the record is no skill")

    def test_projects_not_using_codex_get_no_copy(self):
        self.make()
        cc.refresh_agent_files(self.root)
        self.assertFalse((self.root / ".agents").exists())

    def test_instructions_only_in_claude_md_are_left_alone(self):
        self.make()
        (self.root / "AGENTS.md").unlink()
        (self.root / "CLAUDE.md").write_text(OLDER_AGENTS, encoding="utf-8")
        self.assertEqual(cc.refresh_agent_files(self.root), "Added the design-ui skill")
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), OLDER_AGENTS)

    def test_a_project_without_skills_gets_only_the_note(self):
        self.make()
        shutil.rmtree(self.skills)
        self.assertEqual(cc.refresh_agent_files(self.root), "Added a Game UI note to AGENTS.md")
        self.assertFalse(self.skills.exists(), "no .claude/skills/: not connected, or the user removed them")

    def test_the_projects_template_and_the_default(self):
        self.make("shooter")
        self.assertEqual(cc.refresh_agent_files(self.root), "Added the design-ui skill and a Game UI note to AGENTS.md")
        self.assertIn("add-enemy-type", self.record(), "the shooter's own skill, composed with the starter's")
        names = set(cc.builtin_skills(self.root))
        self.assertTrue({"add-enemy-type", "design-ui", "make-prop"} <= names, names)
        starter = cc.builtin_skills(self.root, "starter")
        self.assertNotIn("add-enemy-type", starter)
        configs = ({"name": "Gem Island"}, {"name": "Gem Island", "template": "no-such-template"})
        for index, config in enumerate(configs):
            with self.subTest(config=config):
                older = self.root / f"older{index}"  # made before New Project recorded the template
                older.mkdir()
                (older / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
                self.assertEqual(cc.agent_template(older), cc.AGENT_TEMPLATE)
                self.assertEqual(cc.builtin_skills(older), starter)

    def test_a_new_project_only_gets_the_record(self):
        (self.root / "gamestudio.json").write_text(json.dumps({"name": "Gem Island"}), encoding="utf-8")
        cc.add_agent_files(self.root)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(cc.refresh_agent_files(self.root), "")
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(set(after) - set(before), {self.skills / skills.RECORD})
        self.assertEqual({p: after[p] for p in before}, before)
        self.assertTrue(all(isinstance(value, str) for value in self.record().values()), "every skill adopted")

    def test_template_folders_and_gamestudios_examples_are_left_alone(self):
        self.make()
        (self.root / projects.TEMPLATE_INFO).write_text("{}", encoding="utf-8")
        self.assertEqual(cc.refresh_agent_files(self.root), "")
        (self.root / projects.TEMPLATE_INFO).unlink()
        with mock.patch.object(cc.projects, "examples", return_value=[self.root]):
            self.assertEqual(cc.refresh_agent_files(self.root), "")
        self.assertFalse((self.skills / "design-ui").exists())
        self.assertFalse((self.skills / skills.RECORD).exists())
        self.assertNotIn(MARKER, (self.root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_never_raises(self):
        self.make()
        with mock.patch.object(cc, "builtin_skills", side_effect=ValueError("broken template")), \
                mock.patch.object(agentfiles, "add_section_pointers", side_effect=OSError("read-only")), \
                self.assertLogs(cc.log, "WARNING"):
            self.assertEqual(cc.refresh_agent_files(self.root), "")

    def test_save_as_template_leaves_the_record_out(self):
        self.make()
        cc.refresh_agent_files(self.root)
        files, _left_out = projects.project_files_for_template(self.root)
        self.assertIn(".claude/skills/design-ui/SKILL.md", files)
        self.assertNotIn(f"{skills.SKILLS}/{skills.RECORD}", files)

    def test_done_when_a_scene_loads(self):
        self.make()
        project.set_project_root_override(self.root)
        self.addCleanup(project.set_project_root_override, None)
        with mock.patch.object(cc.environment, "notify") as notify, \
                mock.patch.object(cc, "mcp_node_command", return_value="node"):
            cc._on_load_post()
            cc._on_load_post()
        notify.assert_called_once_with("Added the design-ui skill and a Game UI note to AGENTS.md", "INFO")
        self.assertTrue((self.skills / "design-ui" / "SKILL.md").is_file())

    def test_note(self):
        self.assertEqual(cc.agent_files_note(), "")
        self.assertEqual(cc.agent_files_note(["a", "b"], sections=["Game UI"]),
                         "Added the a and b skills and a Game UI note to AGENTS.md")
        self.assertEqual(cc.agent_files_note(updated=["a", "b", "c", "d"]),
                         "Updated 4 skills to this GameStudio's version")
        self.assertEqual(cc.agent_files_note(["a"], ["b"]),
                         "Added the a skill; updated the b skill to this GameStudio's version")


if __name__ == "__main__":
    unittest.main()
