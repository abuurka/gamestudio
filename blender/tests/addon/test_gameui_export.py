# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI export (docs/GAME-UI.md §3, §4): canvas extraction, files, index, Play from a UI file."""

import json
import math
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from unittest import mock

import bpy
from bpy_extras import anim_utils

from gamestudio.core import components, export, gameui, gameui_export, ids, project, registry

import helpers


def ui_registry() -> dict:
    data = helpers.fixture_registry()
    with open(helpers.FIXTURES / "gameui_components.json", "r", encoding="utf-8") as f:
        data["components"] = data["components"] + json.load(f)["components"]
    return data


def bundled_font(name: str) -> pathlib.Path:
    return pathlib.Path(bpy.utils.system_resource("DATAFILES")) / "fonts" / name


def png_rows(data: bytes) -> tuple:
    """(width, height, [rows of RGBA bytes]) of a PNG written by gameui_export (filter 0, RGBA8)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    return width, height, [raw[r * stride + 1:(r + 1) * stride] for r in range(height)]


def emission_material(name, color, strength=1.0):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength
    links.new(emission.outputs[0], out.inputs["Surface"])
    return mat


def principled_material(name, color, alpha=1.0):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Alpha"].default_value = alpha
    links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def curve(obj, path, index):
    """The F-curve of obj's action (its slot) for path[index], or None."""
    anim = obj.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(anim.action, anim.action_slot)
    return next((fc for fc in bag.fcurves if (fc.data_path, fc.array_index) == (path, index)), None)


def keys(obj, path, index, values: dict, interpolation="LINEAR"):
    """Keyframe obj.<path>[index] at {frame: value}; linear by default, so sampled values are easy to state."""
    for frame, value in values.items():
        getattr(obj, path)[index] = value
        obj.keyframe_insert(path, index=index, frame=frame)
    for point in curve(obj, path, index).keyframe_points:
        point.interpolation = interpolation


def textured_material(name, size=(64, 32)):
    mat = emission_material(name, (1.0, 1.0, 1.0, 1.0))
    nodes = mat.node_tree.nodes
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.new(name, *size, alpha=True)
    mat.node_tree.links.new(tex.outputs["Color"], next(n for n in nodes if n.type == "EMISSION").inputs["Color"])
    return mat


def mix_transparent_material(name, color, factor, transparent_first):
    mat = emission_material(name, color)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    emission = next(n for n in nodes if n.type == "EMISSION")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = factor
    first, second = (transparent, emission) if transparent_first else (emission, transparent)
    links.new(first.outputs[0], mix.inputs[1])
    links.new(second.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


class UiFileTestCase(helpers.ProjectTestCase):
    """A project whose open file is ui/ui.blend, with the canvas 'Title' set up the standard way."""

    registry_data = ui_registry()

    def setUp(self):
        super().setUp()
        (self.root / "ui").mkdir()
        self.scene = bpy.context.scene
        self.scene.name = "Title"
        gameui.setup_canvas_scene(self.scene)
        self.save()

    def save(self, path=None):
        bpy.ops.wm.save_as_mainfile(filepath=str(path or self.root / "ui" / "ui.blend"))

    def ui_dir(self) -> pathlib.Path:
        return gameui.export_dir(self.root)

    def canvas(self, scene=None) -> dict:
        data, ex = gameui_export.extract_canvas(scene or self.scene)
        self.warnings = ex.warnings
        return data

    def element(self, data, name) -> dict:
        return next(e for e in data["elements"] if e["name"] == name)

    def link(self, obj, scene=None):
        (scene or self.scene).collection.objects.link(obj)
        return obj

    def text(self, name, body="Play", at=(0, 0, 0), size=0.5, scene=None):
        curve = bpy.data.curves.new(name, "FONT")
        curve.body = body
        curve.size = size
        obj = self.link(bpy.data.objects.new(name, curve), scene)
        obj.location = at
        return obj

    def empty(self, name, at=(0, 0, 0), parent=None, scene=None):
        obj = self.link(bpy.data.objects.new(name, None), scene)
        obj.location = at
        obj.parent = parent
        return obj

    def plane(self, name, size=(2.0, 1.0), at=(0, 0, 0), material=None, scene=None):
        w, h = size[0] / 2, size[1] / 2
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(-w, -h, 0), (w, -h, 0), (w, h, 0), (-w, h, 0)], [], [(0, 1, 2, 3)])
        if material is not None:
            mesh.materials.append(material)
        obj = self.link(bpy.data.objects.new(name, mesh), scene)
        obj.location = at
        return obj

    def at_px(self, px, z=0.0):
        x, y = gameui.from_px(self.scene, px)
        return (x, y, z)


class TextExtractionTest(UiFileTestCase):
    def test_builtin_font_text_fields(self):
        obj = self.text("Label", "Score {score}", at=(0, 0, 0), size=0.5)
        curve = obj.data
        curve.align_x, curve.align_y = "CENTER", "CENTER"
        curve.space_line, curve.space_character, curve.space_word = 1.2, 1.5, 2.0
        curve.offset_x, curve.offset_y, curve.shear = 0.1, 0.2, 0.25
        obj.data.materials.append(emission_material("Ink", (0.2, 0.4, 0.6, 1.0)))
        element = self.element(self.canvas(), "Label")
        self.assertEqual(element["type"], "text")
        self.assertEqual(element["transform"], {"x": 960.0, "y": 540.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0})
        text = element["text"]
        self.assertEqual(text["body"], "Score {score}")
        self.assertEqual(text["font"], "assets/fonts/Inter.woff2")
        self.assertEqual(text["size"], 50.0)
        self.assertEqual(text["color"], [0.2, 0.4, 0.6, 1.0])
        self.assertEqual((text["align"], text["valign"]), ("center", "center"))
        self.assertEqual((text["lineSpacing"], text["letterSpacing"], text["wordSpacing"]), (1.2, 1.5, 2.0))
        self.assertIsNone(text["box"])
        self.assertEqual(text["offset"], [10.0, -20.0])
        self.assertEqual(text["shear"], 0.25)
        # Blender's built-in font aligns with ascent 0.8 / descent 0.2; Inter is drawn with its capitals
        # as tall as the built-in font's (0.682 of the size).
        self.assertEqual((text["ascent"], text["descent"]), (40.0, 10.0))
        self.assertAlmostEqual(text["fontSize"], 50 * 0.682 / (1490 / 2048), places=2)
        self.assertEqual(text["overflow"], "none")

    def test_valign_names_and_default_white(self):
        obj = self.text("T")
        for align, name in (("TOP_BASELINE", "top-baseline"), ("TOP", "top"), ("BOTTOM", "bottom"),
                            ("BOTTOM_BASELINE", "bottom-baseline")):
            obj.data.align_y = align
            self.assertEqual(self.element(self.canvas(), "T")["text"]["valign"], name)
        self.assertEqual(self.element(self.canvas(), "T")["text"]["color"], [1.0, 1.0, 1.0, 1.0])

    def test_text_box_is_blenders_frame(self):
        obj = self.text("Boxed", size=0.5)
        box = obj.data.text_boxes[0]
        box.x, box.y, box.width, box.height = -1.0, 0.5, 2.0, 1.0
        # With a height, Blender draws the frame one size above the box's y.
        self.assertEqual(self.element(self.canvas(), "Boxed")["text"]["box"], [-100.0, -100.0, 200.0, 100.0])
        box.height = 0.0
        self.assertEqual(self.element(self.canvas(), "Boxed")["text"]["box"], [-100.0, -50.0, 200.0, 0.0])
        box.width = 0.0
        self.assertIsNone(self.element(self.canvas(), "Boxed")["text"]["box"])

    def test_bold_characters_warn(self):
        obj = self.text("Bold", "Hi")
        obj.data.body_format[0].use_bold = True
        self.canvas()
        self.assertTrue(any("bold and italic" in w for w in self.warnings), self.warnings)

    def test_project_font_and_outside_font(self):
        fonts = self.root / "assets" / "fonts"
        fonts.mkdir(parents=True)
        shutil.copy2(bundled_font("Inter.woff2"), fonts / "Inter.woff2")
        inside = self.text("Inside", size=1.0)
        inside.data.font = bpy.data.fonts.load(str(fonts / "Inter.woff2"))
        outside = self.text("Outside", size=1.0)
        outside.data.font = bpy.data.fonts.load(str(bundled_font("DejaVuSansMono.woff2")))
        result = gameui_export.export_ui_file(self.root)
        data = json.loads((self.ui_dir() / result["canvases"][0]["file"]).read_text(encoding="utf-8"))
        text = self.element(data, "Inside")["text"]
        self.assertEqual(text["font"], "assets/fonts/Inter.woff2")
        # Measured in Blender: Inter's ascent and descent; its em follows from its ascender (WOFF2).
        self.assertAlmostEqual(text["ascent"], 67.736, delta=0.05)
        self.assertAlmostEqual(text["descent"], 16.866, delta=0.05)
        self.assertAlmostEqual(text["fontSize"], 100 * 2048 / 2929, delta=0.1)
        self.assertTrue(data["assets"]["assets/fonts/Inter.woff2"].startswith("../files/assets/fonts/Inter.woff2?v="))
        # A copy of Blender's Inter without its licence still gets Inter's OFL next to the exported font.
        files = project.export_dir(self.root) / "files"
        licence = (files / "assets" / "fonts" / "Inter-OFL.txt").read_text(encoding="utf-8")
        self.assertIn("Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)", licence)
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", licence)
        other = self.element(data, "Outside")["text"]
        self.assertIsNone(other["fontSize"])  # a WOFF2 font Blender's Python cannot read
        self.assertGreater(other["ascent"], 0)
        url = data["assets"][other["font"]]
        self.assertTrue(url.startswith("../files/external/"), url)
        self.assertTrue((self.ui_dir() / url.split("?")[0]).is_file())
        # Datafiles/fonts has no licence next to DejaVu Sans Mono: nothing is made up for it.
        self.assertEqual([p.name for p in (files / "external").iterdir() if p.suffix == ".txt"], [])

    def test_font_licences_go_with_the_exported_fonts(self):
        fonts = self.root / "assets" / "fonts"
        fonts.mkdir(parents=True)
        shutil.copy2(bundled_font("DejaVuSansMono.woff2"), fonts / "Mono.woff2")
        (fonts / "OFL.txt").write_text("Mono's licence", encoding="utf-8")
        mono = self.text("Mono", size=1.0)
        mono.data.font = bpy.data.fonts.load(str(fonts / "Mono.woff2"))
        self.text("Builtin", size=1.0)  # Blender's built-in font: the game draws Blender's Inter
        gameui_export.export_ui_file(self.root)
        files = project.export_dir(self.root) / "files" / "assets" / "fonts"
        self.assertEqual((files / "Mono-OFL.txt").read_text(encoding="utf-8"), "Mono's licence")
        self.assertTrue((files / "Inter.woff2").is_file())
        self.assertEqual((files / "Inter-OFL.txt").read_bytes(), gameui_export.INTER_LICENCE.read_bytes())
        self.assertEqual(gameui_export.INTER_LICENCE.name, "Inter-OFL.txt")

    def test_font_axes_of_variable_fonts(self):
        """§11.5: text.fontAxes for variable fonts (Inter, also as the built-in font's stand-in), none for static
        ones, and a warning for variable WOFF2 fonts Blender's Python cannot read."""
        fonts = self.root / "assets" / "fonts"
        fonts.mkdir(parents=True)
        shutil.copy2(bundled_font("Inter.woff2"), fonts / "Inter.woff2")
        self.text("Builtin")
        inter = self.text("Inter", at=(0, 1, 0))
        inter.data.font = bpy.data.fonts.load(str(fonts / "Inter.woff2"))
        mono = self.text("Mono", at=(0, 2, 0))
        mono.data.font = bpy.data.fonts.load(str(bundled_font("DejaVuSansMono.woff2")))
        thai = self.text("Thai", at=(0, 3, 0))
        thai.data.font = bpy.data.fonts.load(str(bundled_font("NotoSansThai-VariableFont_wdth,wght.woff2")))
        data = self.canvas()
        self.assertEqual(self.element(data, "Builtin")["text"]["fontAxes"], {"opsz": 16.0, "wght": 400.0})
        self.assertEqual(self.element(data, "Inter")["text"]["fontAxes"], {"opsz": 16.0, "wght": 400.0})
        self.assertNotIn("fontAxes", self.element(data, "Mono")["text"])
        self.assertNotIn("fontAxes", self.element(data, "Thai")["text"])
        self.assertEqual(sum("is a variable WOFF2 font" in w for w in self.warnings), 1, self.warnings)

    @unittest.skipUnless(os.path.isfile(r"C:\Windows\Fonts\SitkaVF.ttf") and
                         os.path.isfile(r"C:\Windows\Fonts\bahnschrift.ttf"), "Windows' variable fonts")
    def test_font_axes_of_variable_ttf_fonts(self):
        # Measured: Blender draws Sitka at opsz 16 (its default is 11) and Bahnschrift at its defaults.
        sitka = self.text("Sitka")
        sitka.data.font = bpy.data.fonts.load(r"C:\Windows\Fonts\SitkaVF.ttf", check_existing=True)
        bahn = self.text("Bahn", at=(0, 1, 0))
        bahn.data.font = bpy.data.fonts.load(r"C:\Windows\Fonts\bahnschrift.ttf", check_existing=True)
        data = self.canvas()
        self.assertEqual(self.element(data, "Sitka")["text"]["fontAxes"], {"opsz": 16.0, "wght": 400.0})
        self.assertEqual(self.element(data, "Bahn")["text"]["fontAxes"], {"wght": 400.0, "wdth": 100.0})


class FontMetricsTest(unittest.TestCase):
    def sfnt(self, upem=2048, y_min=-500, y_max=2000, ascender=1900, descender=-450, woff=False, typo=None,
             axes=None) -> bytes:
        head = bytearray(54)
        head[18:20] = struct.pack(">H", upem)
        head[36:44] = struct.pack(">hhhh", -100, y_min, 1000, y_max)
        hhea = bytearray(36)
        hhea[4:8] = struct.pack(">hh", ascender, descender)
        tables = [(b"head", bytes(head)), (b"hhea", bytes(hhea))]
        if axes is not None:  # an fvar table: 16-byte header, then 20-byte axis records (no instances)
            fvar = bytearray(struct.pack(">HHHHHHHH", 1, 0, 16, 2, len(axes), 20, 0, 4 + 4 * len(axes)))
            for tag, low, default, high in axes:
                fvar += struct.pack(">4siiiHH", tag.encode(), int(low * 65536), int(default * 65536),
                                    int(high * 65536), 0, 256)
            tables.append((b"fvar", bytes(fvar)))
        if typo is not None:  # an OS/2 table with USE_TYPO_METRICS and these typographic metrics
            os2 = bytearray(96)
            os2[0:2] = struct.pack(">H", 4)
            os2[62:64] = struct.pack(">H", 128)
            os2[68:72] = struct.pack(">hh", *typo)
            tables.append((b"OS/2", bytes(os2)))
        if woff:
            out = bytearray(44 + 20 * len(tables))
            out[0:4] = b"wOFF"
            out[12:14] = struct.pack(">H", len(tables))
            for i, (tag, data) in enumerate(tables):
                packed = zlib.compress(data)
                packed = packed if len(packed) < len(data) else data  # WOFF stores a table raw unless smaller
                struct.pack_into(">4sIIII", out, 44 + 20 * i, tag, len(out), len(packed), len(data), 0)
                out += packed
            return bytes(out)
        out = bytearray(12 + 16 * len(tables))
        out[0:4] = b"\x00\x01\x00\x00"
        out[4:6] = struct.pack(">H", len(tables))
        for i, (tag, data) in enumerate(tables):
            struct.pack_into(">4sIII", out, 12 + 16 * i, tag, 0, len(out), len(data))
            out += data
        return bytes(out)

    def test_metrics_follow_freetype_bbox_scale(self):
        # Arial's numbers: Blender put the baseline 0.66356 below the origin for align TOP.
        a, d, em = gameui_export.sfnt_metrics(self.sfnt(2048, -665, 2129, 1854, -434))
        self.assertAlmostEqual(a, 0.66356, places=4)
        self.assertAlmostEqual(d, 0.15533, places=4)
        self.assertAlmostEqual(em, 2048 / 2794, places=6)
        self.assertEqual(gameui_export.sfnt_metrics(self.sfnt(2048, -665, 2129, 1854, -434, woff=True)), (a, d, em))
        self.assertIsNone(gameui_export.sfnt_metrics(b"wOF2" + bytes(64)))

    def test_use_typo_metrics_flag_picks_the_os2_metrics(self):
        # FreeType (so Blender) honours OS/2 USE_TYPO_METRICS: OptimisticText-Bold's numbers, which
        # Blender lays out with ascent 0.5497 and descent 0.2033 (not hhea's 0.7696 / 0.2007).
        a, d, _em = gameui_export.sfnt_metrics(self.sfnt(2000, -685, 1971, 2044, -533, typo=(1460, -540)))
        self.assertAlmostEqual(a, 1460 / 2656, places=6)
        self.assertAlmostEqual(d, 540 / 2656, places=6)

    def woff2(self, tags) -> bytes:
        """A WOFF2 header and table directory (no table data): tags by WOFF2's known-tag index, else spelled out."""
        known = {"head": 1, "hhea": 2, "glyf": 10, "loca": 11, "fvar": 47}
        out = bytearray(48)
        out[0:4] = b"wOF2"
        out[12:14] = struct.pack(">H", len(tags))
        for tag in tags:
            index = known.get(tag, 63)
            out.append(index)  # version 0: glyf and loca are transformed (a second length follows)
            if index == 63:
                out += tag.encode()
            out += bytes([0x81, 0x00])  # UIntBase128 128
            if index in (10, 11):
                out += bytes([0x40])
        return bytes(out)

    def test_variable_font_axes_and_the_instance_blender_draws(self):
        axes = [("wght", 100, 400, 900), ("opsz", 8, 12, 72), ("wdth", 75, 100, 100)]
        for woff in (False, True):
            self.assertEqual(gameui_export.sfnt_axes(self.sfnt(axes=axes, woff=woff)),
                             [(t, float(a), float(b), float(c)) for t, a, b, c in axes])
        self.assertEqual(gameui_export.sfnt_axes(self.sfnt()), [])  # static
        # Blender lays text objects out at optical size 16 (within the axis), every other axis at its default.
        self.assertEqual(gameui_export.drawn_axes(gameui_export.sfnt_axes(self.sfnt(axes=axes))),
                         {"wght": 400.0, "opsz": 16.0, "wdth": 100.0})
        self.assertEqual(gameui_export.drawn_axes([("opsz", 20, 24, 40), ("ital", 0, 0, 1)]), {"opsz": 20.0, "ital": 0.0})
        self.assertEqual(gameui_export.drawn_axes(gameui_export.INTER_AXES), {"opsz": 16.0, "wght": 400.0})
        # WOFF2: whether there is an fvar table is known from the directory, its axes are not (Brotli).
        self.assertIsNone(gameui_export.sfnt_axes(self.woff2(["head", "glyf", "loca", "fvar"])))
        self.assertIsNone(gameui_export.sfnt_axes(self.woff2(["glyf", "loca", "STAT", "fvar"])))
        self.assertEqual(gameui_export.sfnt_axes(self.woff2(["head", "glyf", "loca", "GSUB"])), [])
        self.assertIsNone(gameui_export.sfnt_axes(b"not a font at all"))


class ImageExtractionTest(UiFileTestCase):
    def test_generated_image_is_written_as_png(self):
        image = bpy.data.images.new("Swatch", 4, 2, alpha=True)
        pixels = [0.0] * (4 * 2 * 4)
        for i in range(4):  # bottom row red, top row blue
            pixels[i * 4:(i + 1) * 4] = [1.0, 0.0, 0.0, 1.0]
            pixels[16 + i * 4:16 + (i + 1) * 4] = [0.0, 0.0, 1.0, 1.0]
        image.pixels.foreach_set(pixels)
        obj = self.empty("Logo")
        obj.empty_display_type = "IMAGE"
        obj.data = image
        obj.empty_display_size = 2.0
        obj.use_empty_image_alpha = True
        obj.color[3] = 0.5
        result = gameui_export.export_ui_file(self.root)
        data = json.loads((self.ui_dir() / result["canvases"][0]["file"]).read_text(encoding="utf-8"))
        element = self.element(data, "Logo")
        self.assertEqual(element["type"], "image")
        block = element["image"]
        self.assertEqual(block["rect"], [-100.0, -50.0, 200.0, 100.0])  # longer side = display size
        self.assertEqual(block["opacity"], 0.5)
        self.assertEqual(element["opacity"], 0.5)  # the colour alpha, on every element type (§10.5)
        self.assertFalse(block["pixelated"])
        self.assertRegex(block["src"], r"^generated/Swatch-[0-9a-f]{10}\.png$")
        url = data["assets"][block["src"]]
        self.assertTrue(url.startswith(f"../files/{block['src']}?v="), url)
        width, height, rows = png_rows((self.ui_dir() / url.split("?")[0]).read_bytes())
        self.assertEqual((width, height), (4, 2))
        self.assertEqual(rows[0][:4], bytes([0, 0, 255, 255]))  # PNG rows go top-down
        self.assertEqual(rows[1][:4], bytes([255, 0, 0, 255]))

    def test_image_file_and_offset(self):
        folder = self.root / "assets" / "ui"
        folder.mkdir(parents=True)
        (folder / "logo.png").write_bytes(gameui_export._png(2, 4, bytes([255] * 32)))
        obj = self.empty("Logo")
        obj.empty_display_type = "IMAGE"
        obj.data = bpy.data.images.load(str(folder / "logo.png"))
        obj.empty_image_offset = (0.0, 0.0)  # bottom-left corner at the origin
        data = self.canvas()
        block = self.element(data, "Logo")["image"]
        self.assertEqual(block["src"], "assets/ui/logo.png")
        self.assertEqual(block["rect"], [0.0, -100.0, 50.0, 100.0])
        self.assertEqual(block["opacity"], 1.0)
        self.assertNotIn("opacity", self.element(data, "Logo"))  # 1: left out

    def test_image_empty_without_image_is_a_group(self):
        obj = self.empty("Frame")
        obj.empty_display_type = "IMAGE"
        self.assertEqual(self.element(self.canvas(), "Frame")["type"], "group")


class MeshExtractionTest(UiFileTestCase):
    def mesh(self, name, material=None):
        self.plane(name, material=material)
        return self.element(self.canvas(), name)["mesh"]

    def test_plane_with_emission(self):
        block = self.mesh("Panel", emission_material("Fill", (1.0, 0.5, 0.0, 1.0)))
        self.assertEqual(len(block["indices"]), 6)
        self.assertEqual(len(block["positions"]), 8)
        corners = {tuple(block["positions"][i:i + 2]) for i in range(0, 8, 2)}
        self.assertEqual(corners, {(-100.0, 50.0), (100.0, 50.0), (100.0, -50.0), (-100.0, -50.0)})
        self.assertEqual(block["bounds"], [-100.0, -50.0, 100.0, 50.0])
        self.assertIsNone(block["uvs"])
        self.assertIsNone(block["colors"])
        self.assertEqual(block["material"], {"color": [1.0, 0.5, 0.0, 1.0], "texture": "", "blend": "opaque"})

    def test_materials(self):
        cases = {
            "Principled": (principled_material("P", (0.1, 0.2, 0.3, 1.0), alpha=0.5), [0.1, 0.2, 0.3, 0.5], "alpha"),
            "FadeA": (mix_transparent_material("MA", (1, 1, 1, 1), 0.25, True), [1.0, 1.0, 1.0, 0.25], "alpha"),
            "FadeB": (mix_transparent_material("MB", (1, 1, 1, 1), 0.25, False), [1.0, 1.0, 1.0, 0.75], "alpha"),
            "Glow": (emission_material("Glow.add", (0.2, 0.2, 0.2, 1), strength=2.0), [0.4, 0.4, 0.4, 1.0],
                     "additive"),
        }
        for name, (mat, color, blend) in cases.items():
            self.plane(name, material=mat)
        data = self.canvas()
        for name, (_mat, color, blend) in cases.items():
            material = self.element(data, name)["mesh"]["material"]
            self.assertEqual(material["color"], color, name)
            self.assertEqual(material["blend"], blend, name)
        self.plane("Bare")
        self.assertEqual(self.element(self.canvas(), "Bare")["mesh"]["material"]["color"], [0.8, 0.8, 0.8, 1.0])

    def test_texture_uvs_and_vertex_colors(self):
        mat = emission_material("Tex", (1, 1, 1, 1))
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.new("Pattern", 2, 2)
        links.new(tex.outputs["Color"], next(n for n in nodes if n.type == "EMISSION").inputs["Color"])
        obj = self.plane("Art", material=mat)
        obj.data.uv_layers.new(name="UVMap")
        attribute = obj.data.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
        for item in attribute.data:
            item.color = (0.5, 0.25, 1.0, 1.0)
        obj.data.color_attributes.active_color = attribute
        block = self.element(self.canvas(), "Art")["mesh"]
        self.assertRegex(block["material"]["texture"], r"^generated/Pattern-[0-9a-f]{10}\.png$")
        self.assertEqual(block["material"]["color"], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(len(block["uvs"]), len(block["positions"]))
        self.assertEqual(len(block["colors"]), len(block["positions"]) * 2)
        self.assertEqual(block["colors"][:4], [0.5, 0.25, 1.0, 1.0])
        self.assertEqual(block["material"]["blend"], "alpha")  # vertex colours may carry alpha

    def test_filled_curve_is_a_mesh(self):
        curve = bpy.data.curves.new("Blob", "CURVE")
        curve.dimensions = "2D"
        curve.fill_mode = "BOTH"
        spline = curve.splines.new("POLY")
        spline.points.add(3)
        for point, co in zip(spline.points, ((-1, -1), (1, -1), (1, 1), (-1, 1))):
            point.co = (co[0], co[1], 0, 1)
        spline.use_cyclic_u = True
        self.link(bpy.data.objects.new("Blob", curve))
        element = self.element(self.canvas(), "Blob")
        self.assertEqual(element["type"], "mesh")
        self.assertGreaterEqual(len(element["mesh"]["indices"]), 6)
        self.assertEqual(element["mesh"]["bounds"], [-100.0, -100.0, 100.0, 100.0])

    def test_triangle_budget_warns(self):
        import numpy as np

        n = 105
        xs, ys = np.meshgrid(np.linspace(-1, 1, n), np.linspace(-1, 1, n))
        verts = np.stack([xs.ravel(), ys.ravel(), np.zeros(n * n)], axis=1).tolist()
        faces = [(r * n + c, r * n + c + 1, (r + 1) * n + c + 1, (r + 1) * n + c)
                 for r in range(n - 1) for c in range(n - 1)]
        mesh = bpy.data.meshes.new("Grid")
        mesh.from_pydata(verts, [], faces)
        self.link(bpy.data.objects.new("Grid", mesh))
        self.canvas()
        self.assertTrue(any("triangles" in w for w in self.warnings), self.warnings)


class LayoutTest(UiFileTestCase):
    def test_hierarchy_transforms_order_and_visibility(self):
        parent = self.empty("Group", at=(1.0, 1.0, 0.0))
        parent.rotation_euler.z = math.pi / 2
        parent.scale = (2.0, 2.0, 1.0)
        child = self.text("Label", at=(1.0, 0.0, 1.0))
        child.parent = parent
        child.rotation_euler.z = math.radians(30)
        mirrored = self.plane("Mirror", at=(0.5, 0.0, 0.0))
        mirrored.parent = parent
        mirrored.scale = (-1.0, 1.0, 1.0)
        other = self.empty("Other", at=(0.0, 0.0, 0.5))
        other.hide_render = True
        bpy.context.view_layer.update()
        data = self.canvas()
        names = [e["name"] for e in data["elements"]]
        self.assertLess(names.index("Group"), names.index("Label"))  # parents before children
        group = self.element(data, "Group")
        self.assertEqual(group["transform"], {"x": 1060.0, "y": 440.0, "rotation": -1.570796, "scaleX": 2.0,
                                              "scaleY": 2.0})
        self.assertIsNone(group["parent"])
        label = self.element(data, "Label")
        self.assertEqual(label["parent"], ids.get_id(parent))
        self.assertEqual(label["transform"], {"x": 100.0, "y": 0.0, "rotation": -0.523599, "scaleX": 1.0,
                                              "scaleY": 1.0})
        self.assertNotIn("anchor", label)
        mirror = self.element(data, "Mirror")["transform"]
        self.assertEqual((mirror["x"], mirror["rotation"], mirror["scaleX"], mirror["scaleY"]), (50.0, 0.0, -1.0, 1.0))
        # Draw order: world Z, ties in outliner order (parents first, then by name).
        orders = {e["name"]: e["order"] for e in data["elements"]}
        self.assertEqual(sorted(orders, key=orders.get), ["Group", "Mirror", "Other", "Label"])
        self.assertFalse(self.element(data, "Other")["visible"])
        self.assertTrue(group["visible"])
        self.assertEqual(group["type"], "group")

    def test_duplicated_elements_get_their_own_ids(self):
        button = self.empty("Button", at=(1.0, 0.0, 0.0))
        label = self.text("Label", "Play")
        label.parent = button
        first = {e["name"]: e["id"] for e in self.canvas()["elements"]}
        # Shift+D and copy/paste copy the custom properties, gs_id included.
        copy = self.link(button.copy())
        copy_label = self.link(label.copy())
        copy_label.parent = copy
        copy.location.x = -1.0
        data = self.canvas()
        by_name = {e["name"]: e["id"] for e in data["elements"]}
        self.assertEqual(len(set(by_name.values())), 4, by_name)
        self.assertEqual((by_name["Button"], by_name["Label"]), (first["Button"], first["Label"]))
        self.assertEqual(self.element(data, copy_label.name)["parent"], by_name[copy.name])
        # Saving repairs copies before the file is written, so the export after the save changes nothing.
        third = self.link(button.copy())
        self.save()
        self.assertNotEqual(ids.get_id(third), ids.get_id(button))
        self.assertFalse(bpy.data.is_dirty)
        saved = json.loads((self.ui_dir() / "Title.ui.json").read_text(encoding="utf-8"))
        self.assertEqual(len({e["id"] for e in saved["elements"]}), 5)

    def test_skew_from_an_unevenly_scaled_parent_warns(self):
        from mathutils import Matrix

        card = self.empty("Card")
        card.rotation_euler.z = math.radians(30)
        card.scale = (3.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        shine = self.plane("Shine", at=(0.5, 0.2, 0.1))
        shine.parent = card
        shine.matrix_parent_inverse = card.matrix_world.inverted()  # Ctrl+P keeps it in place: a skew
        self.canvas()
        self.assertTrue(any("'Shine' is skewed" in w for w in self.warnings), self.warnings)
        shine.matrix_parent_inverse = Matrix.Identity(4)  # rotated and scaled with its parent: no skew
        self.canvas()
        self.assertFalse(any("skewed" in w for w in self.warnings), self.warnings)

    def test_scripted_moves_are_read_before_the_depsgraph_runs(self):
        self.scene.camera.location = (1.0, 2.0, 10.0)
        self.assertEqual(gameui.camera_center(self.scene), (1.0, 2.0))
        self.assertEqual(gameui.to_px(self.scene, (1.0, 2.0)), (960.0, 540.0))
        probe = self.empty("Probe", at=(1.0, 2.0, 0.0))
        self.assertEqual(self.element(self.canvas(), "Probe")["transform"]["x"], 960.0)
        probe.location.x = 2.0  # no view layer update: extraction evaluates the scene itself
        self.assertEqual(self.element(self.canvas(), "Probe")["transform"]["x"], 1060.0)

    def test_anchors(self):
        spots = {"TL": (100, 100), "Mid": (960, 540), "BR": (1800, 1000), "Top": (960, 100), "Left": (200, 540)}
        for name, px in spots.items():
            self.empty(name, at=self.at_px(px))
        explicit = self.empty("Bar", at=self.at_px((960, 540)))
        components.add_component(explicit, "UiAnchor", {"anchor": "bottom", "stretch": "width"})
        child = self.empty("Child", parent=explicit)
        components.add_component(child, "UiAnchor", {"anchor": "top"})
        data = self.canvas()
        expected = {"TL": "top-left", "Mid": "center", "BR": "bottom-right", "Top": "top", "Left": "left"}
        for name, anchor in expected.items():
            element = self.element(data, name)
            self.assertEqual((element["anchor"], element["stretch"]), (anchor, "none"), name)
        bar = self.element(data, "Bar")
        self.assertEqual((bar["anchor"], bar["stretch"]), ("bottom", "width"))
        child_element = self.element(data, "Child")
        self.assertNotIn("anchor", child_element)
        self.assertNotIn("UiAnchor", child_element["components"])
        self.assertTrue(any("UiAnchor works on top-level" in w for w in self.warnings), self.warnings)

    def test_settings_defaults_and_ui_canvas(self):
        self.assertEqual(self.canvas()["settings"], gameui_export.DEFAULT_SETTINGS)
        button = self.empty("Play")
        components.add_component(button, "UiButton", {"action": "close"})
        components.add_component(self.scene.camera, "UiCanvas", {"kind": "hud", "open": "start", "focus": button,
                                                                 "backdrop": [0, 0, 0, 0.6]})
        data = self.canvas()
        settings = data["settings"]
        self.assertEqual((settings["kind"], settings["open"]), ("hud", "start"))
        self.assertEqual(settings["focus"], ids.get_id(button))
        self.assertAlmostEqual(settings["backdrop"][3], 0.6, places=5)
        self.assertEqual(data["size"], [1920, 1080])
        self.assertEqual(self.element(data, "Play")["components"]["UiButton"]["action"], "close")
        self.assertNotIn(self.scene.camera.name, [e["name"] for e in data["elements"]])
        outsider = bpy.data.objects.new("Elsewhere", None)
        components.set_field(self.scene.camera, "UiCanvas", "focus", outsider)
        self.assertIsNone(self.canvas()["settings"]["focus"])
        self.assertTrue(any("not in this screen" in w for w in self.warnings), self.warnings)

    def test_unsupported_objects_and_camera_problems(self):
        self.link(bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT")))
        self.link(bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig")))
        self.link(bpy.data.objects.new("OtherCam", bpy.data.cameras.new("OtherCam")))
        hidden = bpy.data.collections.new("Hidden")
        self.scene.collection.children.link(hidden)
        hidden.objects.link(bpy.data.objects.new("Excluded", None))
        bpy.context.view_layer.layer_collection.children["Hidden"].exclude = True
        self.scene.camera.data.type = "PERSP"
        data = self.canvas()
        names = [e["name"] for e in data["elements"]]
        self.assertEqual(names, [])
        listed = next(w for w in self.warnings if "not exported" in w)
        self.assertIn("Rig", listed)
        self.assertIn("OtherCam", listed)
        self.assertNotIn("Lamp", listed)
        self.assertTrue(any("orthographic" in w for w in self.warnings), self.warnings)


class AnimationExportTest(UiFileTestCase):
    """§10.5: timeline markers name a canvas's animations; tracks are sampled from the keyframes per frame."""

    def setUp(self):
        super().setUp()
        self.scene.render.fps = 10
        self.scene.frame_start, self.scene.frame_end = 1, 16
        self.scene.frame_current = 1

    def markers(self, **frames):
        for name, frame in frames.items():
            self.scene.timeline_markers.new(name, frame=frame)

    def assertSeries(self, values, expected, places=4):
        self.assertEqual(len(values), len(expected), values)
        for got, want in zip(values, expected):
            self.assertAlmostEqual(got, want, places=places, msg=values)

    def build(self):
        """Slide (x keyed), Fade (opacity in and out), Spin (rotation and scale, in a scaled group), Label (a still
        child of Slide) and Still; markers open at 1 and close at 11, the scene ending at 16; frame 1."""
        self.markers(open=1, close=11)
        slide = self.empty("Slide")
        keys(slide, "location", 0, {1: -1.0, 11: 0.0})
        self.text("Label", at=(0.0, 0.5, 0.0)).parent = slide
        fade = self.empty("Fade", at=(-5.0, 0.0, 0.0))
        keys(fade, "color", 3, {1: 0.0, 11: 1.0, 16: 0.0})
        group = self.empty("Group", at=(3.0, 0.0, 0.0))
        group.scale = (2.0, 2.0, 1.0)
        spin = self.plane("Spin", at=(0.5, 0.0, 0.0))
        spin.parent = group
        keys(spin, "rotation_euler", 2, {1: 0.0, 11: math.pi / 2})
        for axis in (0, 1):
            keys(spin, "scale", axis, {1: 0.5, 11: 1.0})
        self.empty("Still", at=(0.0, -3.0, 0.0))
        self.scene.frame_set(1)
        return slide

    def test_markers_name_the_ranges_and_the_rest_frame(self):
        scene = self.scene
        scene.frame_end = 30
        for name, frame in (("open", 1), ("Loop", 10), ("hold", 15), ("close", 20), ("open", 25)):
            scene.timeline_markers.new(name, frame=frame)
        problems = []
        ranges = gameui.animation_ranges(scene, problems)
        # Each runs to the next marker of any name; names match without case; the first of two counts.
        self.assertEqual(ranges, {"open": (1, 10), "close": (20, 25), "loop": (10, 15)})
        self.assertEqual(list(ranges), ["open", "close", "loop"])
        self.assertTrue(any("2 'open' markers" in p for p in problems), problems)
        self.assertEqual(gameui.rest_frame(scene), 10)  # where open ends
        scene.timeline_markers.clear()
        scene.frame_current = 7
        self.assertEqual(gameui.rest_frame(scene), 7)  # no markers: the current frame
        # No open marker: a loop (else a close) starts from the rest pose, wherever the timeline was scrubbed.
        loop = scene.timeline_markers.new("loop", frame=3)
        self.assertEqual(gameui.rest_frame(scene), 3)
        scene.timeline_markers.remove(loop)
        close = scene.timeline_markers.new("close", frame=5)
        self.assertEqual(gameui.rest_frame(scene), 5)
        scene.timeline_markers.remove(close)
        scene.timeline_markers.new("close", frame=30)
        problems = []
        self.assertEqual(gameui.animation_ranges(scene, problems), {})  # no frames after it
        self.assertTrue(any("no frames after it" in p for p in problems), problems)
        scene.timeline_markers.new("open", frame=24)
        self.assertEqual(gameui.animation_ranges(scene), {"open": (24, 30)})
        self.assertEqual(gameui.rest_frame(scene), 30)

    def test_tracks_hold_the_channels_that_change_from_the_rest_pose(self):
        self.build()
        data = self.canvas()
        by_name = {e["name"]: e["id"] for e in data["elements"]}
        animations = data["animations"]
        self.assertEqual(list(animations), ["open", "close"])
        opening, closing = animations["open"], animations["close"]
        self.assertEqual((opening["duration"], opening["fps"], closing["duration"], closing["fps"]), (1.0, 10, 0.5, 10))
        # The rest pose is the scene where open ends (frame 11), not at the current frame (1).
        self.assertEqual(self.element(data, "Slide")["transform"]["x"], 960.0)
        self.assertNotIn("opacity", self.element(data, "Fade"))  # 1 at rest
        spin = self.element(data, "Spin")["transform"]
        self.assertEqual((spin["x"], spin["y"], spin["rotation"], spin["scaleX"], spin["scaleY"]),
                         (50.0, 0.0, -1.570796, 1.0, 1.0))
        # One value per frame (duration x fps + 1), local px / clockwise radians; Label moves with Slide.
        tracks = opening["tracks"]
        self.assertEqual(list(tracks), [by_name["Fade"], by_name["Spin"], by_name["Slide"]])  # element order
        self.assertEqual(list(tracks[by_name["Slide"]]), ["x"])
        self.assertSeries(tracks[by_name["Slide"]]["x"], [860.0 + 10 * i for i in range(11)])
        self.assertEqual(list(tracks[by_name["Fade"]]), ["opacity"])
        self.assertSeries(tracks[by_name["Fade"]]["opacity"], [i / 10 for i in range(11)])
        self.assertEqual(list(tracks[by_name["Spin"]]), ["rotation", "scaleX", "scaleY"])
        self.assertSeries(tracks[by_name["Spin"]]["rotation"], [-math.pi / 20 * i for i in range(11)])
        self.assertSeries(tracks[by_name["Spin"]]["scaleY"], [0.5 + 0.05 * i for i in range(11)])
        # Close: the keys of the others hold, so only the fade out.
        self.assertEqual(closing["tracks"], {by_name["Fade"]: {"opacity": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]}})

    def test_tracks_match_blenders_own_evaluation(self):
        """Bezier keys, a delta, a parent inverse, a rotated and scaled parent, a quaternion and a turn past half
        a circle: every sampled value is what Blender evaluates at that frame."""
        self.markers(open=1, close=11)
        holder = self.empty("Holder", at=(1.0, -0.5, 0.0))
        holder.rotation_euler.z = 0.3
        holder.scale = (1.5, 1.5, 1.0)
        bpy.context.view_layer.update()
        card = self.plane("Card", at=(0.2, 0.1, 0.0))
        card.parent = holder
        card.matrix_parent_inverse = holder.matrix_world.inverted()
        card.delta_location = (0.1, 0.0, 0.0)
        for axis, (a, b) in enumerate(((0.0, 1.0), (0.5, -0.5))):
            keys(card, "location", axis, {1: a, 11: b}, interpolation="BEZIER")
        keys(card, "rotation_euler", 2, {1: -0.4, 6: 2.0, 11: 3.5}, interpolation="BEZIER")
        keys(card, "scale", 0, {1: 0.2, 11: 1.2}, interpolation="BEZIER")
        floater = self.empty("Floater", at=(-2.0, 1.0, 0.0))
        floater.rotation_mode = "QUATERNION"
        keys(floater, "location", 1, {1: 1.0, 11: -1.0}, interpolation="BEZIER")
        keys(floater, "rotation_quaternion", 0, {1: 1.0, 11: 0.7})
        keys(floater, "rotation_quaternion", 3, {1: 0.0, 11: 0.7})  # not normalised: Blender normalises
        data = self.canvas()
        tracks = data["animations"]["open"]["tracks"]
        card_track, floater_track = tracks[ids.get_id(card)], tracks[ids.get_id(floater)]
        self.assertEqual(set(card_track), {"x", "y", "rotation", "scaleX"})  # scale y: the parent's, uniform
        self.assertEqual(set(floater_track), {"y", "rotation"})
        turns = card_track["rotation"]
        self.assertLess(max(abs(b - a) for a, b in zip(turns, turns[1:])), 1.0)  # no jump at half a turn
        k, size = gameui.px_per_unit(self.scene), gameui.design_size(self.scene)
        center = gameui.camera_center(self.scene)
        channels = ("x", "y", "rotation", "scaleX", "scaleY")
        try:
            for i, frame in enumerate(range(1, 12)):
                self.scene.frame_set(frame)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                world = {o: o.evaluated_get(depsgraph).matrix_world.copy() for o in (holder, card, floater)}
                expected = {
                    "Card": (card_track, gameui_export._transform(world[holder].inverted() @ world[card], False, False,
                                                                  center, k, size)),
                    "Floater": (floater_track, gameui_export._transform(world[floater], False, True, center, k, size)),
                }
                for name, (track, values) in expected.items():
                    want = dict(zip(channels, values))
                    for channel, series in track.items():
                        got = series[i]
                        if channel == "rotation":
                            got = want[channel] + math.remainder(got - want[channel], math.tau)
                        self.assertAlmostEqual(got, want[channel], delta=0.01 if channel in "xy" else 1e-4,
                                               msg=f"{name} {channel} at frame {frame}")
        finally:
            self.scene.frame_set(1)
        rest = self.element(data, "Card")["transform"]
        self.assertEqual((rest["x"], rest["y"]), (card_track["x"][-1], card_track["y"][-1]))  # rest = frame 11

    def test_unkeyed_edits_the_frame_and_the_file_stay_as_they_are(self):
        slide = self.build()
        slide.location.x = -0.5  # not keyed: Blender keeps it until the frame changes
        self.save()  # exports the canvases
        self.assertFalse(bpy.data.is_dirty)
        gameui_export.export_ui_file(self.root)
        self.assertEqual(self.scene.frame_current, 1)
        self.assertEqual(slide.location.x, -0.5)
        self.assertFalse(bpy.data.is_dirty)
        data = json.loads((self.ui_dir() / "Title.ui.json").read_text(encoding="utf-8"))
        self.assertEqual(self.element(data, "Slide")["transform"]["x"], 960.0)  # the rest pose, from the keys
        self.assertEqual(list(data)[-3:], ["elements", "animations", "assets"])

    def test_without_an_open_marker_the_rest_pose_is_where_close_starts(self):
        self.markers(close=11)
        slide = self.empty("Slide")
        keys(slide, "location", 0, {1: -1.0, 11: 0.0, 16: 1.0})
        self.scene.frame_set(3)  # a scrubbed timeline does not move the rest pose
        data = self.canvas()
        self.assertEqual(self.element(data, "Slide")["transform"]["x"], 960.0)  # frame 11, where close starts
        self.assertEqual(list(data["animations"]), ["close"])
        track = data["animations"]["close"]["tracks"][ids.get_id(slide)]["x"]
        self.assertEqual((track[0], track[-1], len(track)), (960.0, 1060.0, 6))

    def test_without_markers_there_are_no_animations(self):
        slide = self.empty("Slide")
        keys(slide, "location", 0, {1: -1.0, 11: 0.0})
        dim = self.empty("Dim")
        dim.color[3] = 0.25
        self.scene.frame_set(6)
        data = self.canvas()
        self.assertNotIn("animations", data)
        self.assertEqual(self.element(data, "Slide")["transform"]["x"], 910.0)  # the scene as it is
        self.assertEqual(self.element(data, "Dim")["opacity"], 0.25)
        self.scene.timeline_markers.new("intro", frame=1)  # other names only end ranges
        self.assertNotIn("animations", self.canvas())
        slide.animation_data_clear()
        self.scene.timeline_markers.new("loop", frame=4)  # nothing keyed: the range, without tracks
        self.assertEqual(self.canvas()["animations"], {"loop": {"duration": 1.2, "fps": 10, "tracks": {}}})

    def test_what_is_not_sampled_warns(self):
        self.markers(open=1, close=11)
        held = self.empty("Held")
        keys(held, "location", 0, {1: 0.0, 11: 1.0})
        held.constraints.new("LIMIT_LOCATION")
        driven = self.empty("Driven")
        driven.driver_add("location", 1).driver.type = "AVERAGE"
        keys(self.scene.camera, "location", 0, {1: 0.0, 11: 1.0})
        self.canvas()
        for text in ("'Held': constraints", "'Driven': drivers", "camera animation is not exported"):
            self.assertTrue(any(text in w for w in self.warnings), (text, self.warnings))


class SliceAndControlsTest(UiFileTestCase):
    """§10.6 9-slice panels and the §10.4 / §10.1 checks of sliders, toggles and value paths."""

    def test_textured_panel_with_ui_slice(self):
        card = self.plane("Card", material=textured_material("Frame", (64, 32)))
        card.data.uv_layers.new(name="UVMap")
        components.add_component(card, "UiSlice", {"left": 10, "right": 12, "top": 8, "bottom": 6})
        card[gameui.SIZE_PROP] = [300.0, 120.0]  # the size gs.gameui built it at
        element = self.element(self.canvas(), "Card")
        self.assertNotIn("slice", element)
        self.assertEqual(element["mesh"]["slice"],
                         {"insets": [10.0, 12.0, 8.0, 6.0], "image": [64, 32], "size": [300.0, 120.0]})
        self.assertEqual(self.warnings, [])
        del card[gameui.SIZE_PROP]  # not built by gs.gameui: the mesh's extent
        self.assertEqual(self.element(self.canvas(), "Card")["mesh"]["slice"]["size"], [200.0, 100.0])
        components.set_field(card, "UiSlice", "left", 60)
        self.canvas()
        self.assertTrue(any("larger than its image" in w for w in self.warnings), self.warnings)

    def test_ui_slice_needs_a_textured_mesh(self):
        components.add_component(self.plane("Plain", material=emission_material("Flat", (1, 1, 1, 1))), "UiSlice")
        components.add_component(self.empty("Box"), "UiSlice")
        data = self.canvas()
        self.assertNotIn("slice", self.element(data, "Plain")["mesh"])
        self.assertNotIn("slice", self.element(data, "Box"))
        for text in ("'Plain': UiSlice needs a textured panel", "'Box': UiSlice works on textured panels"):
            self.assertTrue(any(text in w for w in self.warnings), (text, self.warnings))

    def test_a_button_on_a_slider_or_toggle_is_dropped(self):
        slider = self.empty("Volume")
        fill = self.plane("VolumeFill")
        fill.parent = slider
        components.add_component(slider, "UiSlider", {"value": "audio.music", "fill": fill})
        components.add_component(slider, "UiButton", {"action": "close"})
        toggle = self.empty("Mute")
        components.add_component(toggle, "UiToggle", {"value": "save.muted"})
        components.add_component(toggle, "UiButton")
        data = self.canvas()
        volume = self.element(data, "Volume")["components"]
        self.assertEqual(list(volume), ["UiSlider"])
        self.assertEqual(volume["UiSlider"]["fill"], ids.get_id(fill))
        self.assertEqual(list(self.element(data, "Mute")["components"]), ["UiToggle"])
        for text in ("'Volume' has both UiButton and UiSlider", "'Mute' has both UiButton and UiToggle"):
            self.assertTrue(any(text in w for w in self.warnings), (text, self.warnings))
        self.assertEqual(len(self.warnings), 2, self.warnings)  # paths set, fill set

    def test_empty_value_paths_and_a_slider_without_fill_warn(self):
        for name, comp in (("Heart", "UiShow"), ("Slider", "UiSlider"), ("Switch", "UiToggle")):
            components.add_component(self.empty(name), comp)
        self.canvas()
        for text in ("'Heart': UiShow.value is empty", "'Slider': UiSlider.value is empty",
                     "'Switch': UiToggle.value is empty", "'Slider': UiSlider has no fill"):
            self.assertTrue(any(text in w for w in self.warnings), (text, self.warnings))
        components.set_field(bpy.data.objects["Heart"], "UiShow", "value", "lives")
        self.canvas()
        self.assertFalse(any("'Heart'" in w for w in self.warnings), self.warnings)


class ExportFilesTest(UiFileTestCase):
    def setUp(self):
        super().setUp()
        self.text("Title Text", "Gem Island")
        self.hud = bpy.data.scenes.new("HUD")
        gameui.setup_canvas_scene(self.hud)
        self.text("Score", "Score {score}", scene=self.hud)

    def read(self, name) -> dict:
        return json.loads((self.ui_dir() / name).read_text(encoding="utf-8"))

    def test_canvas_files_index_and_revisions(self):
        components.add_component(self.hud.camera, "UiCanvas", {"kind": "hud", "open": "start"})
        result = gameui_export.export_ui_file(self.root)
        # Title was exported empty when setUp saved the file: its text makes rev 2.
        self.assertEqual({c["canvas"]: c["rev"] for c in result["canvases"]}, {"HUD": 1, "Title": 2})
        index = self.read("index.json")
        self.assertEqual(index["format"], "gamestudio.ui-index")
        self.assertEqual(index["canvases"]["HUD"], {"file": "HUD.ui.json", "rev": 1, "source": "ui/ui.blend",
                                                   "kind": "hud", "open": "start"})
        self.assertEqual(index["canvases"]["Title"]["kind"], "menu")
        hud = self.read("HUD.ui.json")
        self.assertEqual((hud["format"], hud["formatVersion"], hud["canvas"], hud["source"], hud["rev"]),
                         ("gamestudio.ui", 1, "HUD", "ui/ui.blend", 1))
        self.assertTrue(hud["exportedAt"].endswith("Z"))
        self.assertEqual(list(hud), ["format", "formatVersion", "canvas", "source", "rev", "exportedAt", "size",
                                     "settings", "elements", "assets"])
        # Nothing changed: the files stay (same rev); a change bumps only that canvas.
        again = gameui_export.export_ui_file(self.root)
        self.assertEqual([c["written"] for c in again["canvases"]], [False, False])
        bpy.data.objects["Score"].location.x = 1.0
        third = {c["canvas"]: c for c in gameui_export.export_ui_file(self.root)["canvases"]}
        self.assertEqual((third["HUD"]["rev"], third["HUD"]["written"]), (2, True))
        self.assertEqual((third["Title"]["rev"], third["Title"]["written"]), (2, False))
        self.assertEqual(self.read("index.json")["canvases"]["HUD"]["rev"], 2)

    def test_removed_and_renamed_canvases_leave_the_index(self):
        gameui_export.export_ui_file(self.root)
        self.hud.name = "Overlay"
        result = gameui_export.export_ui_file(self.root)
        self.assertEqual(result["removed"], ["HUD"])
        index = self.read("index.json")["canvases"]
        self.assertEqual(sorted(index), ["Overlay", "Title"])
        self.assertFalse((self.ui_dir() / "HUD.ui.json").exists())
        self.assertTrue((self.ui_dir() / "Overlay.ui.json").is_file())

    def test_the_project_registry_is_loaded_first_and_without_ui_components_nothing_is_written(self):
        title = bpy.data.objects["Title Text"]
        components.add_component(title, "UiButton", {"action": "close"})
        registry.set_registry(helpers.fixture_registry())  # loaded: a registry without the UI components
        result = gameui_export.export_ui_file(self.root)  # reads the project's, which has them
        self.assertEqual({c["canvas"] for c in result["canvases"]}, {"HUD", "Title"})
        data = self.read("Title.ui.json")
        self.assertEqual(self.element(data, "Title Text")["components"]["UiButton"]["action"], "close")
        # An engine whose registry has no UI components: refuse, rather than write component-less canvases.
        helpers.write_registry(self.root, helpers.fixture_registry())
        registry.set_registry(helpers.fixture_registry())
        title.location.x = 2.0
        with self.assertRaises(export.ExportError) as ctx:
            gameui_export.export_ui_file(self.root)
        self.assertIn("no game UI components", str(ctx.exception))
        self.assertEqual(self.read("Title.ui.json"), data)

    def test_names_differing_only_in_case_warn_and_get_their_own_files(self):
        lower = bpy.data.scenes.new("title")
        gameui.setup_canvas_scene(lower)
        self.text("Small", "small", scene=lower)
        result = gameui_export.export_ui_file(self.root)
        files = {c["canvas"]: c["file"] for c in result["canvases"]}
        self.assertEqual(len({f.lower() for f in files.values()}), 3, files)  # one file each, on Windows too
        self.assertEqual((self.read(files["Title"])["canvas"], self.read(files["title"])["canvas"]), ("Title", "title"))
        self.assertTrue(any("differ only in letter case" in w for w in result["warnings"]), result["warnings"])
        # Renaming a screen to another case keeps its export: on Windows the old file is the new one.
        bpy.data.scenes.remove(lower)
        self.hud.name = "hud"
        result = gameui_export.export_ui_file(self.root)
        self.assertEqual(sorted(result["removed"]), ["HUD", "title"])
        file_name = gameui_export.read_index(self.root)["canvases"]["hud"]["file"]
        self.assertEqual(self.read(file_name)["canvas"], "hud")

    def test_other_files_entries_are_kept_dropped_or_win(self):
        self.ui_dir().mkdir(parents=True, exist_ok=True)
        (self.root / "ui" / "a.blend").write_bytes(b"")   # sorts before ui.blend
        (self.root / "ui" / "z.blend").write_bytes(b"")   # sorts after
        for name in ("Shared.ui.json", "Kept.ui.json", "Gone.ui.json", "Title.ui.json"):
            (self.ui_dir() / name).write_text("{}", encoding="utf-8")
        entry = {"rev": 3, "kind": "menu", "open": "manual"}
        index = {"format": "gamestudio.ui-index", "formatVersion": 1, "canvases": {
            "HUD": {**entry, "file": "Shared.ui.json", "source": "ui/a.blend"},      # a.blend wins HUD
            "Kept": {**entry, "file": "Kept.ui.json", "source": "ui/z.blend"},
            "Gone": {**entry, "file": "Gone.ui.json", "source": "ui/deleted.blend"},
            "Title": {**entry, "file": "Title.ui.json", "source": "ui/z.blend"},     # ui.blend wins Title
        }}
        (self.ui_dir() / "index.json").write_text(json.dumps(index), encoding="utf-8")
        result = gameui_export.export_ui_file(self.root)
        skipped = {c["canvas"]: c for c in result["canvases"] if c.get("skipped")}
        self.assertEqual(list(skipped), ["HUD"])
        self.assertIn("ui/a.blend", skipped["HUD"]["skipped"])
        canvases = self.read("index.json")["canvases"]
        self.assertEqual(sorted(canvases), ["HUD", "Kept", "Title"])
        self.assertEqual(canvases["HUD"]["source"], "ui/a.blend")
        self.assertEqual(canvases["Kept"]["source"], "ui/z.blend")
        self.assertEqual(canvases["Title"]["source"], "ui/ui.blend")
        self.assertFalse((self.ui_dir() / "Gone.ui.json").exists())
        self.assertTrue((self.ui_dir() / "Kept.ui.json").is_file())
        self.assertEqual(self.read("Title.ui.json")["canvas"], "Title")
        self.assertIn("Gone", result["removed"])

    def test_component_asset_files_are_copied(self):
        sounds = self.root / "assets" / "sfx"
        sounds.mkdir(parents=True)
        (sounds / "click.ogg").write_bytes(b"OggS-test")
        button = self.empty("Button")
        components.add_component(button, "UiButton", {"sound": str(sounds / "click.ogg")})
        result = gameui_export.export_ui_file(self.root)
        file_name = next(c["file"] for c in result["canvases"] if c["canvas"] == "Title")
        data = self.read(file_name)
        self.assertEqual(self.element(data, "Button")["components"]["UiButton"]["sound"], "assets/sfx/click.ogg")
        url = data["assets"]["assets/sfx/click.ogg"]
        self.assertTrue(url.startswith("../files/assets/sfx/click.ogg?v="), url)
        self.assertEqual((self.ui_dir() / url.split("?")[0]).read_bytes(), b"OggS-test")
        # The builtin font is copied from Blender, not written into the project's assets/, with Inter's licence.
        self.assertTrue((project.export_dir(self.root) / "files" / "assets" / "fonts" / "Inter.woff2").is_file())
        self.assertTrue((project.export_dir(self.root) / "files" / "assets" / "fonts" / "Inter-OFL.txt").is_file())
        self.assertFalse((self.root / "assets" / "fonts").exists())

    def test_saving_exports(self):
        self.save()
        self.assertEqual(sorted(self.read("index.json")["canvases"]), ["HUD", "Title"])
        before = self.read("Title.ui.json")["rev"]
        self.save()
        self.assertEqual(self.read("Title.ui.json")["rev"], before)  # unchanged: not rewritten
        self.assertFalse(bpy.data.is_dirty)

    def test_stale_ui_files(self):
        self.assertEqual(gameui_export.stale_ui_files(self.root), [])  # saving exported it
        shutil.rmtree(self.ui_dir())
        self.assertEqual(gameui_export.stale_ui_files(self.root), [self.root / "ui" / "ui.blend"])
        gameui_export.export_ui_file(self.root)
        self.assertEqual(gameui_export.stale_ui_files(self.root), [])
        later = time.time() + 5
        os.utime(self.root / "ui" / "ui.blend", (later, later))
        self.assertEqual(gameui_export.stale_ui_files(self.root), [self.root / "ui" / "ui.blend"])
        # Saved again without changes (or after the live link wrote them): no canvas is rewritten, and
        # the file is still up to date, so Play does not export it in a background Blender every time.
        past = time.time() - 60
        for name in ("Title.ui.json", "HUD.ui.json"):
            os.utime(self.ui_dir() / name, (past, past))
        before = self.read("Title.ui.json")["rev"]
        self.save()
        self.assertEqual(self.read("Title.ui.json")["rev"], before)
        self.assertEqual(gameui_export.stale_ui_files(self.root), [])

    def test_a_project_opened_through_a_directory_link_has_ui_files(self):
        # A subst or mapped drive, a junction or a symlink: find_project_root resolves it.
        base = pathlib.Path(tempfile.mkdtemp(prefix="gs_link_", dir=helpers.tmp_base()))
        self.addCleanup(shutil.rmtree, base, True)
        link = base / "game"
        try:
            os.symlink(self.root, link, target_is_directory=True)
        except OSError:
            made = os.name == "nt" and subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.root)],
                                                      capture_output=True).returncode == 0
            if not made:
                self.skipTest("cannot make a directory link here")
        self.addCleanup(os.rmdir if os.name == "nt" else os.unlink, link)  # the link, not the project
        self.assertTrue(gameui.is_ui_file(link / "ui" / "ui.blend"))
        self.assertFalse(gameui.is_ui_file(link / "scenes" / "main.blend"))

    def test_level_export_refuses_a_ui_file(self):
        with self.assertRaises(export.ExportError) as ctx:
            export.export_scene(self.scene)
        self.assertIn("UI file", str(ctx.exception))


SHINE = {"name": "Shine", "description": "", "builtin": True, "source": "", "ui": True, "fields": [
    {"name": "color", "type": "color", "default": [1, 1, 1, 1]},
    {"name": "speed", "type": "float", "default": 1.0},
    {"name": "mask", "type": "asset", "default": "", "extensions": [".png"]}]}


def shader_ui_registry() -> dict:
    data = ui_registry()
    data["shaders"] = list(data.get("shaders") or []) + [json.loads(json.dumps(SHINE))]
    return data


def cube_mesh(name, size=0.5):
    s = size / 2
    mesh = bpy.data.meshes.new(name)
    verts = [(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    return mesh


def read_glb(path) -> dict:
    return export.read_glb(path)[0]


def root_nodes(gltf) -> list:
    nodes = gltf.get("nodes", [])
    return [nodes[i] for i in gltf["scenes"][gltf.get("scene", 0)]["nodes"]]


def child(gltf, node, name) -> dict:
    nodes = gltf["nodes"]
    return next(nodes[i] for i in node.get("children", []) if nodes[i].get("name") == name)


class ModelExportTest(UiFileTestCase):
    """§11.1: the objects under a UiModel empty go into <Canvas>.ui.glb, in the empty's space."""

    registry_data = shader_ui_registry()

    def setUp(self):
        super().setUp()
        self.model = self.empty("Trophy", at=self.at_px((400, 300)))
        self.model.rotation_euler.z = 0.5
        self.model.scale = (2.0, 2.0, 2.0)
        components.add_component(self.model, "UiModel", {"size": [300, 200], "view": "front"})
        self.cup = self.cube("Cup", at=(0.5, 0.0, 0.25), parent=self.model)
        self.handle = self.cube("Handle", size=0.1, at=(0.2, 0.0, 0.0), parent=self.cup)
        self.label = self.text("Label", "Trophy")

    def cube(self, name, size=0.5, at=(0, 0, 0), parent=None, scene=None):
        obj = self.link(bpy.data.objects.new(name, cube_mesh(name, size)), scene)
        obj.location = at
        obj.parent = parent
        return obj

    def read(self, name="Title.ui.json") -> dict:
        return json.loads((self.ui_dir() / name).read_text(encoding="utf-8"))

    def exporting(self):
        """Counts the glTF exporter runs (export._gltf)."""
        return mock.patch.object(export, "_gltf", wraps=export._gltf)

    def test_a_model_is_one_element_and_its_objects_are_not_elements(self):
        data = self.canvas()
        self.assertEqual(sorted(e["name"] for e in data["elements"]), ["Label", "Trophy"])
        element = self.element(data, "Trophy")
        self.assertEqual(element["type"], "model")
        self.assertEqual(element["model"], {"glb": "", "node": element["id"], "clips": []})
        self.assertEqual(element["components"]["UiModel"]["view"], "front")
        self.assertEqual(element["transform"]["scaleX"], 2.0)  # the element keeps the empty's 2D transform
        self.assertFalse(any("not exported" in w or "UiModel" in w for w in self.warnings), self.warnings)

    def test_the_glb_holds_each_model_in_its_empty_space(self):
        names = sorted(o.name for o in bpy.data.objects)
        gameui_export.export_ui_file(self.root)
        data = self.read()
        element = self.element(data, "Trophy")
        self.assertEqual(element["model"], {"glb": "Title.ui.glb", "node": element["id"], "clips": []})
        path = self.ui_dir() / "Title.ui.glb"
        files = {"Title.ui.glb": export.file_hash(path.read_bytes())}
        self.assertEqual(data["files"], files)
        self.assertEqual(list(data)[-2:], ["assets", "files"])
        self.assertEqual(gameui_export.read_index(self.root)["canvases"]["Title"]["files"], files)
        gltf = read_glb(path)
        roots = root_nodes(gltf)
        self.assertEqual([n["name"] for n in roots], [element["id"]])
        self.assertFalse({"translation", "rotation", "scale", "matrix"} & set(roots[0]))  # the empty's transform: left out
        cup = child(gltf, roots[0], "Cup")
        for got, want in zip(cup["translation"], (0.5, 0.25, 0.0)):  # Z-up -> Y-up
            self.assertAlmostEqual(got, want, places=5)
        self.assertNotIn("rotation", cup)
        self.assertNotIn("scale", cup)
        self.assertIn("mesh", cup)
        handle = child(gltf, cup, "Handle")
        self.assertAlmostEqual(handle["translation"][0], 0.2, places=5)
        self.assertNotIn("extras", cup)
        self.assertTrue(gltf["asset"]["extras"]["gs_signature"])
        # The user's file is as it was: no temporary scene or copies, the same parents.
        self.assertEqual(sorted(o.name for o in bpy.data.objects), names)
        self.assertNotIn(gameui_export.MODEL_SCENE, bpy.data.scenes)
        self.assertEqual((self.cup.parent, self.handle.parent), (self.model, self.cup))

    def test_the_glb_is_exported_again_only_when_the_model_changes(self):
        with self.exporting() as gltf:
            first = gameui_export.export_ui_file(self.root)["canvases"][0]
            self.assertEqual(gltf.call_count, 1)
            path = self.ui_dir() / "Title.ui.glb"
            stamp = path.stat().st_mtime_ns
            again = gameui_export.export_ui_file(self.root)["canvases"][0]
            self.assertEqual((gltf.call_count, again["written"], again["rev"]), (1, False, first["rev"]))
            self.assertEqual(path.stat().st_mtime_ns, stamp)
            # Not a model edit: the canvas changes, the GLB stays (the live link's fast path).
            self.label.location.x = 1.0
            moved = gameui_export.export_canvas(self.scene, self.root)
            self.assertEqual((gltf.call_count, moved["written"]), (1, True))
            self.assertEqual(moved["data"]["files"], self.read()["files"])
            hashes = [moved["data"]["files"]["Title.ui.glb"]]
            # Model edits: a vertex, a transform under the model, a material.
            edits = (lambda: self.cup.data.vertices[0].co.__setitem__(0, -0.4),
                     lambda: setattr(self.handle, "location", (0.3, 0.0, 0.0)),
                     lambda: self.cup.data.materials.append(principled_material("Gold", (1.0, 0.8, 0.1, 1.0))))
            for count, edit in enumerate(edits, start=2):
                edit()
                result = gameui_export.export_canvas(self.scene, self.root)
                self.assertEqual((gltf.call_count, result["written"]), (count, True))
                self.assertGreater(result["rev"], moved["rev"])
                hashes.append(result["data"]["files"]["Title.ui.glb"])
                self.assertEqual(gameui_export.read_index(self.root)["canvases"]["Title"]["files"],
                                 result["data"]["files"])
            self.assertEqual(len(set(hashes)), 4)
            # Moving the model's empty moves the element, not what is in the GLB.
            self.model.location.x += 1.0
            gameui_export.export_canvas(self.scene, self.root)
            self.assertEqual(gltf.call_count, 4)

    def test_a_new_session_reads_the_signature_from_the_glb(self):
        gameui_export.export_ui_file(self.root)
        data = self.read()
        gameui_export.clear_caches()
        with self.exporting() as gltf:
            gameui_export.export_ui_file(self.root)
            self.assertEqual(gltf.call_count, 0)
            self.assertEqual(self.read()["files"], data["files"])
            (self.ui_dir() / "Title.ui.glb").unlink()
            gameui_export.export_ui_file(self.root)
            self.assertEqual(gltf.call_count, 1)
        self.assertEqual(self.read()["files"], data["files"])  # the same bytes again
        self.assertTrue((self.ui_dir() / "Title.ui.glb").is_file())

    def test_clips_of_object_and_armature_actions(self):
        from gamestudio import kits

        hero, body = kits.create("character_rigged", {"name": "Hero"})
        for obj in (hero, body, *hero.children_recursive):
            if obj.name not in self.scene.objects:
                self.scene.collection.objects.link(obj)
        hero.parent = self.model
        keys(self.cup, "rotation_euler", 2, {1: 0.0, 30: math.tau})
        predicted = self.element(self.canvas(), "Trophy")["model"]["clips"]
        gameui_export.export_ui_file(self.root)
        clips = self.element(self.read(), "Trophy")["model"]["clips"]
        gltf = read_glb(self.ui_dir() / "Title.ui.glb")
        self.assertEqual(sorted(clips), sorted(a["name"] for a in gltf["animations"]))
        self.assertEqual(sorted(clips), sorted(predicted))
        self.assertIn(self.cup.animation_data.action.name, clips)
        self.assertGreater(len(clips), 3)
        self.assertEqual(len(gltf.get("skins", [])), 1)
        root = root_nodes(gltf)[0]
        self.assertEqual(sorted(gltf["nodes"][i]["name"] for i in root["children"]), ["Cup", "Hero"])
        self.assertNotIn(gameui_export.MODEL_SCENE, bpy.data.scenes)
        self.assertEqual([m.object for m in body.modifiers if m.type == "ARMATURE"], [hero])  # not the copy's

    def test_collection_instances_of_assets(self):
        gem = bpy.data.collections.new("Gem")
        gem.objects.link(bpy.data.objects.new("GemMesh", cube_mesh("GemMesh", 0.3)))
        inst = self.empty("GemInstance", parent=self.model)
        inst.instance_type, inst.instance_collection = "COLLECTION", gem
        # An asset dropped in the canvas with UiModel on it is a model of its own.
        badge = self.empty("Badge", at=self.at_px((1400, 300)))
        badge.instance_type, badge.instance_collection = "COLLECTION", gem
        components.add_component(badge, "UiModel")
        result = gameui_export.export_ui_file(self.root)
        self.assertFalse(any("instances a collection" in w for w in result["warnings"]), result["warnings"])
        data = self.read()
        ids_by_name = {e["name"]: e["id"] for e in data["elements"]}
        self.assertEqual(sorted(ids_by_name), ["Badge", "Label", "Trophy"])
        gltf = read_glb(self.ui_dir() / "Title.ui.glb")
        roots = {n["name"]: n for n in root_nodes(gltf)}
        self.assertEqual(sorted(roots), sorted((ids_by_name["Badge"], ids_by_name["Trophy"])))
        instance = child(gltf, roots[ids_by_name["Trophy"]], "GemInstance")
        self.assertIn("mesh", child(gltf, instance, "GemMesh"))
        self.assertIn("mesh", child(gltf, roots[ids_by_name["Badge"]], "GemMesh"))
        # Editing the asset exports the GLB again.
        with self.exporting() as gltf_runs:
            gem.objects["GemMesh"].location.z = 0.5
            gameui_export.export_ui_file(self.root)
            self.assertEqual(gltf_runs.call_count, 1)

    def test_game_shaders_of_model_materials(self):
        from gamestudio.core import shaders

        gold = principled_material("Gold", (1.0, 0.8, 0.1, 1.0))
        masks = self.root / "assets" / "ui"
        masks.mkdir(parents=True)
        (masks / "mask.png").write_bytes(b"\x89PNG-test")
        shaders.set_shader(gold, "Shine", {"speed": 2.0, "mask": str(masks / "mask.png")})
        self.cup.data.materials.append(gold)
        gameui_export.export_ui_file(self.root)
        data = self.read()
        self.assertEqual(data["materials"], {"Gold": {"shader": "Shine", "values": {
            "color": [1.0, 1.0, 1.0, 1.0], "speed": 2.0, "mask": "assets/ui/mask.png"}}})
        self.assertEqual(list(data)[-3:], ["materials", "assets", "files"])
        self.assertTrue(data["assets"]["assets/ui/mask.png"].startswith("../files/assets/ui/mask.png?v="))
        # Shader values are the canvas's: changing them does not export the GLB again.
        with self.exporting() as gltf:
            shaders.set_values(gold, {"speed": 3.0})
            result = gameui_export.export_canvas(self.scene, self.root)
            self.assertEqual(gltf.call_count, 0)
        self.assertEqual(result["data"]["materials"]["Gold"]["values"]["speed"], 3.0)

    def test_a_canvas_without_models_has_no_glb(self):
        gameui_export.export_ui_file(self.root)
        self.assertTrue((self.ui_dir() / "Title.ui.glb").is_file())
        components.remove_component(self.model, "UiModel")
        gameui_export.export_ui_file(self.root)
        data = self.read()
        self.assertNotIn("files", data)
        self.assertNotIn("files", gameui_export.read_index(self.root)["canvases"]["Title"])
        self.assertFalse((self.ui_dir() / "Title.ui.glb").exists())
        self.assertEqual(self.element(data, "Cup")["type"], "mesh")  # its objects are elements again
        # A removed canvas takes its GLB along.
        components.add_component(self.model, "UiModel")
        hud = bpy.data.scenes.new("HUD")
        gameui.setup_canvas_scene(hud)
        gameui_export.export_ui_file(self.root)
        self.assertTrue((self.ui_dir() / "Title.ui.glb").is_file())
        self.assertTrue(gameui_export.remove_canvas("Title", self.root))
        self.assertFalse((self.ui_dir() / "Title.ui.glb").exists())

    def test_a_model_inside_a_model_is_part_of_it(self):
        inner = self.empty("Inner", parent=self.model)
        components.add_component(inner, "UiModel")
        self.cube("Gem", parent=inner)
        data = self.canvas()
        self.assertEqual(sorted(e["name"] for e in data["elements"]), ["Label", "Trophy"])
        self.assertTrue(any("inside the model 'Trophy'" in w for w in self.warnings), self.warnings)
        empty = self.empty("Empty Model", at=self.at_px((1500, 800)))
        components.add_component(empty, "UiModel")
        self.canvas()
        self.assertTrue(any("'Empty Model' has UiModel but no objects" in w for w in self.warnings), self.warnings)


class UiMeshShaderTest(UiFileTestCase):
    """§11.4: a UI mesh's material with a game shader."""

    registry_data = shader_ui_registry()

    def test_mesh_material_shader_and_its_assets(self):
        from gamestudio.core import shaders

        masks = self.root / "assets" / "ui"
        masks.mkdir(parents=True)
        (masks / "mask.png").write_bytes(b"\x89PNG-test")
        mat = emission_material("Button Shine", (0.2, 0.4, 1.0, 1.0))
        shaders.set_shader(mat, "Shine", {"color": [1, 0.9, 0.5, 1], "mask": str(masks / "mask.png")})
        self.plane("Button", material=mat)
        self.plane("Plain", at=(3, 0, 0), material=emission_material("Flat", (1, 1, 1, 1)))
        gameui_export.export_ui_file(self.root)
        data = json.loads((self.ui_dir() / "Title.ui.json").read_text(encoding="utf-8"))
        material = self.element(data, "Button")["mesh"]["material"]
        self.assertEqual(material["shader"], {"name": "Shine", "values": {
            "color": [1.0, 0.9, 0.5, 1.0], "speed": 1.0, "mask": "assets/ui/mask.png"}})
        self.assertEqual(material["color"], [0.2, 0.4, 1.0, 1.0])
        self.assertNotIn("shader", self.element(data, "Plain")["mesh"]["material"])
        self.assertIn("assets/ui/mask.png", data["assets"])
        self.assertNotIn("materials", data)  # flat UI meshes carry their shader themselves

    def test_unknown_shader_warns(self):
        mat = emission_material("Odd", (1, 1, 1, 1))
        mat.gs.shader = "Wobble"
        self.plane("Panel", material=mat)
        element = self.element(self.canvas(), "Panel")
        self.assertEqual(element["mesh"]["material"]["shader"], {"name": "Wobble", "values": {}})
        self.assertTrue(any('game shader "Wobble"' in w for w in self.warnings), self.warnings)


class LocalesExportTest(UiFileTestCase):
    """§11.3: ui/locales/<code>.json are checked, copied to the export and listed in the index."""

    def table(self, code, data):
        folder = self.root / "ui" / "locales"
        folder.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        (folder / f"{code}.json").write_text(text, encoding="utf-8")

    def exported(self, code):
        path = self.ui_dir() / "locales" / f"{code}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def test_tables_are_checked_copied_and_indexed(self):
        self.table("fr", {"name": "Français", "strings": {"Play": "Jouer", "Score {score}": "Score {score}",
                                                         "Lives": 3}})
        self.table("es", {"strings": {"Play": "Jugar"}})
        self.table("de", "{not json")
        self.table("notes", {"name": "Notes", "strings": {}})
        result = gameui_export.export_ui_file(self.root)
        self.assertEqual(result["locales"], ["es", "fr"])
        index = json.loads((self.ui_dir() / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["sourceLocale"], "en")
        self.assertEqual(index["locales"], {"es": {"file": "locales/es.json", "name": "es"},
                                            "fr": {"file": "locales/fr.json", "name": "Français"}})
        self.assertEqual(self.exported("fr"), {"name": "Français", "strings": {"Play": "Jouer",
                                                                                "Score {score}": "Score {score}"}})
        self.assertEqual(self.exported("es"), {"name": "es", "strings": {"Play": "Jugar"}})
        self.assertIsNone(self.exported("de"))
        self.assertIsNone(self.exported("notes"))
        warnings = "\n".join(result["warnings"])
        for text in ("ui/locales/de.json is not valid JSON", 'ui/locales/es.json has no "name"',
                     "'notes' is not a language code", "1 translation(s) are not text"):
            self.assertIn(text, warnings)
        # Canvas exports (the live link) keep the index's translations.
        self.text("Play")
        gameui_export.export_canvas(self.scene, self.root)
        self.assertEqual(gameui_export.read_index(self.root)["locales"], index["locales"])

    def test_source_locale_changes_and_removed_tables(self):
        self.table("fr", {"name": "Français", "strings": {}})
        self.assertTrue(gameui_export.export_locales(self.root)["changed"])
        self.assertFalse(gameui_export.export_locales(self.root)["changed"])
        config = json.loads((self.root / "gamestudio.json").read_text(encoding="utf-8"))
        config["locale"] = "de"
        (self.root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
        os.utime(self.root / "gamestudio.json", (time.time() + 5, time.time() + 5))
        result = gameui_export.export_locales(self.root)
        self.assertEqual((result["sourceLocale"], result["changed"]), ("de", True))
        self.assertEqual(gameui_export.read_index(self.root)["sourceLocale"], "de")
        (self.root / "ui" / "locales" / "fr.json").unlink()
        result = gameui_export.export_locales(self.root)
        self.assertEqual((result["locales"], result["changed"]), ({}, True))
        self.assertIsNone(self.exported("fr"))
        self.assertEqual(gameui_export.read_index(self.root)["locales"], {})
        self.assertEqual(gameui_export.exported_locales(self.root),
                         {"sourceLocale": "de", "locales": {}, "tables": {}})

    def test_play_exports_changed_tables(self):
        gameui_export.export_ui_file(self.root)
        self.table("fr", {"name": "Français", "strings": {"Play": "Jouer"}})
        gameui_export.export_stale(self.root)
        self.assertEqual(self.exported("fr")["strings"], {"Play": "Jouer"})

    def test_projects_without_game_ui_get_no_index(self):
        shutil.rmtree(self.ui_dir(), ignore_errors=True)
        (self.root / "ui" / "ui.blend").unlink()
        self.assertFalse(gameui_export.export_locales(self.root)["changed"])
        self.assertFalse((self.ui_dir() / "index.json").exists())


class PlayFromUiFileTest(UiFileTestCase):
    def test_play_uses_the_existing_start_level_export(self):
        out = project.export_dir(self.root)
        out.mkdir(parents=True, exist_ok=True)
        manifest = {"format": "gamestudio.scene", "scene": "main", "rev": 7, "entities": [{"id": "gs_000000000001"}]}
        (out / "main.scene.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.text("Hello")
        result = gameui_export.play_export(self.root)
        self.assertEqual((result["scene"], result["rev"], result["entities"], result["tier"]), ("main", 7, 1, "reuse"))
        self.assertEqual([c["canvas"] for c in result["ui"]["canvases"]], ["Title"])
        self.assertEqual(export.scene_load_message(result)["url"], "/__gs/export/main.scene.json")
        self.assertTrue((self.ui_dir() / "Title.ui.json").is_file())

    def test_missing_start_level_is_an_error(self):
        with self.assertRaises(export.ExportError) as ctx:
            gameui_export.play_level(self.root)
        self.assertIn("scenes/main.blend", str(ctx.exception))


class PlayInBlenderFromALevelTest(helpers.ProjectTestCase):
    """The bridge's export_scene (MCP playtest) and play_in_blender export stale UI files from a level, as
    Blender's Play does (§4); failures come back as ui_errors and never stop them."""

    def test_stale_ui_files_are_exported_and_their_errors_reported(self):
        from unittest import mock

        from gamestudio.bridge import methods
        from gamestudio.core import play

        level = {"scene": "main", "rev": 3, "entities": 1, "tier": "reuse"}
        stale = {"ui": [], "errors": [{"file": "ui/ui.blend", "error": "Blender crashed"}]}
        with mock.patch.object(export, "export_scene", return_value=level), \
                mock.patch.object(gameui_export, "export_stale", return_value=stale) as export_stale, \
                mock.patch.object(play, "request_play", return_value=False), \
                mock.patch.object(play, "status", return_value=""):
            played = methods.play_in_blender({})
            exported = methods.export_scene_method({})
            stale["errors"] = []
            clean = methods.export_scene_method({})
        self.assertEqual(export_stale.call_args_list, [mock.call(self.root)] * 3)
        self.assertEqual(played["ui_errors"], [{"file": "ui/ui.blend", "error": "Blender crashed"}])
        self.assertEqual((played["export"]["scene"], played["sent_now"]), ("main", False))
        self.assertEqual(exported["ui_errors"], played["ui_errors"])
        self.assertEqual(exported["scene_load"]["scene"], "main")
        self.assertNotIn("ui_errors", clean)


class BackgroundExportTest(helpers.ProjectTestCase):
    """Export All and Play export other files in a background Blender (a few seconds each)."""

    registry_data = ui_registry()

    def test_export_all_and_play_export_levels_and_ui_files_in_the_background(self):
        (self.root / "scenes").mkdir()
        (self.root / "ui").mkdir()
        level = bpy.context.scene
        level.name = "main"
        helpers.new_object("Ground", helpers.new_mesh())
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "scenes" / "main.blend"))
        # The second UI file is exported in the background; the open one here.
        bpy.context.scene.name = "Pause"
        bpy.data.objects.remove(bpy.data.objects["Ground"])
        gameui.setup_canvas_scene(bpy.context.scene)
        model = helpers.new_object("Spinner")  # a 3D model (§11.1): its GLB is written in the background too
        components.add_component(model, "UiModel")
        helpers.new_object("Blade", cube_mesh("Blade"), parent=model)
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "ui" / "extra.blend"))
        bpy.data.objects.remove(bpy.data.objects["Blade"])
        bpy.data.objects.remove(model)
        bpy.context.scene.name = "Title"
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "ui" / "ui.blend"))
        result = gameui_export.play_export(self.root)
        self.assertEqual((result["scene"], result["tier"]), ("main", "full"))
        self.assertTrue((project.export_dir(self.root) / "main.scene.json").is_file())
        self.assertEqual(result["ui"]["errors"], [])
        canvases = gameui_export.read_index(self.root)["canvases"]
        self.assertEqual({n: e["source"] for n, e in canvases.items()},
                         {"Pause": "ui/extra.blend", "Title": "ui/ui.blend"})
        glb = gameui.export_dir(self.root) / "Pause.ui.glb"
        self.assertEqual(canvases["Pause"]["files"], {"Pause.ui.glb": export.file_hash(glb.read_bytes())})
        self.assertEqual([child(read_glb(glb), n, "Blade")["name"] for n in root_nodes(read_glb(glb))], ["Blade"])
        everything = export.export_all()
        self.assertEqual(everything["errors"], [])
        self.assertEqual([s["scene"] for s in everything["scenes"]], ["main"])
        self.assertEqual(sorted(u["file"] for u in everything["ui"]), ["ui/extra.blend", "ui/ui.blend"])
