# SPDX-License-Identifier: GPL-3.0-or-later
"""The scripting loop: the code editor command, clickable error locations, New
Component / New System, and the Project editor's script list.

No code editor is ever started: code_editor._spawn and _open_with_system are
replaced by recorders, and the tests check the commands they would have run.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import bpy

from gamestudio import prefs
from gamestudio.core import code_editor, link, locations, project, registry, scaffold, scripts
from gamestudio.editors import console
from gamestudio.editors import project as project_editor

import helpers

WINDOWS = sys.platform == "win32"
VSCODE_EXE = r"C:\Program Files\Microsoft VS Code\Code.exe"
FAKE_VSCODE = code_editor.Editor("vscode", "VS Code", VSCODE_EXE)
FAKE_CURSOR = code_editor.Editor("cursor", "Cursor", r"C:\Users\me\AppData\Local\Programs\cursor\Cursor.exe")
ICONS = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
LAYOUT_ATTRS = set(bpy.types.UILayout.bl_rna.properties.keys())


def write(path: pathlib.Path, text: str = "") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- A UILayout stand-in that records operators and their properties ---------------------


class _Op:
    def __init__(self, test, idname, record):
        category, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
        object.__setattr__(self, "_names", set(rna.properties.keys()))
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "_record", record)

    def __setattr__(self, name, value):
        self._test.assertIn(name, self._names, f"{self._record['op']} has no property {name!r}")
        self._record[name] = value


class Layout:
    def __init__(self, test, texts=None, ops=None):
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "texts", texts if texts is not None else [])
        object.__setattr__(self, "ops", ops if ops is not None else [])

    def __setattr__(self, name, value):
        self._test.assertIn(name, LAYOUT_ATTRS, f"UILayout has no attribute {name!r}")

    def _child(self, *_args, **_kwargs):
        return Layout(self._test, self.texts, self.ops)

    row = column = box = split = _child

    def label(self, text="", icon="NONE", **_kwargs):
        self._test.assertIn(icon, ICONS)
        self.texts.append(text)

    def prop(self, data, prop, text=None, icon="NONE", **_kwargs):
        self._test.assertIn(icon, ICONS)
        self._test.assertIn(prop, data.bl_rna.properties, f"{data!r} has no property {prop!r}")

    def operator(self, idname, text="", icon="NONE", **_kwargs):
        self._test.assertIn(icon, ICONS)
        if text:
            self.texts.append(text)
        record = {"op": idname, "text": text}
        self.ops.append(record)
        return _Op(self._test, idname, record)

    def separator(self, **_kwargs):
        pass

    def separator_spacer(self):
        pass


def fake_context(in_editor=True):
    space = types.SimpleNamespace(type="EXTENSION_EDITOR" if in_editor else "VIEW_3D")
    return types.SimpleNamespace(scene=bpy.context.scene, window_manager=bpy.context.window_manager,
                                 space_data=space, region=None, active_object=None)


class _EditorCase(helpers.ProjectTestCase):
    """A project, VS Code "installed" at VSCODE_EXE, and recorders instead of launching anything."""

    preset = "AUTO"

    def setUp(self):
        super().setUp()
        self.commands, self.opened = [], []
        self._saved = (prefs._defaults.code_editor, prefs._defaults.code_editor_command)
        prefs._defaults.code_editor, prefs._defaults.code_editor_command = self.preset, ""
        self._patches = [
            mock.patch.object(code_editor, "_spawn", self.commands.append),
            mock.patch.object(code_editor, "_open_with_system", self.opened.append),
            mock.patch.object(code_editor, "detect", lambda refresh=False: {"vscode": FAKE_VSCODE, "cursor": None}),
        ]
        for patch in self._patches:
            patch.start()
        scripts.invalidate()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        prefs._defaults.code_editor, prefs._defaults.code_editor_command = self._saved
        helpers.restore_global_link()
        scripts.invalidate()
        super().tearDown()


# --- Code editor commands ----------------------------------------------------------------


class CodeEditorCommandTest(unittest.TestCase):
    def test_vscode_opens_the_project_and_the_file_at_the_line(self):
        root, file = r"F:\My Games\demo", r"F:\My Games\demo\src\systems\a b.ts"
        argv = code_editor.editor_command(FAKE_VSCODE, file, 12, 5, project=root)
        self.assertEqual(argv, [VSCODE_EXE, root, "-g", file + ":12:5"])
        # Each path stays one argument: this is the command line Windows passes to Code.exe.
        self.assertEqual(code_editor.command_line(argv),
                         r'"C:\Program Files\Microsoft VS Code\Code.exe" "F:\My Games\demo" '
                         r'-g "F:\My Games\demo\src\systems\a b.ts:12:5"')
        self.assertEqual(code_editor.editor_command(FAKE_VSCODE, project=root), [VSCODE_EXE, root])
        self.assertEqual(code_editor.editor_command(FAKE_VSCODE, file)[-1], file + ":1:1")

    def test_custom_command_placeholders(self):
        template = code_editor.split_command(
            r'"C:\Tools\My Editor\ed.exe" --goto "{file}:{line}:{column}" --root={project}')
        self.assertEqual(template, [r"C:\Tools\My Editor\ed.exe", "--goto", "{file}:{line}:{column}",
                                    "--root={project}"])
        file, root = r"F:\My Games\src\a.ts", r"F:\My Games"
        self.assertEqual(code_editor.expand(template, file, 3, 9, root),
                         [r"C:\Tools\My Editor\ed.exe", "--goto", file + ":3:9", "--root=" + root])
        # Only the project: the file argument and its flag go.
        self.assertEqual(code_editor.expand(template, project=root), [r"C:\Tools\My Editor\ed.exe", "--root=" + root])
        split = code_editor.split_command
        self.assertEqual(code_editor.expand(split("notepad"), file), ["notepad", file])
        self.assertEqual(code_editor.expand(split("subl {file}"), project=root), ["subl", root])
        self.assertEqual(code_editor.expand(split("ed -n{line} {file}"), file), ["ed", "-n1", file])
        self.assertEqual(code_editor.expand(split("ed {col} {file}"), r"F:\{line}\a.ts", 2, 4),
                         ["ed", "4", r"F:\{line}\a.ts"])  # values are never expanded again
        self.assertEqual(split('ed "" x'), ["ed", "", "x"])
        with self.assertRaises(code_editor.CodeEditorError):
            split('ed "unclosed')

    def test_presets(self):
        with mock.patch.object(code_editor, "detect", lambda refresh=False: {"vscode": FAKE_VSCODE, "cursor": FAKE_CURSOR}):
            self.assertIs(code_editor.resolve("AUTO").editor, FAKE_VSCODE)
            self.assertIs(code_editor.resolve("CURSOR").editor, FAKE_CURSOR)
            self.assertIsNone(code_editor.resolve("SYSTEM").editor)
        with mock.patch.object(code_editor, "detect", lambda refresh=False: {"vscode": None, "cursor": FAKE_CURSOR}):
            self.assertIs(code_editor.resolve("AUTO").editor, FAKE_CURSOR)
            with self.assertRaises(code_editor.CodeEditorError) as cm:
                code_editor.resolve("VSCODE")
            self.assertIn("VS Code was not found", str(cm.exception))
        with mock.patch.object(code_editor, "detect", lambda refresh=False: {"vscode": None, "cursor": None}):
            target = code_editor.resolve("AUTO")
            self.assertEqual((target.editor, target.custom, target.label), (None, None, "System default"))
            self.assertIsNone(code_editor.build(target, "a.ts", 3))
        with self.assertRaises(code_editor.CodeEditorError):
            code_editor.resolve("CUSTOM", "  ")
        self.assertEqual(code_editor.resolve("CUSTOM", "ed {file}").custom, ["ed", "{file}"])

    @unittest.skipUnless(WINDOWS, "install folders are Windows paths")
    def test_detection_prefers_path_then_install_folders(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_editors_", dir=helpers.tmp_base()))
        try:
            local = tmp / "Local"
            vscode = local / "Programs" / "Microsoft VS Code"
            write(vscode / "bin" / "code.cmd")
            write(vscode / "Code.exe")
            cursor = local / "Programs" / "cursor"
            write(cursor / "resources" / "app" / "bin" / "cursor.cmd")
            write(cursor / "Cursor.exe")
            on_path = tmp / "D drive" / "Microsoft VS Code"
            write(on_path / "bin" / "code.cmd")
            write(on_path / "Code.exe")
            env = {"LOCALAPPDATA": str(local)}
            nothing = lambda name: None  # noqa: E731

            found = code_editor.find_editor("vscode", env, nothing)
            self.assertEqual(found.label, "VS Code")
            expected = vscode / "Code.exe" if WINDOWS else vscode / "bin" / "code.cmd"
            self.assertEqual(pathlib.Path(found.executable), expected)
            found = code_editor.find_editor("cursor", env, nothing)
            self.assertEqual(pathlib.Path(found.executable), cursor / "Cursor.exe")

            which = lambda name: str(on_path / "bin" / "code.CMD") if name == "code" else None  # noqa: E731
            found = pathlib.Path(code_editor.find_editor("vscode", env, which).executable)
            self.assertEqual(found, on_path / "Code.exe" if WINDOWS else on_path / "bin" / "code.CMD")

            if WINDOWS:  # without Code.exe the code.cmd shim is used
                (on_path / "Code.exe").unlink()
                self.assertEqual(pathlib.Path(code_editor.find_editor("vscode", env, which).executable).name.lower(),
                                 "code.cmd")
            self.assertIsNone(code_editor.find_editor("vscode", {}, nothing))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class OpenInCodeEditorTest(_EditorCase):
    def test_open_path_opens_scripts_at_the_line(self):
        path = write(self.root / "src" / "systems" / "a b.ts", "export {};\n")
        self.assertEqual(bpy.ops.gamestudio.open_path(filepath=str(path), line=7, column=3), {"FINISHED"})
        self.assertEqual(self.commands[-1], [VSCODE_EXE, str(self.root), "-g", f"{path}:7:3"])
        # Without a line (the sidebar console's button), the file opens at the top.
        bpy.ops.gamestudio.open_path(filepath="src/systems/a b.ts")
        self.assertEqual(self.commands[-1][-1], f"{path}:1:1")
        self.assertEqual(self.opened, [])

    def test_open_project(self):
        self.assertEqual(bpy.ops.gamestudio.open_code_project(), {"FINISHED"})
        self.assertEqual(self.commands, [[VSCODE_EXE, str(self.root)]])
        project.set_project_root_override(None)
        self.assertFalse(bpy.ops.gamestudio.open_code_project.poll())

    def test_custom_system_and_missing_editors(self):
        program = write(pathlib.Path(tempfile.mkdtemp(dir=helpers.tmp_base())) / "My Editor" / "ed.exe")
        try:
            prefs._defaults.code_editor = "CUSTOM"
            prefs._defaults.code_editor_command = f'"{program}" --line {{line}} "{{file}}"'
            path = write(self.root / "src" / "components" / "coin.ts")
            bpy.ops.gamestudio.open_path(filepath=str(path), line=4)
            self.assertEqual(self.commands[-1], [str(program), "--line", "4", str(path)])
            self.assertIn(subprocess.list2cmdline([str(program)]), code_editor.command_line(self.commands[-1]))

            prefs._defaults.code_editor_command = '"C:\\No Such Folder\\ed.exe" {file}'
            with self.assertRaises(RuntimeError) as cm:
                bpy.ops.gamestudio.open_path(filepath=str(path))
            self.assertIn("Could not find the editor program", str(cm.exception))

            prefs._defaults.code_editor = "SYSTEM"
            bpy.ops.gamestudio.open_path(filepath=str(path), line=4)
            self.assertEqual(self.opened, [path])

            prefs._defaults.code_editor = "CURSOR"
            with self.assertRaises(RuntimeError) as cm:
                bpy.ops.gamestudio.open_code_project()
            self.assertIn("Cursor was not found", str(cm.exception))
        finally:
            shutil.rmtree(program.parent.parent, ignore_errors=True)

    def test_open_newest_error(self):
        target = write(self.root / "src" / "systems" / "spawn.ts", "x\n")
        logs = link.get_link().logs
        self.assertEqual(bpy.ops.gamestudio.open_latest_error(), {"CANCELLED"})  # nothing to open
        logs.append({"t": "error", "level": "error", "msg": "Old", "file": "src/systems/spawn.ts", "line": 2, "col": 4,
                     "time": 1.0})
        logs.append({"t": "log", "level": "info", "msg": "hello", "time": 2.0})
        logs.append({"t": "error", "level": "error", "msg": "No location", "time": 3.0})
        self.assertEqual(bpy.ops.gamestudio.open_latest_error(), {"FINISHED"})
        self.assertEqual(self.commands[-1][-1], f"{target}:2:4")
        logs.append({"t": "error", "level": "error", "kind": "build", "msg": "Transform failed",
                     "file": "src/systems/spawn.ts", "line": 9, "col": 1, "time": 4.0})
        bpy.ops.gamestudio.open_latest_error()
        self.assertEqual(self.commands[-1][-1], f"{target}:9:1")


# --- Error locations ---------------------------------------------------------------------


class LocationParsingTest(unittest.TestCase):
    def test_locations_in_text(self):
        L = locations.Location
        cases = {
            "at update (http://localhost:5173/src/systems/coins.ts?t=1712:12:5)":
                [L("/src/systems/coins.ts", 12, 5)],
            "at run (http://127.0.0.1:5190/@fs/F:/Dev%20Games/engine/src/ecs/scheduler.ts:40:12)":
                [L("F:/Dev Games/engine/src/ecs/scheduler.ts", 40, 12)],
            "update@http://localhost:5173/src/a.ts:3:1\nstep@http://localhost:5173/src/b.ts:4:2":
                [L("/src/a.ts", 3, 1), L("/src/b.ts", 4, 2)],
            "src/systems/coins.ts(12,5): error TS2322: Type 'string' is not assignable": [L("src/systems/coins.ts", 12, 5)],
            "src/systems/coins.ts (3:9): Expected ';'": [L("src/systems/coins.ts", 3, 9)],
            "at F:\\My Games\\demo\\src\\a b.ts:3:9": [L("F:\\My Games\\demo\\src\\a b.ts", 3, 9)],
            "file:///F:/My%20Games/src/a.ts:3:4": [L("F:/My Games/src/a.ts", 3, 4)],
            "[hmr] Failed to reload /src/systems/coins.ts. This could be due to syntax errors":
                [L("/src/systems/coins.ts")],
            "src/components/coin.ts:7": [L("src/components/coin.ts", 7)],
            "/src/a.ts?t=99:4:5": [L("/src/a.ts", 4, 5)],
            "loaded vite.config.ts at 10:30:00, version 1.2.3": [],
        }
        for text, expected in cases.items():
            self.assertEqual(locations.parse_text(text), expected, text)

    def test_entry_locations(self):
        error = {"t": "error", "msg": "TypeError: x", "file": "src/systems/a.ts", "line": 3, "col": 7,
                 "stack": "TypeError: x\n    at update (src/systems/a.ts:3:7)\n    at run (http://h:1/src/main.ts:1:2)"}
        self.assertEqual(locations.entry_locations(error), [locations.Location("src/systems/a.ts", 3, 7),
                                                            locations.Location("/src/main.ts", 1, 2)])
        log = {"t": "log", "level": "info", "msg": "hi", "source": "src/systems/b.ts:12:5"}
        self.assertEqual(locations.entry_locations(log), [locations.Location("src/systems/b.ts", 12, 5)])
        self.assertEqual(locations.parse_location("http://localhost:5173/src/a.ts?t=1:3:4"),
                         locations.Location("/src/a.ts", 3, 4))
        self.assertEqual(locations.Location("F:/x/src/a.ts", 3, 4).text(), "a.ts:3:4")
        self.assertEqual(locations.entry_locations({"msg": "no location here"}), [])


class LocationResolveTest(helpers.ProjectTestCase):
    def test_resolve_entries_against_the_project(self):
        own = write(self.root / "src" / "systems" / "a.ts")
        outside = write(pathlib.Path(tempfile.mkdtemp(dir=helpers.tmp_base())) / "engine" / "src" / "scheduler.ts")
        try:
            entry = {"t": "error", "level": "error", "msg": "Boom", "file": outside.as_posix(), "line": 40, "col": 2,
                     "stack": "at a (http://localhost:5173/src/systems/a.ts?t=5:3:7)\n"
                              "at dep (http://localhost:5173/node_modules/.vite/deps/three.js?v=1:9:9)\n"
                              "at gone (http://localhost:5173/src/systems/missing.ts:1:1)"}
            found = locations.resolve_entry(entry, self.root)
            self.assertEqual([(r.path, r.line, r.col) for r in found], [(outside, 40, 2), (own, 3, 7)])
            self.assertEqual((found[0].in_project, found[1].in_project), (False, True))
            self.assertEqual(found[1].text, "a.ts:3:7")
            own.unlink()
            self.assertIs(locations.resolve_entry(entry, self.root), found)  # cached per entry and root
            self.assertEqual(locations.resolve(locations.Location("src/x.ts"), None), None)
            # Vite's "/src/..." is project-relative even where a leading slash means absolute.
            own_again = write(self.root / "src" / "systems" / "b.ts")
            self.assertEqual(locations.resolve(locations.Location("/src/systems/b.ts"), self.root), own_again)
            self.assertEqual(locations.resolve(locations.Location(outside.as_posix()), self.root), outside)
        finally:
            shutil.rmtree(outside.parent.parent.parent, ignore_errors=True)

    def test_newest_error(self):
        write(self.root / "src" / "a.ts")
        logs = [{"level": "error", "msg": "located", "file": "src/a.ts", "line": 1, "time": 1.0},
                {"level": "warn", "msg": "warning", "time": 2.0},
                {"t": "error", "msg": "bare", "time": 3.0}]
        self.assertEqual(locations.latest_error(logs)["msg"], "bare")
        self.assertEqual(locations.latest_error(logs, with_location_in=self.root)["msg"], "located")
        self.assertIsNone(locations.latest_error(logs, since=3.0))
        self.assertIsNone(locations.latest_error([{"level": "info", "msg": "x"}]))


# --- New Component / New System --------------------------------------------------------------


MAIN_TS = ("import { createGame } from '@gamestudio/engine';\n"
           "import.meta.glob('./components/*.ts', { eager: true });\n"
           "import.meta.glob('./systems/*.ts', { eager: true });\n"
           "createGame({ canvas: '#game' });\n")


class ScaffoldTest(_EditorCase):
    def setUp(self):
        super().setUp()
        write(self.root / "src" / "main.ts", MAIN_TS)

    def test_names(self):
        for name, stem in (("HealthPack", "health-pack"), ("HUDLabel", "hud-label"), ("Level2Door", "level2-door"),
                           ("Coin", "coin")):
            self.assertEqual(scaffold.kebab(name), stem)
        self.assertEqual(scaffold.check_name("Regen", "system"), "RegenSystem")
        self.assertEqual(scaffold.check_name("RegenSystem", "system"), "RegenSystem")
        self.assertEqual(scaffold.file_name("RegenSystem", "system"), "regen.ts")
        self.assertEqual(scaffold.check_name("HealthPack", "component"), "HealthPack")
        for bad in ("", "healthPack", "Health Pack", "Health-Pack", "1Up", "Héros", "A" * 42):
            with self.subTest(name=bad), self.assertRaises(scaffold.ScaffoldError):
                scaffold.check_name(bad, "component")
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.check_name("System", "system")

    def test_new_component(self):
        info = scaffold.create(self.root, "component", "HealthPack")
        path = self.root / "src" / "components" / "health-pack.ts"
        self.assertEqual((info["path"], info["rel"], info["name"], info["warning"]),
                         (path, "src/components/health-pack.ts", "HealthPack", ""))
        text = path.read_text(encoding="utf-8")
        self.assertIn("export const HealthPack = defineComponent('HealthPack', {", text)
        self.assertIn("from '@gamestudio/engine/schema'", text)
        self.assertNotIn("{{", text)
        self.assertIn("field.float(", text.splitlines()[info["line"] - 1])
        self.assertIn("HealthPack", scripts.component_names(self.root))

    def test_refusals(self):
        scaffold.create(self.root, "component", "Lamp")
        before = (self.root / "src" / "components" / "lamp.ts").read_text(encoding="utf-8")
        with self.assertRaisesRegex(scaffold.ScaffoldError, "already exists"):
            scaffold.create(self.root, "component", "Lamp")
        self.assertEqual((self.root / "src" / "components" / "lamp.ts").read_text(encoding="utf-8"), before)
        door = write(self.root / "src" / "components" / "door.ts", "// my own file\n")
        with self.assertRaisesRegex(scaffold.ScaffoldError, "door.ts already exists"):
            scaffold.create(self.root, "component", "Door")
        self.assertEqual(door.read_text(encoding="utf-8"), "// my own file\n")
        with self.assertRaisesRegex(scaffold.ScaffoldError, "built in to the engine"):
            scaffold.create(self.root, "component", "Spin")
        with self.assertRaisesRegex(scaffold.ScaffoldError, "src/components/coin.ts"):
            scaffold.create(self.root, "component", "Coin")  # in the registry, its file not on disk
        write(self.root / "src" / "components" / "misc.ts",
              "export const Torch = defineComponent('Torch', {});\nexport const Gem = defineComponent(\"Gem\");\n")
        scripts.invalidate()
        with self.assertRaisesRegex(scaffold.ScaffoldError, "already defined"):
            scaffold.create(self.root, "component", "Gem")
        scaffold.create(self.root, "system", "Regen")
        with self.assertRaisesRegex(scaffold.ScaffoldError, "regen.ts already exists"):
            scaffold.create(self.root, "system", "RegenSystem")
        write(self.root / "src" / "systems" / "other.ts", "export default defineSystem({\n  name: 'MoveSystem',\n});\n")
        scripts.invalidate()
        with self.assertRaisesRegex(scaffold.ScaffoldError, "MoveSystem is already defined"):
            scaffold.create(self.root, "system", "Move")
        with self.assertRaisesRegex(scaffold.ScaffoldError, "Save the .blend"):
            scaffold.create(None, "system", "Fine")

    def test_new_system_imports_its_query(self):
        write(self.root / "src" / "components" / "coin.ts", "export const Coin = defineComponent('Coin', {});\n")
        text = scaffold.create(self.root, "system", "Collect", "Coin")["path"].read_text(encoding="utf-8")
        self.assertIn("import { defineSystem } from '@gamestudio/engine';\nimport { Coin } from '../components/coin';\n",
                      text)
        self.assertIn("name: 'CollectSystem'", text)
        self.assertIn("query: [Coin]", text)
        text = scaffold.create(self.root, "system", "Wobble", "Spin")["path"].read_text(encoding="utf-8")
        self.assertIn("import { defineSystem, Spin } from '@gamestudio/engine';\n\n/**", text)
        text = scaffold.create(self.root, "system", "Clock")["path"].read_text(encoding="utf-8")
        self.assertNotIn("\n  query:", text)  # only mentioned in the comment
        self.assertNotIn("{{", text)
        with self.assertRaisesRegex(scaffold.ScaffoldError, "Nope"):
            scaffold.create(self.root, "system", "Broken", "Nope")
        self.assertFalse((self.root / "src" / "systems" / "broken.ts").exists())

    def test_operators_create_then_open(self):
        self.assertEqual(bpy.ops.gamestudio.new_component(name="Health"), {"FINISHED"})
        path = self.root / "src" / "components" / "health.ts"
        self.assertTrue(path.is_file())
        exe, folder, flag, where = self.commands[-1]
        self.assertEqual((exe, folder, flag), (VSCODE_EXE, str(self.root), "-g"))
        line = int(where.rsplit(":", 2)[1])
        self.assertIn("field.float(", path.read_text(encoding="utf-8").splitlines()[line - 1])
        self.assertEqual(bpy.ops.gamestudio.new_system(name="Regen", component="Health"), {"FINISHED"})
        self.assertIn("query: [Health]", (self.root / "src" / "systems" / "regen.ts").read_text(encoding="utf-8"))
        self.assertEqual(bpy.ops.gamestudio.new_system(name="Timer", open_editor=False), {"FINISHED"})
        self.assertEqual(len(self.commands), 2)
        with self.assertRaises(RuntimeError) as cm:
            bpy.ops.gamestudio.new_component(name="health")
        self.assertIn("PascalCase", str(cm.exception))
        project.set_project_root_override(None)
        self.assertFalse(bpy.ops.gamestudio.new_component.poll())

    def test_discovery_warning(self):
        self.assertEqual(scaffold.discovery_warning(self.root, "system"), "")
        write(self.root / "src" / "main.ts", "import './systems/only-this';\n")
        warning = scaffold.create(self.root, "system", "Lonely")["warning"]
        self.assertIn("does not load src/systems/*.ts", warning)


# --- The script list ---------------------------------------------------------------------------


class ScriptListTest(_EditorCase):
    def test_scan_is_cached_and_polled(self):
        write(self.root / "src" / "components" / "lamp.ts", "export const Lamp = defineComponent('Lamp');\n")
        write(self.root / "src" / "components" / "notes.md", "not a script")
        write(self.root / "src" / "components" / "types.d.ts", "export {};")
        listed = scripts.listing(self.root)
        self.assertEqual([(s["file"], s["names"]) for s in listed["component"]], [("lamp.ts", ["Lamp"])])
        self.assertEqual(listed["system"], [])
        self.assertFalse(scripts.poll())
        system = write(self.root / "src" / "systems" / "move.ts", "defineSystem({ name: 'MoveSystem' })")
        self.assertEqual(scripts.listing(self.root)["system"], [])  # draw code never rescans
        self.assertTrue(scripts.poll())
        self.assertEqual([s["names"] for s in scripts.listing(self.root)["system"]], [["MoveSystem"]])
        self.assertFalse(scripts.poll())
        system.write_text("defineSystem({ query: [], name: 'WalkSystem', update() {} })", encoding="utf-8")
        os.utime(system, (1e9, 1e9))
        self.assertTrue(scripts.poll())
        self.assertEqual(scripts.system_names(self.root), {"WalkSystem"})
        system.unlink()
        self.assertTrue(scripts.poll())
        self.assertEqual(scripts.system_names(self.root), set())

    def test_components_merge_the_registry(self):
        write(self.root / "src" / "components" / "coin.ts", "export const Coin = defineComponent('Coin', {});\n")
        write(self.root / "src" / "components" / "lamp.ts", "export const Lamp = defineComponent('Lamp');\n")
        built = write(self.root / "src" / "components" / "built.ts", "const name = 'Showcase';\n")
        registry.set_registry(helpers.modified_registry(
            lambda d: helpers.component(d, "Showcase").update(source="src/components/built.ts")))
        found = {s["file"]: [(name, entry is not None) for name, entry in s["entries"]]
                 for s in scripts.components(self.root)}
        self.assertEqual(found, {"built.ts": [("Showcase", True)], "coin.ts": [("Coin", True)],
                                 "lamp.ts": [("Lamp", False)]})
        self.assertTrue(built.is_file())

    def test_project_editor_lists_scripts(self):
        write(self.root / "src" / "components" / "coin.ts", "export const Coin = defineComponent('Coin', {});\n")
        write(self.root / "src" / "components" / "lamp.ts", "export const Lamp = defineComponent('Lamp');\n")
        write(self.root / "src" / "systems" / "regen.ts", "export default defineSystem({ name: 'RegenSystem' });\n")
        write(self.root / "src" / "systems" / "empty.ts", "")
        project_editor.invalidate()
        texts, ops = [], []
        project_editor.draw_scripts(Layout(self, texts, ops), fake_context())
        for expected in ("New Component...", "New System...", "Coin", "coin.ts", "Lamp", "lamp.ts", "RegenSystem",
                         "regen.ts", "empty.ts"):
            self.assertIn(expected, texts)
        self.assertIn("not in Add Component yet", " ".join(texts))
        opened = {pathlib.Path(op["filepath"]).name for op in ops if op["op"] == "gamestudio.open_path"}
        self.assertTrue({"coin.ts", "lamp.ts", "regen.ts", "empty.ts"} <= opened)
        texts = []
        project_editor.draw_overview(Layout(self, texts), fake_context())
        self.assertIn("Open in VS Code", texts)
        menu = types.SimpleNamespace(layout=Layout(self, texts := []))
        project_editor.draw_project_menu(menu, fake_context())
        self.assertEqual(texts, ["Open Project in VS Code", "New Component...", "New System..."])


class ConsoleLocationTest(_EditorCase):
    def tearDown(self):
        console._dismissed["time"] = None
        super().tearDown()

    def test_console_opens_sources(self):
        system = write(self.root / "src" / "systems" / "coins.ts", "x\n")
        engine = write(self.root / "node_modules" / "@gamestudio" / "engine" / "src" / "scheduler.ts", "x\n")
        logs = link.get_link().logs
        logs.append({"t": "log", "level": "info", "msg": "Coin collected", "source": "src/systems/coins.ts:12:5",
                     "time": 1.0})
        logs.append({"t": "error", "level": "error", "msg": "TypeError: boom", "file": "src/systems/coins.ts",
                     "line": 30, "col": 9, "time": 2.0,
                     "stack": "TypeError: boom\n at update (http://localhost:5173/src/systems/coins.ts?t=1:30:9)\n"
                              " at run (http://localhost:5173/node_modules/@gamestudio/engine/src/scheduler.ts:40:12)"})
        logs.append({"t": "error", "level": "error", "kind": "build", "msg": "Transform failed with 1 error",
                     "file": "src/systems/coins.ts", "line": 6, "col": 15, "time": 3.0})
        logs.append({"t": "log", "level": "warn", "msg": "unmapped", "source": "http://localhost:5173/@vite/client:1:1",
                     "time": 4.0})
        bpy.context.window_manager.gs_console_stack = True
        try:
            texts, ops = [], []
            console.draw_log(Layout(self, texts, ops), fake_context())
        finally:
            bpy.context.window_manager.gs_console_stack = False
        opens = [(pathlib.Path(op["filepath"]), op.get("line"), op.get("column"), op["text"]) for op in ops
                 if op["op"] == "gamestudio.open_path"]
        self.assertEqual(opens[0], (system, 6, 15, "Open"))  # the banner: the newest error
        self.assertIn((system, 12, 5, ""), opens)
        self.assertIn((system, 30, 9, ""), opens)
        self.assertIn((engine, 40, 12, ""), opens)  # a stack frame
        self.assertIn("Build error: Transform failed with 1 error", texts)
        self.assertIn("    at client:1:1", texts)  # Vite's own module: shown, not openable
        texts = []
        console.draw_header(Layout(self, texts, ops := []), fake_context())
        self.assertIn("2 errors, 1 warning", texts)
        self.assertIn("gamestudio.open_latest_error", [op["op"] for op in ops])

    def test_dismissing_the_banner(self):
        write(self.root / "src" / "a.ts")
        link.get_link().logs.append({"t": "error", "level": "error", "msg": "Boom", "file": "src/a.ts", "line": 1,
                                     "time": 5.0})
        self.assertEqual(console.newest_error()["msg"], "Boom")
        self.assertEqual(bpy.ops.gamestudio.dismiss_errors(), {"FINISHED"})
        self.assertIsNone(console.newest_error())
        texts = []
        console.draw_banner(Layout(self, texts), fake_context())
        self.assertEqual(texts, [])
        link.get_link().logs.append({"t": "error", "level": "error", "msg": "Again", "time": console._dismissed["time"] + 1})
        self.assertEqual(console.newest_error()["msg"], "Again")


class ProgramPathTest(unittest.TestCase):
    def test_program_paths_hide_the_user_folder(self):
        home = pathlib.Path.home()
        inside = str(home / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe")
        self.assertTrue(prefs.short_program_path(inside).startswith("~"))
        elsewhere = os.path.join(os.sep, "Users", "someone", "Programs", "Microsoft VS Code", "Code.exe")
        self.assertEqual(prefs.short_program_path(elsewhere), os.path.join("…", "Microsoft VS Code", "Code.exe"))
        self.assertEqual(prefs.short_program_path(os.path.join("C:" + os.sep, "ed.exe")),
                         os.path.join("C:" + os.sep, "ed.exe"))  # short paths stay as they are


if __name__ == "__main__":
    unittest.main()
