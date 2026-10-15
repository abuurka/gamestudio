# SPDX-License-Identifier: GPL-3.0-or-later
"""gs.gameui, the game UI builder (docs/GAME-UI.md §6): canvases, each element kind, create-or-update,
colours, px <-> Blender units, parenting, anchors, set_font, remove and describe."""

import json
import math
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import bpy

from gamestudio import gameui
from gamestudio.bridge import api
from gamestudio.core import components, gameui as core, project, registry
from gamestudio.gameui import build, files, locales

import helpers


def ui_registry() -> dict:
    """The fixture registry plus the engine's UI components."""
    data = helpers.fixture_registry()
    with open(helpers.FIXTURES / "gameui_components.json", "r", encoding="utf-8") as f:
        data["components"] = data["components"] + json.load(f)["components"]
    return data


def builtin_font_file(name="DejaVuSansMono.woff2") -> pathlib.Path:
    return pathlib.Path(bpy.utils.system_resource("DATAFILES", path="fonts")) / name


def write_png(path, width, height, color=(1.0, 0.0, 0.0, 1.0)) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = bpy.data.images.new("gs_test_png", width, height, alpha=True)
    img.pixels[:] = list(color) * (width * height)
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return path


class UiFileTestCase(helpers.ProjectTestCase):
    """A temporary project whose ui/ui.blend is open (built in this Blender) with one canvas, "Menu"."""

    registry_data = ui_registry()

    def setUp(self):
        super().setUp()
        api.agent_dirty = False
        self.path = gameui.new_ui_file(self.root)
        self.outside = pathlib.Path(tempfile.mkdtemp(prefix="gs_outside_", dir=helpers.tmp_base()))

    def tearDown(self):
        shutil.rmtree(self.outside, ignore_errors=True)
        super().tearDown()

    def assertPx(self, obj, at, scene="Menu", places=3):
        x, y = build.origin_px(bpy.data.scenes[scene], obj)
        self.assertAlmostEqual(x, at[0], places=places)
        self.assertAlmostEqual(y, at[1], places=places)


class NewUiFileTest(UiFileTestCase):
    def test_the_new_file_is_open_with_a_menu_canvas(self):
        self.assertEqual(self.path, self.root / "ui" / "ui.blend")
        self.assertTrue(self.path.is_file())
        self.assertEqual(pathlib.Path(bpy.data.filepath), self.path)
        self.assertTrue(core.is_ui_file())
        self.assertEqual(gameui.canvas_names(), ["Menu"])
        scene = bpy.data.scenes["Menu"]
        cam = scene.camera
        self.assertEqual(cam.data.type, "ORTHO")
        self.assertAlmostEqual(cam.data.ortho_scale, 19.2, places=5)
        self.assertEqual(tuple(cam.location), (0.0, 0.0, 10.0))
        self.assertAlmostEqual(core.px_per_unit(scene), 100.0, places=4)
        self.assertEqual(scene.view_settings.view_transform, "Standard")
        self.assertTrue(scene.tool_settings.use_snap)
        self.assertEqual(scene.tool_settings.snap_elements, {"INCREMENT"})
        settings = components.get_values(cam, "UiCanvas")
        self.assertEqual((settings["kind"], settings["open"]), ("menu", "manual"))
        self.assertEqual([round(c, 3) for c in settings["backdrop"]], [0, 0, 0, 0.6])

    def test_an_untitled_session_loads_the_projects_registry_first(self):
        # A factory-startup Blender: no file, so no project and no registry, until the UI file exists.
        bpy.ops.wm.read_homefile(use_empty=True)
        project.set_project_root_override(None)
        registry.reload_registry()
        self.assertIsNone(registry.schema_ref("UiCanvas"))
        other = helpers.make_project(ui_registry())
        self.addCleanup(shutil.rmtree, other, True)
        path = gameui.new_ui_file(other)
        self.assertEqual(pathlib.Path(bpy.data.filepath), path)
        self.assertEqual(gameui.canvas_names(), ["Menu"])
        self.assertTrue(components.has_component(bpy.data.scenes["Menu"].camera, "UiCanvas"))

    def test_refuses_to_overwrite(self):
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.new_ui_file(self.root)
        self.assertEqual(ctx.exception.code, "ui_file_exists")

    def test_canvas_specs(self):
        self.assertEqual(files.canvas_specs(()), [{"name": "Menu"}])
        self.assertEqual(files.canvas_specs(["Title", {"name": "HUD", "kind": "hud"}]),
                         [{"name": "Title"}, {"name": "HUD", "kind": "hud"}])
        for bad in (["A", "A"], [{"kind": "hud"}], ["gs_x"], [3]):
            with self.assertRaises(gameui.GameUIError):
                files.canvas_specs(bad)

    def test_written_by_a_background_blender_when_a_file_is_open(self):
        level = self.root / "scenes" / "main.blend"
        level.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(level))
        self.path.unlink()
        path = gameui.new_ui_file(self.root, ["Title", {"name": "HUD", "kind": "hud", "open": "start"}])
        self.assertTrue(path.is_file())
        self.assertEqual(pathlib.Path(bpy.data.filepath), level, "the open file stays open")
        bpy.ops.wm.open_mainfile(filepath=str(path))
        self.assertEqual(sorted(gameui.canvas_names()), ["HUD", "Title"])
        hud = components.get_values(bpy.data.scenes["HUD"].camera, "UiCanvas")
        self.assertEqual((hud["kind"], hud["open"], hud["backdrop"]), ("hud", "start", [0.0, 0.0, 0.0, 0.0]))


class CanvasTest(UiFileTestCase):
    def test_create_and_update_keep_what_is_not_given(self):
        hud = gameui.canvas("HUD", kind="hud", open="start", size=(1280, 720))
        self.assertEqual(core.design_size(hud), (1280, 720))
        self.assertAlmostEqual(core.px_per_unit(hud), 100.0, places=4)
        values = components.get_values(hud.camera, "UiCanvas")
        self.assertEqual((values["kind"], values["open"], values["backdrop"]), ("hud", "start", [0.0, 0.0, 0.0, 0.0]))
        gameui.canvas("HUD", layer=2, backdrop="#00000080")
        values = components.get_values(hud.camera, "UiCanvas")
        self.assertEqual((values["kind"], values["open"], values["layer"]), ("hud", "start", 2))
        self.assertAlmostEqual(values["backdrop"][3], 128 / 255, places=4)
        self.assertIs(gameui.canvas(hud, pause_menu=True), hud)
        self.assertTrue(components.get_values(hud.camera, "UiCanvas")["pauseMenu"])

    def test_a_new_design_size_keeps_the_camera_place_and_px_per_unit(self):
        scene = bpy.data.scenes["Menu"]
        cam = scene.camera
        cam.location = (3.0, -1.0, 10.0)
        cam.data.ortho_scale = 9.6  # 200 px per unit
        gameui.canvas("Menu", size=(1280, 720))
        self.assertEqual(tuple(round(v, 4) for v in cam.location), (3.0, -1.0, 10.0))
        self.assertAlmostEqual(core.px_per_unit(scene), 200.0, places=3)
        self.assertAlmostEqual(cam.data.ortho_scale, 6.4, places=5)
        scene.gs_ui.width = 1600  # the Scene > Game UI Screen panel
        self.assertEqual(tuple(round(v, 4) for v in cam.location), (3.0, -1.0, 10.0))
        self.assertAlmostEqual(core.px_per_unit(scene), 200.0, places=3)
        # A camera that cannot frame a canvas (perspective) is set up from scratch: centred, 100 px per unit.
        cam.data.type = "PERSP"
        gameui.canvas("Menu", size=(1920, 1080))
        self.assertEqual(tuple(round(v, 4) for v in cam.location), (0.0, 0.0, 10.0))
        self.assertAlmostEqual(core.px_per_unit(scene), 100.0, places=4)

    def test_sensor_fit_decides_which_side_ortho_scale_spans(self):
        scene = bpy.data.scenes["Menu"]
        cam = scene.camera
        for fit, scale in (("AUTO", 19.2), ("HORIZONTAL", 19.2), ("VERTICAL", 10.8)):
            cam.data.sensor_fit = fit
            cam.data.ortho_scale = scale
            self.assertAlmostEqual(core.px_per_unit(scene), 100.0, places=4, msg=fit)
            self.assertEqual(tuple(round(v, 2) for v in core.to_px(scene, (1.0, 1.0))), (1060.0, 440.0), fit)
        gameui.canvas("Menu", size=(1280, 720))  # Vertical: ortho_scale spans the height
        self.assertAlmostEqual(cam.data.ortho_scale, 7.2, places=5)
        self.assertAlmostEqual(core.px_per_unit(scene), 100.0, places=4)

    def test_bad_values_change_nothing(self):
        for kwargs in ({"kind": "menus"}, {"backdrop": "red"}, {"size": (0, 10)}, {"pause": "yes"}):
            with self.assertRaises(ValueError, msg=kwargs):
                gameui.canvas("Pause", **kwargs)
        self.assertNotIn("Pause", bpy.data.scenes)
        with self.assertRaises(gameui.GameUIError):
            gameui.canvas("gs_hidden")

    def test_needs_the_ui_file_and_the_ui_components(self):
        registry.set_registry(helpers.fixture_registry())
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.canvas("HUD")
        self.assertEqual(ctx.exception.code, "ui_components_missing")
        self.assertIn("dev server", str(ctx.exception))
        registry.set_registry(ui_registry())
        (self.root / "scenes").mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "scenes" / "main.blend"))
        for call in (lambda: gameui.canvas("HUD"), lambda: gameui.text("Menu", "T", "x", (0, 0)),
                     lambda: gameui.remove("Menu", "T"), lambda: gameui.update("Menu", "T", at=(0, 0))):
            with self.assertRaises(gameui.GameUIError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "ui_file_not_open")
        self.assertEqual(gameui.describe(), {"file": None, "open": False, "ui_file": "ui/ui.blend", "exists": True,
                                             "canvases": [], "hint": gameui.describe()["hint"]})

    def test_focus_is_a_button_of_the_canvas(self):
        scene = bpy.data.scenes["Menu"]
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.canvas("Options", focus="Back")
        self.assertIn("new canvas has no buttons", str(ctx.exception))
        self.assertNotIn("Options", bpy.data.scenes, "nothing made")
        gameui.group("Menu", "Buttons", (960, 600))
        play = gameui.button("Menu", "Play", "Play", (960, 600), parent="Buttons")
        gameui.button("Menu", "Back", "Back", (960, 710), parent="Buttons")
        gameui.canvas("Menu", focus="Back")
        self.assertEqual(components.get_values(scene.camera, "UiCanvas")["focus"], "Back")
        self.assertEqual(gameui.describe("Menu")["canvases"][0]["settings"]["focus"], "Back")
        gameui.canvas("Menu", layer=1)
        self.assertEqual(components.get_values(scene.camera, "UiCanvas")["focus"], "Back", "kept")
        gameui.canvas("HUD", kind="hud")
        gameui.button("HUD", "PauseButton", "II", (1800, 60))
        for bad, words in (("PlayLabel", "'PlayLabel' is a text element, not a button"),
                           ("Buttons", "'Buttons' is a group element, not a button"),
                           ("Plya", "Did you mean 'Play'"), ("PauseButton", "has no element 'PauseButton'")):
            with self.assertRaises(gameui.GameUIError, msg=bad) as ctx:
                gameui.canvas("Menu", focus=bad)
            self.assertIn(words, str(ctx.exception))
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.canvas("Menu", focus="Buttons")
        self.assertEqual(ctx.exception.hint, "Buttons of 'Menu': Play, Back.")
        self.assertEqual(components.get_values(scene.camera, "UiCanvas")["focus"], "Back", "unchanged")
        gameui.canvas("Menu", focus=play)  # an Object works too
        self.assertEqual(components.get_values(scene.camera, "UiCanvas")["focus"], "Play")
        gameui.canvas("Menu", focus="")
        self.assertIsNone(components.get_values(scene.camera, "UiCanvas")["focus"], "cleared")

    def test_unknown_canvas_suggests(self):
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.text("Menuu", "T", "x", (0, 0))
        self.assertIn("Did you mean 'Menu'", str(ctx.exception))


class ColorTest(unittest.TestCase):
    def test_parse_and_format(self):
        self.assertEqual(build.parse_color("#FFFFFF"), (1.0, 1.0, 1.0, 1.0))
        r, g, b, a = build.parse_color("#80808080")
        self.assertAlmostEqual(r, build.srgb_to_linear(128 / 255))
        self.assertAlmostEqual(a, 128 / 255)
        self.assertEqual(build.parse_color([0.5, 0.25, 0]), (0.5, 0.25, 0.0, 1.0))
        self.assertEqual(build.parse_color("ffd400"), build.parse_color("#FFD400"))
        for bad in ("#FFF", "#GGGGGG", [1, 2, 3], [0, 0], "red", None, [True, 0, 0]):
            with self.assertRaises(gameui.GameUIError, msg=bad):
                build.parse_color(bad)
        for text in ("#FFD400", "#17171C", "#00000080", "#4CAF50"):
            self.assertEqual(build.to_hex(build.parse_color(text)), text)


