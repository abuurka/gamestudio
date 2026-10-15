# SPDX-License-Identifier: GPL-3.0-or-later
"""The Game workspace (workspaces.py) and its Console, the "Game Console" text (editors/console.py)."""

import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

import bpy

import helpers
from gamestudio import projects, workspaces
from gamestudio.core import code_editor, link, project
from gamestudio.editors import console


def window():
    return bpy.context.window_manager.windows[0]


def area_of(workspace, kind):
    return next(a for a in workspace.screens[0].areas if a.type == kind)


class GameWorkspaceTests(unittest.TestCase):
    def setUp(self):
        helpers.reset_scene()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_ws_", dir=helpers.tmp_base()))
        (self.root / "gamestudio.json").write_text('{"name": "ws-test", "startScene": "main"}', encoding="utf-8")
        (self.root / ".gamestudio").mkdir()
        self.blend = self.root / "scenes" / "main.blend"
        self.blend.parent.mkdir()

    def tearDown(self):
        project.set_project_root_override(None)
        helpers.reset_scene()
        shutil.rmtree(self.root, ignore_errors=True)

    def _reopen(self):
        bpy.ops.wm.open_mainfile(filepath=str(self.blend))
        project.invalidate_cache()

    def test_bundled_file_has_the_game_workspace(self):
        self.assertTrue(workspaces.BLEND.is_file(), "run python tools/build_workspaces.py")
        with bpy.data.libraries.load(str(workspaces.BLEND)) as (source, _target):
            self.assertEqual(list(source.workspaces), [workspaces.NAME])
        workspace = workspaces._append()
        screen = workspace.screens[0]
        self.assertEqual(sorted(a.type for a in screen.areas), ["OUTLINER", "PROPERTIES", "TEXT_EDITOR", "VIEW_3D"])
        self.assertEqual(workspaces.state(workspace)["version"], workspaces.VERSION)
        view = area_of(workspace, "VIEW_3D")
        console_area = area_of(workspace, "TEXT_EDITOR")
        right = [area_of(workspace, "OUTLINER"), area_of(workspace, "PROPERTIES")]
        self.assertFalse(view.spaces.active.show_region_ui, "the 3D Viewport's sidebar is closed")
        self.assertEqual(console_area.x, view.x, "the Console sits under the 3D Viewport")
        self.assertLess(console_area.y, view.y)
        self.assertTrue(all(a.x > view.x + view.width - 5 for a in right), "Outliner and Properties on the right")
        self.assertGreater(right[0].y, right[1].y, "the Outliner above the Properties")
        self.assertEqual(area_of(workspace, "PROPERTIES").spaces.active.context, "OBJECT")
        # The console shows its text at once; the window-only steps wait for a window.
        space = console_area.spaces.active
        self.assertTrue(console.is_console(space.text))
        self.assertFalse(space.show_line_numbers)
        self.assertTrue(space.show_word_wrap)
        self.assertIn("TEXT_EDITOR", workspace[workspaces.LAYOUT_PROP].to_dict())
        self.assertFalse(workspaces.modified(workspace))

    def test_add_does_not_switch_unless_asked(self):
        before = window().workspace
        self.assertEqual(workspaces.missing(), [workspaces.NAME])
        self.assertEqual(workspaces.add(activate=False), [workspaces.NAME])
        self.assertEqual(workspaces.missing(), [])
        self.assertEqual(workspaces.add(activate=False), [], "a second time adds nothing")
        self.assertEqual(window().workspace, before)
        self.assertNotIn("Game.001", bpy.data.workspaces)
        self.assertEqual(list(bpy.data.libraries), [], "no library left pointing into the extension")

    def test_new_project_shows_the_game_workspace(self):
        shown = []
        start = self.blend
        start.write_bytes(b"")  # a template's shipped start scene (opening it is mocked)
        with mock.patch.object(projects, "start_scene", lambda root: start), \
                mock.patch.object(projects, "open_shipped_scene", lambda root: start), \
                mock.patch.object(workspaces, "show", lambda ws, win=None: shown.append(ws.name)):
            self.assertEqual(projects.finish_new_project(self.root), start)
        self.assertEqual(shown, [workspaces.NAME])
        self.assertIsNotNone(workspaces.find())

    def test_opening_a_project_file_adds_it_once_without_switching(self):
        bpy.ops.wm.save_as_mainfile(filepath=str(self.blend))
        self._reopen()
        self.assertIsNotNone(workspaces.find(), "a project file gets the Game workspace")
        self.assertEqual(window().workspace.name, "Layout", "the file opens on its own workspace")
        bpy.ops.wm.save_mainfile()  # saved with it: remembered
        workspace = workspaces.find()
        bpy.data.batch_remove(ids=[workspace, *workspace.screens])
        bpy.ops.wm.save_mainfile()
        self._reopen()
        self.assertIsNone(workspaces.find(), "the user removed it: not added again")

    def test_files_outside_projects_are_left_alone(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="gs_ws_out_", dir=helpers.tmp_base()))
        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(outside / "plain.blend"))
            bpy.ops.wm.open_mainfile(filepath=str(outside / "plain.blend"))
            self.assertIsNone(workspaces.find())
        finally:
            helpers.reset_scene()
            shutil.rmtree(outside, ignore_errors=True)

    def test_an_outdated_unchanged_copy_is_refreshed(self):
        project.set_project_root_override(self.root)
        workspace = workspaces._append()
        workspace[workspaces.STATE_PROP] = {**workspaces.state(workspace), "version": 0}
        self.assertTrue(workspaces.outdated(workspace))
        self.assertIn("Updated", workspaces.check_project_file())
        fresh = workspaces.find()
        self.assertEqual(fresh.name, workspaces.NAME)
        self.assertEqual(workspaces.state(fresh)["version"], workspaces.VERSION)
        self.assertEqual([ws.name for ws in bpy.data.workspaces if ws.name.startswith(workspaces.NAME)],
                         [workspaces.NAME], "the old copy is removed")
        self.assertEqual(workspaces.check_project_file(), "", "up to date now")

    def test_an_outdated_copy_the_user_changed_stays(self):
        project.set_project_root_override(self.root)
        workspace = workspaces._append()
        workspace[workspaces.STATE_PROP] = {**workspaces.state(workspace), "version": 0}
        screen = workspace.screens[0]
        area = area_of(workspace, "OUTLINER")
        with bpy.context.temp_override(window=window(), screen=screen, area=area):
            area.ui_type = "INFO"
        self.assertTrue(workspaces.modified(workspace))
        self.assertIn("Reset Game Workspace", workspaces.check_project_file())
        self.assertEqual(workspaces.state(workspaces.find())["version"], 0, "left as it is")
        # Reset replaces it on request.
        with bpy.context.temp_override(window=window()):
            self.assertEqual(bpy.ops.gamestudio.reset_game_workspace(), {"FINISHED"})
        self.assertEqual(workspaces.state(workspaces.find())["version"], workspaces.VERSION)
        self.assertFalse(workspaces.modified(workspaces.find()))


