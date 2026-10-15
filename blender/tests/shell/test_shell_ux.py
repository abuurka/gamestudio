# SPDX-License-Identifier: GPL-3.0-or-later
"""The extension's user-facing promises in stock Blender: the top bar menus and play buttons, the
play shortcuts, the status bar, the GameStudio workspaces, the launcher and the Inspector.

Runs inside run_shell_tests.py (extension enabled, --background). Menus and panels are drawn on
test_editors.MockLayout, which checks every property, operator, menu and icon they use.
"""

import importlib
import pathlib
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

import bpy

import test_editors as te

EXTENSION = te.EXTENSION

host = te.host
keymap = importlib.import_module(f"{EXTENSION}.keymap")
ui = importlib.import_module(f"{EXTENSION}.ui")
menus = importlib.import_module(f"{EXTENSION}.menus")
topbar = importlib.import_module(f"{EXTENSION}.ui_topbar")
launcher = importlib.import_module(f"{EXTENSION}.launcher")
workspaces = importlib.import_module(f"{EXTENSION}.workspaces")
environment = importlib.import_module(f"{EXTENSION}.environment")
link = te.link
play = te.play
projects = importlib.import_module(f"{EXTENSION}.projects")
inspector = importlib.import_module(f"{EXTENSION}.editors.inspector")
assistant_ui = importlib.import_module(f"{EXTENSION}.assistant.ui")
agent = importlib.import_module(f"{EXTENSION}.assistant.agent")
proposals = importlib.import_module(f"{EXTENSION}.assistant.proposals")


def _key(kmi) -> tuple:
    return (kmi.type, bool(kmi.shift), bool(kmi.ctrl), bool(kmi.alt), bool(kmi.oskey))


class Recorder(te.MockLayout):
    """A MockLayout that also records the operators and menus drawn: [(kind, idname, text)]."""

    def __init__(self, test, calls=None, texts=None):
        super().__init__(test, texts)
        object.__setattr__(self, "calls", calls if calls is not None else [])

    def _child(self, *_args, **_kwargs):
        return Recorder(self._test, self.calls, self.texts)

    row = column = box = split = grid_flow = column_flow = _child

    def operator(self, idname, text="", icon="NONE", **kwargs):
        self.calls.append(("operator", idname, text))
        return super().operator(idname, text=text, icon=icon, **kwargs)

    def menu(self, menu, text="", icon="NONE", **kwargs):
        self.calls.append(("menu", menu, text))
        return super().menu(menu, text=text, icon=icon, **kwargs)

    def popover(self, panel, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertTrue(hasattr(bpy.types, panel), f"panel {panel} is not registered")
        self.calls.append(("popover", panel, text))
        self.texts.append(text)


def draw_menu(test, name, context=None) -> Recorder:
    """Draw a registered menu or header (with everything appended to it) on a Recorder."""
    cls = getattr(bpy.types, name)
    layout = Recorder(test)
    cls.draw(te._FakeSelf(cls, layout), context or te.fake_context())
    return layout


def ids(layout) -> list[str]:
    return [idname for _kind, idname, _text in layout.calls]


class PlayShortcutTests(unittest.TestCase):
    """F5 Play, F6 Pause, F7 Step, F8 Stop: in the add-on keymap, on keys Blender leaves free."""

    @classmethod
    def setUpClass(cls):
        # --background builds no keymaps; this is what Blender runs at GUI startup.
        bpy.utils.keyconfig_init()
        bpy.context.window_manager.keyconfigs.update()

    def test_items_are_in_the_addon_window_keymap(self):
        kc = bpy.context.window_manager.keyconfigs.addon
        km = kc.keymaps.get(keymap.KEYMAP[0])
        self.assertIsNotNone(km)
        self.assertEqual((km.space_type, km.region_type), keymap.KEYMAP[1:])
        found = {(kmi.idname, kmi.type, bool(kmi.shift)) for kmi in km.keymap_items if kmi.value == "PRESS"}
        for item in keymap.PLAY_KEYS:
            self.assertIn(item, found)

    def test_shortcut_text(self):
        self.assertEqual([keymap.shortcut_text(op) for op, _k, _s in keymap.PLAY_KEYS], ["F5", "F6", "F7", "F8"])
        self.assertEqual(keymap.shortcut_text("gamestudio.stop"), "F8")
        self.assertEqual(keymap.shortcut_text("gamestudio.export_scene"), "")
        self.assertEqual(keymap.shortcut_text("gamestudio.play", platform="darwin"), "Fn F5", "media keys on a Mac")

    def test_operators_exist(self):
        for idname, _key_type, _shift in keymap.PLAY_KEYS:
            category, name = idname.split(".")
            self.assertTrue(hasattr(getattr(bpy.ops, category), name), idname)

    def test_no_blender_default_uses_the_play_keys(self):
        """No active item of Blender's own keymaps takes F5, F6, F7 or F8, with or without modifiers
        the play keys use. Region and area keymaps run before the window's, so a binding in any of
        them would win over the play shortcuts there, and an extension must not take Blender's keys."""
        ours = {(key, shift, False, False, False) for _op, key, shift in keymap.PLAY_KEYS}
        clashes = []
        for km in bpy.context.window_manager.keyconfigs.default.keymaps:
            for kmi in km.keymap_items:
                if not kmi.active or kmi.value not in ("PRESS", "ANY", "CLICK"):
                    continue
                if _key(kmi) in ours or (kmi.any and kmi.type in {k[0] for k in ours}):
                    clashes.append(f"{km.name}: {kmi.idname} {kmi.to_string()}")
        self.assertEqual(clashes, [])

    def test_blenders_editor_keys_stay_active(self):
        """Shift+F5 (3D Viewport) is Blender's, and GameStudio leaves it on."""
        km = bpy.context.window_manager.keyconfigs.user.keymaps["Window"]
        shift_f5 = [kmi for kmi in km.keymap_items if kmi.type == "F5" and kmi.shift and not kmi.ctrl]
        self.assertTrue(shift_f5)
        self.assertTrue(all(kmi.active and kmi.idname == "screen.space_type_set_or_cycle" for kmi in shift_f5))
        self.assertEqual(keymap.SHADOWED_DEFAULTS, ())

    def test_play_items_stay_active_in_the_user_keymap(self):
        km = bpy.context.window_manager.keyconfigs.user.keymaps[keymap.KEYMAP[0]]
        active = {(kmi.idname, kmi.type, bool(kmi.shift)) for kmi in km.keymap_items if kmi.active}
        for item in keymap.PLAY_KEYS:
            self.assertIn(item, active)

    def test_preferences_list_the_shortcuts(self):
        texts = []
        layout = te.MockLayout(self, texts)
        with mock.patch("rna_keymap_ui.draw_kmi") as draw_kmi:
            keymap.draw_preferences(layout, te.fake_context())
        self.assertIn("Play Shortcuts", texts)
        self.assertEqual(draw_kmi.call_count, len(keymap.PLAY_KEYS))


class MenuTests(unittest.TestCase):
    """The GameStudio and Game menus in the top bar, and the play buttons."""

    def test_top_bar_gets_the_two_menus(self):
        layout = Recorder(self)
        menus.draw_editor_menus(te._FakeSelf(bpy.types.TOPBAR_MT_editor_menus, layout), te.fake_context())
        self.assertEqual(layout.calls, [("menu", "GAMESTUDIO_MT_projects", "GameStudio"),
                                        ("menu", "GAMESTUDIO_MT_game", "")])
        upstream = draw_menu(self, "TOPBAR_MT_editor_menus")
        self.assertEqual([c for c in upstream.calls if c[1].startswith("GAMESTUDIO")], layout.calls)

    def test_game_menu_lists_the_play_operators(self):
        layout = draw_menu(self, "GAMESTUDIO_MT_game")
        for label in ("Play", "Pause / Resume", "Step", "Stop", "Open Game Window", "Start Dev Server"):
            self.assertIn(label, layout.texts)
        self.assertEqual(ids(layout)[:4], ["gamestudio.play", "gamestudio.pause", "gamestudio.step", "gamestudio.stop"])

    def test_gamestudio_menu(self):
        layout = draw_menu(self, "GAMESTUDIO_MT_projects")
        found = ids(layout)
        for idname in ("gamestudio.project_new", "gamestudio.project_open", "GAMESTUDIO_MT_recent",
                       "GAMESTUDIO_MT_examples", "gamestudio.project_reveal", "gamestudio.open_code_project",
                       "gamestudio.new_component", "gamestudio.new_system", "gamestudio.add_workspaces",
                       "gamestudio.launcher", "preferences.addon_show", "wm.url_open"):
            self.assertIn(idname, found)
        # The project and script actions first, then the workspaces, Node.js and help.
        self.assertLess(found.index("gamestudio.new_system"), found.index("gamestudio.add_workspaces"))
        self.assertLess(found.index("gamestudio.add_workspaces"), found.index("gamestudio.launcher"))

    def test_setup_shows_only_when_needed(self):
        """Set Up GameStudio... while the last check found something missing; progress while it runs;
        what failed with Retry and Show Setup Log after a failure. Never "install Node.js"."""
        need = environment.Need("node", "Node.js 24.21.0", 36)
        job = environment.JOB
        try:
            with mock.patch.object(environment, "cached_needs", lambda *_a, **_k: [need]):
                found = ids(draw_menu(self, "GAMESTUDIO_MT_projects"))
            self.assertIn("gamestudio.setup", found)
            self.assertNotIn("gamestudio.install_node", found)
            with mock.patch.object(environment, "cached_needs", lambda *_a, **_k: []):
                self.assertNotIn("gamestudio.setup", ids(draw_menu(self, "GAMESTUDIO_MT_projects")))
            job.state, job.title, job.message, job.progress = "running", environment.SETUP_TITLE, "Downloading", 0.5
            job.step, job.steps = 2, 4
            layout = draw_menu(self, "GAMESTUDIO_MT_projects")
            self.assertIn("Setting up GameStudio 2/4: Downloading 50%", layout.texts)
            self.assertIn("gamestudio.setup_cancel", ids(layout))
            job.state, job.message, job.log_path = "failed", "Could not download Node.js", pathlib.Path("setup.log")
            job.finished = time.time()
            layout = draw_menu(self, "GAMESTUDIO_MT_projects")
            self.assertIn("Could not download Node.js", layout.texts)
            self.assertIn("gamestudio.setup_retry", ids(layout))
            self.assertIn("gamestudio.open_path", ids(layout))
        finally:
            job.__init__()

    def test_recent_and_examples_submenus(self):
        entries = [{"name": "space-race", "path": "C:/games/space-race", "opened": time.time()}]
        with mock.patch.object(projects, "recent", lambda: entries):
            self.assertIn("space-race", draw_menu(self, "GAMESTUDIO_MT_recent").texts)
        with mock.patch.object(projects, "recent", lambda: []):
            self.assertIn("No recent projects yet", draw_menu(self, "GAMESTUDIO_MT_recent").texts)
        examples = draw_menu(self, "GAMESTUDIO_MT_examples")
        self.assertTrue(examples.texts)  # the demo of this checkout, or "No examples"

    def test_play_buttons_in_the_middle_of_the_top_bar(self):
        """Right of the workspace tabs, or by the scene selector when the window is narrow; in a project."""
        play = ["gamestudio.play", "gamestudio.pause", "gamestudio.step", "gamestudio.stop"]
        root = pathlib.Path(tempfile.mkdtemp(prefix="gs_topbar_project_"))
        (root / "gamestudio.json").write_text('{"name": "topbar"}', encoding="utf-8")

        def draw(func, part, left_width):
            left = types.SimpleNamespace(alignment="NONE", type="HEADER", width=left_width, height=30)
            right = types.SimpleNamespace(alignment="RIGHT", type="HEADER", width=300, height=30)
            area = types.SimpleNamespace(type="TOPBAR", regions=[left, right], show_menus=True)
            layout = Recorder(self)
            func(te._FakeSelf(bpy.types.TOPBAR_HT_upper_bar, layout),
                 types.SimpleNamespace(region=left if part == "left" else right, area=area,
                                       preferences=bpy.context.preferences))
            return [i for i in ids(layout) if i in play]

        try:
            te.project.set_project_root_override(root)
            self.assertEqual(draw(topbar.draw_upper_bar_left, "left", 4000), play)
            self.assertEqual(draw(topbar.draw_upper_bar_right, "right", 4000), [])
            self.assertEqual(draw(topbar.draw_upper_bar_left, "left", 300), [])
            self.assertEqual(draw(topbar.draw_upper_bar_right, "right", 300), play)
            te.project.set_project_root_override(None)
            self.assertEqual(draw(topbar.draw_upper_bar_left, "left", 4000), [], "no project, no play buttons")
        finally:
            te.project.set_project_root_override(None)
            shutil.rmtree(root, ignore_errors=True)

    def test_play_buttons_show_the_state(self):
        class Playing:
            connected = True
            game_state = "paused"

            def game_peers(self):
                return [{"peer": "p1", "role": "game"}]

        calls = []

        class Depress(Recorder):
            def operator(self, idname, text="", icon="NONE", **kwargs):
                calls.append((idname, bool(kwargs.get("depress"))))
                return super().operator(idname, text=text, icon=icon, **kwargs)

            def _child(self, *_args, **_kwargs):
                return Depress(self._test, self.calls, self.texts)

            row = column = _child

        with mock.patch.object(topbar.link, "get_link", Playing):
            topbar.draw_play_controls(Depress(self), te.fake_context())
        self.assertEqual(calls[:2], [("gamestudio.play", True), ("gamestudio.pause", True)],
                         "paused: Play stays on, Pause is on")


class StatusBarTests(unittest.TestCase):
    """The game, the dev server and a running setup in the status bar."""

    def setUp(self):
        self.client = link.get_link()
        self.saved = {k: getattr(self.client, k) for k in ("connected", "peers", "game_state", "session", "state",
                                                           "last_error")}
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_status_project_"))
        (self.root / "gamestudio.json").write_text('{"name": "status"}', encoding="utf-8")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(self.client, k, v)
        self.client.logs.clear()
        te.project.set_project_root_override(None)
        play.cancel()
        environment.JOB.__init__()
        shutil.rmtree(self.root, ignore_errors=True)

    def _connect(self, game_state):
        self.client.connected, self.client.state = True, "connected"
        self.client.peers = [{"role": "game", "id": "g1"}]
        self.client.game_state = game_state
        self.client.session = {"url": "http://localhost:5173/"}

    def test_the_status_bar_draws_the_status(self):
        funcs = bpy.types.STATUSBAR_HT_header.draw._draw_funcs
        self.assertIn(ui.draw_statusbar, funcs)

    def test_states(self):
        self.assertEqual(ui.status_items(), [("No game project", "FILE_FOLDER", False)])
        te.project.set_project_root_override(self.root)
        self.assertEqual([t for t, _i, _a in ui.status_items()], ["Stopped", "Dev server stopped"])
        self._connect("playing")
        self.assertEqual(ui.status_items()[:2], [("Playing", "PLAY", False), ("Dev server :5173", "LINKED", False)])
        self._connect("paused")
        self.assertEqual(ui.game_status()[0], "Paused")
        self._connect("stopped")
        self.assertEqual(ui.game_status()[0], "Stopped")
        self.client.logs.append({"level": "error", "msg": "boom"})
        self.assertEqual(ui.status_items()[-1], ("1 error", "CANCEL", True))

    def test_setup_progress_comes_first(self):
        job = environment.JOB
        job.state, job.title, job.message, job.progress = "running", "Installing Node.js", "Downloading Node.js", 0.42
        self.assertEqual(ui.status_items()[0], ("Installing Node.js: Downloading Node.js 42%", "SORTTIME", False))
        job.step, job.steps = 1, 3
        self.assertEqual(ui.status_items()[0][0], "Installing Node.js 1/3: Downloading Node.js 42%")
        job.state, job.finished, job.progress = "failed", time.time(), None
        self.assertEqual(ui.status_items()[0], ("Installing Node.js failed: Downloading Node.js", "ERROR", True))
        layout = Recorder(self)
        environment.draw_statusbar_actions(layout)
        self.assertIn("gamestudio.setup_retry", ids(layout), "Retry next to the reason")
        job.finished = time.time() - environment.SHOW_FAILURE_FOR - 1
        self.assertEqual(ui.status_items(), [("No game project", "FILE_FOLDER", False)])

    def test_starting_and_failing(self):
        te.project.set_project_root_override(self.root)
        with mock.patch.object(play, "status", lambda: "Waiting for the game..."), \
                mock.patch.object(play, "is_pending", lambda: True):
            self.assertEqual(ui.game_status(), ("Waiting for the game...", "SORTTIME", False))
        with mock.patch.object(play, "status", lambda: "No game connected (timed out)"), \
                mock.patch.object(play, "is_pending", lambda: False):
            self.assertEqual(ui.game_status(), ("No game connected (timed out)", "ERROR", True))

    def test_status_icons_and_draw(self):
        te.project.set_project_root_override(self.root)
        for state in (None, "playing", "paused", "loading"):
            if state:
                self._connect(state)
            texts = []
            header = bpy.types.STATUSBAR_HT_header
            ui.draw_statusbar(te._FakeSelf(header, te.MockLayout(self, texts)), te.fake_context())
            self.assertTrue(texts)

    def test_tick_redraws_on_change_and_restores_the_item(self):
        header = bpy.types.STATUSBAR_HT_header
        header.remove(ui.draw_statusbar)
        try:
            self.assertEqual(ui._statusbar_tick(), 0.5)
        finally:
            self.assertIn(ui.draw_statusbar, header.draw._draw_funcs)
        te.project.set_project_root_override(self.root)
        with mock.patch.object(ui, "tag_redraw") as redraw:
            ui._statusbar_tick()
            self._connect("playing")
            ui._statusbar_tick()
            ui._statusbar_tick()
        self.assertEqual(redraw.call_count, 2)


class WorkspaceTests(unittest.TestCase):
    """Add Game Workspace: the Game workspace from the bundled .blend, stock editors only."""

    def test_bundled_file_has_the_workspace(self):
        self.assertTrue(workspaces.BLEND.is_file(), "run python tools/build_workspaces.py")
        with bpy.data.libraries.load(str(workspaces.BLEND)) as (source, _target):
            names = set(source.workspaces)
        self.assertEqual(names, set(workspaces.NAMES))

    def test_add_workspace(self):
        window = bpy.context.window_manager.windows[0]
        before = window.workspace
        self.assertEqual(workspaces.missing(), list(workspaces.NAMES))
        with bpy.context.temp_override(window=window, screen=window.screen):
            self.assertTrue(bpy.ops.gamestudio.add_workspaces.poll())
            self.assertEqual(bpy.ops.gamestudio.add_workspaces(), {"FINISHED"})
            self.assertEqual(workspaces.missing(), [])
            self.assertFalse(bpy.ops.gamestudio.add_workspaces.poll(), "a second time adds nothing")
        self.assertEqual(bpy.types.GAMESTUDIO_OT_add_workspaces.bl_label, "Add Game Workspace")
        self.assertNotIn("Game.001", bpy.data.workspaces)
        try:
            ws = workspaces.find()
            screen = ws.screens[0]
            # Stock editors only: stock Blender would show an unknown editor as a 3D Viewport.
            self.assertEqual(sorted(a.type for a in screen.areas),
                             ["OUTLINER", "PROPERTIES", "TEXT_EDITOR", "VIEW_3D"])
            console_area = workspaces.find_area(screen, "TEXT_EDITOR")
            self.assertTrue(console_area.spaces.active.text.get("gamestudio_console"))
            self.assertFalse(workspaces.find_area(screen, "VIEW_3D").spaces.active.show_region_ui)
            # The Console's height waits for a window; the Object tab is set once an object is active.
            spec = ws[workspaces.LAYOUT_PROP].to_dict()
            self.assertEqual(spec["TEXT_EDITOR"], {"height": spec["TEXT_EDITOR"]["height"]})
            properties = workspaces.find_area(screen, "PROPERTIES").spaces.active
            self.assertTrue(properties.context == "OBJECT" or spec.get("PROPERTIES") == {"context": "OBJECT"})
            # The workspace tab menu offers Reset once the file has it.
            layout = draw_menu(self, "TOPBAR_MT_workspace_menu")
            self.assertIn("gamestudio.reset_game_workspace", ids(layout))
        finally:
            window.workspace = before

    def test_find_area_keys(self):
        screen = bpy.data.workspaces["Scripting"].screens[0]
        self.assertEqual(workspaces.find_area(screen, "OUTLINER/LIBRARIES").spaces.active.display_mode, "LIBRARIES")
        self.assertIsNone(workspaces.find_area(screen, "VIEW_3D@1"))
        self.assertIsNotNone(workspaces.find_area(screen, "VIEW_3D@0"))

    def test_tabs_are_retried_until_the_sidebar_draws(self):
        """--background draws nothing, so the sidebar has no tabs yet: the step is kept and retried,
        then dropped after RETRIES."""
        window = types.SimpleNamespace(screen=bpy.data.workspaces["Layout"].screens[0],
                                       workspace=bpy.data.workspaces["Layout"])
        ws = window.workspace
        ws[workspaces.LAYOUT_PROP] = {"VIEW_3D": {"sidebar": "Game"}, "SEQUENCE_EDITOR": {"sidebar": "Game"}}
        workspaces._tries.clear()
        try:
            self.assertFalse(workspaces.apply_layout(window))
            self.assertEqual(ws[workspaces.LAYOUT_PROP].to_dict(), {"VIEW_3D": {"sidebar": "Game"}})
            for _ in range(workspaces.RETRIES):
                done = workspaces.apply_layout(window)
            self.assertTrue(done)
            self.assertNotIn(workspaces.LAYOUT_PROP, ws)
        finally:
            ws.pop(workspaces.LAYOUT_PROP, None)
            workspaces._tries.clear()


class LauncherTests(unittest.TestCase):
    def test_relative_time(self):
        now = time.mktime((2026, 10, 9, 15, 0, 0, 0, 0, -1))
        cases = [(now - 5, "just now"), (now - 600, "10 min ago"), (now - 3 * 3600, "3 h ago"),
                 (now - 86400, "Yesterday"), (time.mktime((2026, 1, 3, 12, 0, 0, 0, 0, -1)), "3 Jan"),
                 (time.mktime((2025, 1, 3, 12, 0, 0, 0, 0, -1)), "3 Jan 2025"), (None, "")]
        for timestamp, expected in cases:
            self.assertEqual(launcher.relative_time(timestamp, now=now), expected, timestamp)

    def test_names_and_paths(self):
        self.assertEqual(launcher.display_name("demo-game"), "Demo Game")
        self.assertEqual(launcher.display_name("My Game"), "My Game")
        long = "C:/" + "/".join(["folder"] * 12) + "/my-game"
        self.assertTrue(launcher.short_path(long).endswith("my-game"))
        self.assertLessEqual(len(launcher.short_path(long)), 40)

    def _draw(self, recent):
        layout = Recorder(self)
        with mock.patch.object(projects, "recent", lambda: recent):
            launcher.draw_launcher(layout, te.fake_context())
        return layout

    def test_launcher_empty_state(self):
        layout = self._draw([])
        for text in ("Start", "New Project...", "Open Project...", "Recent Projects", "No recent projects yet.",
                     "Documentation", "Open Scene File..."):
            self.assertIn(text, layout.texts)
        self.assertIn("F5 Play, F6 Pause, F7 Step, F8 Stop", layout.texts)

    def test_launcher_recent_rows(self):
        entries = [{"name": "space-race", "path": "C:/games/space-race", "opened": time.time() - 120}]
        layout = self._draw(entries)
        self.assertIn("Space Race", layout.texts)
        self.assertIn("2 min ago", layout.texts)
        self.assertIn("C:/games/space-race", layout.texts)
        self.assertNotIn("No recent projects yet.", layout.texts)

    def test_launcher_offers_setup(self):
        needs = [environment.Need("node", "Node.js 24.21.0", 36), environment.Need("runtime", "the game packages", 60)]
        with mock.patch.object(environment, "cached_needs", lambda *_a, **_k: needs), \
                mock.patch.object(workspaces, "missing", lambda: ["Game"]):
            layout = self._draw([])
        self.assertIn("gamestudio.setup", ids(layout))
        self.assertNotIn("gamestudio.install_node", ids(layout))
        self.assertIn("gamestudio.add_workspaces", ids(layout))
        self.assertIn("GameStudio sets itself up the first time you open or play a game", layout.texts)
        self.assertIn("About 96 MB, once: Node.js, game packages", layout.texts)
        ready = {"ok": True, "source": "portable", "version": "24.21.0", "path": "", "problem": ""}
        with mock.patch.object(environment, "cached_needs", lambda *_a, **_k: []), \
                mock.patch.object(environment.toolchain, "node_status", lambda *_a: ready):
            layout = self._draw([])
        self.assertNotIn("gamestudio.setup", ids(layout))
        self.assertIn("Node.js 24.21.0 (set up by GameStudio)", layout.texts)

    def test_launcher_shows_a_running_setup(self):
        job = environment.JOB
        try:
            job.state, job.title, job.message, job.progress = "running", "Installing Node.js", "Downloading", 0.5
            layout = self._draw([])
            self.assertIn("Installing Node.js", layout.texts)
            self.assertIn("gamestudio.setup_cancel", ids(layout))
            job.state, job.message, job.progress = "failed", "Could not download it", None
            self.assertIn("Could not download it", self._draw([]).texts)
        finally:
            job.__init__()

    def test_when_it_opens(self):
        def show(prefs, filepath="", background=False, interactive=False):
            return launcher.should_show(prefs, filepath, background, interactive)

        prefs = types.SimpleNamespace(bl_rna=None, show_launcher_at_startup=False, welcomed=False)
        self.assertTrue(show(prefs, interactive=True), "once after the first enable from Preferences")
        self.assertFalse(show(prefs), "at startup Blender's splash is up")
        self.assertFalse(show(prefs, "C:/game/scenes/main.blend", interactive=True))
        self.assertFalse(show(prefs, background=True, interactive=True))
        prefs.welcomed = True
        self.assertFalse(show(prefs, interactive=True))
        prefs.show_launcher_at_startup = True
        self.assertTrue(show(prefs), "at startup, instead of the splash")
        self.assertFalse(show(prefs, "C:/game/scenes/main.blend"))
        self.assertFalse(show(types.SimpleNamespace(), interactive=True), "not as an extension")
        self.assertFalse(bpy.app.timers.is_registered(launcher._startup_tick), "never in --background")

    def test_the_welcome_opens_over_the_preferences(self):
        """Install from Disk leaves the Preferences window in front. It shows a temporary screen, which
        temp_override refuses (TypeError), so the launcher gets the window alone there."""
        temporary = types.SimpleNamespace(screen=types.SimpleNamespace(is_temporary=True))
        main = types.SimpleNamespace(screen=types.SimpleNamespace(is_temporary=False))
        self.assertEqual(launcher._override(temporary), {"window": temporary})
        self.assertEqual(launcher._override(main), {"window": main, "screen": main.screen})

    def test_a_welcome_that_failed_waits_for_the_next_time(self):
        import contextlib

        prefs = types.SimpleNamespace(bl_rna=None, show_launcher_at_startup=False, welcomed=False)
        window = types.SimpleNamespace(screen=types.SimpleNamespace(is_temporary=False, areas=[]))

        def refuse(*_args, **_kwargs):
            raise TypeError("a temporary screen")

        fake = types.SimpleNamespace(
            context=types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[window]),
                                          temp_override=lambda **_kwargs: contextlib.nullcontext(),
                                          preferences=types.SimpleNamespace(use_preferences_save=False)),
            app=types.SimpleNamespace(background=False, handlers=types.SimpleNamespace(load_post=[])),
            data=types.SimpleNamespace(filepath=""),
            ops=types.SimpleNamespace(gamestudio=types.SimpleNamespace(launcher=refuse)))
        with mock.patch.object(launcher, "bpy", fake), mock.patch.object(launcher, "get_prefs", lambda: prefs), \
                mock.patch.dict(launcher._state, {"interactive": True, "tries": 0}):
            self.assertIsNone(launcher._startup_tick())
            self.assertFalse(prefs.welcomed, "it did not show: the next enable shows it")
            fake.ops.gamestudio.launcher = lambda *_args, **_kwargs: {"RUNNING_MODAL"}
            launcher._startup_tick()
        self.assertTrue(prefs.welcomed)

    def test_startup_preference_swaps_the_splash(self):
        prefs = bpy.context.preferences.addons[EXTENSION].preferences
        view = bpy.context.preferences.view
        try:
            prefs.show_launcher_at_startup = True
            self.assertFalse(view.show_splash)
            prefs.show_launcher_at_startup = False
            self.assertTrue(view.show_splash)
        finally:
            prefs.show_launcher_at_startup = False
            view.show_splash = True

    def test_operator_and_preferences_draw(self):
        self.assertEqual(bpy.ops.gamestudio.launcher.idname(), "GAMESTUDIO_OT_launcher")
        prefs = bpy.context.preferences.addons[EXTENSION].preferences
        texts = []
        with mock.patch("rna_keymap_ui.draw_kmi"):
            type(prefs).draw(te._FakeSelf(prefs, te.MockLayout(self, texts)), te.fake_context())
        self.assertIn("GameStudio", texts)
        self.assertIn("Interface", texts)
        # Play: the game display (Embedded by default) and its hint; the window options apply to Window.
        self.assertEqual(prefs.game_display, "EMBEDDED")
        hint = next((t for t in texts if t.startswith("Embedded: click the game")), None)
        self.assertIsNotNone(hint, "the Embedded hint")
        prefs.game_display = "WINDOW"
        try:
            texts = []
            with mock.patch("rna_keymap_ui.draw_kmi"):
                type(prefs).draw(te._FakeSelf(prefs, te.MockLayout(self, texts)), te.fake_context())
            self.assertNotIn(hint, texts)
        finally:
            prefs.game_display = "EMBEDDED"


