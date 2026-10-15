# SPDX-License-Identifier: GPL-3.0-or-later
"""The game UI bridge methods (docs/GAME-UI.md §8): ui_list, ui_open, ui_canvas, ui_add, ui_set and ui_remove,
called over the socket like the MCP tools do: results, strict parameters, error codes, one undo step per
write, Lock from AI and pause."""

import pathlib
import unittest
from unittest import mock

import bpy

from gamestudio import gameui
from gamestudio.bridge import api, server
from gamestudio.core import components

import helpers
from test_bridge import BridgeTestCase
from test_gameui_build import GRADIENT, SHINE, ui_registry, write_png


class UiBridgeTestCase(BridgeTestCase):
    registry_data = ui_registry()

    def open_ui(self) -> dict:
        api.agent_dirty = False
        return self.client.result("ui_open")


class UiOpenTest(UiBridgeTestCase):
    def test_list_and_write_outside_a_ui_file(self):
        out = self.client.result("ui_list")
        self.assertEqual((out["open"], out["ui_file"], out["exists"], out["canvases"]),
                         (False, "ui/ui.blend", False, []))
        for method, params in (("ui_add", {"canvas": "Menu", "kind": "group", "name": "G", "at": [0, 0]}),
                               ("ui_set", {"canvas": "Menu", "name": "G", "at": [0, 0]}),
                               ("ui_canvas", {"name": "HUD"}), ("ui_remove", {"canvas": "Menu"}),
                               ("ui_list", {"canvas": "Menu"})):
            err = self.client.error(method, params)
            self.assertEqual(err["data"]["code"], "ui_file_not_open", method)
            self.assertIn("ui_open", err["data"]["hint"])

    def test_open_creates_then_reopens(self):
        out = self.open_ui()
        self.assertEqual(out, {"file": "ui/ui.blend", "created": True,
                               "canvases": [{"name": "Menu", "kind": "menu", "open": "manual", "elements": 0}]})
        self.assertEqual(pathlib.Path(bpy.data.filepath), self.root / "ui" / "ui.blend")
        self.assertEqual(self.client.result("ui_open")["created"], False)
        listed = self.client.result("ui_list")
        self.assertEqual((listed["file"], listed["open"], [c["name"] for c in listed["canvases"]]),
                         ("ui/ui.blend", True, ["Menu"]))

    def test_unsaved_changes(self):
        level = self.save_scene()
        helpers.new_object("Marker")
        api.agent_dirty = True
        err = self.client.error("ui_open")
        self.assertEqual(err["data"]["code"], "unsaved_changes")
        self.assertIn("save: true", err["data"]["hint"])
        self.assertEqual(pathlib.Path(bpy.data.filepath), level)
        err = self.client.error("ui_open", {"create": False, "save": True})
        self.assertEqual(err["data"]["code"], "ui_file_missing")
        out = self.client.result("ui_open", {"save": True})  # a background Blender writes it, then it opens
        self.assertTrue(out["created"])
        self.assertEqual(pathlib.Path(bpy.data.filepath), self.root / "ui" / "ui.blend")
        bpy.ops.wm.open_mainfile(filepath=str(level))
        self.assertIn("Marker", bpy.data.objects, "saved first")

    def test_bad_params(self):
        err = self.client.error("ui_open", {"create": "yes"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        err = self.client.error("ui_open", {"craete": True})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("Did you mean 'create'", err["message"])


class UiWriteTest(UiBridgeTestCase):
    def setUp(self):
        super().setUp()
        self.open_ui()

    def test_canvas(self):
        out = self.client.result("ui_canvas", {"name": "HUD", "kind": "hud", "open": "start", "size": [1280, 720]})
        self.assertEqual((out["name"], out["size"], out["elements"]), ("HUD", [1280, 720], []))
        self.assertEqual((out["settings"]["kind"], out["settings"]["open"], out["settings"]["backdrop"]),
                         ("hud", "start", [0.0, 0.0, 0.0, 0.0]))
        out = self.client.result("ui_canvas", {"name": "HUD", "layer": 3})
        self.assertEqual((out["settings"]["kind"], out["settings"]["layer"]), ("hud", 3), "the rest is kept")
        err = self.client.error("ui_canvas", {"name": "HUD", "kind": "overlay"})
        self.assertIn("kind", err["message"])
        err = self.client.error("ui_canvas", {"name": "HUD", "scaleMode": "fit"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("scale_mode", err["message"])

    def test_canvas_focus(self):
        self.client.result("ui_add", {"canvas": "Menu", "kind": "group", "name": "Buttons", "at": [960, 600],
                                      "anchor": "center"})
        for name, y in (("Play", 600), ("Back", 710)):
            self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": name, "label": name,
                                          "at": [960, y], "parent": "Buttons"})
        out = self.client.result("ui_canvas", {"name": "Menu", "focus": "Back"})
        self.assertEqual(out["settings"]["focus"], "Back")
        self.assertEqual(components.get_values(bpy.data.scenes["Menu"].camera, "UiCanvas")["focus"], "Back")
        self.assertEqual(self.client.result("ui_canvas", {"name": "Menu", "back": "previous"})["settings"]["focus"],
                         "Back", "kept")
        err = self.client.error("ui_canvas", {"name": "Menu", "focus": "Bakc"})
        self.assertEqual(err["data"]["code"], "no_element")
        self.assertIn("Did you mean 'Back'", err["message"])
        err = self.client.error("ui_canvas", {"name": "Menu", "focus": "Buttons"})
        self.assertIn("not a button", err["message"])
        self.assertIn("Play, Back", err["data"]["hint"])
        self.assertIsNone(self.client.result("ui_canvas", {"name": "Menu", "focus": None})["settings"]["focus"])
        self.client.result("ui_canvas", {"name": "Menu", "focus": "Play"})
        self.assertIsNone(self.client.result("ui_canvas", {"name": "Menu", "focus": ""})["settings"]["focus"])
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertEqual(self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["settings"]["focus"], "Play")

    def test_add_each_kind(self):
        png = write_png(self.root / "assets" / "ui" / "icon.png", 32, 32)
        (self.root / "assets" / "sfx").mkdir(parents=True)
        (self.root / "assets" / "sfx" / "click.wav").write_bytes(b"\0" * 16)
        calls = {
            "text": {"body": "Score {score}", "size": 40, "color": "#FFD400", "align": "left", "box": [300, 60],
                     "outline": 2, "shadow": [2, 2, 6]},
            "image": {"path": "assets/ui/icon.png", "width": 64},
            "panel": {"size": [400, 200], "color": [0, 0, 0, 0.5], "radius": 12, "border": 2},
            "button": {"label": "Play", "action": "open", "target": "Options", "order": 1, "size": [300, 80],
                       "sound": "assets/sfx/click.wav", "focus_sound": "assets/sfx/click.wav"},
            "bar": {"size": [300, 24], "value": "health", "max": 100, "direction": "up"},
            "group": {"anchor": ["bottom", "width"]},
        }
        for i, (kind, extra) in enumerate(calls.items()):
            out = self.client.result("ui_add", {"canvas": "Menu", "kind": kind, "name": f"E{i}", "at": [100 + i, 200],
                                                **extra})
            element = out["element"]
            self.assertEqual((out["canvas"], out["created"], element["name"], element["at"]),
                             ("Menu", True, f"E{i}", [100.0 + i, 200.0]), kind)
            self.assertEqual(element["kind"], kind)
        self.assertTrue(png.is_file())
        listed = {e["name"]: e for e in self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["elements"]}
        self.assertEqual(listed["E0"]["text"], "Score {score}")
        self.assertEqual(listed["E0"]["components"]["UiText"], {"outline": 2.0, "shadow": [2.0, 2.0], "shadowBlur": 6.0})
        self.assertEqual(listed["E1"]["size"], [64.0, 64.0])
        self.assertEqual(listed["E2"]["color"], "#00000080")
        self.assertEqual(listed["E3"]["components"]["UiButton"], {"action": "open", "target": "Options", "order": 1,
                                                                  "sound": "assets/sfx/click.wav",
                                                                  "focusSound": "assets/sfx/click.wav"})
        self.assertEqual(listed["E4"]["components"]["UiBar"]["direction"], "up")
        self.assertEqual((listed["E5"]["anchor"], listed["E5"]["stretch"]), ("bottom", "width"))
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "text", "name": "E0", "at": [500, 500]})
        self.assertFalse(out["created"])
        self.assertEqual((out["element"]["text"], out["element"]["at"]), ("Score {score}", [500.0, 500.0]))

    def test_add_hidden_and_list_the_shape(self):
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "text", "name": "NewBest", "body": "New best!",
                                            "at": [960, 400], "visible": False})
        self.assertFalse(out["element"]["visible"])
        self.assertTrue(bpy.data.objects["NewBest"].hide_render, "Disable in Renders: it starts hidden")
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Again", "label": "Again",
                                            "at": [960, 700], "visible": False})
        self.assertFalse(out["element"]["visible"])
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540],
                                            "size": [400, 200], "radius": 24, "border": 2, "border_color": "#FFD400"})
        self.assertTrue(out["element"]["visible"])
        self.assertEqual({k: out["element"][k] for k in ("radius", "border", "border_color")},
                         {"radius": 24.0, "border": 2.0, "border_color": "#FFD400"})
        listed = {e["name"]: e for e in self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["elements"]}
        self.assertEqual((listed["Again"]["radius"], listed["AgainBg"]["radius"]), (42.0, 42.0))
        self.assertNotIn("rotation", listed["Card"])
        err = self.client.error("ui_add", {"canvas": "Menu", "kind": "group", "name": "G", "at": [0, 0],
                                           "visible": "no"})
        self.assertIn("visible", err["message"])
        self.assertNotIn("G", bpy.data.objects)

    def test_add_checks_its_params(self):
        base = {"canvas": "Menu", "name": "T", "at": [0, 0]}
        err = self.client.error("ui_add", {**base, "kind": "label"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        err = self.client.error("ui_add", {**base, "kind": "text", "body": "x", "radius": 4})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("radius", err["message"])
        self.assertIn("outline_color", err["data"]["hint"])
        err = self.client.error("ui_add", {"canvas": "Menu", "name": "T", "kind": "text", "body": "x"})
        self.assertIn("'at'", err["message"])
        err = self.client.error("ui_add", {**base, "kind": "panel"})
        self.assertIn("panel needs size", err["message"])
        err = self.client.error("ui_add", {**base, "kind": "text", "body": "x", "color": "yellow"})
        self.assertIn("#RRGGBB", err["message"])
        err = self.client.error("ui_add", {**base, "canvas": "Menuu", "kind": "group"})
        self.assertEqual(err["data"]["code"], "no_canvas")
        self.client.result("ui_add", {**base, "kind": "group"})
        err = self.client.error("ui_add", {**base, "kind": "text", "body": "x"})
        self.assertIn("group element, not a text", err["message"])
        self.assertNotIn("T2", bpy.data.objects)

    def test_add_and_set_controls_icons_and_sliced_panels(self):
        """§10.7: ui_add kinds icons, slider, toggle and panel image + slice; ui_set value, min, max, step, count."""
        for name in ("heart.png", "heart-empty.png"):
            write_png(self.root / "assets" / "ui" / name, 16, 16)
        write_png(self.root / "assets" / "ui" / "panel.png", 64, 64)
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "icons", "name": "Lives", "at": [200, 60],
                                            "value": "lives", "count": 4})
        self.assertEqual((out["created"], out["element"]["kind"], out["element"]["count"], out["element"]["value"]),
                         (True, "icons", 4, "lives"))
        hearts = {p["name"]: p for p in out["parts"]}
        self.assertEqual(sorted(hearts), ["Lives1", "Lives2", "Lives3", "Lives4", "LivesSlot1", "LivesSlot2",
                                          "LivesSlot3", "LivesSlot4"])
        self.assertEqual(hearts["Lives3"]["components"]["UiShow"], {"value": "lives", "than": 3.0})
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "slider", "name": "Music", "at": [960, 500],
                                            "value": "audio.music"})
        self.assertEqual({k: out["element"][k] for k in ("kind", "value", "min", "max", "step", "size")},
                         {"kind": "slider", "value": "audio.music", "min": 0.0, "max": 1.0, "step": 0.1,
                          "size": [360.0, 28.0]})
        self.assertEqual(out["element"]["components"]["UiSlider"],
                         {"value": "audio.music", "fill": "MusicFill", "handle": "MusicHandle"})
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "toggle", "name": "Subtitles",
                                            "at": [960, 600], "value": "save.subtitles", "on_color": "#4CAF50"})
        self.assertEqual(out["element"]["components"]["UiToggle"],
                         {"value": "save.subtitles", "on": "SubtitlesOn", "off": "SubtitlesOff"})
        self.assertEqual(sorted(p["name"] for p in out["parts"]),
                         ["SubtitlesBg", "SubtitlesOff", "SubtitlesOn", "SubtitlesOnKnob"])
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540],
                                            "size": [400, 300], "image": "assets/ui/panel.png",
                                            "slice": [16, 16, 8, 8]})
        self.assertEqual({k: out["element"][k] for k in ("kind", "image", "slice", "size")},
                         {"kind": "panel", "image": "assets/ui/panel.png", "slice": [16.0, 16.0, 8.0, 8.0],
                          "size": [400.0, 300.0]})
        self.assertEqual(len(bpy.data.objects["Card"].data.polygons), 9)
        listed = {e["name"]: e for e in self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["elements"]}
        self.assertEqual([listed[n]["kind"] for n in ("Lives", "Music", "Subtitles", "Card")],
                         ["icons", "slider", "toggle", "panel"])
        # ui_set's new keys.
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Music", "value": "audio.sfx", "min": 0,
                                            "max": 2, "step": 0.25})
        self.assertEqual({k: out["element"][k] for k in ("value", "min", "max", "step")},
                         {"value": "audio.sfx", "min": 0.0, "max": 2.0, "step": 0.25})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Lives", "count": 2})
        self.assertEqual((out["element"]["count"], len(out["parts"])), (2, 4))
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Subtitles", "value": "save.captions"})
        self.assertEqual(out["element"]["value"], "save.captions")
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Card", "size": [500, 300]})
        self.assertEqual(out["element"]["size"], [500.0, 300.0])
        self.assertEqual(sorted({round(v.co.x, 2) for v in bpy.data.objects["Card"].data.vertices}),
                         [-2.5, -2.34, 2.34, 2.5], "the 9-slice quads are rebuilt")
        for name, change, words in (("Music", {"count": 3}, "does not apply"), ("Card", {"step": 1}, "does not apply"),
                                    ("Music", {"action": "close"}, "control of its own"),
                                    ("Music", {"min": 5}, "min must be below its max")):
            err = self.client.error("ui_set", {"canvas": "Menu", "name": name, **change})
            self.assertIn(words, err["message"], change)
        # Strict per kind.
        err = self.client.error("ui_add", {"canvas": "Menu", "kind": "slider", "name": "S", "at": [0, 0],
                                           "label": "x"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("handle_color", err["data"]["hint"])
        err = self.client.error("ui_add", {"canvas": "Menu", "kind": "toggle", "name": "T", "at": [0, 0]})
        self.assertIn("toggle needs value", err["message"])
        err = self.client.error("ui_add", {"canvas": "Menu", "kind": "panel", "name": "P", "at": [0, 0],
                                           "size": [10, 10], "slice": 4})
        self.assertIn("slice needs image", err["message"])
        self.assertNotIn("P", bpy.data.objects)

    def test_canvas_transition(self):
        self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540],
                                      "size": [400, 300]})
        out = self.client.result("ui_canvas", {"name": "Menu", "transition": {"open": "pop"}})
        self.assertEqual(out["animations"], {"open": 0.25, "close": 0.25})
        self.assertEqual(out["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
        out = self.client.result("ui_canvas", {"name": "Menu", "transition": {"close": "slide-down", "duration": 0.5,
                                                                            "loop": "bob"}})
        self.assertEqual(out["transition"], {"open": "pop", "close": "slide-down", "duration": 0.5, "loop": "bob"},
                         "keys not given are kept")
        self.assertEqual(out["animations"], {"open": 0.5, "close": 0.5, "loop": round(38 / 24, 3)})
        self.assertEqual(self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["animations"],
                         out["animations"])
        out = self.client.result("ui_canvas", {"name": "Menu", "transition": {"loop": None}})
        self.assertEqual(out["transition"], {"open": "pop", "close": "slide-down", "duration": 0.5})
        out = self.client.result("ui_canvas", {"name": "Pause", "pause_menu": True,
                                               "transition": {"open": "fade", "close": "none"}})
        self.assertEqual((out["settings"]["pauseMenu"], out["animations"]), (True, {"open": 0.25}))
        out = self.client.result("ui_canvas", {"name": "Menu", "transition": None})
        self.assertNotIn("animations", out)
        self.assertNotIn("transition", out)
        self.assertIsNone(bpy.data.objects["Card"].animation_data)
        for transition, code, words in (({"opne": "pop"}, server.INVALID_PARAMS, "Did you mean 'open'"),
                                        ({"open": "zoom"}, None, "open must be one of"),
                                        ("pop", server.INVALID_PARAMS, "transition must be an object")):
            err = self.client.error("ui_canvas", {"name": "Menu", "layer": 4, "transition": transition})
            self.assertIn(words, err["message"], transition)
            if code is not None:
                self.assertEqual(err["code"], code)
        self.assertEqual(self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["settings"]["layer"], 0,
                         "a bad transition changes nothing")
        bpy.data.objects["Card"].gs.ai_lock = True
        err = self.client.error("ui_canvas", {"name": "Menu", "transition": {}})
        self.assertIn("locked", err["message"])
        bpy.data.objects["Card"].gs.ai_lock = False
        self.assertEqual(self.client.result("ui_canvas", {"name": "Menu", "transition": {}})["transition"],
                         {"open": "pop", "close": "fade", "duration": 0.25})
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertNotIn("animations", self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0])

    def test_element_loop(self):
        """ui_add / ui_set loop: one element loops (children too); ui_list reports it next to the canvas transition."""
        self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540],
                                      "size": [400, 300]})
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play",
                                            "at": [960, 540], "parent": "Card", "loop": "pulse"})
        self.assertEqual(out["element"]["loop"], "pulse")
        out = self.client.result("ui_canvas", {"name": "Menu", "transition": {"open": "pop"}})
        self.assertEqual(out["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
        self.assertEqual(out["animations"], {"open": 0.25, "close": 0.25, "loop": round(29 / 24, 3)})
        listed = {e["name"]: e for e in out["elements"]}
        self.assertEqual(listed["Play"]["loop"], "pulse")
        self.assertNotIn("loop", listed["Card"])
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "loop": "bob"})
        self.assertEqual(out["element"]["loop"], "bob")
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "loop": "none"})
        self.assertNotIn("loop", out["element"])
        canvas = self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]
        self.assertEqual(canvas["animations"], {"open": 0.25, "close": 0.25}, "the loop range went with it")
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Play", "loop": "spin"})
        self.assertIn("loop must be one of", err["message"])

    def test_results_list_own_parts_and_removed_parts(self):
        """parts: the element's own parts only; removed: the parts a change deleted (a panel's border for 9-slice
        art)."""
        write_png(self.root / "assets" / "ui" / "panel.png", 64, 64)
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540],
                                            "size": [400, 300], "border": 4})
        self.assertEqual([p["name"] for p in out["parts"]], ["CardBorder"])
        self.assertNotIn("removed", out)
        self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "BackButton", "label": "Back",
                                      "at": [960, 600], "parent": "Card"})
        self.client.result("ui_add", {"canvas": "Menu", "kind": "toggle", "name": "Sub", "at": [960, 500],
                                      "parent": "Card", "value": "save.sub"})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Card", "at": [900, 540]})
        self.assertEqual([p["name"] for p in out["parts"]], ["CardBorder"], "not the parts of its children")
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [900, 540],
                                            "size": [400, 300], "image": "assets/ui/panel.png", "slice": 16})
        self.assertEqual(out["removed"], ["CardBorder"], "9-slice art has its own border")
        self.assertNotIn("parts", out)
        self.assertNotIn("CardBorder", bpy.data.objects)
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Sub", "size": [80, 40]})
        self.assertEqual(sorted(p["name"] for p in out["parts"]), ["SubBg", "SubOff", "SubOn", "SubOnKnob"])
        for name in ("heart.png", "heart-empty.png"):
            write_png(self.root / "assets" / "ui" / name, 16, 16)
        self.client.result("ui_add", {"canvas": "Menu", "kind": "icons", "name": "Lives", "at": [200, 60], "count": 3})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Lives", "count": 2})
        self.assertEqual(sorted(out["removed"]), ["Lives3", "LivesSlot3"])

    def test_set(self):
        self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play",
                                      "at": [960, 600]})
        self.client.result("ui_add", {"canvas": "Menu", "kind": "group", "name": "Box", "at": [960, 500]})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "at": [960, 700], "body": "Start",
                                            "size": [400, 90], "color": "#336699", "disabled": True,
                                            "anchor": "bottom", "stretch": "none", "text_size": 48})
        element = out["element"]
        self.assertEqual((element["at"], element["size"], element["anchor"]), ([960.0, 700.0], [400.0, 90.0], "bottom"))
        self.assertEqual(element["components"]["UiButton"], {"disabled": True})
        parts = {p["part"]: p for p in out["parts"]}
        self.assertEqual((parts["label"]["text"], parts["label"]["text_size"]), ("Start", 48.0))
        self.assertEqual(parts["bg"]["color"], "#336699")
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "parent": "Box", "visible": False})
        self.assertEqual((out["element"]["parent"], out["element"]["visible"]), ("Box", False))
        self.assertNotIn("UiAnchor", out["element"].get("components", {}), "anchors are for top-level elements")
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "parent": None})
        self.assertNotIn("parent", out["element"])
        self.assertEqual(out["element"]["at"], [960.0, 700.0])
        # Sounds of an element with UiButton, and a text's shadow.
        (self.root / "assets" / "sfx").mkdir(parents=True)
        (self.root / "assets" / "sfx" / "click.ogg").write_bytes(b"\0" * 16)
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "sound": "assets/sfx/click.ogg",
                                            "focus_sound": "assets/sfx/click.ogg"})
        self.assertEqual(out["element"]["components"]["UiButton"],
                         {"disabled": True, "sound": "assets/sfx/click.ogg", "focusSound": "assets/sfx/click.ogg"})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "focus_sound": ""})
        self.assertNotIn("focusSound", out["element"]["components"]["UiButton"])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Play", "sound": "assets/sfx/clik.ogg"})
        self.assertIn("not found", err["message"])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Box", "sound": "assets/sfx/click.ogg"})
        self.assertIn("goes with buttons", err["message"])
        self.client.result("ui_add", {"canvas": "Menu", "kind": "text", "name": "Title", "body": "Hi", "at": [960, 200]})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Title", "shadow": [0, 6, 18]})
        self.assertEqual(out["element"]["components"]["UiText"], {"shadow": [0.0, 6.0], "shadowBlur": 18.0})
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Play", "colour": "#FFFFFF"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("Did you mean 'color'", err["message"])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Box", "size": [10, 10]})
        self.assertIn("does not apply", err["message"])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Play"})
        self.assertIn("nothing to change", err["message"])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Plya", "at": [0, 0]})
        self.assertIn("Did you mean 'Play'", err["message"])

    def test_remove(self):
        self.client.result("ui_canvas", {"name": "HUD", "kind": "hud"})
        self.client.result("ui_add", {"canvas": "HUD", "kind": "bar", "name": "Health", "at": [200, 60],
                                      "size": [300, 20]})
        out = self.client.result("ui_remove", {"canvas": "HUD", "name": "Health"})
        self.assertEqual(sorted(out["removed"]), ["Health", "HealthBg", "HealthFill"])
        out = self.client.result("ui_remove", {"canvas": "HUD"})
        self.assertEqual((out["removed_canvas"], out["canvases"]), ("HUD", ["Menu"]))
        err = self.client.error("ui_remove", {"canvas": "Menu"})
        self.assertIn("only canvas", err["message"])

    def test_one_undo_step_per_write(self):
        self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play",
                                      "at": [960, 600]})
        self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "at": [100, 100]})
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertEqual(self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["elements"][0]["at"],
                         [960.0, 600.0])
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertNotIn("Play", bpy.data.objects)
        self.assertNotIn("PlayLabel", bpy.data.objects, "the parts went with the same step")

    def test_locked_and_paused(self):
        from gamestudio import activity

        self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play",
                                      "at": [960, 600]})
        bpy.data.objects["PlayLabel"].gs.ai_lock = True
        for method, params in (("ui_set", {"canvas": "Menu", "name": "Play", "body": "Go"}),
                               ("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Go",
                                           "at": [0, 0]}),
                               ("ui_remove", {"canvas": "Menu", "name": "Play"})):
            err = self.client.error(method, params)
            self.assertIn("locked", err["message"], method)
        self.assertEqual(bpy.data.objects["PlayLabel"].data.body, "Play")
        # Locking the button locks its parts: its label and art cannot be changed or removed one by one.
        bpy.data.objects["PlayLabel"].gs.ai_lock = False
        bpy.data.objects["Play"].gs.ai_lock = True
        for method, params in (("ui_set", {"canvas": "Menu", "name": "PlayLabel", "body": "Go"}),
                               ("ui_add", {"canvas": "Menu", "kind": "text", "name": "PlayLabel", "body": "Go",
                                           "at": [0, 0]}),
                               ("ui_remove", {"canvas": "Menu", "name": "PlayBg"})):
            err = self.client.error(method, params)
            self.assertIn("'Play' is locked", err["message"], method)
        self.assertEqual(bpy.data.objects["PlayLabel"].data.body, "Play")
        self.assertIn("PlayBg", bpy.data.objects)
        activity.set_paused(True)
        try:
            for method, params in (("ui_add", {"canvas": "Menu", "kind": "group", "name": "G", "at": [0, 0]}),
                                   ("ui_open", {})):
                self.assertEqual(self.client.error(method, params)["code"], server.PAUSED_BY_USER, method)
            self.client.result("ui_list")  # reading still works
        finally:
            activity.set_paused(False)
        self.assertNotIn("G", bpy.data.objects)


class UiPhase3Test(UiBridgeTestCase):
    """§11: ui_add kind model and ui_set its settings, panel shaders, project_name, Language buttons, ui_strings and
    ui_translate."""

    registry_data = dict(ui_registry(), shaders=[SHINE, GRADIENT])

    def setUp(self):
        super().setUp()
        self.open_ui()

    def test_model(self):
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "model", "name": "Gem", "at": [960, 420],
                                            "kit": "crate", "kit_params": {"width": 2}, "size": [400, 300],
                                            "spin": 1.2, "view": "front", "background": "#00000080"})
        element = out["element"]
        self.assertEqual((out["created"], element["kind"], element["type"], element["size"], element["view"],
                          element["spin"], element["background"], element["source"]),
                         (True, "model", "model", [400.0, 300.0], "front", 1.2, "#00000080",
                          [{"object": "GemModel", "kit": "crate"}]))
        self.assertNotIn("parts", out, "the model's objects are not parts")
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Gem", "zoom": 1.5, "light": "flat",
                                            "size": [300, 300]})
        self.assertEqual((out["element"]["zoom"], out["element"]["light"], out["element"]["size"]),
                         (1.5, "flat", [300.0, 300.0]))
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertEqual((bpy.data.objects["Gem"].gs_ui.width, components.get_values(bpy.data.objects["Gem"],
                                                                                     "UiModel")["zoom"]), (400, 1.0))
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "model", "name": "Gem", "at": [960, 420],
                                            "clip": ""})
        self.assertEqual((out["created"], out["element"]["source"][0]["object"]), (False, "GemModel"), "kept")
        listed = self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["elements"]
        self.assertEqual([e["name"] for e in listed], ["Gem"], "its objects are not elements")
        for params, words in (({"kit": "crate", "asset": "Tree"}, "one of source, kit or asset"),
                              ({"kit": "nope"}, "Unknown kit"), ({"view": "back"}, "view must be one of")):
            err = self.client.error("ui_add", {"canvas": "Menu", "kind": "model", "name": "Other", "at": [0, 0],
                                               **params})
            self.assertIn(words, err["message"], params)
        err = self.client.error("ui_add", {"canvas": "Menu", "kind": "model", "name": "Other", "at": [0, 0],
                                           "label": "x"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("kit_params", err["data"]["hint"])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Gem", "kit": "barrel"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertNotIn("Other", bpy.data.objects)

    def test_shader_project_name_and_language_button(self):
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540],
                                            "size": [400, 200], "shader": "Shine", "shader_values": {"width": 0.4}})
        self.assertEqual((out["element"]["shader"], out["element"]["shader_values"]), ("Shine", {"width": 0.4}))
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Card", "shader": "Gradient"})
        self.assertEqual(out["element"]["shader"], "Gradient")
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Card", "shader": "Glow"})
        self.assertIn("Unknown game shader 'Glow'", err["message"])
        self.assertIn("Shine", err["data"]["hint"])
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "text", "name": "GameTitle", "body": "Gem Island",
                                            "at": [960, 200], "project_name": True})
        self.assertTrue(out["element"]["project_name"])
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Language",
                                            "label": "{ui.localeName}", "at": [960, 700], "action": "locale",
                                            "target": "pt-br"})
        self.assertEqual(out["element"]["components"]["UiButton"], {"action": "locale", "target": "pt-BR"})
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "Language", "target": "Options"})
        self.assertIn("language code", err["data"]["hint"])

    def test_strings_and_translate(self):
        from gamestudio.core import gameui_export

        exported = []
        with mock.patch.object(gameui_export, "export_locales", create=True,
                               side_effect=lambda root: exported.append(root) or {"locales": ["fr"]}):
            self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play",
                                          "at": [960, 600]})
            self.client.result("ui_add", {"canvas": "Menu", "kind": "text", "name": "Score", "body": "Score {score}",
                                          "at": [100, 60]})
            out = self.client.result("ui_strings", {"locale": "fr"})
            self.assertEqual((out["source"], out["locale"], out["exists"], out["missing"]),
                             ("ui/ui.blend", "fr", False, 2))
            err = self.client.error("ui_translate", {"locale": "fr", "strings": {"Play": "Jouer"}})
            self.assertIn("name the language", err["message"])
            out = self.client.result("ui_translate", {"locale": "fr", "name": "Français",
                                                      "strings": {"Play": "Jouer", "Score {score}": "{score} pts"}})
            self.assertEqual((out["created"], out["added"], out["strings"], out["missing"], out["exported"]),
                             (True, 2, 2, 0, {"locales": ["fr"]}))
            self.assertEqual(exported, [self.root])
            self.assertTrue((self.root / "ui" / "locales" / "fr.json").is_file())
            out = self.client.result("ui_strings", {"locale": "fr"})
            self.assertEqual({t["text"]: t["translation"] for t in out["texts"]},
                             {"Play": "Jouer", "Score {score}": "{score} pts"})
            # Strict parameters and codes.
            err = self.client.error("ui_translate", {"locale": "fr", "strigns": {}})
            self.assertEqual(err["code"], server.INVALID_PARAMS)
            self.assertIn("Did you mean 'strings'", err["message"])
            err = self.client.error("ui_translate", {"locale": "fr"})
            self.assertEqual(err["code"], server.INVALID_PARAMS)
            err = self.client.error("ui_strings", {"locale": "francais"})
            self.assertEqual(err["data"]["code"], "invalid_locale")
            err = self.client.error("ui_translate", {"locale": "fr", "strings": {"Score {score}": "Points"}})
            self.assertIn("keeps the text's bindings", err["message"])
            # From a level: the exported texts, and the tables are written all the same.
            level = self.save_scene()
            self.assertEqual(pathlib.Path(bpy.data.filepath), level)
            out = self.client.result("ui_strings")
            self.assertEqual(out["source"], "export")
            out = self.client.result("ui_translate", {"locale": "de", "name": "Deutsch", "strings": {"Play": "Los"}})
            self.assertTrue(out["created"])


class UiToolFixesTest(UiBridgeTestCase):
    """Hex shader colours (ui_add, ui_set, set_material_shader), a button's colours through ui_set, a level's
    object as a model, texts' rect, and save's ui_export."""

    registry_data = dict(ui_registry(), shaders=[SHINE, GRADIENT])

    def setUp(self):
        super().setUp()
        self.open_ui()

    def test_hex_shader_colours_and_button_colours(self):
        from gamestudio.core import shaders

        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play",
                                            "at": [960, 600]})
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "PlayFocus", "shader": "Shine",
                                            "shader_values": {"color": "#FFFFFF80", "width": 0.3}})
        self.assertEqual(out["element"]["shader"], "Shine")
        color = out["element"]["shader_values"]["color"]
        self.assertEqual([round(c, 3) for c in color], [1.0, 1.0, 1.0, 0.502])
        err = self.client.error("ui_set", {"canvas": "Menu", "name": "PlayFocus", "shader_values": {"color": "#FFF"}})
        self.assertIn("shader_values.color must be '#RRGGBB'", err["message"])
        out = self.client.result("ui_set", {"canvas": "Menu", "name": "Play", "text_color": "#17171C",
                                            "focus_color": "#FFFFFF", "focus_text_color": "#FF0000"})
        colours = {p["part"]: p.get("color") for p in out["parts"]}
        self.assertEqual((colours["label"], colours["focus"], colours["focus_label"]),
                         ("#17171C", "#FFFFFF", "#FF0000"))
        # set_material_shader takes hex colours too, made linear.
        mat = bpy.data.objects["PlayBg"].data.materials[0]
        out = self.client.result("set_material_shader", {"material": mat.name, "shader": "Shine",
                                                         "values": {"color": "#808080"}})
        self.assertAlmostEqual(out["materials"][0]["values"]["color"][0], 0.2158605, places=5)
        self.assertAlmostEqual(shaders.get_values(mat)["color"][3], 1.0, places=6)
        err = self.client.error("set_material_shader", {"material": mat.name, "shader": "Shine",
                                                        "values": {"color": "#80808"}})
        self.assertEqual(err["code"], server.INVALID_PARAMS)
        self.assertIn("values.color must be '#RRGGBB'", err["message"])

    def test_text_rect_linked_model_and_save(self):
        title = self.client.result("ui_add", {"canvas": "Menu", "kind": "text", "name": "Title", "body": "Gem Island",
                                              "at": [960, 200], "size": 100})["element"]
        x, y, w, h = title["rect"]
        self.assertAlmostEqual(x + w / 2, 960, delta=8)
        listed = self.client.result("ui_list", {"canvas": "Menu"})["canvases"][0]["elements"]
        self.assertEqual(listed[0]["rect"], title["rect"])
        # save reports what it exported for the game.
        out = self.client.result("save")
        self.assertEqual(out["ui_export"]["canvases"][0]["canvas"], "Menu")
        self.assertEqual(set(out["ui_export"]["canvases"][0]), {"canvas", "file", "rev", "written", "elements"})
        self.assertTrue(out["ui_export"]["canvases"][0]["written"])
        self.assertEqual((out["ui_export"]["locales"], out["ui_export"]["warnings"]), ([], []))
        again = self.client.result("save")
        self.assertFalse(again["ui_export"]["canvases"][0]["written"], "unchanged: not written again")
        self.assertEqual(again["ui_export"]["canvases"][0]["rev"], out["ui_export"]["canvases"][0]["rev"])
        # A level's object as a model.
        from test_gameui_build import LinkedModelTest

        ui = pathlib.Path(bpy.data.filepath)
        bpy.ops.wm.read_homefile(use_empty=True)
        LinkedModelTest.starter_level(bpy.context.scene)
        level = self.save_scene()
        bpy.ops.wm.open_mainfile(filepath=str(ui))
        out = self.client.result("ui_add", {"canvas": "Menu", "kind": "model", "name": "Hero", "at": [1500, 540],
                                            "source": "scenes/main.blend:Player", "view": "front"})
        self.assertEqual(out["element"]["source"], [{"object": "HeroModel", "linked": "scenes/main.blend:Player",
                                                     "kit": "character"}])
        err = self.client.error("ui_add", {"canvas": "Menu", "kind": "model", "name": "Bad", "at": [0, 0],
                                           "source": "scenes/main.blend:Hero"})
        self.assertIn("scenes/main.blend has no object 'Hero'", err["message"])
        self.assertIn("Player", err["data"]["hint"])
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertNotIn("Hero", bpy.data.objects)
        self.assertTrue(level.is_file())
        # A level's save has no ui_export.
        bpy.ops.wm.open_mainfile(filepath=str(level))
        self.assertNotIn("ui_export", self.client.result("save"))


class UiValidateTest(UiBridgeTestCase):
    """validate_assets in a UI file: every canvas, no PBR or mesh checks for flat UI art, missing files still."""

    def test_validate_assets_checks_every_canvas_without_pbr_noise(self):
        from test_gameui_build import builtin_font_file

        self.open_ui()
        self.client.result("ui_canvas", {"name": "HUD", "kind": "hud", "open": "start"})
        png = write_png(self.root / "assets" / "ui" / "heart.png", 16, 16)
        font = self.root / "assets" / "fonts" / "Mono.woff2"
        font.parent.mkdir(parents=True, exist_ok=True)
        font.write_bytes(builtin_font_file().read_bytes())
        adds = [
            {"canvas": "HUD", "kind": "image", "name": "Heart", "path": "assets/ui/heart.png", "at": [60, 60]},
            {"canvas": "HUD", "kind": "bar", "name": "Health", "at": [300, 60], "size": [300, 24], "value": "health"},
            {"canvas": "HUD", "kind": "text", "name": "Lives", "body": "x {lives}", "at": [120, 60],
             "font": "assets/fonts/Mono.woff2"},
            {"canvas": "Menu", "kind": "panel", "name": "Card", "at": [960, 540], "size": [640, 400], "radius": 24,
             "border": 2},
            {"canvas": "Menu", "kind": "button", "name": "Play", "label": "Play", "at": [960, 600]},
            {"canvas": "Menu", "kind": "text", "name": "Title", "body": "Game Over", "at": [960, 200]},
        ]
        for params in adds:
            self.client.result("ui_add", params)
        self.assertEqual(bpy.context.scene.name, "Menu")  # the canvas on screen; HUD is checked too
        out = self.client.result("validate_assets")
        self.assertIn("2 canvas(es) (HUD, Menu)", out["scope"])
        # Flat Emission materials, single-face panels, the canvas camera's empty UiCanvas.focus and a bar's fill in
        # another canvas than the one on screen are all fine.
        self.assertEqual((out["ok"], out["totals"]["error"], out["totals"]["warning"]), (True, 0, 0), out)
        verbose = {o["name"]: o for o in self.client.result("validate_assets", {"verbose": True})["objects"]}
        for name in ("Heart", "HealthFill", "Lives", "Card", "CardBorder", "PlayLabel", "Title", "HUD Camera"):
            self.assertIn(name, verbose)
        self.assertEqual((verbose["Heart"]["canvas"], verbose["Heart"]["kind"]), ("HUD", "image"))
        self.assertEqual(verbose["Card"]["canvas"], "Menu")
        self.assertNotIn("mesh", verbose["Card"], "no mesh, UV or material checks for UI art")
        # Missing files are still errors: an image and a font, in a canvas that is not on screen.
        png.unlink()
        font.unlink()
        out = self.client.result("validate_assets")
        self.assertFalse(out["ok"])
        by_name = {o["name"]: o for o in out["objects"]}
        self.assertEqual(set(by_name), {"Heart", "Lives"})
        self.assertEqual(by_name["Heart"]["canvas"], "HUD")
        [issue] = by_name["Heart"]["issues"]
        self.assertEqual((issue["severity"], issue["check"]), ("error", "files"))
        self.assertIn("missing image file", issue["message"])
        [issue] = by_name["Lives"]["issues"]
        self.assertIn("missing font file", issue["message"])
        named = self.client.result("validate_assets", {"names": ["Heart"]})
        self.assertEqual([o["name"] for o in named["objects"]], ["Heart"])

    def test_validate_assets_is_quiet_about_controls_icons_sliced_panels_and_animations(self):
        self.open_ui()
        for name in ("heart.png", "heart-empty.png", "panel.png"):
            write_png(self.root / "assets" / "ui" / name, 16, 16)
        adds = [
            {"kind": "icons", "name": "Lives", "at": [200, 60]},
            {"kind": "slider", "name": "Music", "at": [960, 400]},
            {"kind": "toggle", "name": "Subtitles", "at": [960, 500], "value": "save.subtitles"},
            {"kind": "panel", "name": "Card", "at": [960, 540], "size": [400, 300], "image": "assets/ui/panel.png",
             "slice": 4},
        ]
        for params in adds:
            self.client.result("ui_add", {"canvas": "Menu", **params})
        self.client.result("ui_canvas", {"name": "Menu", "transition": {"loop": "pulse"}})
        out = self.client.result("validate_assets")
        self.assertEqual((out["ok"], out["totals"]["error"], out["totals"]["warning"]), (True, 0, 0), out)


if __name__ == "__main__":
    unittest.main()