class GameConsoleTextTests(unittest.TestCase):
    def setUp(self):
        helpers.reset_scene()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_console_", dir=helpers.tmp_base()))
        (self.root / "gamestudio.json").write_text('{"name": "console-test"}', encoding="utf-8")
        (self.root / "src" / "systems").mkdir(parents=True)
        self.source = self.root / "src" / "systems" / "coins.ts"
        self.source.write_text("export {}\n", encoding="utf-8")
        project.set_project_root_override(self.root)
        self.logs = link.get_link().logs
        self.saved_logs = list(self.logs)
        self.logs.clear()

    def tearDown(self):
        self.logs.clear()
        self.logs.extend(self.saved_logs)
        project.set_project_root_override(None)
        helpers.reset_scene()
        shutil.rmtree(self.root, ignore_errors=True)

    def _log(self, msg, level="info", **extra):
        self.logs.append({"t": "log", "msg": msg, "level": level, "time": time.time(), **extra})

    def test_the_text_mirrors_the_log(self):
        self.assertIsNone(console.console_text(), "no console text until a workspace shows it")
        text = console.console_text(create=True)
        self.assertIn("appear here", text.as_string())
        self._log("player spawned", source="src/systems/coins.ts:2:3")
        self._log("TypeError: x is undefined", level="error", file="src/systems/coins.ts", line=12, col=5)
        self.assertTrue(console.sync())
        body = text.as_string()
        self.assertIn("info   player spawned", body)
        self.assertIn("ERROR  TypeError: x is undefined", body)
        self.assertIn("at src/systems/coins.ts:12:5", body)
        self.assertFalse(console.sync(), "unchanged: not rewritten")
        bpy.context.window_manager.gs_console_level = "ERROR"
        try:
            self.assertNotIn("player spawned", text.as_string(), "the filter rewrites it")
        finally:
            bpy.context.window_manager.gs_console_level = "ALL"
        self.assertFalse(bpy.data.is_dirty, "mirroring the log does not make the file unsaved")

    def test_the_line_count_is_capped(self):
        text = console.console_text(create=True)
        for i in range(40):
            self._log(f"message {i}")
        with mock.patch.object(console, "MAX_LINES", 10):
            console.sync(force=True)
        lines = [line.body for line in text.lines]
        self.assertLessEqual(len(lines), 10)
        self.assertIn("older lines not shown", lines[0])
        self.assertTrue(lines[-1].endswith("message 39"))

    def test_the_log_is_not_saved_in_the_blend(self):
        text = console.console_text(create=True)
        self._log("a secret message from the game")
        console.sync(force=True)
        path = self.root / "saved.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
        self.assertIn("a secret message", text.as_string(), "refilled after the save")
        with bpy.data.libraries.load(str(path)) as (source, target):
            target.texts = [name for name in source.texts if name.startswith(console.TEXT_NAME)]
        try:
            self.assertEqual([t.as_string() for t in target.texts], [""])
        finally:
            bpy.data.batch_remove(ids=list(target.texts))

    def test_double_click_opens_the_source(self):
        workspace = workspaces._append()
        area = area_of(workspace, "TEXT_EDITOR")
        text = area.spaces.active.text
        self._log("TypeError: x is undefined", level="error", file="src/systems/coins.ts", line=12, col=5)
        self._log("plain message")
        console.sync(force=True)
        lines = [line.body for line in text.lines]
        first = next(i for i, body in enumerate(lines) if "TypeError" in body)
        plain = next(i for i, body in enumerate(lines) if "plain message" in body)
        opened = []

        def fake_open(path, line, column, project=None):
            opened.append((pathlib.Path(path).name, line, column))
            return "Opened"

        with mock.patch.object(code_editor, "open_location", fake_open), \
                bpy.context.temp_override(window=window(), screen=workspace.screens[0], area=area):
            self.assertTrue(bpy.ops.gamestudio.console_open_line.poll())
            self.assertEqual(bpy.ops.gamestudio.console_open_line(line=first), {"FINISHED"})
            self.assertEqual(bpy.ops.gamestudio.console_open_line(line=first + 1), {"FINISHED"}, "the 'at' line")
            self.assertEqual(bpy.ops.gamestudio.console_open_line(line=plain), {"CANCELLED"})
        self.assertEqual(opened, [("coins.ts", 12, 5), ("coins.ts", 12, 5)])
        # Another text: the keymap item does nothing there (the click goes on to Blender).
        area.spaces.active.text = bpy.data.texts.new("notes.py")
        with bpy.context.temp_override(window=window(), screen=workspace.screens[0], area=area):
            self.assertFalse(bpy.ops.gamestudio.console_open_line.poll())

    def test_location_survives_a_reload(self):
        """Without the line map (another session), the location in the line still opens."""
        text = console.console_text(create=True)
        text.from_string("12:00:00  ERROR  boom\n                 at src/systems/coins.ts:3:9")
        console._text_state.update(lines=[], refs=[])
        where = console.location_at(text, 1)
        self.assertEqual((where.path, where.line, where.col), (self.source, 3, 9))
        self.assertIsNone(console.location_at(text, 0))


class RegionVisibilityTests(unittest.TestCase):
    """Showing or hiding a region re-initialises the area through the context window; a timer has
    none, and Blender crashed (WM_window_get_active_workspace on NULL). Without a window showing
    the screen, set_region_shown must not touch the flag, and must not set an unchanged one."""

    class _Space:
        def __init__(self, footer):
            object.__setattr__(self, "show_region_footer", footer)
            object.__setattr__(self, "writes", [])

        def __setattr__(self, name, value):
            self.writes.append((name, value))
            object.__setattr__(self, name, value)

    def area(self, footer):
        space = self._Space(footer)
        return type("Area", (), {"spaces": type("Spaces", (), {"active": space})()})(), space

    def test_unchanged_flag_is_not_written(self):
        area, space = self.area(footer=False)
        self.assertTrue(workspaces.set_region_shown(None, None, area, "show_region_footer", False))
        self.assertEqual(space.writes, [])

    def test_no_window_waits(self):
        area, space = self.area(footer=True)
        screen = object()
        self.assertFalse(workspaces.set_region_shown(None, screen, area, "show_region_footer", False))
        other = type("Window", (), {"screen": object()})()
        self.assertFalse(workspaces.set_region_shown(other, screen, area, "show_region_footer", False))
        self.assertEqual(space.writes, [])
        self.assertTrue(space.show_region_footer)

    def test_layout_steps_without_a_window_never_toggle_regions(self):
        workspace = bpy.data.workspaces.get("Layout") or bpy.data.workspaces[0]
        screen = workspace.screens[0]
        view = workspaces.find_area(screen, "VIEW_3D")
        view.spaces.active.show_region_ui = False  # background: no window, so no area re-init here
        left = workspaces.apply_steps(None, screen, {"VIEW_3D": {"sidebar": "Game"}})
        self.assertEqual(left, {"VIEW_3D": {"sidebar": "Game"}}, "kept for when a window shows it")
        self.assertFalse(view.spaces.active.show_region_ui)


if __name__ == "__main__":
    unittest.main()