class TextTest(UiFileTestCase):
    def test_text_in_px(self):
        obj = gameui.text("Menu", "Title", "Gem Island", (960, 200), size=72, color="#FFD400", align="left",
                          valign="top-baseline")
        self.assertEqual(obj.type, "FONT")
        self.assertAlmostEqual(obj.location.x, 0.0, places=5)
        self.assertAlmostEqual(obj.location.y, 3.4, places=5)
        self.assertAlmostEqual(obj.data.size, 0.72, places=5)
        self.assertEqual((obj.data.align_x, obj.data.align_y), ("LEFT", "TOP_BASELINE"))
        # The default font is Blender's Inter, copied into the project: the game draws the same glyphs.
        self.assertEqual(build.font_path(obj.data.font), "assets/fonts/Inter.woff2")
        self.assertTrue((self.root / "assets" / "fonts" / "Inter.woff2").is_file())
        # With Inter's licence next to it: the OFL wants its copyright notice and text with every copy.
        licence = (self.root / "assets" / "fonts" / "Inter-OFL.txt").read_text(encoding="utf-8")
        self.assertTrue(licence.startswith("Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)"))
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", licence)
        mat = obj.data.materials[0]
        self.assertEqual(build.to_hex(build.material_color(mat)), "#FFD400")
        self.assertEqual(mat.node_tree.nodes["Material Output"].inputs["Surface"].links[0].from_node.type, "EMISSION")
        self.assertFalse(components.has_component(obj, "UiText"))
        info = build.describe_element(bpy.data.scenes["Menu"], obj)
        self.assertEqual({k: info[k] for k in ("name", "kind", "type", "at", "text", "text_size", "color", "align",
                                               "valign", "font", "visible", "anchor", "anchor_auto")},
                         {"name": "Title", "kind": "text", "type": "text", "at": [960.0, 200.0], "text": "Gem Island",
                          "text_size": 72.0, "color": "#FFD400", "align": "left", "valign": "top-baseline",
                          "font": "assets/fonts/Inter.woff2",
                          "visible": True, "anchor": "top", "anchor_auto": True})

    def test_update_changes_only_what_is_given(self):
        obj = gameui.text("Menu", "Score", "Score 0", (100, 60), size=40, color="#FF0000")
        again = gameui.text("Menu", "Score", "Score 10", (120, 60))
        self.assertIs(again, obj)
        self.assertEqual(obj.data.body, "Score 10")
        self.assertAlmostEqual(obj.data.size, 0.4, places=5)
        self.assertEqual(build.to_hex(build.object_color(obj)), "#FF0000")
        self.assertPx(obj, (120, 60))
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.panel("Menu", "Score", (0, 0), (10, 10))
        self.assertIn("text element, not a panel", str(ctx.exception))

    def test_box_outline_and_shadow(self):
        obj = gameui.text("Menu", "Tip", "Long text that wraps", (960, 900), box=(600, 120), outline=3,
                          outline_color="#102030", shadow=(2, 4))
        box = obj.data.text_boxes[0]
        # Blender draws a box with a height one text size (0.48) above its y: the frame is centred on `at`.
        self.assertEqual([round(v, 4) for v in (box.x, box.y, box.width, box.height)], [-3.0, 0.12, 6.0, 1.2])
        values = components.get_values(obj, "UiText")
        self.assertEqual((values["outline"], values["shadow"]), (3.0, [2.0, 4.0]))
        self.assertEqual(build.to_hex(values["outlineColor"]), "#102030")
        self.assertEqual(build.describe_element(bpy.data.scenes["Menu"], obj)["size"], [600.0, 120.0])
        gameui.text("Menu", "Tip", "x", (960, 900), box=[], outline=0)
        self.assertEqual(obj.data.text_boxes[0].width, 0.0)
        self.assertEqual(components.get_values(obj, "UiText")["outline"], 0.0)
        gameui.text("Menu", "Tip", "x", (960, 900), box=(10, -20, 300, 40))
        self.assertEqual([round(v, 4) for v in (box.x, box.y, box.width, box.height)], [0.1, -0.28, 3.0, 0.4])
        gameui.text("Menu", "Tip", "x", (960, 900), size=24)  # the frame stays where it is
        self.assertEqual([round(v, 4) for v in (box.x, box.y, box.width, box.height)], [0.1, -0.04, 3.0, 0.4])

    def test_shadow_blur_and_update(self):
        obj = gameui.text("Menu", "Title", "Gem Island", (960, 300), shadow=(0, 6, 18))
        values = components.get_values(obj, "UiText")
        self.assertEqual((values["shadow"], values["shadowBlur"]), ([0.0, 6.0], 18.0))
        gameui.update("Menu", "Title", shadow=[2, 4])  # ui_set: [x, y] keeps the blur
        values = components.get_values(obj, "UiText")
        self.assertEqual((values["shadow"], values["shadowBlur"]), ([2.0, 4.0], 18.0))
        gameui.update("Menu", "Title", shadow=[0, 0, 0])
        self.assertEqual(build.describe_element(bpy.data.scenes["Menu"], obj).get("components", {}).get("UiText", {}),
                         {}, "no shadow")
        plain = gameui.text("Menu", "Plain", "x", (960, 500))
        gameui.update("Menu", "Plain", shadow=(1, 2, 3))
        self.assertEqual(components.get_values(plain, "UiText")["shadowBlur"], 3.0)
        gameui.group("Menu", "G", (0, 0))
        for name, shadow in (("Plain", (1, 2, 3, 4)), ("Plain", (1, 2, -1)), ("G", (1, 2))):
            with self.assertRaises(gameui.GameUIError, msg=(name, shadow)):
                gameui.update("Menu", name, shadow=shadow)

    def test_bad_arguments_change_nothing(self):
        for kwargs in ({"size": 0}, {"align": "middle"}, {"color": "#12"}, {"box": (1, 2, 3)}, {"shadow": 3},
                       {"font": "missing.ttf"}, {"font": str(self.root / "gamestudio.json")}):
            with self.assertRaises(gameui.GameUIError, msg=kwargs):
                gameui.text("Menu", "Bad", "x", (0, 0), **kwargs)
        self.assertNotIn("Bad", bpy.data.objects)
        for at in ((1,), "here", (1, None)):
            with self.assertRaises(gameui.GameUIError):
                gameui.text("Menu", "Bad", "x", at)
        self.assertNotIn("Bad", bpy.data.objects)


class ShapesTest(UiFileTestCase):
    def test_panel_rounded_with_border(self):
        obj = gameui.panel("Menu", "Card", (960, 540), (400, 200), color="#000000AA", radius=20, border=4,
                           border_color="#FFFFFF")
        xs = [v.co.x for v in obj.data.vertices]
        ys = [v.co.y for v in obj.data.vertices]
        self.assertAlmostEqual(max(xs) - min(xs), 3.92, places=5)  # the fill sits inside the 4 px border
        self.assertAlmostEqual(max(ys) - min(ys), 1.92, places=5)
        self.assertGreater(len(obj.data.vertices), 4)
        self.assertEqual(len(obj.data.polygons), 1)
        ring = build.find_part(obj, "border")
        self.assertEqual(ring.name, "CardBorder")
        rxs = [v.co.x for v in ring.data.vertices]
        self.assertAlmostEqual(max(rxs) - min(rxs), 4.0, places=5)
        self.assertGreater(build.world_matrix(ring).translation.z, build.world_matrix(obj).translation.z)
        mat = obj.data.materials[0]
        self.assertEqual(mat.surface_render_method, "BLENDED")
        self.assertAlmostEqual(build.material_color(mat)[3], 0xAA / 255, places=4)
        self.assertEqual(build.to_hex(build.object_color(ring)), "#FFFFFF")
        self.assertIs(build.flat_material(build.parse_color("#000000AA")), mat, "materials are shared by colour")
        info = build.describe_element(bpy.data.scenes["Menu"], obj)
        self.assertEqual((info["size"], info["color"], info["anchor"]), ([400.0, 200.0], "#000000AA", "center"))
        gameui.panel("Menu", "Card", (960, 540), (400, 200), border=0)
        self.assertIsNone(build.find_part(obj, "border"))
        self.assertNotIn("CardBorder", bpy.data.objects)
        xs = [v.co.x for v in obj.data.vertices]
        self.assertAlmostEqual(max(xs) - min(xs), 4.0, places=5)

    def test_describe_tells_the_shape_and_rotation(self):
        """ui_list gives what it takes to match an existing card or pill: radius, border and its colour, and the
        rotation of rotated elements; defaults (0) are left out."""
        import math

        scene = bpy.data.scenes["Menu"]
        card = gameui.panel("Menu", "Card", (960, 540), (400, 200), radius=24, border=3, border_color="#FFD400")
        info = build.describe_element(scene, card)
        self.assertEqual((info["radius"], info["border"], info["border_color"]), (24.0, 3.0, "#FFD400"))
        self.assertNotIn("rotation", info)
        plain = build.describe_element(scene, gameui.panel("Menu", "Plain", (200, 200), (100, 50)))
        for key in ("radius", "border", "border_color", "rotation"):
            self.assertNotIn(key, plain, key)
        # A pill: the radius is clamped to half the height.
        self.assertEqual(build.describe_element(scene, gameui.panel("Menu", "Pill", (500, 500), (300, 80),
                                                                    radius=60))["radius"], 40.0)
        button = gameui.button("Menu", "Play", "Play", (960, 800))
        info = build.describe_element(scene, button)
        self.assertEqual(info["radius"], 42.0)
        self.assertEqual(build.describe_element(scene, build.find_part(button, "bg"))["radius"], 42.0)
        bar = gameui.bar("Menu", "Health", (300, 60), (300, 24), radius=12)
        self.assertEqual(build.describe_element(scene, bar)["radius"], 12.0)
        self.assertEqual(build.describe_element(scene, build.find_part(bar, "fill"))["radius"], 12.0)
        # Rotation: degrees clockwise on the canvas (y down), a Blender +Z rotation is counter-clockwise.
        card.rotation_euler.z = math.radians(15)
        self.assertEqual(build.describe_element(scene, card)["rotation"], -15.0)
        self.assertEqual(build.describe_element(scene, build.find_part(card, "border"))["rotation"], -15.0,
                         "parents included")
        card.rotation_euler.z = math.radians(-90)
        self.assertEqual(build.describe_element(scene, card)["rotation"], 90.0)
        card.rotation_euler.z = 0.0
        card.scale.x = -1.0  # mirrored, not turned
        self.assertNotIn("rotation", build.describe_element(scene, card))

    def test_elements_can_start_hidden(self):
        scene = bpy.data.scenes["Menu"]
        png = write_png(self.root / "assets" / "ui" / "star.png", 8, 8)
        made = [
            gameui.text("Menu", "NewBest", "New best!", (960, 400), visible=False),
            gameui.image("Menu", "Star", str(png), (100, 100), visible=False),
            gameui.panel("Menu", "Card", (960, 540), (400, 200), visible=False),
            gameui.button("Menu", "Again", "Again", (960, 700), visible=False),
            gameui.bar("Menu", "Health", (300, 60), (300, 24), visible=False),
            gameui.group("Menu", "Lives", (80, 60), visible=False),
        ]
        for obj in made:
            self.assertTrue(obj.hide_render, obj.name)
            self.assertFalse(build.describe_element(scene, obj)["visible"], obj.name)
        shown = gameui.text("Menu", "Score", "0", (100, 60))
        self.assertFalse(shown.hide_render, "visible by default")
        gameui.text("Menu", "NewBest", "New best!", (960, 400))
        self.assertTrue(made[0].hide_render, "an update without visible keeps it")
        gameui.text("Menu", "NewBest", "New best!", (960, 400), visible=True)
        self.assertFalse(made[0].hide_render)
        with self.assertRaises(gameui.GameUIError):
            gameui.group("Menu", "Bad", (0, 0), visible="no")
        self.assertNotIn("Bad", bpy.data.objects)

    def test_shared_colour_materials_are_reused_only_while_they_have_that_colour(self):
        a = gameui.panel("Menu", "A", (200, 200), (100, 100), color="#FF000080")
        b = gameui.panel("Menu", "B", (400, 200), (100, 100), color="#FF000080")
        self.assertIs(a.data.materials[0], b.data.materials[0])
        emission = next(n for n in a.data.materials[0].node_tree.nodes if n.type == "EMISSION")
        emission.inputs["Color"].default_value = (0.0, 0.0, 1.0, 1.0)  # edited in Blender: A and B turn blue
        c = gameui.panel("Menu", "C", (600, 200), (100, 100), color="#FF000080")
        self.assertEqual(build.to_hex(build.object_color(c)), "#FF000080")
        self.assertEqual(build.to_hex(build.object_color(a)), "#0000FF80")
        gameui.update("Menu", "A", color="#FF000080")
        self.assertIs(a.data.materials[0], c.data.materials[0])

    def test_pill_panel_has_distinct_vertices(self):
        obj = gameui.panel("Menu", "Pill", (500, 500), (300, 80), radius=40)
        coords = [tuple(round(c, 6) for c in v.co) for v in obj.data.vertices]
        self.assertEqual(len(coords), len(set(coords)))
        ys = [v.co.y for v in obj.data.vertices]
        self.assertAlmostEqual(max(ys) - min(ys), 0.8, places=5)

    def test_image_copied_in_and_sized_in_px(self):
        src = write_png(self.outside / "logo.png", 200, 100)
        obj = gameui.image("Menu", "Logo", str(src), (960, 260))
        copy = self.root / "assets" / "ui" / "logo.png"
        self.assertTrue(copy.is_file())
        self.assertEqual(pathlib.Path(bpy.path.abspath(obj.data.filepath)).resolve(), copy.resolve())
        self.assertEqual((obj.empty_display_type, tuple(obj.empty_image_offset)), ("IMAGE", (-0.5, -0.5)))
        self.assertAlmostEqual(obj.empty_display_size, 2.0, places=5)
        scene = bpy.data.scenes["Menu"]
        self.assertEqual([round(v, 3) for v in build.element_size(scene, obj)], [200.0, 100.0])
        gameui.image("Menu", "Logo", str(src), (960, 260), width=400, opacity=0.5)
        self.assertEqual([round(v, 3) for v in build.element_size(scene, obj)], [400.0, 200.0])
        self.assertTrue(obj.use_empty_image_alpha)
        gameui.image("Menu", "Logo", "assets/ui/logo.png", (960, 260), width=300, height=300)
        self.assertEqual([round(v, 3) for v in build.element_size(scene, obj)], [300.0, 300.0])
        info = build.describe_element(scene, obj)
        self.assertEqual((info["image"], info["opacity"]), ("assets/ui/logo.png", 0.5))
        other = write_png(self.outside / "sub" / "logo.png", 10, 10, (0, 1, 0, 1))
        gameui.image("Menu", "Logo2", str(other), (100, 100))
        self.assertTrue((self.root / "assets" / "ui" / "logo-2.png").is_file(), "a different file gets a new name")
        gameui.image("Menu", "Logo3", str(src), (100, 100))
        self.assertFalse((self.root / "assets" / "ui" / "logo-3.png").exists(), "the same file is reused")

    def test_button_parts_and_updates(self):
        scene = bpy.data.scenes["Menu"]
        obj = gameui.button("Menu", "Play", "Play", (960, 700), action="close", order=1)
        self.assertEqual((obj.type, obj.empty_display_type), ("EMPTY", "PLAIN_AXES"))
        values = components.get_values(obj, "UiButton")
        self.assertEqual((values["action"], values["order"]), ("close", 1))
        parts = {c[build.PART_PROP]: c for c in obj.children}
        self.assertEqual(set(parts), {"bg", "focus", "label", "focus_label"})
        self.assertEqual(parts["bg"].name, "PlayBg")
        self.assertEqual(components.get_values(parts["focus"], "UiState")["show"], build.FOCUS_STATE)
        self.assertEqual(components.get_values(parts["focus_label"], "UiState")["show"], build.FOCUS_STATE)
        self.assertFalse(components.has_component(parts["label"], "UiState"))
        self.assertEqual(parts["label"].data.body, "Play")
        self.assertEqual(build.to_hex(build.object_color(parts["focus"])), "#FFD400")
        self.assertEqual(build.to_hex(build.object_color(parts["focus_label"])), "#17171C")
        # Bg < Label < Focus < FocusLabel: the focus art hides the normal label, which would show through
        # the focus label's anti-aliased edges.
        stack = ("bg", "label", "focus", "focus_label")
        zs = [build.world_matrix(parts[p]).translation.z for p in stack]
        self.assertEqual(zs, sorted(zs))
        self.assertEqual(len(set(zs)), 4)
        # A button built with the old stacking (label above the focus art) is re-stacked by any update.
        parts["label"].location.z, parts["focus"].location.z = 3 * build.PART_Z, 2 * build.PART_Z
        gameui.button("Menu", "Play", "Play", (960, 700))
        self.assertEqual([build.world_matrix(parts[p]).translation.z for p in stack], zs)
        self.assertEqual([round(v, 3) for v in build.element_size(scene, obj)], [360, 84])
        for part in parts.values():
            self.assertPx(part, (960, 700))
        gameui.button("Menu", "Play", "Start", (960, 650), size=(400, 100), color="#202020")
        self.assertEqual(parts["label"].data.body, "Start")
        self.assertEqual(parts["focus_label"].data.body, "Start")
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["bg"])], [400.0, 100.0])
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["focus"])], [400.0, 100.0])
        self.assertEqual(build.to_hex(build.object_color(parts["bg"])), "#202020")
        self.assertPx(parts["label"], (960, 650))
        self.assertEqual(components.get_values(obj, "UiButton")["action"], "close", "kept")
        gameui.button("Menu", "Play", "Start", (960, 650), focus_text_color="#FFFFFF")
        self.assertIsNone(build.find_part(obj, "focus_label"), "the same colours need one label")
        info = build.describe_element(scene, obj)
        self.assertEqual(info["components"]["UiButton"], {"action": "close", "order": 1})
        self.assertEqual(sorted(info["parts"]), ["PlayBg", "PlayFocus", "PlayLabel"])

    def test_button_sounds(self):
        scene = bpy.data.scenes["Menu"]
        sfx = self.root / "assets" / "sfx"
        sfx.mkdir(parents=True)
        for name in ("click.wav", "tick.ogg"):
            (sfx / name).write_bytes(b"\0" * 16)
        obj = gameui.button("Menu", "Play", "Play", (960, 700), sound="assets/sfx/click.wav",
                            focus_sound=str(sfx / "tick.ogg"))  # absolute paths in the project become relative
        values = components.get_values(obj, "UiButton")
        self.assertEqual((values["sound"], values["focusSound"]), ("assets/sfx/click.wav", "assets/sfx/tick.ogg"))
        gameui.button("Menu", "Play", "Start", (960, 700))
        self.assertEqual(components.get_values(obj, "UiButton")["sound"], "assets/sfx/click.wav", "kept")
        gameui.update("Menu", "Play", focus_sound="")
        self.assertEqual(build.describe_element(scene, obj)["components"]["UiButton"], {"sound": "assets/sfx/click.wav"})
        # Bad paths change nothing.
        (self.outside / "boom.wav").write_bytes(b"\0" * 16)
        for bad, words in (("assets/sfx/missing.wav", "not found"), ("assets/sfx/click.txt", "not a ogg/mp3/wav"),
                           (str(self.outside / "boom.wav"), "outside the project"), (3, "must be a file path")):
            with self.assertRaises(gameui.GameUIError, msg=bad) as ctx:
                gameui.update("Menu", "Play", sound=bad)
            self.assertIn(words, str(ctx.exception))
        self.assertEqual(components.get_values(obj, "UiButton")["sound"], "assets/sfx/click.wav")
        # Any element with UiButton takes them; other elements become buttons first (an action).
        link = gameui.text("Menu", "Link", "More", (960, 900))
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "Link", sound="assets/sfx/click.wav")
        self.assertIn("goes with buttons", str(ctx.exception))
        self.assertFalse(components.has_component(link, "UiButton"))
        gameui.update("Menu", "Link", action="open", target="Options", sound="assets/sfx/click.wav")
        gameui.update("Menu", "Link", focus_sound="assets/sfx/tick.ogg")
        values = components.get_values(link, "UiButton")
        self.assertEqual((values["action"], values["sound"], values["focusSound"]),
                         ("open", "assets/sfx/click.wav", "assets/sfx/tick.ogg"))

    def test_bar(self):
        obj = gameui.bar("Menu", "Health", (200, 60), (300, 24), value="health", max=100, color="#E53935",
                         direction="left", radius=12)
        values = components.get_values(obj, "UiBar")
        fill = build.find_part(obj, "fill")
        self.assertEqual((values["value"], values["max"], values["direction"], values["fill"]),
                         ("health", 100.0, "left", fill.name))
        self.assertEqual(fill.name, "HealthFill")
        self.assertEqual(build.to_hex(build.object_color(fill)), "#E53935")
        self.assertEqual(build.to_hex(build.object_color(build.find_part(obj, "bg"))), "#00000080")
        gameui.update("Menu", "Health", size=(400, 30), max=50, value="save.hp")
        self.assertEqual([round(v, 3) for v in build.panel_size(fill)], [400.0, 30.0])
        self.assertEqual(components.get_values(obj, "UiBar")["max"], 50.0)
        self.assertEqual(components.get_values(obj, "UiBar")["fill"], fill.name)
        with self.assertRaises(gameui.GameUIError):
            gameui.bar("Menu", "Mana", (0, 0), (10, 10), direction="sideways")


def xy(obj, places=4) -> list:
    """The sorted distinct x and y vertex coordinates of a mesh (Blender units)."""
    return [sorted({round(v.co[i], places) + 0.0 for v in obj.data.vertices}) for i in (0, 1)]


class ControlsTest(UiFileTestCase):
    """§10.7: icon rows (hearts), sliders and toggles: their parts, components, entity references and looks."""

    def hearts(self):
        write_png(self.root / "assets" / "ui" / "heart.png", 16, 16)
        write_png(self.root / "assets" / "ui" / "heart-empty.png", 16, 16, (0.2, 0.2, 0.2, 1.0))

    def test_icons(self):
        self.hearts()
        scene = bpy.data.scenes["Menu"]
        row = gameui.icons("Menu", "Lives", (200, 60))
        self.assertEqual((row.type, build.kind_of(row)), ("EMPTY", "icons"))
        parts = {c[build.PART_PROP]: c for c in row.children}
        self.assertEqual(set(parts), {"slot1", "slot2", "slot3", "icon1", "icon2", "icon3"})
        self.assertEqual([parts[f"icon{i}"].name for i in (1, 2, 3)], ["Lives1", "Lives2", "Lives3"])
        self.assertEqual(parts["slot2"].name, "LivesSlot2")
        for i in (1, 2, 3):
            self.assertEqual(components.get_values(parts[f"icon{i}"], "UiShow"),
                             {"value": "lives", "compare": "atLeast", "than": float(i)})
            self.assertFalse(components.has_component(parts[f"slot{i}"], "UiShow"), "slots always show")
            self.assertPx(parts[f"icon{i}"], (200 + (i - 2) * 64, 60))  # 52 px icons, 12 px apart, centred
            self.assertPx(parts[f"slot{i}"], (200 + (i - 2) * 64, 60))
            self.assertGreater(build.world_matrix(parts[f"icon{i}"]).translation.z,
                               build.world_matrix(parts[f"slot{i}"]).translation.z, "icons cover their slots")
        self.assertEqual(files.project_relative(bpy.path.abspath(parts["icon1"].data.filepath)), "assets/ui/heart.png")
        self.assertEqual([round(v, 3) for v in build.element_size(scene, parts["icon1"])], [52.0, 52.0])
        info = build.describe_element(scene, row)
        self.assertEqual({k: info[k] for k in ("kind", "size", "count", "value", "image", "empty", "icon_size",
                                                "spacing")},
                         {"kind": "icons", "size": [180.0, 52.0], "count": 3, "value": "lives",
                          "image": "assets/ui/heart.png", "empty": "assets/ui/heart-empty.png", "icon_size": 52.0,
                          "spacing": 12.0})
        self.assertEqual(sorted(info["parts"]), ["Lives1", "Lives2", "Lives3", "LivesSlot1", "LivesSlot2",
                                                 "LivesSlot3"])
        # More, fewer, no slots, another value: an update keeps what it is not given.
        gameui.icons("Menu", "Lives", (200, 60), count=5, size=40, spacing=0)
        self.assertEqual(sorted(c.name for c in row.children if c.name.startswith("Lives") and "Slot" not in c.name),
                         ["Lives1", "Lives2", "Lives3", "Lives4", "Lives5"])
        self.assertPx(bpy.data.objects["Lives5"], (280, 60))
        self.assertEqual(components.get_values(bpy.data.objects["Lives5"], "UiShow")["than"], 5.0)
        gameui.update("Menu", "Lives", count=2, value="save.hearts")  # ui_set's count and value
        self.assertEqual(sorted(c.name for c in row.children), ["Lives1", "Lives2", "LivesSlot1", "LivesSlot2"])
        self.assertNotIn("Lives3", bpy.data.objects)
        self.assertEqual(components.get_values(bpy.data.objects["Lives2"], "UiShow")["value"], "save.hearts")
        gameui.icons("Menu", "Lives", (200, 60), empty="")
        self.assertEqual(sorted(c.name for c in row.children), ["Lives1", "Lives2"])
        self.assertEqual(build.describe_element(scene, row)["empty"], "")

    def test_icons_refuse_what_they_cannot_draw(self):
        for kwargs, words in (({}, "heart.png"), ({"image": "assets/ui/star.png"}, "not found"),
                              ({"count": 0}, "count must be"), ({"size": 0}, "size")):
            with self.assertRaises(gameui.GameUIError, msg=kwargs) as ctx:
                gameui.icons("Menu", "Lives", (200, 60), **kwargs)
            self.assertIn(words, str(ctx.exception))
        self.assertIn("starter template", self._hint({"image": "assets/ui/star.png"}))
        self.assertNotIn("Lives", bpy.data.objects)
        self.hearts()
        gameui.text("Menu", "Lives", "x", (0, 0))
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "Lives", count=2)  # a text has no count

    def _hint(self, kwargs) -> str:
        try:
            gameui.icons("Menu", "Lives", (200, 60), **kwargs)
        except gameui.GameUIError as exc:
            return exc.hint or ""
        return ""

    def test_slider(self):
        scene = bpy.data.scenes["Menu"]
        obj = gameui.slider("Menu", "Music", (960, 500))
        self.assertEqual((obj.type, build.kind_of(obj)), ("EMPTY", "slider"))
        parts = {c[build.PART_PROP]: c for c in obj.children}
        self.assertEqual({p: o.name for p, o in parts.items()},
                         {"bg": "MusicBg", "fill": "MusicFill", "handle": "MusicHandle"})
        values = components.get_values(obj, "UiSlider")
        self.assertEqual({k: values[k] for k in ("value", "min", "max", "step", "fill", "handle", "direction")},
                         {"value": "audio.music", "min": 0.0, "max": 1.0, "step": 0.1, "fill": "MusicFill",
                          "handle": "MusicHandle", "direction": "right"})
        self.assertAlmostEqual(values["step"], 0.1)
        self.assertEqual([build.to_hex(build.object_color(parts[p])) for p in ("bg", "fill", "handle")],
                         ["#FFFFFF33", "#FFD400", "#FFFFFF"])
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["fill"])], [360.0, 28.0])
        self.assertEqual(build.describe_element(scene, parts["fill"])["radius"], 14.0, "a pill")
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["handle"])], [42.0, 42.0])
        self.assertPx(parts["handle"], (1140, 500))  # at the end of the range: Blender shows it full
        zs = [build.world_matrix(parts[p]).translation.z for p in ("bg", "fill", "handle")]
        self.assertEqual(zs, sorted(zs))
        info = build.describe_element(scene, obj)
        self.assertEqual({k: info[k] for k in ("kind", "size", "value", "min", "max", "step", "radius")},
                         {"kind": "slider", "size": [360.0, 28.0], "value": "audio.music", "min": 0.0, "max": 1.0,
                          "step": 0.1, "radius": 14.0})
        self.assertEqual(sorted(info["parts"]), ["MusicBg", "MusicFill", "MusicHandle"])
        # Updates: size and direction move the handle; ui_set's value, min, max, step and the control keys.
        gameui.slider("Menu", "Music", (960, 500), size=(400, 40), direction="left", color="#00FF00")
        self.assertPx(parts["handle"], (760, 500))
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["handle"])], [60.0, 60.0])
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["bg"])], [400.0, 40.0])
        self.assertEqual(build.to_hex(build.object_color(parts["fill"])), "#00FF00")
        (self.root / "assets" / "sfx").mkdir(parents=True)
        (self.root / "assets" / "sfx" / "tick.wav").write_bytes(b"\0" * 16)
        gameui.update("Menu", "Music", value="save.difficulty", min=1, max=3, step=1, order=2, disabled=True,
                      sound="assets/sfx/tick.wav")
        values = components.get_values(obj, "UiSlider")
        self.assertEqual({k: values[k] for k in ("value", "min", "max", "step", "order", "disabled", "sound")},
                         {"value": "save.difficulty", "min": 1.0, "max": 3.0, "step": 1.0, "order": 2,
                          "disabled": True, "sound": "assets/sfx/tick.wav"})
        self.assertFalse(components.has_component(obj, "UiButton"), "a slider is not a button")
        for kwargs, words in (({"min": 3}, "min must be below its max"), ({"action": "close"}, "control of its own"),
                              ({"focus_sound": "assets/sfx/tick.wav"}, "control of its own"),
                              ({"count": 2}, "does not apply")):
            with self.assertRaises(gameui.GameUIError, msg=kwargs) as ctx:
                gameui.update("Menu", "Music", **kwargs)
            self.assertIn(words, str(ctx.exception))
        # Without a handle, and back.
        gameui.slider("Menu", "Music", (960, 500), handle=False)
        self.assertIsNone(build.find_part(obj, "handle"))
        self.assertNotIn("MusicHandle", bpy.data.objects)
        self.assertIsNone(components.get_values(obj, "UiSlider")["handle"])
        gameui.slider("Menu", "Music", (960, 500), handle=True)
        self.assertEqual(components.get_values(obj, "UiSlider")["handle"], "MusicHandle")
        # A slider can be selected first; a bad one changes nothing.
        gameui.canvas("Menu", focus="Music")
        self.assertEqual(components.get_values(scene.camera, "UiCanvas")["focus"], "Music")
        with self.assertRaises(gameui.GameUIError):
            gameui.slider("Menu", "Sfx", (960, 600), min=1, max=0)
        self.assertNotIn("Sfx", bpy.data.objects)

    def test_toggle(self):
        scene = bpy.data.scenes["Menu"]
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.toggle("Menu", "Subtitles", (960, 600))
        self.assertIn("toggle needs value", str(ctx.exception))
        self.assertNotIn("Subtitles", bpy.data.objects)
        obj = gameui.toggle("Menu", "Subtitles", (960, 600), "save.subtitles")
        self.assertEqual((obj.type, build.kind_of(obj)), ("EMPTY", "toggle"))
        parts = {c[build.PART_PROP]: c for c in obj.children}
        self.assertEqual({p: o.name for p, o in parts.items()},
                         {"bg": "SubtitlesBg", "off": "SubtitlesOff", "on": "SubtitlesOn"})
        knob = build.find_part(parts["on"], "knob")
        self.assertEqual(knob.name, "SubtitlesOnKnob")
        values = components.get_values(obj, "UiToggle")
        self.assertEqual({k: values[k] for k in ("value", "on", "off")},
                         {"value": "save.subtitles", "on": "SubtitlesOn", "off": "SubtitlesOff"})
        self.assertEqual([build.to_hex(build.object_color(o)) for o in (parts["bg"], parts["on"], parts["off"], knob)],
                         ["#FFFFFF33", "#FFD400", "#FFFFFF", "#FFFFFF"])
        self.assertEqual([round(v, 3) for v in build.panel_size(parts["on"])], [96.0, 48.0])
        self.assertEqual([round(v, 3) for v in build.panel_size(knob)], [40.0, 40.0])
        self.assertPx(parts["off"], (936, 600))  # the knob 4 px in from the left end
        self.assertPx(knob, (984, 600))
        zs = [build.world_matrix(o).translation.z for o in (parts["bg"], parts["off"], parts["on"], knob)]
        self.assertEqual(zs, sorted(zs), "Blender shows the On look on top")
        info = build.describe_element(scene, obj)
        self.assertEqual({k: info[k] for k in ("kind", "size", "value")},
                         {"kind": "toggle", "size": [96.0, 48.0], "value": "save.subtitles"})
        self.assertEqual(sorted(info["parts"]), ["SubtitlesBg", "SubtitlesOff", "SubtitlesOn"])
        gameui.update("Menu", "Subtitles", size=(120, 60), value="save.captions", order=3)
        self.assertEqual([round(v, 3) for v in build.panel_size(knob)], [50.0, 50.0])
        self.assertPx(parts["off"], (930, 600))
        self.assertPx(knob, (990, 600))
        self.assertEqual(components.get_values(obj, "UiToggle")["value"], "save.captions")
        gameui.toggle("Menu", "Subtitles", (960, 600), on_color="#4CAF50", knob_color="#17171C")
        self.assertEqual([build.to_hex(build.object_color(o)) for o in (parts["on"], parts["off"], knob)],
                         ["#4CAF50", "#17171C", "#17171C"])
        self.assertEqual(components.get_values(obj, "UiToggle")["on"], "SubtitlesOn", "kept")
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "Subtitles", min=0)
        removed = gameui.remove("Menu", "Subtitles")
        self.assertEqual(sorted(removed),
                         ["Subtitles", "SubtitlesBg", "SubtitlesOff", "SubtitlesOn", "SubtitlesOnKnob"])


class NineSliceTest(UiFileTestCase):
    """§10.6: panel(image=, slice=) builds 9 quads with matching UVs for its design size and rebuilds them on
    resize."""

    def setUp(self):
        super().setUp()
        write_png(self.root / "assets" / "ui" / "panel.png", 64, 48)

    def test_quads_and_uvs(self):
        scene = bpy.data.scenes["Menu"]
        card = gameui.panel("Menu", "Card", (960, 540), (400, 200), image="assets/ui/panel.png",
                            slice=[16, 8, 12, 4])
        self.assertEqual((len(card.data.vertices), len(card.data.polygons)), (16, 9))
        self.assertTrue(all(len(p.vertices) == 4 for p in card.data.polygons))
        self.assertEqual(xy(card), [[-2.0, -1.84, 1.92, 2.0], [-1.0, -0.96, 0.88, 1.0]])
        uv = card.data.uv_layers[0]
        corners = {(round(v.co.x, 4), round(v.co.y, 4)): None for v in card.data.vertices}
        for loop in card.data.loops:
            co = card.data.vertices[loop.vertex_index].co
            corners[(round(co.x, 4), round(co.y, 4))] = tuple(round(c, 4) for c in uv.data[loop.index].uv)
        self.assertEqual(corners[(-2.0, 1.0)], (0.0, 1.0))  # top-left
        self.assertEqual(corners[(-1.84, 0.88)], (0.25, 0.75))  # inside the top-left corner: 16/64, 1 - 12/48
        self.assertEqual(corners[(1.92, -0.96)], (0.875, 0.0833))  # 1 - 8/64, 4/48
        self.assertEqual(corners[(2.0, -1.0)], (1.0, 0.0))
        self.assertEqual(list(card[build.SIZE_PROP]), [400.0, 200.0])
        self.assertEqual(components.get_values(card, "UiSlice"),
                         {"left": 16.0, "right": 8.0, "top": 12.0, "bottom": 4.0})
        mat = card.data.materials[0]
        self.assertEqual(build.material_image(mat).name, "panel.png")
        self.assertEqual(mat.surface_render_method, "BLENDED")
        self.assertEqual(build.material_color(mat)[3], 1.0, "the alpha is the image's")
        info = build.describe_element(scene, card)
        self.assertEqual({k: info[k] for k in ("kind", "size", "image", "slice")},
                         {"kind": "panel", "size": [400.0, 200.0], "image": "assets/ui/panel.png",
                          "slice": [16.0, 8.0, 12.0, 4.0]})
        self.assertNotIn("color", info)
        other = gameui.panel("Menu", "Other", (100, 100), (64, 48), image="assets/ui/panel.png")
        self.assertIs(other.data.materials[0], mat, "textured materials are shared by image")

    def test_resizing_rebuilds_the_quads(self):
        card = gameui.panel("Menu", "Card", (960, 540), (400, 200), image="assets/ui/panel.png", slice=16)
        gameui.update("Menu", "Card", size=(600, 300))  # ui_set size: the corners keep their 16 px
        self.assertEqual(xy(card), [[-3.0, -2.84, 2.84, 3.0], [-1.5, -1.34, 1.34, 1.5]])
        self.assertEqual(list(card[build.SIZE_PROP]), [600.0, 300.0])
        card.gs_ui.width = 500  # the Layout box
        self.assertEqual(xy(card), [[-2.5, -2.34, 2.34, 2.5], [-1.5, -1.34, 1.34, 1.5]])
        self.assertEqual(list(card[build.SIZE_PROP]), [500.0, 300.0])
        gameui.update("Menu", "Card", size=(20, 300))  # too narrow for both corners: they shrink together
        self.assertEqual(xy(card)[0], [-0.1, 0.0, 0.1])
        self.assertEqual(len(card.data.polygons), 9)
        # Without slice it is the image stretched over the panel; without image, a flat panel again.
        gameui.panel("Menu", "Card", (960, 540), (300, 100), slice=[])
        self.assertFalse(components.has_component(card, "UiSlice"))
        self.assertEqual((len(card.data.vertices), len(card.data.polygons)), (4, 1))
        self.assertIsNotNone(build.panel_image(card))
        uvs = sorted({tuple(round(c, 4) for c in d.uv) for d in card.data.uv_layers[0].data})
        self.assertEqual(uvs, [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)])
        gameui.panel("Menu", "Card", (960, 540), (300, 100), slice=[4, 4, 4, 4])
        self.assertEqual(len(card.data.polygons), 9)
        gameui.panel("Menu", "Card", (960, 540), (300, 100), image="", color="#20304080")
        self.assertIsNone(build.panel_image(card))
        self.assertFalse(components.has_component(card, "UiSlice"))
        self.assertEqual(build.to_hex(build.object_color(card)), "#20304080")
        self.assertEqual(len(card.data.polygons), 1)

    def test_bad_slices_change_nothing(self):
        for kwargs, words in (({"slice": 16}, "slice needs image"),
                              ({"image": "assets/ui/panel.png", "color": "#FFFFFF"}, "color does not apply"),
                              ({"image": "assets/ui/panel.png", "slice": 8, "radius": 12}, "radius and border"),
                              ({"image": "assets/ui/panel.png", "slice": [40, 40, 0, 0]}, "does not fit the image"),
                              ({"image": "assets/ui/panel.png", "slice": [1, 2, 3]}, "slice must be"),
                              ({"image": "assets/ui/missing.png"}, "not found")):
            with self.assertRaises(gameui.GameUIError, msg=kwargs) as ctx:
                gameui.panel("Menu", "Card", (960, 540), (400, 200), **kwargs)
            self.assertIn(words, str(ctx.exception))
            self.assertNotIn("Card", bpy.data.objects, kwargs)
        card = gameui.panel("Menu", "Card", (960, 540), (400, 200), image="assets/ui/panel.png", slice=16)
        with self.assertRaises(gameui.GameUIError):
            gameui.panel("Menu", "Card", (960, 540), (400, 200), slice=[50, 50, 0, 0])
        self.assertEqual(components.get_values(card, "UiSlice")["left"], 16.0, "unchanged")
        self.assertEqual(xy(card)[0], [-2.0, -1.84, 1.84, 2.0])


class AnimateTest(UiFileTestCase):
    """§10.5 presets: keyframes on the canvas's top-level elements, the markers, the rest frame, replacing and
    removing them, and keeping them in step with later builder changes."""

    def setUp(self):
        super().setUp()
        self.scene = bpy.data.scenes["Menu"]
        self.card = gameui.panel("Menu", "Card", (960, 540), (400, 200))
        gameui.text("Menu", "CardTitle", "Options", (960, 480), parent="Card")
        self.play = gameui.button("Menu", "Play", "Play", (960, 800))

    def keys(self, obj, path, index) -> list:
        fcurve = build._fcurve(obj, path, index)
        return [(round(p.co.x, 3), round(p.co.y, 4)) for p in fcurve.keyframe_points] if fcurve else []

    def markers(self) -> list:
        return sorted((m.frame, m.name) for m in self.scene.timeline_markers)

    def test_pop_and_fade(self):
        self.assertEqual(self.scene.render.fps, 24)
        self.assertIs(gameui.animate("Menu"), self.scene)  # pop, fade, 0.25 s: 6 frames each
        self.assertEqual(self.markers(), [(1, "open"), (7, "close")])
        self.assertEqual((self.scene.frame_start, self.scene.frame_end, self.scene.frame_current), (1, 13, 7))
        for obj in (self.card, self.play):
            self.assertEqual(self.keys(obj, "color", 3), [(1.0, 0.0), (7.0, 1.0), (13.0, 0.0)], obj.name)
            self.assertEqual(self.keys(obj, "scale", 0), [(1.0, 0.9), (7.0, 1.0)], obj.name)
            self.assertEqual(self.keys(obj, "scale", 1), [(1.0, 0.9), (7.0, 1.0)], obj.name)
            self.assertEqual(self.keys(obj, "location", 0), [], "pop does not move it")
        first = build._fcurve(self.card, "scale", 0).keyframe_points[0]
        self.assertEqual((first.interpolation, first.easing), ("BACK", "EASE_OUT"))
        close = build._fcurve(self.card, "color", 3).keyframe_points[1]
        self.assertEqual((close.interpolation, close.easing), ("CUBIC", "EASE_IN"))
        self.assertIsNone(bpy.data.objects["CardTitle"].animation_data, "children follow their parent")
        self.assertIsNone(bpy.data.objects["PlayLabel"].animation_data)
        # The rest pose is where the elements were: their values now, and the animation at the rest frame.
        self.assertPx(self.card, (960, 540))
        self.assertEqual((tuple(self.card.scale), self.card.color[3]), ((1.0, 1.0, 1.0), 1.0))
        self.assertEqual(gameui.animations(self.scene), {"open": 0.25, "close": 0.25})
        self.assertEqual(gameui.animation_settings(self.scene),
                         {"open": "pop", "close": "fade", "duration": 0.25, "loop": None})
        described = build.describe_canvas(self.scene)
        self.assertEqual(described["animations"], {"open": 0.25, "close": 0.25})
        self.assertEqual(described["transition"], {"open": "pop", "close": "fade", "duration": 0.25})

    def test_slides_loop_replace_and_none(self):
        intro = self.scene.timeline_markers.new("intro", frame=0)  # hand-made: kept
        gameui.animate("Menu")
        self.card.keyframe_insert("rotation_euler", index=2, frame=40)  # hand-made: kept
        gameui.animate("Menu", open="slide-up", close="slide-left", duration=0.5, loop="pulse")
        # 12 frames each; a pulse cycle is 1.2 s: 29 frames.
        self.assertEqual(self.markers(), [(0, "intro"), (1, "open"), (13, "loop"), (42, "close")])
        self.assertEqual((self.scene.frame_end, self.scene.frame_current), (54, 13))
        ly = self.card.location.y
        self.assertEqual(self.keys(self.card, "location", 1), [(1.0, round(ly - 1.08, 4)), (13.0, round(ly, 4))])
        lx = self.card.location.x
        self.assertEqual(self.keys(self.card, "location", 0), [(42.0, round(lx, 4)), (54.0, round(lx - 1.92, 4))])
        self.assertEqual(self.keys(self.card, "scale", 0), [(13.0, 1.0), (27.0, 1.04), (42.0, 1.0)], "pop's keys went")
        self.assertEqual(self.keys(self.card, "color", 3), [(1.0, 0.0), (13.0, 1.0), (42.0, 1.0), (54.0, 0.0)])
        self.assertEqual(self.keys(self.card, "rotation_euler", 2), [(40.0, 0.0)])
        self.assertEqual(gameui.animations(self.scene), {"open": 0.5, "close": 0.5, "loop": round(29 / 24, 3)})
        tag = json.loads(self.card[build.ANIM_PROP])
        self.assertIn(["location", 1, 1], tag["keys"])
        # none removes the presets' keys and markers, and only those.
        gameui.animate("Menu", open="none", close="none")
        self.assertEqual(self.markers(), [(0, "intro")])
        self.assertEqual((intro.name, intro.frame), ("intro", 0))
        self.assertNotIn(build.ANIM_PROP, self.scene)
        self.assertNotIn(build.ANIM_PROP, self.card)
        self.assertEqual(self.keys(self.card, "rotation_euler", 2), [(40.0, 0.0)], "the hand-made key stays")
        self.assertEqual([self.keys(self.card, p, i) for p, i in (("location", 0), ("location", 1), ("scale", 0),
                                                                  ("color", 3))], [[], [], [], []])
        self.assertIsNone(self.play.animation_data, "the action animate() made is gone")
        self.assertNotIn("PlayAction", bpy.data.actions)
        self.assertPx(self.card, (960, 540))
        self.assertEqual(self.card.color[3], 1.0)
        self.assertNotIn("animations", build.describe_canvas(self.scene))
        # Only close: the rest pose is the current frame, where close starts.
        gameui.animate("Menu", open="none", close="fade", duration=0.1)
        self.assertEqual(self.markers(), [(0, "intro"), (1, "close")])
        self.assertEqual((self.scene.frame_end, self.scene.frame_current), (3, 1))

    def test_bad_settings_change_nothing(self):
        for kwargs, words in (({"open": "zoom"}, "open must be one of"), ({"close": "slide"}, "Did you mean"),
                              ({"duration": 0}, "duration"), ({"loop": "spin"}, "loop must be one of")):
            with self.assertRaises(gameui.GameUIError, msg=kwargs) as ctx:
                gameui.animate("Menu", **kwargs)
            self.assertIn(words, str(ctx.exception))
        self.assertEqual(self.markers(), [])
        self.assertIsNone(self.card.animation_data)

    def test_builder_changes_keep_the_keys_in_step(self):
        gameui.animate("Menu", open="slide-left", close="fade")
        self.assertEqual(self.keys(self.card, "location", 0), [(1.0, 1.92), (7.0, 0.0)])
        gameui.update("Menu", "Card", at=(1000, 540))  # ui_set at: the slide starts from the new place
        self.assertEqual(self.keys(self.card, "location", 0), [(1.0, 2.32), (7.0, 0.4)])
        self.assertPx(self.card, (1000, 540))
        self.card.gs_ui.x = 1100  # the Layout box
        self.assertEqual(self.keys(self.card, "location", 0), [(1.0, 3.32), (7.0, 1.4)])
        # At another frame (the animation shows the slide's start), the builder goes back to the rest pose first:
        # `at` is always where the element rests.
        self.scene.frame_set(1)
        self.assertAlmostEqual(self.card.location.x, 3.32, places=4)
        gameui.update("Menu", "Card", at=(960, 540))
        self.assertEqual(self.scene.frame_current, 7)
        self.assertEqual(self.keys(self.card, "location", 0), [(1.0, 1.92), (7.0, 0.0)])
        self.assertPx(self.card, (960, 540))
        # A new top-level element is keyed; one moved into a group loses its keys.
        hint = gameui.text("Menu", "Hint", "Press Enter", (960, 950))
        self.assertEqual(self.keys(hint, "color", 3), [(1.0, 0.0), (7.0, 1.0), (13.0, 0.0)])
        gameui.update("Menu", "Hint", parent="Card")
        self.assertIsNone(hint.animation_data)
        self.assertNotIn(build.ANIM_PROP, hint)
        self.assertPx(hint, (960, 950))
        gameui.update("Menu", "Hint", parent=None)
        self.assertEqual(len(self.keys(hint, "color", 3)), 3)
        # An image's fade shows in Blender: its colour alpha is used.
        png = write_png(self.root / "assets" / "ui" / "logo.png", 8, 8)
        logo = gameui.image("Menu", "Logo", str(png), (960, 200))
        self.assertTrue(logo.use_empty_image_alpha)
        self.assertEqual(self.keys(logo, "color", 3)[1], (7.0, 1.0))

    def described(self) -> dict:
        canvas = build.describe_canvas(self.scene)
        return {"canvas": canvas, **{e["name"]: e for e in canvas["elements"]}}

    def test_element_loops(self):
        """§6 loop: one element loops on its own (children too), over the canvas's loop range; animate() keeps it."""
        title = bpy.data.objects["CardTitle"]
        # A canvas without animations gets a loop range: one pulse cycle, 1.2 s = 29 frames, from frame 1.
        gameui.update("Menu", "Play", loop="pulse")
        self.assertEqual(self.markers(), [(1, "loop")])
        self.assertEqual((self.scene.frame_start, self.scene.frame_end, self.scene.frame_current), (1, 30, 1))
        self.assertEqual(self.keys(self.play, "scale", 0), [(1.0, 1.0), (15.0, 1.04), (30.0, 1.0)])
        self.assertIsNone(self.card.animation_data, "only that element is keyed")
        # A child loops in its local transform; the range keeps its length (blink, 1 s, fits one cycle of 29).
        gameui.update("Menu", "CardTitle", loop="blink")
        self.assertEqual(self.keys(title, "color", 3), [(1.0, 1.0), (15.0, 0.35), (30.0, 1.0)])
        self.assertEqual(json.loads(title[build.ANIM_PROP])["top"], False)
        out = self.described()
        self.assertEqual((out["Play"]["loop"], out["CardTitle"]["loop"]), ("pulse", "blink"))
        self.assertNotIn("loop", out["Card"])
        self.assertNotIn("transition", out["canvas"], "element loops are not canvas presets")
        self.assertEqual(out["canvas"]["animations"], {"loop": round(29 / 24, 3)})
        self.assertIsNone(gameui.animation_settings(self.scene))
        # animate() lays the presets out around them and keeps them; running it again duplicates nothing.
        for _ in range(2):
            gameui.animate("Menu")
            self.assertEqual(self.markers(), [(1, "open"), (7, "loop"), (36, "close")])
            self.assertEqual(self.keys(self.card, "color", 3), [(1.0, 0.0), (7.0, 1.0), (36.0, 1.0), (42.0, 0.0)])
            self.assertEqual(self.keys(self.card, "scale", 0), [(1.0, 0.9), (7.0, 1.0)])
            self.assertEqual(self.keys(self.play, "scale", 0), [(1.0, 0.9), (7.0, 1.0), (21.0, 1.04), (36.0, 1.0)])
            self.assertEqual(self.keys(title, "color", 3), [(7.0, 1.0), (21.0, 0.35), (36.0, 1.0)])
        self.assertEqual(self.scene.frame_current, 7)
        out = self.described()
        self.assertEqual(out["canvas"]["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
        self.assertEqual((out["Play"]["loop"], out["CardTitle"]["loop"]), ("pulse", "blink"))
        # A canvas loop (bob, 1.6 s = 38 frames) keys the others; an element's own loop stays in its place, with
        # the whole cycles that fit (blink: 2 of 19 frames).
        gameui.animate("Menu", loop="bob")
        self.assertEqual(self.markers(), [(1, "open"), (7, "loop"), (45, "close")])
        ly = round(self.card.location.y, 4)
        self.assertEqual(self.keys(self.card, "location", 1), [(7.0, ly), (26.0, round(ly + 0.08, 4)), (45.0, ly)])
        self.assertEqual(self.keys(self.play, "location", 1), [], "Play pulses instead")
        self.assertEqual(self.keys(self.play, "scale", 0)[2:], [(26.0, 1.04), (45.0, 1.0)])
        self.assertEqual(self.keys(title, "color", 3),
                         [(7.0, 1.0), (16.0, 0.35), (26.0, 1.0), (35.0, 0.35), (45.0, 1.0)])
        # "none" gives the element the canvas's loop back.
        gameui.update("Menu", "Play", loop="none")
        py = round(self.play.location.y, 4)
        self.assertEqual(self.keys(self.play, "location", 1), [(7.0, py), (26.0, round(py + 0.08, 4)), (45.0, py)])
        self.assertEqual(self.keys(self.play, "scale", 0), [(1.0, 0.9), (7.0, 1.0)])
        self.assertNotIn("loop", self.described()["Play"])
        # ui_list tells what is there: keys changed by hand are not reported as the presets.
        self.assertEqual(self.described()["canvas"]["transition"]["loop"], "bob")
        self.card.keyframe_delete("location", index=1, frame=26)
        self.assertEqual(self.described()["canvas"]["transition"],
                         {"open": "pop", "close": "fade", "duration": 0.25, "edited": ["loop"]})
        title.keyframe_delete("color", index=3, frame=16)
        self.assertNotIn("loop", self.described()["CardTitle"])
        # Moving an element re-keys its own loop from its new place.
        gameui.update("Menu", "CardTitle", at=(960, 500))
        self.assertEqual(self.described()["CardTitle"]["loop"], "blink")
        self.assertEqual(len(self.keys(title, "color", 3)), 5)
        # Export: the child's loop is a track of the loop animation.
        from gamestudio.core import gameui_export

        data, _ex = gameui_export.extract_canvas(self.scene)
        ids = {e["name"]: e["id"] for e in data["elements"]}
        tracks = data["animations"]["loop"]["tracks"]
        self.assertIn("opacity", tracks[ids["CardTitle"]])
        self.assertEqual(min(tracks[ids["CardTitle"]]["opacity"]), 0.35)

    def test_element_loops_come_and_go(self):
        # The last element loop of a canvas without presets takes the record and the marker with it.
        gameui.update("Menu", "Play", loop="pulse")
        gameui.update("Menu", "Play", loop="none")
        self.assertEqual(self.markers(), [])
        self.assertNotIn(build.ANIM_PROP, self.scene)
        self.assertIsNone(self.play.animation_data)
        self.assertPx(self.play, (960, 800))
        # ui_add's loop: a new element; its loop range comes in between open and close.
        gameui.animate("Menu")
        hint = gameui.text("Menu", "Hint", "Press Enter", (960, 950), loop="blink")
        self.assertEqual(self.markers(), [(1, "open"), (7, "loop"), (31, "close")])
        self.assertEqual(self.keys(hint, "color", 3), [(1.0, 0.0), (7.0, 1.0), (19.0, 0.35), (31.0, 1.0), (37.0, 0.0)])
        self.assertEqual(self.keys(self.card, "color", 3), [(1.0, 0.0), (7.0, 1.0), (31.0, 1.0), (37.0, 0.0)])
        # Into a group: it keeps its own loop, not the canvas's presets.
        gameui.update("Menu", "Hint", parent="Card")
        self.assertEqual(self.keys(hint, "color", 3), [(7.0, 1.0), (19.0, 0.35), (31.0, 1.0)])
        self.assertEqual(self.keys(hint, "scale", 0), [])
        self.assertPx(hint, (960, 950))
        # Changing the loop range keys every animated element again: a locked one refuses it first.
        self.card.gs.ai_lock = True
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "Hint", loop="none")
        self.assertIn("locked", str(ctx.exception))
        self.assertEqual(self.described()["Hint"]["loop"], "blink")
        self.card.gs.ai_lock = False
        # Removing the last looping element takes the loop range away.
        gameui.remove("Menu", "Hint")
        self.assertEqual(self.markers(), [(1, "open"), (7, "close")])
        self.assertEqual(self.keys(self.card, "color", 3), [(1.0, 0.0), (7.0, 1.0), (13.0, 0.0)])
        # Bad values and hand-made markers change nothing.
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "Play", loop="spin")
        self.assertIn("loop must be one of", str(ctx.exception))
        hand = gameui.canvas("Hand")
        gameui.text("Hand", "Blink", "Hi", (960, 540))
        hand.timeline_markers.new("Loop", frame=3)
        for call in (lambda: gameui.update("Hand", "Blink", loop="blink"),
                     lambda: gameui.text("Hand", "New", "New", (960, 700), loop="pulse")):
            with self.assertRaises(gameui.GameUIError) as ctx:
                call()
            self.assertIn("made by hand", str(ctx.exception))
        self.assertNotIn("New", bpy.data.objects)
        self.assertIsNone(bpy.data.objects["Blink"].animation_data)


class LayoutTest(UiFileTestCase):
    def test_children_take_absolute_px(self):
        group = gameui.group("Menu", "Window", (960, 540))
        gameui.update("Menu", "Window", at=(900, 500))
        title = gameui.text("Menu", "WindowTitle", "Options", (900, 400), parent="Window")
        self.assertIs(title.parent, group)
        self.assertPx(title, (900, 400))
        self.assertAlmostEqual(title.location.y, 1.0, places=5)  # local to the group
        gameui.update("Menu", "Window", at=(1000, 500))
        self.assertPx(title, (1000, 400))
        gameui.update("Menu", "WindowTitle", parent=None)
        self.assertIsNone(title.parent)
        self.assertPx(title, (1000, 400))
        gameui.update("Menu", "WindowTitle", parent="Window")
        self.assertPx(title, (1000, 400))
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "Window", parent="WindowTitle")  # a cycle
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "WindowTitle", anchor="top")
        self.assertIn("top-level", str(ctx.exception))

    def test_anchors(self):
        scene = bpy.data.scenes["Menu"]
        corner = gameui.text("Menu", "Score", "0", (100, 60))
        self.assertEqual(build.resolve_anchor(scene, corner), ("top-left", "none", True))
        bottom = gameui.panel("Menu", "Bottom", (960, 1000), (1920, 80), anchor=("bottom", "width"))
        self.assertEqual(build.resolve_anchor(scene, bottom), ("bottom", "width", False))
        gameui.update("Menu", "Bottom", stretch="none")
        self.assertEqual(build.resolve_anchor(scene, bottom), ("bottom", "none", False))
        gameui.update("Menu", "Score", anchor="top-right")
        self.assertEqual(build.resolve_anchor(scene, corner), ("top-right", "none", False))
        right = gameui.group("Menu", "Right", (1800, 540))
        self.assertEqual(build.resolve_anchor(scene, right)[0], "right")
        self.assertEqual(build.anchor_point_px(scene, "bottom-right"), (1920, 1080))
        with self.assertRaises(gameui.GameUIError):
            gameui.group("Menu", "Bad", (0, 0), anchor="middle")

    def test_new_elements_go_on_top(self):
        a = gameui.panel("Menu", "A", (960, 540), (100, 100))
        b = gameui.button("Menu", "B", "B", (960, 540))
        c = gameui.text("Menu", "C", "C", (960, 540))
        za, zb, zc = (build.world_matrix(o).translation.z for o in (a, b, c))
        self.assertEqual(za, 0.0)
        self.assertGreater(zb, za)
        self.assertGreater(zc, max(build.world_matrix(p).translation.z for p in b.children))
        gameui.text("Menu", "C", "C", (960, 540), z=-1)
        self.assertEqual(build.world_matrix(c).translation.z, -1.0)
        self.assertEqual([o.name for o in gameui.elements("Menu")][0], "C")
        with self.assertRaises(gameui.GameUIError):
            gameui.text("Menu", "C", "C", (960, 540), z=50)

    def test_update_rejects_what_does_not_apply(self):
        gameui.group("Menu", "G", (0, 0))
        gameui.text("Menu", "T", "x", (0, 0))
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "G", size=(10, 10))
        self.assertIn("does not apply", str(ctx.exception))
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "T", value="x")
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "T", colour="#FFFFFF")
        self.assertIn("Did you mean 'color'", str(ctx.exception))
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "Missing", at=(0, 0))
        obj = gameui.update("Menu", "T", text_size=30, body="y", visible=False, action="open", target="Options")
        self.assertEqual((obj.data.body, round(obj.data.size, 3), obj.hide_render), ("y", 0.3, True))
        self.assertEqual(components.get_values(obj, "UiButton")["target"], "Options", "any element can be a button")
        gameui.update("Menu", "T", size=(200, 50))
        self.assertEqual(obj.data.text_boxes[0].width, 2.0)

    def test_names_are_unique_in_the_file(self):
        gameui.canvas("HUD", kind="hud")
        gameui.text("HUD", "Score", "0", (0, 0))
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.text("Menu", "Score", "0", (0, 0))
        self.assertIn("canvas 'HUD'", str(ctx.exception))
        with self.assertRaises(gameui.GameUIError):
            gameui.text("Menu", bpy.data.scenes["Menu"].camera.name, "0", (0, 0))

    def test_set_font_copies_into_assets(self):
        source = builtin_font_file()
        outside = self.outside / source.name
        shutil.copy2(source, outside)
        title = gameui.text("Menu", "Title", "Hi", (960, 200))
        font = gameui.set_font(title, str(outside))
        copy = self.root / "assets" / "fonts" / source.name
        self.assertTrue(copy.is_file())
        self.assertIs(title.data.font, font)
        self.assertEqual(build.font_path(font), f"assets/fonts/{source.name}")
        gameui.button("Menu", "Play", "Play", (960, 600))
        gameui.set_font("Menu", "")  # the canvas: every text, labels too; "" is the default font
        self.assertEqual({build.font_path(o.data.font) for o in bpy.data.objects if o.type == "FONT"},
                         {"assets/fonts/Inter.woff2"})
        gameui.set_font("Menu", "<builtin>")
        self.assertEqual({o.data.font.filepath for o in bpy.data.objects if o.type == "FONT"}, {"<builtin>"})
        label = gameui.text("Menu", "Sub", "x", (0, 0), font=f"assets/fonts/{source.name}")
        self.assertIs(label.data.font, font)
        with self.assertRaises(gameui.GameUIError):
            gameui.set_font(title, str(self.outside / "missing.ttf"))


class RemoveDescribeTest(UiFileTestCase):
    def test_remove_element_with_its_contents(self):
        gameui.button("Menu", "Play", "Play", (960, 600))
        removed = gameui.remove("Menu", "Play")
        self.assertEqual(sorted(removed), ["Play", "PlayBg", "PlayFocus", "PlayFocusLabel", "PlayLabel"])
        self.assertFalse(any(name in bpy.data.objects for name in removed))
        self.assertNotIn("PlayBg", bpy.data.meshes)
        with self.assertRaises(gameui.GameUIError):
            gameui.remove("Menu", "Play")

    def test_remove_canvas(self):
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.remove("Menu")
        self.assertIn("only canvas", str(ctx.exception))
        gameui.canvas("HUD", kind="hud")
        gameui.text("HUD", "Score", "0", (0, 0))
        removed = gameui.remove("HUD")
        self.assertIn("Score", removed)
        self.assertEqual(gameui.canvas_names(), ["Menu"])
        self.assertNotIn("Score", bpy.data.objects)

    def test_describe(self):
        gameui.canvas("HUD", kind="hud", open="start")
        gameui.text("HUD", "Score", "Score {score}", (100, 60), size=40)
        gameui.bar("HUD", "Health", (1700, 60), (300, 24), value="health")
        out = gameui.describe()
        self.assertEqual((out["file"], out["open"]), ("ui/ui.blend", True))
        self.assertEqual(sorted(c["name"] for c in out["canvases"]), ["HUD", "Menu"])
        hud = gameui.describe("HUD")["canvases"][0]
        self.assertEqual(hud["size"], [1920, 1080])
        self.assertEqual((hud["settings"]["kind"], hud["settings"]["open"]), ("hud", "start"))
        names = [e["name"] for e in hud["elements"]]
        self.assertEqual(names, ["Score", "Health", "HealthBg", "HealthFill"])
        health = hud["elements"][1]
        self.assertEqual({k: health[k] for k in ("kind", "type", "at", "size", "anchor")},
                         {"kind": "bar", "type": "group", "at": [1700.0, 60.0], "size": [300.0, 24.0],
                          "anchor": "top-right"})
        self.assertEqual(health["components"]["UiBar"], {"value": "health", "fill": "HealthFill"})
        fill = hud["elements"][3]
        self.assertEqual((fill["part"], fill["parent"]), ("fill", "Health"))
        self.assertNotIn("anchor", fill)
        json.dumps(out)  # JSON-able


class ExportReadsTheBuilderTest(UiFileTestCase):
    """The export (core/gameui_export.py) reads what the builder writes: positions, colours, alpha, anchors."""

    def test_round_trip(self):
        from gamestudio.core import gameui_export

        if not hasattr(gameui_export, "extract_canvas"):
            self.skipTest("the game UI export is not there yet")
        gameui.panel("Menu", "Card", (300, 200), (400, 240), color="#20304080", radius=10, border=2)
        gameui.text("Menu", "Title", "Hello", (960, 120), size=64, color="#FFD400", align="left")
        gameui.button("Menu", "Play", "Play", (960, 700), action="close", anchor=("bottom", "width"))
        gameui.text("Menu", "Tip", "A tip that wraps", (960, 900), size=48, box=(600, 120))
        gameui.text("Menu", "Note", "Note", (300, 900), size=30, box=(0, -50, 200, 100))
        gameui.update("Menu", "Note", text_size=60)
        data, _ex = gameui_export.extract_canvas(bpy.data.scenes["Menu"])
        by_name = {e["name"]: e for e in data["elements"]}
        # A text box is its frame in local px: [w, h] centred on `at`, [x, y, w, h] from its top-left corner,
        # also after the text size changes.
        self.assertEqual(by_name["Tip"]["text"]["box"], [-300, -60, 600, 120])
        self.assertEqual(by_name["Note"]["text"]["box"], [0, -50, 200, 100])
        card = by_name["Card"]
        self.assertEqual((card["type"], card["transform"]["x"], card["transform"]["y"], card["anchor"]),
                         ("mesh", 300, 200, "top-left"))
        mat = card["mesh"]["material"]
        self.assertEqual(build.to_hex(mat["color"]), "#20304080")
        self.assertEqual(mat["blend"], "alpha")
        self.assertEqual(build.to_hex(by_name["CardBorder"]["mesh"]["material"]["color"]), "#FFFFFF")
        title = by_name["Title"]
        self.assertEqual((title["text"]["body"], round(title["text"]["size"], 3), title["text"]["align"]),
                         ("Hello", 64, "left"))
        self.assertEqual(build.to_hex(title["text"]["color"]), "#FFD400")
        play = by_name["Play"]
        self.assertEqual((play["type"], play["anchor"], play["stretch"]), ("group", "bottom", "width"))
        self.assertEqual(play["components"]["UiButton"]["action"], "close")
        self.assertEqual(by_name["PlayFocus"]["components"]["UiState"]["show"], "focusedOrPressed")
        self.assertEqual(by_name["PlayLabel"]["transform"]["x"], 0)
        orders = [by_name[n]["order"] for n in ("PlayBg", "PlayLabel", "PlayFocus", "PlayFocusLabel")]
        self.assertEqual(orders, sorted(orders))
        self.assertGreater(by_name["Play"]["order"], by_name["Title"]["order"], "newer elements draw on top")

    def test_round_trip_of_controls_icons_and_sliced_panels(self):
        from gamestudio.core import gameui_export

        if not hasattr(gameui_export, "extract_canvas"):
            self.skipTest("the game UI export is not there yet")
        for name in ("panel.png", "heart.png", "heart-empty.png"):
            write_png(self.root / "assets" / "ui" / name, 32, 32)
        gameui.panel("Menu", "Card", (960, 540), (400, 300), image="assets/ui/panel.png", slice=8)
        gameui.slider("Menu", "Music", (960, 500))
        gameui.toggle("Menu", "Subtitles", (960, 600), "save.subtitles")
        gameui.icons("Menu", "Lives", (200, 60))
        data, _ex = gameui_export.extract_canvas(bpy.data.scenes["Menu"])
        by_name = {e["name"]: e for e in data["elements"]}
        card = by_name["Card"]["mesh"]
        self.assertEqual((len(card["indices"]), card["bounds"]), (54, [-200, -150, 200, 150]))  # 9 quads
        self.assertTrue(card["material"]["texture"].endswith("panel.png"))
        self.assertEqual((card["material"]["blend"], card["material"]["color"][3]), ("alpha", 1.0))
        self.assertIsNotNone(card["uvs"])
        self.assertEqual(by_name["Card"]["components"]["UiSlice"], {"left": 8, "right": 8, "top": 8, "bottom": 8})
        slider = by_name["Music"]["components"]["UiSlider"]
        self.assertEqual((slider["value"], slider["fill"], slider["handle"]),
                         ("audio.music", by_name["MusicFill"]["id"], by_name["MusicHandle"]["id"]))
        toggle = by_name["Subtitles"]["components"]["UiToggle"]
        self.assertEqual((toggle["on"], toggle["off"]), (by_name["SubtitlesOn"]["id"], by_name["SubtitlesOff"]["id"]))
        self.assertEqual(by_name["SubtitlesOnKnob"]["parent"], by_name["SubtitlesOn"]["id"])
        self.assertEqual(by_name["Lives2"]["components"]["UiShow"], {"value": "lives", "compare": "atLeast", "than": 2})
        self.assertEqual((by_name["Lives2"]["type"], by_name["LivesSlot2"]["type"]), ("image", "image"))
        self.assertEqual(by_name["Lives3"]["transform"]["x"], 64, "64 px right of the row's centre")


def extent(obj) -> tuple:
    """(largest side, centre x, centre y, top z) of a model's content in its empty's local space."""
    lo, hi = build.model_bounds(obj)
    return max(hi - lo), (lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z


class ModelTest(UiFileTestCase):
    """3D models in canvases (docs/GAME-UI.md §11.1): an empty with UiModel whose children are the model."""

    def test_a_kit_model(self):
        trophy = gameui.model("Menu", "Trophy", (960, 400), kit="crate", kit_params={"width": 2.0})
        scene = bpy.data.scenes["Menu"]
        self.assertEqual((trophy.type, build.kind_of(trophy), build.export_type(trophy)), ("EMPTY", "model", "model"))
        self.assertPx(trophy, (960, 400))
        values = components.get_values(trophy, "UiModel")
        self.assertEqual((values["size"], values["view"], values["light"]), ([320.0, 320.0], "threeQuarter", "studio"))
        self.assertAlmostEqual(values["spin"], 0.6, places=5)
        crate = bpy.data.objects["TrophyModel"]
        self.assertIs(crate.parent, trophy)
        self.assertIn(scene, crate.users_scene)
        # The content is the model, not elements: it is not listed, laid out or counted for the draw order.
        self.assertEqual([o.name for o in build.elements(scene)], ["Trophy"])
        self.assertFalse(build.is_element(crate, scene))
        self.assertIs(build.model_of(crate), trophy)
        # Seen from above in Blender: its longest side 80 % of the view's shorter side, centred, hanging below.
        side, cx, cy, top = extent(trophy)
        self.assertAlmostEqual(side, 0.8 * 320 / 100, places=3)
        self.assertAlmostEqual(cx, 0.0, places=4)
        self.assertAlmostEqual(cy, 0.0, places=4)
        self.assertAlmostEqual(top, 0.0, places=4)
        text = gameui.text("Menu", "Caption", "Gold", (960, 600))
        self.assertAlmostEqual(build.world_matrix(text).translation.z, build.world_matrix(trophy).translation.z + 0.01,
                               places=4, msg="above the model, not above its content")
        out = build.describe_element(scene, trophy)
        self.assertEqual({k: out[k] for k in ("kind", "type", "size", "view", "zoom", "spin", "clip", "light",
                                              "background", "source")},
                         {"kind": "model", "type": "model", "size": [320.0, 320.0], "view": "threeQuarter",
                          "zoom": 1.0, "spin": 0.6, "clip": "", "light": "studio", "background": "#00000000",
                          "source": [{"object": "TrophyModel", "kit": "crate"}]})
        self.assertNotIn("clips", out)
        json.dumps(gameui.describe())

    def test_settings_and_size(self):
        gameui.model("Menu", "Gem", (300, 300), kit="crate", size=(200, 400), view="front", zoom=1.5, spin=0,
                     light="soft", background="#10203080")
        gem = bpy.data.objects["Gem"]
        values = components.get_values(gem, "UiModel")
        self.assertEqual((values["size"], values["view"], values["zoom"], values["spin"], values["light"]),
                         ([200.0, 400.0], "front", 1.5, 0.0, "soft"))
        self.assertEqual(build.to_hex(values["background"]), "#10203080")
        self.assertAlmostEqual(extent(gem)[0], 0.8 * 200 / 100, places=3)
        self.assertEqual(build.element_size(bpy.data.scenes["Menu"], gem), (200.0, 400.0))
        # A new size rescales the model in Blender (by the shorter side); the other settings stay.
        gameui.update("Menu", "Gem", size=(300, 600), view="side", clip="", background=[0, 0, 0, 0])
        values = components.get_values(gem, "UiModel")
        self.assertEqual((values["size"], values["view"], values["zoom"]), ([300.0, 600.0], "side", 1.5))
        self.assertAlmostEqual(extent(gem)[0], 0.8 * 300 / 100, places=3)
        self.assertAlmostEqual(extent(gem)[3], 0.0, places=4, msg="still hanging from the empty")
        # The Layout box resizes it too.
        gem.gs_ui.width = 400
        self.assertEqual(components.get_values(gem, "UiModel")["size"], [400.0, 600.0])
        for bad in ({"view": "back"}, {"zoom": 0}, {"light": "neon"}, {"size": [0, 10]}, {"background": "blue"},
                    {"kit": "crate"}, {"shader": "Shine"}):
            with self.assertRaises(gameui.GameUIError, msg=bad):
                gameui.update("Menu", "Gem", **bad)

    def test_clips(self):
        hero = gameui.model("Menu", "Hero", (960, 540), kit="character_rigged", clip="Walk", view="front")
        self.assertEqual(components.get_values(hero, "UiModel")["clip"], "Walk")
        out = build.describe_element(bpy.data.scenes["Menu"], hero)
        self.assertIn("Walk", out["clips"])
        self.assertEqual(out["source"], [{"object": "HeroModel", "kit": "character_rigged"}])
        self.assertIn("HeroModel_Mesh", [o.name for o in build.model_content(hero)])
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "Hero", clip="Wakl")
        self.assertIn("Did you mean 'Walk'", str(ctx.exception))
        # New content is checked against the clip it keeps, before the old content goes.
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.model("Menu", "Hero", (960, 540), kit="crate")
        self.assertIn("no animation clips", ctx.exception.hint)
        self.assertIn("HeroModel", bpy.data.objects, "kept")
        self.assertNotIn("HeroModel.001", bpy.data.objects)
        gameui.model("Menu", "Hero", (960, 540), kit="crate", clip="")
        self.assertEqual([c.name for c in hero.children], ["HeroModel.001"])
        self.assertNotIn("HeroModel", bpy.data.objects, "the old content is gone")

    def test_replace_and_remove(self):
        gameui.model("Menu", "Prize", (960, 540), kit="crate")
        gameui.model("Menu", "Prize", (960, 540), kit="barrel", kit_params={"name": "Keg"})
        prize = bpy.data.objects["Prize"]
        self.assertEqual([c.name for c in prize.children], ["Keg"])
        self.assertNotIn("PrizeModel", bpy.data.objects)
        self.assertAlmostEqual(extent(prize)[0], 2.56, places=3)
        removed = gameui.remove("Menu", "Prize")
        self.assertEqual(sorted(removed), ["Keg", "Prize"])
        self.assertNotIn("Keg", bpy.data.meshes)

    def test_source_objects_and_collections(self):
        # An object of the file outside the canvases (with a child), moved into the model.
        statue = bpy.data.objects.new("Statue", helpers.new_mesh("StatueMesh"))
        base = bpy.data.objects.new("StatueBase", helpers.new_mesh("BaseMesh"))
        base.parent = statue
        staging = bpy.data.scenes.new("gs_staging")
        for obj in (statue, base):
            staging.collection.objects.link(obj)
        statue.location = (40.0, -3.0, 2.0)
        with self.assertRaises(gameui.GameUIError) as ctx:  # checked before it moves: it stays where it was
            gameui.model("Menu", "Show", (500, 500), source="Statue", clip="Walk")
        self.assertIn("no animation clips", ctx.exception.hint)
        self.assertIsNone(statue.parent)
        self.assertEqual(list(statue.users_scene), [staging])
        self.assertNotIn("Show", bpy.data.objects)
        model = gameui.model("Menu", "Show", (500, 500), source="Statue")
        scene = bpy.data.scenes["Menu"]
        self.assertIs(statue.parent, model)
        for obj in (statue, base):
            self.assertEqual(list(obj.users_scene), [scene], obj.name)
        side, cx, cy, top = extent(model)
        self.assertAlmostEqual(side, 2.56, places=3)
        self.assertAlmostEqual(cx, 0.0, places=4)
        self.assertAlmostEqual(top, 0.0, places=4)
        self.assertEqual(build.describe_element(scene, model)["source"], [{"object": "Statue", "type": "mesh"}])
        # A collection becomes an instance; a collection in the canvas would be drawn twice, so it leaves it.
        coll = bpy.data.collections.new("Rocks")
        scene.collection.children.link(coll)
        rock = bpy.data.objects.new("Rock", helpers.new_mesh("RockMesh"))
        coll.objects.link(rock)
        self.assertTrue(build.is_element(rock, scene))
        pile = gameui.model("Menu", "Pile", (1400, 500), source="Rocks")
        inst = pile.children[0]
        self.assertEqual((inst.instance_type, inst.instance_collection), ("COLLECTION", coll))
        self.assertNotIn(coll, list(scene.collection.children))
        self.assertEqual(build.describe_element(scene, pile)["source"], [{"object": inst.name, "collection": "Rocks"}])
        self.assertAlmostEqual(extent(pile)[0], 2.56, places=3)

    def test_refusals_change_nothing(self):
        gameui.text("Menu", "Title", "Hi", (960, 100))
        cases = [
            ({}, "needs what it shows"),
            ({"kit": "crate", "asset": "Tree"}, "not kit and asset"),
            ({"kit_params": {"width": 2}}, "kit_params go with kit"),
            ({"kit": "cratee"}, "Unknown kit"),
            ({"kit": "sky"}, "makes a sun"),
            ({"kit": "crate", "kit_params": {"width": 99}}, "crate.width"),
            ({"source": "Title"}, "made by the builder"),
            ({"source": "Menu Camera"}, "camera"),
            ({"source": "Nothing"}, "No object or collection"),
            ({"asset": "Tree"}, "asset"),
            ({"kit": "crate", "clip": "Walk"}, "no clip"),
            ({"kit": "crate", "view": "behind"}, "view must be one of"),
        ]
        for kwargs, words in cases:
            with self.assertRaises(gameui.GameUIError, msg=kwargs) as ctx:
                gameui.model("Menu", "Bad", (960, 540), **kwargs)
            self.assertIn(words, str(ctx.exception), kwargs)
            self.assertNotIn("Bad", bpy.data.objects)
            self.assertNotIn("BadModel", bpy.data.objects)
        gameui.model("Menu", "Gem", (960, 540), kit="crate")
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.text("Menu", "Label", "x", (960, 540), parent="Gem")
        self.assertIn("is a 3D model", str(ctx.exception))
        with self.assertRaises(gameui.GameUIError):
            gameui.panel("Menu", "Gem", (960, 540), (10, 10))  # another kind of element
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.text("Menu", "GemModel", "x", (960, 540))
        self.assertIn("part of the 3D model 'Gem'", str(ctx.exception))
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "GemModel", at=(0, 0))  # not an element


class LinkedModelTest(UiFileTestCase):
    """A model of a level's object (source "scenes/main.blend:Player"): linked from the level with its children,
    in a collection of its own, instanced at the model's origin, so it follows the level."""

    def make_level(self, build_level, name="main"):
        """Save the open UI file, write scenes/<name>.blend with build_level(scene) in a new file, open ui/ui.blend
        again. Returns the level's path."""
        ui = pathlib.Path(bpy.data.filepath)
        bpy.ops.wm.save_mainfile()
        bpy.ops.wm.read_homefile(use_empty=True)
        build_level(bpy.context.scene)
        path = self.root / "scenes" / f"{name}.blend"
        path.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(ui))
        return path

    def edit_level(self, edit, name="main"):
        """Open scenes/<name>.blend, edit(scene) it, save it, and open ui/ui.blend again."""
        ui = pathlib.Path(bpy.data.filepath)
        bpy.ops.wm.save_mainfile()
        bpy.ops.wm.open_mainfile(filepath=str(self.root / "scenes" / f"{name}.blend"))
        edit(bpy.context.scene)
        bpy.ops.wm.save_mainfile()
        bpy.ops.wm.open_mainfile(filepath=str(ui))

    @staticmethod
    def starter_level(scene):
        """The starter's player: the character kit, turned to face away from the camera, with its components."""
        from gamestudio import kits

        player = kits.create("character", {"name": "Player", "color": "#3B82F6"}, location=(4.0, -2.0, 0.0),
                             collection=scene.collection)[0]
        player.rotation_euler.z = math.radians(180)
        components.add_component(player, "RigidBody", {"type": "kinematic"})
        components.add_component(player, "Collider", {"shape": "capsule"})
        components.add_component(player, "CharacterController")
        ground = bpy.data.objects.new("Ground", helpers.new_mesh("GroundMesh"))
        scene.collection.objects.link(ground)

    def assertCentred(self, model, side=0.8 * 320 / 100):
        got_side, cx, cy, top = extent(model)
        self.assertAlmostEqual(got_side, side, places=3)
        self.assertAlmostEqual(cx, 0.0, places=4)
        self.assertAlmostEqual(cy, 0.0, places=4)
        self.assertAlmostEqual(top, 0.0, places=4)

    def test_the_level_player(self):
        self.make_level(self.starter_level)
        scene = bpy.data.scenes["Menu"]
        hero = gameui.model("Menu", "Hero", (960, 540), source="scenes/main.blend:Player", view="front", spin=0)
        inst = hero.children[0]
        coll = inst.instance_collection
        self.assertEqual((inst.name, inst.instance_type, coll.get(build.LINK_PROP)),
                         ("HeroModel", "COLLECTION", "scenes/main.blend:Player"))
        self.assertIsNone(coll.library, "the collection is the UI file's")
        player = coll.objects[0]
        self.assertEqual([o.name for o in coll.objects], ["Player"])
        self.assertIsNotNone(player.library, "the player itself is linked")
        self.assertEqual(bpy.data.libraries[0].filepath.replace("\\", "/"), "//../scenes/main.blend")
        self.assertNotIn("Ground", bpy.data.objects, "only the object and its children")
        self.assertFalse(any(coll in list(s.collection.children_recursive) for s in bpy.data.scenes))
        self.assertEqual([o.name for o in build.elements(scene)], ["Hero"], "its objects are not elements")
        # At the model's origin, facing as the kit made it (front: -Y): the level's place and turn are left out.
        self.assertCentred(hero)
        content = build.world_matrix(hero).inverted() @ build.world_matrix(inst)
        self.assertAlmostEqual(abs(content.decompose()[1].to_euler().z), math.pi, places=4,
                               msg="the instance undoes the level's half turn...")
        root_in_model = content @ player.matrix_basis  # ...so the player stands unturned in the model's space
        self.assertAlmostEqual(root_in_model.decompose()[1].to_euler().z, 0.0, places=4)
        out = build.describe_element(scene, hero)
        self.assertEqual(out["source"], [{"object": "HeroModel", "linked": "scenes/main.blend:Player",
                                          "kit": "character"}])
        self.assertNotIn("clips", out)
        # Saved and opened again, the link holds; its components are the level's, and stay out of the UI.
        bpy.ops.wm.save_mainfile()
        bpy.ops.wm.open_mainfile(filepath=bpy.data.filepath)
        hero = bpy.data.objects["Hero"]
        coll = hero.children[0].instance_collection
        self.assertEqual([(o.name, o.library is not None) for o in coll.objects], [("Player", True)])
        self.assertEqual([o.name for o in build.elements(bpy.data.scenes["Menu"])], ["Hero"])

    def test_it_follows_the_level(self):
        from gamestudio.core import gameui_export

        self.make_level(self.starter_level)
        hero = gameui.model("Menu", "Hero", (960, 540), source="scenes/main.blend:Player")
        inst = hero.children[0]
        before = len(inst.instance_collection.objects["Player"].data.vertices)
        self.assertIsNone(gameui_export._link_correction(inst))

        def change(scene):  # the player grows a hat, moves and turns
            player = scene.objects["Player"]
            player.location = (10.0, 6.0, 1.0)
            player.rotation_euler.z = math.radians(90)
            mesh = player.data
            mesh.vertices.add(1)
            mesh.vertices[-1].co = (0.0, 0.0, 2.2)

        self.edit_level(change)
        hero = bpy.data.objects["Hero"]
        inst = hero.children[0]
        player = inst.instance_collection.objects["Player"]
        self.assertEqual(len(player.data.vertices), before + 1, "the linked data is the level's, as saved")
        # The export keeps the model at its origin: its copy of the instance cancels the player's new place.
        correction = gameui_export._link_correction(inst)
        self.assertIsNotNone(correction)
        moved = build.world_matrix(hero).inverted() @ build.world_matrix(inst) @ correction
        root = (moved @ build.world_matrix(player)).translation
        before_root = (build.world_matrix(hero).inverted() @ build.world_matrix(inst)
                       @ gameui_export._basis_world(player))
        self.assertGreater((before_root.translation - root).length, 1.0, "uncorrected, it would be off-centre")
        # Any change through the builder puts it back at the model's origin in Blender too.
        gameui.update("Menu", "Hero", spin=0.3)
        self.assertIsNone(gameui_export._link_correction(inst))
        lo, hi = build.model_bounds(hero)
        self.assertAlmostEqual((lo.x + hi.x) / 2, 0.0, delta=0.05)
        self.assertAlmostEqual((lo.y + hi.y) / 2, 0.0, delta=0.05)

    def test_a_rigged_character_with_its_clips_and_the_export(self):
        from gamestudio import kits
        from gamestudio.core import export, gameui_export

        def level(scene):
            kits.create("character_rigged", {"name": "Knight"}, location=(-3.0, 2.0, 0.0),
                        collection=scene.collection)
            scene.objects["Knight"].rotation_euler.z = math.radians(45)

        self.make_level(level)
        hero = gameui.model("Menu", "Hero", (960, 540), source="scenes/main.blend:Knight", clip="Wave")
        coll = hero.children[0].instance_collection
        self.assertEqual(sorted(o.name for o in coll.objects), ["Knight", "Knight_Mesh"])
        self.assertEqual(components.get_values(hero, "UiModel")["clip"], "Wave")
        self.assertIn("Wave", build.describe_element(bpy.data.scenes["Menu"], hero)["clips"])
        self.assertCentred(hero)
        local = sorted(o.name for o in bpy.data.objects if o.library is None)
        result = gameui_export.export_ui_file(self.root)
        self.assertFalse([w for w in result["warnings"] if "could not be exported" in w], result["warnings"])
        data = json.loads((core.export_dir(self.root) / "Menu.ui.json").read_text(encoding="utf-8"))
        element = next(e for e in data["elements"] if e["name"] == "Hero")
        self.assertEqual(element["model"]["glb"], "Menu.ui.glb")
        self.assertIn("Wave", element["model"]["clips"])
        gltf = export.read_glb(core.export_dir(self.root) / "Menu.ui.glb")[0]
        self.assertEqual(len(gltf.get("skins", [])), 1)
        self.assertTrue(any(m for m in gltf.get("meshes", [])))
        self.assertEqual(sorted(o.name for o in bpy.data.objects if o.library is None), local,
                         "the export left nothing behind")
        self.assertIn("HeroModel", local)

    def test_replace_remove_and_refusals(self):
        self.make_level(self.starter_level)
        gameui.model("Menu", "Hero", (960, 540), source="scenes/main.blend:Player")
        gameui.model("Menu", "Twin", (1400, 540), source="scenes/main.blend:Player")
        colls = [c for c in bpy.data.collections if c.get(build.LINK_PROP)]
        self.assertEqual(len(colls), 1, "one collection per source, shared")
        cases = [
            ("scenes/nope.blend:Player", "not found"),
            ("scenes/main.blend:Playr", "Did you mean 'Player'"),
            ("scenes/main.blend:", "names no object"),
            ("ui/ui.blend:Hero", "is the open file"),
            (str(pathlib.Path(self.outside) / "level.blend") + ":Player", "not found"),
        ]
        for source, words in cases:
            with self.assertRaises(gameui.GameUIError, msg=source) as ctx:
                gameui.model("Menu", "Bad", (960, 540), source=source)
            self.assertIn(words, str(ctx.exception) + str(ctx.exception.hint or ""), source)
            self.assertNotIn("Bad", bpy.data.objects)
        outside = pathlib.Path(self.outside) / "level.blend"
        shutil.copy(self.root / "scenes" / "main.blend", outside)
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.model("Menu", "Bad", (960, 540), source=f"{outside}:Player")
        self.assertIn("outside the project", str(ctx.exception))
        with self.assertRaises(gameui.GameUIError) as ctx:  # a clip it does not have: nothing is left linked
            gameui.model("Menu", "Hero", (960, 540), source="scenes/main.blend:Player", clip="Wave")
        self.assertIn("no animation clips", ctx.exception.hint)
        self.assertEqual(len([c for c in bpy.data.collections if c.get(build.LINK_PROP)]), 1)
        # Another content for one: the collection stays for the other; removing that one too drops it.
        gameui.model("Menu", "Hero", (960, 540), kit="crate")
        self.assertEqual(len([c for c in bpy.data.collections if c.get(build.LINK_PROP)]), 1)
        gameui.remove("Menu", "Twin")
        self.assertFalse([c for c in bpy.data.collections if c.get(build.LINK_PROP)])
        self.assertFalse([o for o in bpy.data.objects if o.library is not None and o.users])


class ButtonColoursAndTextRectTest(UiFileTestCase):
    def test_set_a_buttons_colours(self):
        scene = bpy.data.scenes["Menu"]
        play = gameui.button("Menu", "Play", "Play", (960, 600))
        parts = lambda: {c[build.PART_PROP]: c for c in play.children}  # noqa: E731
        hexes = lambda: {k: build.to_hex(build.object_color(v)) for k, v in parts().items()}  # noqa: E731
        self.assertEqual(hexes(), {"bg": "#FFFFFF33", "focus": "#FFD400", "label": "#FFFFFF", "focus_label": "#17171C"})
        gameui.update("Menu", "Play", color="#202020", text_color="#E0E0E0", focus_color="#00AAFF",
                      focus_text_color="#FFFFFF")
        self.assertEqual(hexes(), {"bg": "#202020", "focus": "#00AAFF", "label": "#E0E0E0", "focus_label": "#FFFFFF"})
        # A new text colour alone: the focused label keeps its colour.
        gameui.update("Menu", "Play", text_color="#101010")
        self.assertEqual(hexes()["label"], "#101010")
        self.assertEqual(hexes()["focus_label"], "#FFFFFF")
        # The same colour for both: one label; then a text colour alone brings the focused one back.
        gameui.update("Menu", "Play", focus_text_color="#101010")
        self.assertNotIn("focus_label", parts())
        gameui.update("Menu", "Play", text_color="#FF0000")
        self.assertEqual((hexes()["label"], hexes()["focus_label"]), ("#FF0000", "#101010"))
        # The focus art is an element of its own too.
        gameui.update("Menu", "PlayFocus", color="#00FF00")
        self.assertEqual(hexes()["focus"], "#00FF00")
        for bad, words in (({"text_color": "red"}, "text_color must be"), ({"focus_color": [2, 0, 0]}, "focus_color")):
            with self.assertRaises(gameui.GameUIError, msg=bad) as ctx:
                gameui.update("Menu", "Play", **bad)
            self.assertIn(words, str(ctx.exception))
        title = gameui.text("Menu", "Title", "Hi", (960, 200))
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "Title", text_color="#FFFFFF")
        self.assertIn("does not apply", str(ctx.exception))
        self.assertTrue(title)
        self.assertEqual(sorted(build.describe_element(scene, play)["parts"]),
                         ["PlayBg", "PlayFocus", "PlayFocusLabel", "PlayLabel"])

    def test_text_rect(self):
        scene = bpy.data.scenes["Menu"]
        title = gameui.text("Menu", "Title", "Gem Island", (960, 200), size=100)
        x, y, w, h = build.describe_element(scene, title)["rect"]
        self.assertAlmostEqual(x + w / 2, 960, delta=8, msg="centred on at")
        self.assertAlmostEqual(y + h / 2, 200, delta=20)
        self.assertGreater(w, 300)
        self.assertLess(w, 700)
        self.assertGreater(h, 50)
        self.assertLess(h, 130)
        gameui.update("Menu", "Title", body="Gem Island Deluxe")
        wider = build.describe_element(scene, title)["rect"][2]
        self.assertGreater(wider, w + 100, "measured from the current body")
        left = gameui.text("Menu", "Score", "Score {score}", (40, 40), size=40, align="left", valign="top")
        x, y, w, h = build.text_rect(scene, left)
        self.assertAlmostEqual(x, 40, delta=6)
        self.assertAlmostEqual(y, 40, delta=12)
        listed = {e["name"]: e for e in gameui.describe("Menu")["canvases"][0]["elements"]}
        self.assertEqual(listed["Score"]["rect"], [x, y, w, h])
        empty = gameui.text("Menu", "Empty", "", (960, 900))
        self.assertNotIn("rect", build.describe_element(scene, empty))


SHINE = {"name": "Shine", "description": "A highlight sweeping across", "builtin": True, "category": "Style", "ui": True,
         "fields": [{"name": "color", "type": "color", "default": [1, 1, 1, 0.5]},
                    {"name": "width", "type": "float", "default": 0.2, "min": 0, "max": 1},
                    {"name": "speed", "type": "float", "default": 1, "min": 0}]}
GRADIENT = {"name": "Gradient", "description": "Two colours", "builtin": True, "category": "Style", "ui": True,
            "fields": [{"name": "angle", "type": "float", "default": 0}]}


class PanelShaderTest(UiFileTestCase):
    """Game shaders on UI panels (§11.4): a material of the panel's own carries Material.gs.shader."""

    registry_data = dict(ui_registry(), shaders=[SHINE, GRADIENT])

    def test_shader_on_its_own_material(self):
        from gamestudio.core import shaders

        plain = gameui.panel("Menu", "Plain", (300, 300), (200, 100), color="#203040")
        card = gameui.panel("Menu", "Card", (960, 540), (400, 200), color="#203040", shader="Shine",
                            shader_values={"width": 0.4})
        mat = card.data.materials[0]
        old_name = mat.name
        self.assertIsNot(mat, plain.data.materials[0], "not the shared colour material")
        self.assertTrue(mat.get(build.SHADED_KEY))
        self.assertNotIn(build.COLOR_KEY, mat)
        self.assertEqual((shaders.get_shader(mat), plain.data.materials[0].gs.shader), ("Shine", ""))
        self.assertAlmostEqual(shaders.get_values(mat)["width"], 0.4, places=5)
        self.assertEqual(build.to_hex(build.object_color(card)), "#203040")
        out = build.describe_element(bpy.data.scenes["Menu"], card)
        self.assertEqual((out["shader"], out["color"]), ("Shine", "#203040"))
        self.assertAlmostEqual(out["shader_values"]["width"], 0.4, places=5)
        # A new colour keeps the shader and its values on a new material of its own; the old one goes.
        gameui.update("Menu", "Card", color="#FF0000")
        new = card.data.materials[0]
        self.assertNotEqual(new.name, old_name)
        self.assertNotIn(old_name, bpy.data.materials, "the old one is gone")
        self.assertEqual((shaders.get_shader(new), build.to_hex(build.object_color(card))), ("Shine", "#FF0000"))
        self.assertAlmostEqual(shaders.get_values(new)["width"], 0.4, places=5)
        self.assertTrue(all(m.gs.shader == "" for m in bpy.data.materials if m.get(build.COLOR_KEY)),
                        "the shared colour materials stay plain")
        # Values alone, another shader, then none: back to the shared colour material.
        gameui.update("Menu", "Card", shader_values={"speed": 2})
        self.assertEqual(shaders.changed_values(card.data.materials[0]), {"width": 0.4, "speed": 2.0})
        gameui.update("Menu", "Card", shader="Gradient", shader_values={"angle": 45})
        self.assertEqual(shaders.get_shader(card.data.materials[0]), "Gradient")
        own = card.data.materials[0].name
        gameui.update("Menu", "Card", shader="")
        self.assertIsNotNone(card.data.materials[0].get(build.COLOR_KEY))
        self.assertIs(card.data.materials[0], build.flat_material(build.parse_color("#FF0000")))
        self.assertEqual(card.data.materials[0].gs.shader, "")
        self.assertNotIn(own, bpy.data.materials, "its own material is gone")
        self.assertNotIn("shader", build.describe_element(bpy.data.scenes["Menu"], card))

    def test_textured_panels_and_refusals(self):
        from gamestudio.core import shaders

        write_png(self.root / "assets" / "ui" / "panel.png", 32, 32)
        card = gameui.panel("Menu", "Card", (960, 540), (400, 300), image="assets/ui/panel.png", slice=8,
                            shader="Shine")
        mat = card.data.materials[0]
        self.assertEqual((shaders.get_shader(mat), build.material_image(mat).name), ("Shine", "panel.png"))
        gameui.update("Menu", "Card", size=(500, 300))
        self.assertIs(card.data.materials[0], mat, "a resize keeps its material")
        for kwargs, words in (({"shader": "Shin"}, "Did you mean 'Shine'"), ({"shader_values": {"glow": 1}}, "no field"),
                              ({"shader_values": {"width": 3}}, "shader_values.width"), ({"shader": 3}, "string")):
            with self.assertRaises(gameui.GameUIError, msg=kwargs) as ctx:
                gameui.update("Menu", "Card", **kwargs)
            self.assertIn(words, str(ctx.exception), kwargs)
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.panel("Menu", "Other", (100, 100), (50, 50), shader_values={"width": 0.5})
        self.assertIn("need a game shader", str(ctx.exception))
        self.assertNotIn("Other", bpy.data.objects)

    def test_colour_values_as_hex(self):
        from gamestudio.core import shaders

        # "#RRGGBB" / "#RRGGBBAA" are sRGB, made linear like every UI colour; lists stay linear.
        card = gameui.panel("Menu", "Card", (960, 540), (400, 200), color="#203040", shader="Shine",
                            shader_values={"color": "#FFD40080", "width": 0.3})
        stored = shaders.get_values(card.data.materials[0])["color"]
        for got, want in zip(stored, build.parse_color("#FFD40080")):
            self.assertAlmostEqual(got, want, places=5)
        self.assertAlmostEqual(stored[1], build.srgb_to_linear(0xD4 / 255), places=5)
        self.assertAlmostEqual(stored[3], 0x80 / 255, places=5)
        part = gameui.button("Menu", "Play", "Play", (960, 800))
        gameui.update("Menu", "PlayFocus", shader="Shine", shader_values={"color": "#FFFFFF"})  # a part, by its name
        focus = build.find_part(part, "focus")
        self.assertEqual(shaders.get_values(focus.data.materials[0])["color"], [1.0, 1.0, 1.0, 1.0])
        gameui.update("Menu", "Card", shader_values={"color": [0.5, 0.25, 0.0, 1.0]})
        self.assertEqual(shaders.get_values(card.data.materials[0])["color"], [0.5, 0.25, 0.0, 1.0])
        self.assertEqual(build.linear_shader_values("Shine", {"width": "#FF0000"}), {"width": "#FF0000"},
                         "only colour fields are colours")
        for bad in ("#FFF", "#GGGGGG", "white"):
            with self.assertRaises(gameui.GameUIError, msg=bad) as ctx:
                gameui.update("Menu", "Card", shader_values={"color": bad})
            self.assertIn("shader_values.color must be '#RRGGBB' or '#RRGGBBAA'", str(ctx.exception))
        self.assertEqual(shaders.get_values(card.data.materials[0])["color"], [0.5, 0.25, 0.0, 1.0], "unchanged")


class ProjectNameAndLanguageButtonTest(UiFileTestCase):
    def test_project_name(self):
        title = gameui.text("Menu", "GameTitle", "Gem Island", (960, 300), project_name=True)
        self.assertIs(title.get(build.PROJECT_NAME_PROP), True)
        scene = bpy.data.scenes["Menu"]
        self.assertTrue(build.describe_element(scene, title)["project_name"])
        gameui.text("Menu", "GameTitle", "Gem Island", (960, 300))
        self.assertTrue(title.get(build.PROJECT_NAME_PROP), "None keeps it")
        gameui.update("Menu", "GameTitle", project_name=False)
        self.assertNotIn(build.PROJECT_NAME_PROP, title)
        self.assertNotIn("project_name", build.describe_element(scene, title))
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "GameTitle", project_name="yes")

    def test_locale_buttons(self):
        language = gameui.button("Menu", "Language", "{ui.localeName}", (960, 700), action="locale")
        self.assertEqual(components.get_values(language, "UiButton")["action"], "locale")
        self.assertEqual(components.get_values(language, "UiButton")["target"], "")
        gameui.button("Menu", "French", "Français", (960, 800), action="locale", target="FR")
        self.assertEqual(components.get_values(bpy.data.objects["French"], "UiButton")["target"], "fr")
        gameui.update("Menu", "French", target="pt_br")
        self.assertEqual(components.get_values(bpy.data.objects["French"], "UiButton")["target"], "pt-BR")
        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.update("Menu", "French", target="Options")
        self.assertIn("language code", ctx.exception.hint)
        gameui.button("Menu", "Opts", "Options", (960, 900), action="open", target="Options")
        with self.assertRaises(gameui.GameUIError):
            gameui.update("Menu", "Opts", action="locale")  # its target is a canvas, not a language
        gameui.update("Menu", "Opts", action="locale", target="")
        self.assertEqual(components.get_values(bpy.data.objects["Opts"], "UiButton")["target"], "")


class LocalesTest(UiFileTestCase):
    """Translation tables (§11.3): gs.gameui.strings / translate, the ui_strings and ui_translate tools."""

    def setUp(self):
        super().setUp()
        from gamestudio.core import gameui_export

        self.exports = []
        patcher = mock.patch.object(gameui_export, "export_locales", create=True,
                                    side_effect=lambda root: self.exports.append(root) or {"locales": ["fr"]})
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_ui(self):
        gameui.text("Menu", "GameTitle", "Gem Island", (960, 200), project_name=True)
        gameui.button("Menu", "Play", "Play", (960, 600), focus_text_color="#000000")
        gameui.text("Menu", "Score", "Score {score}", (100, 60))
        gameui.text("Menu", "Count", "{coins}", (100, 120))  # nothing to translate
        gameui.text("Menu", "Again", "Play", (960, 900))

    def test_normalize(self):
        for code, want in (("fr", "fr"), ("PT-br", "pt-BR"), ("zh_hans", "zh-Hans"), ("zh-Hant-TW", "zh-Hant-TW"),
                           ("es-419", "es-419")):
            self.assertEqual(locales.normalize(code), want)
        for bad in ("french", "f", "en-", "", 3, "en-USA-x"):
            with self.assertRaises(gameui.GameUIError, msg=bad) as ctx:
                locales.normalize(bad)
            self.assertEqual(ctx.exception.code, "invalid_locale")

    def test_strings_and_translate(self):
        self.build_ui()
        out = gameui.strings()
        self.assertEqual((out["source"], out["source_locale"], out["locales"]), ("ui/ui.blend", "en", []))
        texts = {t["text"]: t for t in out["texts"]}
        self.assertEqual(list(texts), ["Gem Island", "Play", "Score {score}"])
        self.assertEqual(texts["Play"]["elements"], ["Menu/PlayLabel", "Menu/PlayFocusLabel", "Menu/Again"])
        self.assertTrue(texts["Gem Island"]["project_name"])
        self.assertNotIn("translation", texts["Play"])

        with self.assertRaises(gameui.GameUIError) as ctx:
            gameui.translate("fr", {"Play": "Jouer"})
        self.assertIn("name the language", str(ctx.exception))
        self.assertFalse((self.root / "ui" / "locales").exists())
        result = gameui.translate("fr", {"Play": "Jouer", "Crashed!": "Accident !"}, name="Français")
        self.assertEqual({k: result[k] for k in ("locale", "name", "file", "created", "added", "changed", "removed",
                                                 "strings", "missing", "unused")},
                         {"locale": "fr", "name": "Français", "file": "ui/locales/fr.json", "created": True,
                          "added": 2, "changed": 0, "removed": 0, "strings": 2, "missing": 1, "unused": 1},
                         "missing: Score {score}; the game's title is not counted")
        self.assertEqual(self.exports, [self.root], "the game gets the table")
        data = json.loads((self.root / "ui" / "locales" / "fr.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"name": "Français", "strings": {"Play": "Jouer", "Crashed!": "Accident !"}})
        result = gameui.translate("FR", {"Play": "Jouer !", "Score {score}": "{score} points"}, remove=["Crashed!"])
        self.assertEqual((result["created"], result["added"], result["changed"], result["removed"], result["strings"]),
                         (False, 1, 1, 1, 2))
        out = gameui.strings("fr")
        self.assertEqual((out["locale"], out["name"], out["exists"], out["translated"], out["missing"]),
                         ("fr", "Français", True, 2, 0), "the title is listed, untranslated, but not missing")
        self.assertEqual({t["text"]: t["translation"] for t in out["texts"]},
                         {"Gem Island": None, "Play": "Jouer !", "Score {score}": "{score} points"})
        self.assertTrue(next(t for t in out["texts"] if t["text"] == "Gem Island")["project_name"])
        self.assertEqual(out["locales"], [{"code": "fr", "name": "Français", "file": "ui/locales/fr.json",
                                           "strings": 2}])
        self.assertEqual(gameui.strings("de")["exists"], False)
        # A title translated all the same counts as translated.
        gameui.translate("fr", {"Gem Island": "L'Île aux gemmes"})
        out = gameui.strings("fr")
        self.assertEqual((out["translated"], out["missing"]), (3, 0))

    def test_refusals_write_nothing(self):
        cases = [
            (("en", {"Play": "Play"}), {"name": "English"}, "language the UI is written in"),
            (("fr", {"Score {score}": "Points"}), {"name": "Français"}, "keeps the text's bindings ({score})"),
            (("fr", {"Play": ""}), {"name": "Français"}, "non-empty"),
            (("fr", {"Play": 3}), {"name": "Français"}, "non-empty"),
            (("fr", ["Play"]), {"name": "Français"}, "must be an object"),
            (("fr", {"Play": "Jouer"}), {"name": ""}, "language's own name"),
            (("fr", {"Play": "Jouer"}), {"name": "Français", "remove": ["Play"]}, "both strings and remove"),
            (("french", {"Play": "Jouer"}), {"name": "Français"}, "not a language code"),
        ]
        for args, kwargs, words in cases:
            with self.assertRaises(gameui.GameUIError, msg=args) as ctx:
                gameui.translate(*args, **kwargs)
            self.assertIn(words, str(ctx.exception), args)
        self.assertFalse((self.root / "ui" / "locales").exists())
        self.assertEqual(self.exports, [])
        # {{ and }} are literal braces, not bindings; bindings may move.
        gameui.translate("de", {"{{x}} {a} and {b:000}": "{b:000} und {a} {{y}}"}, name="Deutsch")

    def test_reads_the_export_from_another_file(self):
        self.build_ui()
        folder = core.export_dir(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.json").write_text(json.dumps({"canvases": {"Title": {"file": "Title.ui.json"}}}),
                                           encoding="utf-8")
        (folder / "Title.ui.json").write_text(json.dumps({"elements": [
            {"name": "GameTitle", "type": "text", "text": {"body": "test-game"}},  # gamestudio.json name
            {"name": "Go", "type": "text", "text": {"body": "Go!"}},
            {"name": "Card", "type": "mesh"}]}), encoding="utf-8")
        level = self.root / "scenes" / "main.blend"
        level.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(level))
        out = gameui.strings("fr")
        self.assertEqual((out["source"], [t["text"] for t in out["texts"]]), ("export", ["test-game", "Go!"]))
        self.assertEqual((out["texts"][0].get("project_name"), out["missing"]), (True, 1),
                         "the export has no mark: the game's name is the title")
        result = gameui.translate("fr", {"Go!": "Allez !"}, name="Français")
        self.assertEqual((result["missing"], result["unused"]), (0, 0))



class LocalesExportTest(UiFileTestCase):
    def test_the_export_gets_the_table(self):
        from gamestudio.core import gameui_export

        gameui.text("Menu", "Play", "Play", (960, 600))
        out = gameui.translate("fr", {"Play": "Jouer"}, name="Français")
        self.assertEqual((out["exported"]["locales"], out["sent"]), (["fr"], False), out)
        data = json.loads((core.export_dir(self.root) / "locales" / "fr.json").read_text(encoding="utf-8"))
        self.assertEqual(data["strings"], {"Play": "Jouer"})
        self.assertEqual(gameui_export.read_index(self.root)["locales"]["fr"]["name"], "Français")
        gameui.translate("fr", {}, remove=["Play"])
        data = json.loads((core.export_dir(self.root) / "locales" / "fr.json").read_text(encoding="utf-8"))
        self.assertEqual(data["strings"], {})


if __name__ == "__main__":
    unittest.main()
