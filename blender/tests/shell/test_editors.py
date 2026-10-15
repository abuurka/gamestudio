# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom editors (host.editors and the providers in gamestudio/editors/).

Runs inside run_shell_tests.py, on both hosts:
  * stock Blender (the extension): providers fall back to 3D View sidebar tabs; fork-only tests skip;
  * GameStudio Editor (host.has("extension_editors")): real Extension Editor panels.

Panels are not drawn in --background, so a mock layout checks that every
property, operator, menu and icon used by the draw code exists.
"""

import importlib
import json
import os
import pathlib
import shutil
import tempfile
import types
import unittest
from unittest import mock

import bpy

EXTENSION = "bl_ext.system.gamestudio"

host = importlib.import_module(f"{EXTENSION}.host")
project = importlib.import_module(f"{EXTENSION}.core.project")
link = importlib.import_module(f"{EXTENSION}.core.link")
play = importlib.import_module(f"{EXTENSION}.core.play")
devserver = importlib.import_module(f"{EXTENSION}.core.devserver")
game_view = importlib.import_module(f"{EXTENSION}.editors.game_view")
PROVIDERS = {
    "GAMESTUDIO_INSPECTOR": importlib.import_module(f"{EXTENSION}.editors.inspector").InspectorEditor,
    "GAMESTUDIO_CONSOLE": importlib.import_module(f"{EXTENSION}.editors.console").ConsoleEditor,
    "GAMESTUDIO_PROJECT": importlib.import_module(f"{EXTENSION}.editors.project").ProjectEditor,
    "GAMESTUDIO_ASSISTANT": importlib.import_module(f"{EXTENSION}.editors.assistant").AssistantEditor,
}
# What stock Blender shows instead of the providers that have no sidebar fallback.
SIDEBAR_EQUIVALENTS = {"GAMESTUDIO_INSPECTOR": "GAMESTUDIO_PT_object",  # in Object properties
                       "GAMESTUDIO_ASSISTANT": "GS_PT_ai_main_view3d"}

FORK = host.has("extension_editors")
# The docked Game View is checked against what the runner says it started, so a fork build
# that lost the capability fails instead of skipping.
GAME_VIEW_FORK = os.environ.get("GS_EDITOR_KIND") == "fork"
ICONS = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
LAYOUT_ATTRS = set(bpy.types.UILayout.bl_rna.properties.keys())


# --- A UILayout stand-in that validates every call ------------------------------------


class _OperatorProps:
    def __init__(self, test, idname):
        category, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
        object.__setattr__(self, "_names", set(rna.properties.keys()))
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "_idname", idname)

    def __setattr__(self, name, value):
        self._test.assertIn(name, self._names, f"{self._idname} has no property {name!r}")


class MockLayout:
    operator_context = "INVOKE_REGION_WIN"  # read back by the draw wrapper of menus with appended items

    def __init__(self, test, texts=None):
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "texts", texts if texts is not None else [])

    def __setattr__(self, name, value):
        self._test.assertIn(name, LAYOUT_ATTRS, f"UILayout has no attribute {name!r}")
        object.__setattr__(self, name, value)

    def _icon(self, icon):
        self._test.assertIn(icon, ICONS, f"unknown icon {icon!r}")

    def _child(self, *_args, **_kwargs):
        return MockLayout(self._test, self.texts)

    row = column = box = split = grid_flow = column_flow = _child

    def panel(self, idname, default_closed=False):
        self._test.assertIsInstance(idname, str)
        return self._child(), self._child()

    def label(self, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self.texts.append(text)

    def prop(self, data, prop, text=None, icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertIn(prop, data.bl_rna.properties, f"{data!r} has no property {prop!r}")

    def textbox(self, data, prop, **_kwargs):
        self._test.assertIn(prop, data.bl_rna.properties, f"{data!r} has no property {prop!r}")

    def operator(self, idname, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        if text:
            self.texts.append(text)
        return _OperatorProps(self._test, idname)

    def menu(self, menu, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertTrue(hasattr(bpy.types, menu), f"menu {menu} is not registered")
        self.texts.append(text)

    def template_header(self):
        self.texts.append("<editor type>")

    def separator(self, **_kwargs):
        pass

    def separator_spacer(self):
        pass

    def progress(self, factor=0.0, text="", **_kwargs):
        self._test.assertTrue(0.0 <= factor <= 1.0, factor)
        self.texts.append(text)

    def context_pointer_set(self, name, data):
        self._test.assertIsInstance(name, str)


class _FakeSelf:
    """Stands in for a Panel or Header instance so its draw() can run on a MockLayout."""

    def __init__(self, cls, layout):
        self.layout = layout
        self._cls = cls

    def __getattr__(self, name):
        return getattr(self._cls, name)


def fake_context(idname="", active_object=None, in_editor=True):
    space = types.SimpleNamespace(type="EXTENSION_EDITOR" if in_editor else "VIEW_3D", provider_idname=idname)
    region = types.SimpleNamespace(type="WINDOW" if in_editor else "UI", width=640, height=900)
    return types.SimpleNamespace(scene=bpy.context.scene, active_object=active_object, space_data=space,
                                 region=region, window_manager=bpy.context.window_manager,
                                 preferences=bpy.context.preferences, workspace=bpy.data.workspaces[0], area=None)


def _draw_nothing(layout, context):
    layout.label(text="test panel")


class _TestEditor:
    idname = "GSTEST_EDITOR"
    label = "Test Editor"
    icon = "NONE"
    description = "Registered by the shell tests"
    panels = [host.editors.panel("main", "Main", _draw_nothing),
              host.editors.panel("detail", "Detail", _draw_nothing, parent="main", options={"DEFAULT_CLOSED"}),
              host.editors.panel("side", "Side", _draw_nothing, region="UI")]


# --- Both hosts ---------------------------------------------------------------------


class _ProjectFixture(unittest.TestCase):
    """A small project on disk (scenes, scripts) and a selected object."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_shell_proj_"))
        (self.root / "gamestudio.json").write_text(json.dumps({"name": "shell-test", "startScene": "main"}),
                                                   encoding="utf-8")
        for rel in ("scenes/main.blend", "scenes/level2.blend", "src/components/coin.ts", "src/systems/spin.ts",
                    "package.json"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        (self.root / ".gamestudio").mkdir()
        project.set_project_root_override(self.root)
        self.obj = bpy.data.objects.new("GSShellTestObject", None)
        bpy.context.scene.collection.objects.link(self.obj)

    def tearDown(self):
        host.editors.unregister(_TestEditor)
        project.set_project_root_override(None)
        bpy.data.objects.remove(self.obj, do_unlink=True)
        link.get_link().logs.clear()
        shutil.rmtree(self.root, ignore_errors=True)


class EditorProviderTests(_ProjectFixture):
    """Run on stock Blender and on GameStudio Editor."""

    def test_providers_are_registered(self):
        for idname, provider in PROVIDERS.items():
            self.assertTrue(host.editors.is_registered(idname), idname)
            self.assertIs(host.editors.provider(idname), provider)
            self.assertIn(provider.icon, ICONS, idname)

    def test_game_view_header_is_set(self):
        self.assertIs(host.editors.header(game_view.IDNAME), game_view.draw_header)

    def test_register_and_unregister_are_idempotent(self):
        host.editors.register(_TestEditor)
        classes = host.editors.registered_classes(_TestEditor.idname)
        host.editors.register(_TestEditor)
        self.assertEqual(host.editors.registered_classes(_TestEditor.idname), classes)
        self.assertEqual(host.editors.providers().count(_TestEditor.idname), 1)
        for cls in classes:
            self.assertTrue(cls.is_registered, cls.bl_idname)
        host.editors.unregister(_TestEditor)
        host.editors.unregister(_TestEditor)
        self.assertFalse(host.editors.is_registered(_TestEditor.idname))
        for cls in classes:
            self.assertFalse(cls.is_registered, cls.bl_idname)
        self.assertFalse(hasattr(bpy.types, "GSTEST_EDITOR_PT_main"))

    def test_reregistering_an_idname_replaces_the_provider(self):
        host.editors.register(_TestEditor)
        old = host.editors.registered_classes(_TestEditor.idname)
        reloaded = type("_TestEditor", (), {k: v for k, v in vars(_TestEditor).items() if not k.startswith("__")})
        host.editors.register(reloaded)
        try:
            self.assertIs(host.editors.provider(_TestEditor.idname), reloaded)
            self.assertTrue(all(not cls.is_registered for cls in old))
        finally:
            host.editors.unregister(reloaded)

    def test_invalid_providers_are_rejected(self):
        for bad in ("1abc", "has space", "x" * 64, ""):
            provider = type("Bad", (), {"idname": bad, "label": "Bad", "panels": []})
            with self.assertRaises(ValueError, msg=bad):
                host.editors.register(provider)
        with self.assertRaises(ValueError):
            host.editors.panel("Not-Snake", "Bad", _draw_nothing)
        with self.assertRaises(ValueError):
            host.editors.panel("ok", "Bad", _draw_nothing, region="HEADER")

    def test_child_panel_must_share_its_parents_region(self):
        class Mixed(_TestEditor):
            panels = [host.editors.panel("main", "Main", _draw_nothing),
                      host.editors.panel("side", "Side", _draw_nothing, parent="main", region="UI")]

        with self.assertRaises(ValueError):
            host.editors.panel_classes(Mixed, native_editor=False)  # caught on stock too

    def test_generated_classes_for_each_host(self):
        native = host.editors.panel_classes(_TestEditor, native_editor=True)
        self.assertEqual([c.bl_idname for c in native],
                         ["GSTEST_EDITOR_PT_main", "GSTEST_EDITOR_PT_detail", "GSTEST_EDITOR_PT_side"])
        for cls in native:
            self.assertEqual(cls.bl_space_type, "EXTENSION_EDITOR")
            self.assertEqual(cls.bl_context, "GSTEST_EDITOR")
        self.assertEqual(native[0].bl_region_type, "WINDOW")
        self.assertEqual((native[1].bl_region_type, native[1].bl_parent_id), ("WINDOW", "GSTEST_EDITOR_PT_main"))
        self.assertEqual(native[1].bl_options, {"DEFAULT_CLOSED"})
        self.assertEqual((native[2].bl_region_type, native[2].bl_category), ("UI", "Test Editor"))
        for cls in host.editors.panel_classes(_TestEditor, native_editor=False):
            self.assertEqual((cls.bl_space_type, cls.bl_region_type, cls.bl_category), ("VIEW_3D", "UI", "Test Editor"))
            self.assertFalse(getattr(cls, "bl_context", ""))

    def test_every_panel_body_draws(self):
        link.get_link().logs.extend([
            {"t": "log", "level": "info", "msg": "hello"},
            {"t": "log", "level": "warn", "msg": "low fps\nsecond line"},
            {"t": "error", "level": "error", "msg": "Boom", "file": "src/components/coin.ts", "line": 3, "col": 7},
        ])
        for in_editor in (True, False):
            for obj in (None, self.obj):
                for idname, provider in PROVIDERS.items():
                    context = fake_context(idname, obj, in_editor)
                    for spec in provider.panels:
                        with self.subTest(idname=idname, panel=spec.id, in_editor=in_editor, obj=bool(obj)):
                            visible = spec.poll is None or spec.poll(context)
                            # The assistant's polls only wait for setup; its bodies draw in any state.
                            if visible or idname == "GAMESTUDIO_ASSISTANT":
                                spec.draw(MockLayout(self), context)

    def test_console_filters(self):
        link.get_link().logs.extend([{"level": "info", "msg": "a"}, {"level": "warn", "msg": "b"},
                                     {"level": "error", "msg": "c", "file": "src/components/coin.ts", "line": 1}])
        console = importlib.import_module(f"{EXTENSION}.ui")
        for level, expected in (("ALL", ["a", "b", "c"]), ("WARN", ["b", "c"]), ("ERROR", ["c"])):
            texts = []
            console.draw_console(MockLayout(self, texts), fake_context(), level=level, open_files=True)
            shown = [t for t in texts if t in ("a", "b", "c")]
            self.assertEqual(shown, expected, level)
        self.assertEqual(console.source_path("http://localhost:5173/src/components/coin.ts?t=1"),
                         self.root / "src/components/coin.ts")

    def test_project_editor_lists_scenes_and_scripts(self):
        mod = importlib.import_module(f"{EXTENSION}.editors.project")
        mod.invalidate()
        texts = []
        context = fake_context("GAMESTUDIO_PROJECT")
        for spec in PROVIDERS["GAMESTUDIO_PROJECT"].panels:
            spec.draw(MockLayout(self, texts), context)
        text = "\n".join(texts)
        for expected in ("shell-test", "main  (start scene)", "level2", "coin.ts", "spin.ts", "New Component",
                         "New System"):
            self.assertIn(expected, text)

    def test_headers_draw(self):
        header = host.editors.EXTENSION_EDITOR_HT_header
        for idname in list(PROVIDERS) + [game_view.IDNAME, "UNKNOWN_PROVIDER"]:
            texts = []
            header.draw(_FakeSelf(header, MockLayout(self, texts)), fake_context(idname))
            self.assertEqual(texts[:1], ["<editor type>"], idname)


# --- Stock Blender only ---------------------------------------------------------------


@unittest.skipIf(FORK, "stock Blender only (this build has the native Extension Editor)")
class StockFallbackTests(_ProjectFixture):
    def test_fallback_sidebar_panels_are_registered(self):
        for idname, provider in PROVIDERS.items():
            classes = host.editors.registered_classes(idname)
            if not getattr(provider, "sidebar_fallback", True):
                self.assertEqual(classes, [], idname)
                self.assertTrue(hasattr(bpy.types, SIDEBAR_EQUIVALENTS[idname]), idname)
                continue
            self.assertEqual(len(classes), len(provider.panels), idname)
            for cls in classes:
                self.assertTrue(cls.is_registered, cls.bl_idname)
                self.assertIs(getattr(bpy.types, cls.bl_idname), cls)
                self.assertEqual((cls.bl_space_type, cls.bl_region_type, cls.bl_category),
                                 ("VIEW_3D", "UI", provider.label))

    def test_fallback_panels_are_draw_callable(self):
        drawn = 0
        prefs = importlib.import_module(f"{EXTENSION}.prefs")
        # The sidebar fallbacks show only with the sidebar_tab preference.
        self.enterContext(mock.patch.object(prefs, "sidebar_enabled", return_value=True))
        for idname in PROVIDERS:
            for cls in host.editors.registered_classes(idname):
                context = fake_context(idname, self.obj, in_editor=False)
                with self.subTest(panel=cls.bl_idname):
                    if cls.gs_spec.poll is not None and not cls.poll(context):
                        continue
                    cls.draw(_FakeSelf(cls, MockLayout(self)), context)
                    drawn += 1
        self.assertGreater(drawn, 0)

    def test_shared_header_is_not_registered(self):
        self.assertFalse(host.editors.EXTENSION_EDITOR_HT_header.is_registered)

    def test_fork_only_calls_are_noops(self):
        self.assertFalse(host.editors.available())
        self.assertFalse(host.editors.open("GAMESTUDIO_CONSOLE"))
        self.assertFalse(host.editors.set_hidden_editor_types(["SEQUENCE_EDITOR"]))


# --- GameStudio Editor only -------------------------------------------------------------


@unittest.skipUnless(FORK, "needs GameStudio Editor (host.has('extension_editors'))")
class ForkEditorTests(unittest.TestCase):
    def tearDown(self):
        host.editors.unregister(_TestEditor)

    def test_providers_are_native_editors(self):
        listed = host.native().editors()
        for idname in PROVIDERS:
            self.assertIn(idname, listed)

    def test_panels_target_the_extension_editor(self):
        for idname, provider in PROVIDERS.items():
            classes = host.editors.registered_classes(idname)
            self.assertEqual(len(classes), len(provider.panels), idname)
            for cls in classes:
                self.assertTrue(cls.is_registered, cls.bl_idname)
                self.assertEqual(cls.bl_space_type, "EXTENSION_EDITOR")
                self.assertEqual(cls.bl_context, idname)
                self.assertIn(cls.bl_region_type, ("WINDOW", "UI"))

    def test_shared_header_is_registered(self):
        header = host.editors.EXTENSION_EDITOR_HT_header
        self.assertTrue(header.is_registered)
        self.assertEqual(header.bl_space_type, "EXTENSION_EDITOR")

    def test_register_and_unregister_reach_the_native_side(self):
        host.editors.register(_TestEditor)
        self.assertIn(_TestEditor.idname, host.native().editors())
        host.editors.register(_TestEditor)
        host.editors.unregister(_TestEditor)
        host.editors.unregister(_TestEditor)
        self.assertNotIn(_TestEditor.idname, host.native().editors())

    def test_open_turns_an_area_into_the_editor(self):
        area = bpy.context.window_manager.windows[0].screen.areas[0]
        previous = area.ui_type
        try:
            self.assertTrue(host.editors.open("GAMESTUDIO_CONSOLE", area))
            self.assertEqual(area.type, "EXTENSION_EDITOR")
            self.assertEqual(area.spaces.active.provider_idname, "GAMESTUDIO_CONSOLE")
        finally:
            area.ui_type = previous

    def test_hidden_editor_types(self):
        if not host.has("editor_type_filter"):
            self.skipTest("no editor_type_filter capability")
        try:
            self.assertTrue(host.editors.set_hidden_editor_types(["SEQUENCE_EDITOR", "CompositorNodeTree"]))
        finally:
            host.editors.set_hidden_editor_types([])


# --- The docked Game View (host.game_view) --------------------------------------------------


def _fake_status(**fields):
    current = {"active": True, "state": "connected", "message": "", "page_state": "running", "mode": "docked",
               "paused": False, "user_paused": False, "captured": False, "cursor_grabbed": False,
               "width": 1280, "height": 720, "frame_id": 1, "frames": {}, "timings": {}}
    current.update(fields)
    return current


class GameViewShellTests(unittest.TestCase):
    """host.game_view and the Game View header. Run on stock Blender and on GameStudio Editor."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_gameview_"))

    def tearDown(self):
        play.cancel()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_project(self, download=True):
        """A project whose node_modules hold electron and @gamestudio/host, as npm installs them."""
        root = self.tmp / "game"
        electron = root / "node_modules" / "electron"
        (electron / "dist").mkdir(parents=True)
        (electron / "package.json").write_text("{}", encoding="utf-8")
        (electron / "path.txt").write_text("electron.exe", encoding="utf-8")
        if download:
            (electron / "dist" / "electron.exe").write_bytes(b"")
        host_dir = root / "node_modules" / "@gamestudio" / "host"
        host_dir.mkdir(parents=True)
        (host_dir / "package.json").write_text("{}", encoding="utf-8")
        return root

    def test_capability_and_fallbacks(self):
        gv = host.game_view
        self.assertEqual(gv.available(), host.has("game_view"))
        if gv.available():
            self.skipTest("this build docks the game; see ForkGameViewTests")
        self.assertEqual(gv.status()["state"], "unavailable")
        self.assertFalse(gv.is_running())
        self.assertEqual(gv.areas(), [])
        self.assertFalse(gv.visible())
        self.assertFalse(gv.open())
        self.assertFalse(gv.stop())
        self.assertFalse(gv.detach())
        self.assertFalse(gv.set_paused(True))
        with self.assertRaises(gv.GameViewError):
            gv.start("http://localhost:5173/")
        self.assertTrue(gv.last_error())

    def test_runtime_host_is_found_like_the_probe(self):
        root = self._fake_project()
        electron, app_dir = host.game_view.find_runtime_host(root)
        self.assertEqual(pathlib.Path(electron), root / "node_modules" / "electron" / "dist" / "electron.exe")
        self.assertEqual(pathlib.Path(app_dir), (root / "node_modules" / "@gamestudio" / "host").resolve())

    def test_runtime_host_errors_say_what_to_do(self):
        gv = host.game_view
        only_project = lambda root: [pathlib.Path(root)]  # noqa: E731 - keep the real checkout out of it
        with mock.patch.object(gv, "_search_roots", only_project):
            root = self._fake_project(download=False)
            with self.assertRaises(gv.GameViewError) as cm:
                gv.find_runtime_host(root)
            self.assertIn("require('electron')", str(cm.exception))
            shutil.rmtree(root / "node_modules" / "@gamestudio")
            with self.assertRaises(gv.GameViewError) as cm:
                gv.find_runtime_host(root)
            self.assertIn("@gamestudio/host", str(cm.exception))

    def test_runtime_host_environment_override(self):
        root = self._fake_project()
        exe = self.tmp / "custom" / "host.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"")
        with mock.patch.dict(os.environ, {host.game_view.ENV_ELECTRON: str(exe),
                                          host.game_view.ENV_HOST_DIR: str(self.tmp / "custom")}):
            electron, app_dir = host.game_view.find_runtime_host(root)
        self.assertEqual((pathlib.Path(electron), pathlib.Path(app_dir)), (exe, self.tmp / "custom"))

    def test_status_text(self):
        cases = [
            (_fake_status(state="waiting", page_state=""), "Starting..."),
            (_fake_status(page_state="loading"), "Loading..."),
            (_fake_status(), "Running"),
            (_fake_status(paused=True), "Paused"),
            (_fake_status(captured=True), "Captured (Shift+Esc)"),
            (_fake_status(mode="window"), "Detached"),
            (_fake_status(page_state="crashed"), "Crashed"),
            (_fake_status(state="closed"), "Stopped"),
            (_fake_status(state="failed", message="boom"), "Failed"),
            # The host got through the handshake, then died: a crash, not a start failure.
            (_fake_status(state="failed", message="exited with code 1", host_pid=1234), "Crashed"),
        ]
        for current, expected in cases:
            text, icon = game_view.status_text(current)
            self.assertEqual(text, expected, current)
            self.assertIn(icon, ICONS)

    def test_header_draws_in_every_state(self):
        states = [_fake_status(active=False, state="none"), _fake_status(state="waiting"), _fake_status(),
                  _fake_status(page_state="crashed"), _fake_status(state="failed", message="no GPU")]
        gv = host.game_view
        for current in states:
            with self.subTest(state=current["state"]), \
                    mock.patch.object(gv, "available", lambda: True), \
                    mock.patch.object(gv, "status", lambda current=current: current):
                texts = []
                game_view.draw_header(MockLayout(self, texts), fake_context(game_view.IDNAME))
                self.assertIn("Detach", texts)
                self.assertIn("DevTools", texts)
                # Play starts the docked game; Restart only exists once there is one.
                self.assertEqual("Restart" in texts, current["active"])
                self.assertNotIn("Start", texts)

    def test_header_play_buttons_match_the_top_bar(self):
        class Recorder(MockLayout):
            def _child(self, *_args, **_kwargs):
                return Recorder(self._test, self.texts)

            row = column = box = split = _child

            def operator(self, idname, text="", icon="NONE", **kwargs):
                calls.append((idname, icon, bool(kwargs.get("depress"))))
                return super().operator(idname, text=text, icon=icon, **kwargs)

        class Playing:
            connected = True
            game_state = "playing"

            def game_peers(self):
                return [{"peer": "p1", "role": "game"}]

        calls = []
        gv = host.game_view
        with mock.patch.object(link, "get_link", Playing), mock.patch.object(gv, "available", lambda: True), \
                mock.patch.object(gv, "status", lambda: _fake_status()):
            game_view.draw_header(Recorder(self), fake_context(game_view.IDNAME))
        self.assertEqual(calls[:4], [("gamestudio.play", "PLAY", True), ("gamestudio.pause", "PAUSE", False),
                                     ("gamestudio.step", "FRAME_NEXT", False),
                                     ("gamestudio.stop", "SNAP_FACE", False)])
        self.assertIn(("gamestudio.game_view_stop", "SNAP_FACE", False), calls)

    def test_play_docks_the_game_when_a_game_view_shows(self):
        gv = host.game_view
        started, windows = [], []
        with mock.patch.object(gv, "available", lambda: True), \
                mock.patch.object(gv, "visible", lambda context=None: True), \
                mock.patch.object(gv, "is_running", lambda: False), \
                mock.patch.object(gv, "start", lambda url, **kwargs: started.append(url)), \
                mock.patch.object(devserver, "open_game_window", lambda url: windows.append(url) or "app"):
            self.assertEqual(play.open_game("http://localhost:5173/"), "docked")
            self.assertEqual((started, windows), (["http://localhost:5173/"], []))

            def fail(url, **kwargs):
                raise gv.GameViewError("Electron was not found")

            with mock.patch.object(gv, "start", fail):
                self.assertEqual(play.open_game("http://localhost:5173/"), "app")
                self.assertEqual(play.open_game("http://localhost:5173/", window_fallback=False), "")
            self.assertEqual(windows, ["http://localhost:5173/"])

            with mock.patch.object(gv, "is_running", lambda: True):
                self.assertEqual(play.open_game("http://localhost:5173/"), "docked")
            self.assertEqual(len(started), 1, "a running docked game is kept, not restarted")

    def test_play_waits_for_the_docked_game_when_a_browser_tab_plays(self):
        gv = host.game_view
        sent = []

        class FakeLink:
            connected = True
            session = {"url": "http://localhost:5173/"}
            peers = [{"peer": "p1", "role": "game", "client": "gamestudio-engine (Chrome/152)"}]

            def game_peers(self):
                return self.peers

        fake = FakeLink()
        docked = {"state": "waiting", "page_state": "", "host_pid": 4321}
        with mock.patch.object(link, "get_link", lambda: fake), \
                mock.patch.object(gv, "available", lambda: True), \
                mock.patch.object(gv, "visible", lambda context=None: True), \
                mock.patch.object(gv, "status", lambda: docked), \
                mock.patch.object(gv, "is_running", lambda: True), \
                mock.patch.object(play, "send_play", lambda client, result, start=True: sent.append(result)):
            self.assertFalse(play.request_play({"rev": 1}), "a browser tab is not the game in the Game View")
            play.pump()
            self.assertEqual(sent, [])
            fake.peers = fake.peers + [{"peer": "p2", "role": "game",
                                        "client": "gamestudio-engine (Electron/44 GameStudioHost/0.1.0 (pid 4321))"}]
            self.assertEqual(play.docked_peer(fake)["peer"], "p2")
            play.pump()
            self.assertEqual(sent, [{"rev": 1}])
            self.assertTrue(play.request_play({"rev": 2}), "the docked game is on the link: play at once")

    def test_play_opens_a_window_without_a_game_view(self):
        gv = host.game_view
        windows = []
        with mock.patch.object(gv, "visible", lambda context=None: False), \
                mock.patch.object(devserver, "open_game_window", lambda url: windows.append(url) or "browser"):
            self.assertFalse(play.can_dock())
            self.assertEqual(play.open_game("http://localhost:5173/"), "browser")
            self.assertEqual(play.open_game("http://localhost:5173/", window_fallback=False), "")
        self.assertEqual(windows, ["http://localhost:5173/"])

    def test_operators_are_registered(self):
        for op in ("game_view_start", "game_view_stop", "game_view_detach", "game_view_devtools"):
            self.assertTrue(hasattr(bpy.ops.gamestudio, op), op)
        self.assertEqual(bpy.ops.gamestudio.game_view_start.poll(), host.game_view.available())
        self.assertFalse(bpy.ops.gamestudio.game_view_stop.poll())
        self.assertFalse(bpy.ops.gamestudio.game_view_devtools.poll(), "no game to inspect")
        self.assertFalse(host.game_view.open_devtools())


@unittest.skipUnless(GAME_VIEW_FORK, "needs GameStudio Editor (GS_EDITOR_KIND=fork)")
class ForkGameViewTests(unittest.TestCase):
    """The native Game View: provider, `_gamestudio.game_view_*`, the capture operator and keymap."""

    def tearDown(self):
        host.game_view.stop()

    def test_capability_and_native_provider(self):
        self.assertTrue(host.has("game_view"), sorted(host.capabilities()))
        self.assertIn(game_view.IDNAME, host.native().editors())
        # Native providers belong to the module: Python cannot remove them.
        self.assertFalse(host.native().unregister_editor(game_view.IDNAME))

    def test_idle_status_and_controls(self):
        current = host.native().game_view_status()
        for key in ("active", "state", "message", "page_state", "mode", "paused", "devtools", "captured",
                    "overlay", "overlay_detail", "width", "height", "frame_id", "frames", "timings", "views"):
            self.assertIn(key, current)
        self.assertEqual((current["active"], current["state"]), (False, "none"))
        self.assertIsNone(host.native().game_view_stop())
        self.assertFalse(host.native().game_view_detach())
        self.assertFalse(host.native().game_view_devtools())
        self.assertFalse(host.native().game_view_set_paused(True))
        self.assertFalse(host.game_view.is_running())

    def test_start_failure_is_reported(self):
        missing = str(pathlib.Path(tempfile.gettempdir()) / "gs_no_such_dir" / "electron.exe")
        with self.assertRaises(RuntimeError) as cm:
            host.native().game_view_start(missing, "http://127.0.0.1:9/", app_dir="", width=320, height=240)
        self.assertTrue(str(cm.exception))
        self.assertEqual(host.native().game_view_status()["state"], "none")
        with self.assertRaises(host.game_view.GameViewError):
            host.game_view.start("http://127.0.0.1:9/", executable=missing, app_dir="")
        self.assertTrue(host.game_view.last_error())

    def test_capture_operator_and_keymap(self):
        op = bpy.ops.gamestudio.game_view_capture
        self.assertEqual(op.idname(), "GAMESTUDIO_OT_game_view_capture")
        self.assertFalse(op.poll(), "nothing to capture without a running game")
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is None:
            self.skipTest("no add-on key configuration in this mode")
        km = kc.keymaps.get(game_view.KEYMAP_NAME)
        self.assertIsNotNone(km)
        self.assertEqual((km.space_type, km.region_type), ("EXTENSION_EDITOR", "WINDOW"))
        self.assertIn(game_view.CAPTURE_OPERATOR, [kmi.idname for kmi in km.keymap_items])

    def test_open_turns_an_area_into_the_game_view(self):
        area = bpy.context.window_manager.windows[0].screen.areas[0]
        previous = area.ui_type
        try:
            self.assertTrue(host.game_view.open(area))
            self.assertTrue(host.game_view.shows_game_view(area))
            self.assertIn(area, host.game_view.areas())
            self.assertTrue(play.can_dock())
        finally:
            area.ui_type = previous
        self.assertNotIn(area, host.game_view.areas())


# --- Lifecycle (last: it re-registers the whole extension) ------------------------------


class ExtensionLifecycleTests(unittest.TestCase):
    def test_unregister_removes_every_editor(self):
        ext = importlib.import_module(EXTENSION)
        classes = [cls for idname in PROVIDERS for cls in host.editors.registered_classes(idname)]
        ext.unregister()
        try:
            self.assertEqual(host.editors.providers(), [])
            self.assertIsNone(host.editors.header(game_view.IDNAME))
            self.assertFalse(host.editors.EXTENSION_EDITOR_HT_header.is_registered)
            self.assertFalse(hasattr(bpy.types.WindowManager, "gs_console_level"))
            self.assertFalse(hasattr(bpy.types, "GAMESTUDIO_OT_game_view_start"))
            for cls in classes:
                self.assertFalse(cls.is_registered, cls.bl_idname)
            if FORK:
                for idname in PROVIDERS:
                    self.assertNotIn(idname, host.native().editors())
        finally:
            ext.register()
        for idname in PROVIDERS:
            self.assertTrue(host.editors.is_registered(idname))


def load_tests(loader, _tests=None, _pattern=None):
    suite = unittest.TestSuite()
    for case in (EditorProviderTests, StockFallbackTests, ForkEditorTests, GameViewShellTests, ForkGameViewTests,
                 ExtensionLifecycleTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite
