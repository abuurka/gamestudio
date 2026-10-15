# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio in the top bar (gamestudio.ui_topbar): the play buttons in the middle (right, by the scene
selector, when the window is too narrow), their state, the agent's button and its popover, the merged
agent status bar item, and the sidebar Agent panel behind the sidebar preference."""

import types
import unittest
from unittest import mock

import bpy

from gamestudio import activity, activity_ui, claude_code, prefs, ui_topbar
from gamestudio.core import project

import helpers
from test_ui import MockLayout, _FakeSelf

PLAY = ["gamestudio.play", "gamestudio.pause", "gamestudio.step", "gamestudio.stop"]
TOGGLE = "gamestudio.toggle_game_display"
BUTTONS = PLAY + [TOGGLE]  # the play buttons, then where Play shows the game


class Layout(MockLayout):
    """test_ui's validating layout, recording operators (with depress and enabled), popovers and spacers."""

    def __init__(self, test, calls=None, enabled=True, texts=None):
        super().__init__(test, texts)
        object.__setattr__(self, "calls", calls if calls is not None else [])
        object.__setattr__(self, "enabled", enabled)

    def _child(self, *_args, **_kwargs):
        return Layout(self._test, self.calls, self.enabled, self.texts)

    row = column = box = split = _child

    def operator(self, idname, text="", icon="NONE", **kwargs):
        self.calls.append(("operator", idname, icon, bool(kwargs.get("depress")), self.enabled))
        return super().operator(idname, text=text, icon=icon, **kwargs)

    def popover(self, panel, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertTrue(hasattr(bpy.types, panel), f"panel {panel} is not registered")
        self.calls.append(("popover", panel, icon, text))

    def separator_spacer(self):
        self.calls.append(("spacer",))


class FakeGame:
    """link.get_link() with a game connected in `state` (None: no game)."""

    def __init__(self, state):
        self.connected = state is not None
        self.game_state = state

    def game_peers(self):
        return [{"peer": "p1", "role": "game"}] if self.connected else []


def region(alignment, width):
    return types.SimpleNamespace(type="HEADER", alignment=alignment, width=width, height=30)


def bridge_state(state, clients=0):
    return mock.patch.object(claude_code, "bridge_state", lambda: (state, 51234 if state != "off" else 0, clients))


class TopBarTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        activity.clear()
        activity.set_paused(False)

    def tearDown(self):
        activity.set_paused(False)
        activity.clear()
        helpers.restore_global_link()
        super().tearDown()

    def draw(self, func, part, left_width=4000) -> list:
        """Draw `func` as the top bar's left or right part, the left part `left_width` pixels wide."""
        left, right = region("NONE", left_width), region("RIGHT", 400)
        area = types.SimpleNamespace(type="TOPBAR", regions=[left, right], show_menus=True)
        context = types.SimpleNamespace(region=left if part == "left" else right, area=area,
                                        preferences=bpy.context.preferences)
        layout = Layout(self)
        func(_FakeSelf(bpy.types.TOPBAR_HT_upper_bar, layout), context)
        return layout.calls

    def both(self, left_width=4000) -> tuple[list, list]:
        return (self.draw(ui_topbar.draw_upper_bar_left, "left", left_width),
                self.draw(ui_topbar.draw_upper_bar_right, "right", left_width))

    @staticmethod
    def ops(calls) -> list[str]:
        return [c[1] for c in calls if c[0] == "operator"]

    def test_registered_around_blenders_own_items(self):
        funcs = bpy.types.TOPBAR_HT_upper_bar.draw._draw_funcs
        self.assertEqual(funcs[0], ui_topbar.draw_upper_bar_right, "prepended: left of the scene selector")
        self.assertEqual(funcs[-1], ui_topbar.draw_upper_bar_left, "appended: right of the workspace tabs")
        self.assertTrue(hasattr(bpy.types, "GAMESTUDIO_PT_agent_popover"))
        self.assertEqual((bpy.types.GAMESTUDIO_PT_agent_popover.bl_space_type,
                          bpy.types.GAMESTUDIO_PT_agent_popover.bl_region_type), ("TOPBAR", "HEADER"))
        import gamestudio

        self.assertIn("ui_topbar", gamestudio._MODULE_NAMES)

    def test_play_buttons_in_the_middle(self):
        left, right = self.both()
        self.assertEqual(left[0], ("spacer",))
        self.assertEqual(left[-1], ("spacer",))
        self.assertEqual(self.ops(left), BUTTONS)
        self.assertEqual([c[2] for c in left if c[0] == "operator"],
                         ["PLAY", "PAUSE", "FRAME_NEXT", "SNAP_FACE", "IMAGE_BACKGROUND"])
        self.assertEqual(self.ops(right), [], "not twice")
        self.assertEqual([c[1] for c in right if c[0] == "popover"], ["GAMESTUDIO_PT_agent_popover"])

    def test_narrow_window_moves_them_next_to_the_scene_selector(self):
        left, right = self.both(left_width=300)
        self.assertEqual(left, [])
        self.assertEqual(self.ops(right), BUTTONS)
        self.assertEqual(right[-1][:2], ("popover", "GAMESTUDIO_PT_agent_popover"), "the agent's button after them")
        # The width they need grows with the workspace tabs.
        unit = ui_topbar._unit(bpy.context)
        needed = ui_topbar._left_content_width(types.SimpleNamespace(preferences=bpy.context.preferences,
                                                                     area=None), unit)
        self.assertGreater(needed, len(bpy.data.workspaces) * unit)
        self.assertEqual(self.ops(self.both(left_width=int(needed + 10 * unit))[0]), BUTTONS)
        self.assertEqual(self.ops(self.both(left_width=int(needed + unit))[1])[:5], BUTTONS)

    def test_nothing_without_a_project(self):
        project.set_project_root_override(None)
        with bridge_state("off"):
            self.assertEqual(self.both(), ([], []))
        with bridge_state("connected", 1):
            left, right = self.both()
        self.assertEqual(left, [], "no play buttons without a project")
        self.assertEqual([c[1] for c in right if c[0] == "popover"], ["GAMESTUDIO_PT_agent_popover"],
                         "the agent's button while the bridge is on")

    def test_play_buttons_show_the_game_state(self):
        def buttons(state):
            with mock.patch.object(ui_topbar.link, "get_link", lambda: FakeGame(state)):
                calls = self.draw(ui_topbar.draw_upper_bar_left, "left")
            return {c[1].split(".")[1]: (c[3], c[4]) for c in calls if c[0] == "operator"}  # (depressed, enabled)

        stopped = buttons(None)
        self.assertEqual(stopped["play"], (False, True))
        self.assertEqual({stopped[n][1] for n in ("pause", "step", "stop")}, {False}, "no game: only Play")
        playing = buttons("playing")
        self.assertEqual((playing["play"], playing["pause"], playing["stop"]), ((True, True), (False, True),
                                                                                (False, True)))
        paused = buttons("paused")
        self.assertEqual((paused["play"][0], paused["pause"][0]), (True, True), "Play stays on, Pause is on")
        self.assertEqual(buttons("stopped")["play"], (False, True))
        with mock.patch.object(ui_topbar.link, "get_link", lambda: FakeGame("paused")):
            self.assertEqual(ui_topbar.game_running(), (True, "paused"))

    def test_buttons_are_the_play_shortcut_operators(self):
        from gamestudio import keymap

        for idname in PLAY:
            category, name = idname.split(".")
            self.assertTrue(hasattr(getattr(bpy.ops, category), name), idname)
        self.assertTrue(set(PLAY) <= {item[0] for item in keymap.PLAY_KEYS})

    def test_game_display_toggle_shows_and_flips_the_preference(self):
        from gamestudio.editors import game_stream

        settings = prefs.get_prefs()
        before = settings.game_display
        self.addCleanup(setattr, settings, "game_display", before)

        def toggle():
            calls = self.draw(ui_topbar.draw_upper_bar_left, "left")
            index = self.ops(calls).index(TOGGLE)
            self.assertEqual(index, len(PLAY), "right after Play, Pause, Step and Stop")
            return [c for c in calls if c[0] == "operator"][index]

        settings.game_display = "EMBEDDED"
        self.assertEqual(toggle()[2], "IMAGE_BACKGROUND")
        self.assertTrue(toggle()[4], "enabled without a running game")
        tip = bpy.types.GAMESTUDIO_OT_toggle_game_display.description(bpy.context, None)
        self.assertIn("Embedded", tip)
        self.assertIn("own window", tip, "says what a click does")
        self.assertEqual(bpy.ops.gamestudio.toggle_game_display(), {"FINISHED"})
        self.assertEqual(settings.game_display, "WINDOW")
        self.assertEqual(toggle()[2], "WINDOW")
        self.assertIn("Window", bpy.types.GAMESTUDIO_OT_toggle_game_display.description(bpy.context, None))
        self.assertEqual(game_stream.display(), "WINDOW")
        bpy.ops.gamestudio.toggle_game_display()
        self.assertEqual(settings.game_display, "EMBEDDED")
        # Narrow: the toggle moves right with the play buttons.
        self.assertEqual(self.ops(self.draw(ui_topbar.draw_upper_bar_right, "right", 300))[:5], BUTTONS)

    def test_agent_button_and_status_bar(self):
        with bridge_state("off"):
            self.assertEqual(activity_ui.status_item(), ("Agent bridge off", "UNLINKED", False, False))
            self.assertEqual(activity_ui.topbar_item()[1], "UNLINKED")
            project.set_project_root_override(None)
            self.assertIsNone(activity_ui.status_item(), "off on purpose, no project: nothing to say")
            project.set_project_root_override(self.root)
        with bridge_state("listening"):
            self.assertEqual(activity_ui.status_item(), ("Claude not connected", "UNLINKED", False, False))
        with bridge_state("connected", 1), mock.patch.object(activity_ui, "agent_name", lambda short=True: "Claude"):
            self.assertEqual(activity_ui.status_item(), ("Claude: idle", "LINKED", False, True))
            self.assertEqual(activity_ui.topbar_item(), ("Claude", "LINKED", False, True))
            activity.set_paused(True)
            self.assertEqual(activity_ui.status_item(), ("Agent paused", "PAUSE", True, True))
            self.assertEqual(activity_ui.topbar_item(), ("Paused", "PAUSE", True, True))
            activity.set_paused(False)
        with bridge_state("connected", 2), mock.patch.object(activity_ui, "agent_name", lambda short=True: "Claude"):
            self.assertEqual(activity_ui.status_item()[0], "Claude (2): idle")
        self.assertNotIn(getattr(claude_code, "draw_statusbar", None), bpy.types.STATUSBAR_HT_header.draw._draw_funcs,
                         "one agent item in the status bar, not two")
        self.assertIn(activity_ui.draw_statusbar, bpy.types.STATUSBAR_HT_header.draw._draw_funcs)

    def test_popover_has_the_agent_controls(self):
        layout = Layout(self)
        context = types.SimpleNamespace(window_manager=bpy.context.window_manager, scene=bpy.context.scene,
                                        view_layer=bpy.context.view_layer)
        with bridge_state("connected", 1):
            bpy.types.GAMESTUDIO_PT_agent_popover.draw(_FakeSelf(bpy.types.GAMESTUDIO_PT_agent_popover, layout),
                                                       context)
        drawn = self.ops(layout.calls)
        for idname in ("gamestudio.agent_pause", "gamestudio.agent_protect", "gamestudio.agent_open_log"):
            self.assertIn(idname, drawn)
        self.assertEqual(drawn.count("gamestudio.agent_open_log"), 2, "the log's icon and Open Activity Log")
        self.assertIn("Recent actions", layout.texts)

    def test_sidebar_agent_panel_follows_the_preference(self):
        panel = bpy.types.GAMESTUDIO_PT_agent
        for on in (False, True):
            with mock.patch.object(prefs, "sidebar_enabled", lambda on=on: on, create=True):
                self.assertEqual(bool(panel.poll(bpy.context)), on)

    def test_unregister_gives_back_blenders_top_bar(self):
        ui_topbar.unregister()
        try:
            funcs = bpy.types.TOPBAR_HT_upper_bar.draw._draw_funcs
            self.assertNotIn(ui_topbar.draw_upper_bar_left, funcs)
            self.assertNotIn(ui_topbar.draw_upper_bar_right, funcs)
        finally:
            ui_topbar.register()
        funcs = bpy.types.TOPBAR_HT_upper_bar.draw._draw_funcs
        self.assertEqual((funcs.count(ui_topbar.draw_upper_bar_left), funcs.count(ui_topbar.draw_upper_bar_right)),
                         (1, 1))


if __name__ == "__main__":
    unittest.main()