class InspectorParityTests(te._ProjectFixture):
    """The Inspector editor and the Game tab draw the same entity UI (one implementation)."""

    def _texts(self, draws, context):
        texts = []
        for draw in draws:
            draw(te.MockLayout(self, texts), context)
        return texts

    def test_editor_and_game_tab_match(self):
        components = importlib.import_module(f"{EXTENSION}.core.components")
        registry = importlib.import_module(f"{EXTENSION}.core.registry")
        registry.set_registry({"components": [
            {"name": "Spin", "category": "Demo", "builtin": True, "description": "Turns the object.",
             "fields": [{"name": "speed", "type": "float", "default": 1.0}]}]})
        try:
            for obj in (None, self.obj):
                if obj is not None and "Spin" not in components.list_components(obj):
                    components.add_component(obj, "Spin")
                editor = self._texts([spec.draw for spec in inspector.InspectorEditor.panels
                                      if spec.poll is None or spec.poll(te.fake_context(active_object=obj))],
                                     te.fake_context("GAMESTUDIO_INSPECTOR", obj))
                sidebar = []
                for cls in (bpy.types.GAMESTUDIO_PT_entity, bpy.types.GAMESTUDIO_PT_components):
                    cls.draw(te._FakeSelf(cls, te.MockLayout(self, sidebar)), te.fake_context(active_object=obj,
                                                                                             in_editor=False))
                if obj is None:
                    self.assertIn("No entity selected", editor)
                    self.assertIn("No entity selected", sidebar)
                else:
                    self.assertEqual(editor, sidebar)
                    self.assertIn("Spin", editor)
                    self.assertIn("Add Component", editor)
        finally:
            registry.reload_registry()

    def test_game_tab_panel_order(self):
        order = [cls.bl_idname for cls in ui._CLASSES if issubclass(cls, bpy.types.Panel)]
        self.assertEqual(order, ["GAMESTUDIO_PT_play", "GAMESTUDIO_PT_entity", "GAMESTUDIO_PT_components",
                                 "GAMESTUDIO_PT_project", "GAMESTUDIO_PT_console"])
        for name in ("GAMESTUDIO_PT_project", "GAMESTUDIO_PT_console"):
            self.assertIn("DEFAULT_CLOSED", getattr(bpy.types, name).bl_options)

    def test_game_tab_follows_the_preference(self):
        """The Inspector is in Object properties; the sidebar's Game tab shows only when asked for."""
        prefs = bpy.context.preferences.addons[EXTENSION].preferences
        panels = [cls for cls in ui._CLASSES if issubclass(cls, bpy.types.Panel)]
        self.assertFalse(prefs.sidebar_tab, "off by default")
        context = te.fake_context(active_object=self.obj, in_editor=False)
        try:
            self.assertEqual([cls.poll(context) for cls in panels], [False] * len(panels))
            prefs.sidebar_tab = True
            self.assertEqual([cls.poll(context) for cls in panels], [True] * len(panels))
        finally:
            prefs.sidebar_tab = False
        self.assertTrue(bpy.types.GAMESTUDIO_PT_object.poll(types.SimpleNamespace(object=self.obj)))

    def test_component_icons(self):
        for name in ("Spin", "Unknown"):
            self.assertIn(ui.component_icon(name), te.ICONS)
        for obj_type in ("MESH", "LIGHT_PROBE", "GREASEPENCIL", "EMPTY"):
            self.assertIn(ui.object_icon(types.SimpleNamespace(type=obj_type)), te.ICONS, obj_type)


class AssistantDrawTests(unittest.TestCase):
    """A conversation and proposal cards draw in the sidebar and the editor, without the SDK."""

    def setUp(self):
        agent.SESSION.transcript[:] = [
            {"role": "user", "text": "Add a coin"}, {"role": "tool", "text": "get_scene_overview: 3 objects"},
            {"role": "assistant", "text": "Staged p1. " * 40}, {"role": "system", "text": "p1 staged"},
            {"role": "error", "text": "The request failed"}]
        proposals.STORE.add(proposals.Proposal(
            id="p1", kind="scene", title="Add a coin", rationale="", source="assistant", risk="high",
            risk_reasons=["deletes an object"], operations=[{"op": "delete_object", "object": "Crate"}]))
        proposals.STORE.add(proposals.Proposal(
            id="p2", kind="file", title="New system", rationale="", source="mcp", file_mode="create",
            path="src/systems/new.ts"))
        proposals.STORE.add(proposals.Proposal(
            id="p3", kind="file", title="Applied edit", rationale="", source="assistant", status="applied",
            file_mode="edit", path="src/systems/old.ts", message="Applied"))

    def tearDown(self):
        agent.SESSION.transcript.clear()
        proposals.STORE.clear()

    def test_conversation_and_cards(self):
        for in_editor in (True, False):
            texts = []
            context = te.fake_context("GAMESTUDIO_ASSISTANT", in_editor=in_editor)
            layout = te.MockLayout(self, texts)
            assistant_ui.draw_transcript(layout, context)
            assistant_ui.draw_proposals(layout, context)  # p2 has no project root: drawn, never raised
            assistant_ui.draw_prompt(layout, context)
            for text in ("You", "Assistant", "Error", "Add a coin", "New system", "Apply", "Reject", "Pending"):
                self.assertIn(text, texts, (in_editor, text))
            self.assertIn("Applied edit", texts)  # applied file changes stay listed for Revert


def load_tests(loader, _tests=None, _pattern=None):
    suite = unittest.TestSuite()
    for case in (PlayShortcutTests, MenuTests, StatusBarTests, WorkspaceTests, LauncherTests, InspectorParityTests,
                 AssistantDrawTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite
