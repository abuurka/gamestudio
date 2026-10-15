# SPDX-License-Identifier: GPL-3.0-or-later
"""Smaller builds (core/compression.py): gamestudio.json "compression", WebP textures from the export,
the per-file size report and export_scene's `compress` measurement of the build's meshopt GLBs."""

import json
import os
import unittest

import bpy
import numpy as np

from gamestudio.core import compression, export, fidelity, toolchain

import helpers
from test_bake import cube, principled
from test_export_fidelity import FidelityTestCase, read, save_image


def webp_pixels(data: bytes, tmp, name):
    """The pixels of encoded image bytes, read back through Blender."""
    path = tmp / name
    path.write_bytes(data)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = "Non-Color"
        return np.array(image.pixels[:], dtype=np.float32)
    finally:
        bpy.data.images.remove(image)


class CompressionTest(FidelityTestCase):
    def set_compression(self, value):
        path = self.root / "gamestudio.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["compression"] = value
        path.write_text(json.dumps(config), encoding="utf-8")
        stamp = path.stat().st_mtime + 2 * (1 + getattr(self, "_writes", 0))
        self._writes = getattr(self, "_writes", 0) + 1
        os.utime(path, (stamp, stamp))  # read_config caches by mtime

    def test_settings(self):
        self.assertEqual(compression.settings(self.root), {"geometry": "meshopt", "textures": "none"})
        self.set_compression({"geometry": "none", "textures": "webp"})
        self.assertEqual(compression.settings(self.root), {"geometry": "none", "textures": "webp"})
        settings = export.export_settings(self.root)
        self.assertTrue(settings["webp"] and settings["webpData"])
        self.set_compression({"geometry": "draco", "textures": "ktx2"})
        with self.assertLogs("gamestudio.compression", "WARNING") as logs:
            self.assertEqual(compression.settings(self.root), compression.DEFAULTS)
        self.assertEqual(len(logs.output), 2)
        self.assertFalse(export.export_settings(self.root)["webp"])

    def test_webp_textures_colour_maps_lossy_and_data_maps_lossless(self):
        color = save_image(self.root, "Albedo", 256, 256)
        normal = save_image(self.root, "Bumps", 256, 256)
        normal.colorspace_settings.name = "Non-Color"
        mat = bpy.data.materials.new("Painted")
        tree = mat.node_tree
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = color
        tree.links.new(tex.outputs["Color"], principled(mat).inputs["Base Color"])
        ntex = tree.nodes.new("ShaderNodeTexImage")
        ntex.image = normal
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(ntex.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], principled(mat).inputs["Normal"])
        cube("Crate", mat)
        self.configure(bake="off")

        plain, plain_glb = read(export.export_scene(bake="off")["glb"])
        plain_images = {i["name"]: plain_glb.view(i["bufferView"]) for i in plain["images"]}
        self.set_compression({"textures": "webp"})
        result = export.export_scene(bake="off")
        gltf, glb = read(result["glb"])
        images = {i["name"]: i for i in gltf["images"]}
        self.assertEqual(images["Albedo"]["mimeType"], "image/webp")
        self.assertEqual(images["Bumps"]["mimeType"], "image/webp")
        self.assertIn(fidelity.WEBP, gltf["extensionsRequired"])
        self.assertTrue(all(fidelity.WEBP in (t.get("extensions") or {}) for t in gltf["textures"]))
        # The normal map is lossless: the same pixels as the PNG.
        tmp = self.root / "pixels"
        tmp.mkdir()
        np.testing.assert_array_equal(webp_pixels(glb.view(images["Bumps"]["bufferView"]), tmp, "a.webp"),
                                      webp_pixels(plain_images["Bumps"], tmp, "b.png"))
        by_name = {t["name"]: t for t in result["textures"]}
        self.assertLess(by_name["Albedo"]["bytes_after"], by_name["Albedo"]["bytes_before"])

    def test_size_report_per_file_and_compressed_build_sizes(self):
        for i in range(3):
            obj = cube(f"Box{i}", bpy.data.materials.new(f"M{i}"))
            obj.location.x = i * 3
            obj.modifiers.new("Smooth", "SUBSURF").levels = 3  # enough vertices for the codec to pay off
        result = export.export_scene(bake="off")
        per_file = result["size"]["per_file"]
        self.assertEqual([i["file"] for i in per_file], ["main.glb"])
        self.assertEqual(per_file[0]["bytes"], os.path.getsize(result["glb"]))
        self.assertGreater(per_file[0]["exported"], 0)  # what Blender's glTF exporter wrote
        self.assertNotIn("compressed", result["size"])

        if toolchain.node_executable(self.root) is None or compression.compressor(self.root) is None:
            self.skipTest("no Node.js or no @gamestudio/devserver for the compressor")
        before = os.path.getsize(result["glb"])
        result = export.export_scene(bake="off", compress=True)
        measured = result["size"]["compressed"]
        self.assertNotIn("error", measured, measured)
        self.assertEqual(measured["geometry"], "meshopt")
        self.assertEqual([f["file"] for f in measured["files"]], ["main.glb"])
        self.assertLess(measured["bytes_after"], measured["bytes_before"])
        self.assertEqual(result["size"]["per_file"][0]["compressed"], measured["bytes_after"])
        self.assertEqual(os.path.getsize(result["glb"]), before)  # a dry run: the export stays plain
        gltf, _glb = read(result["glb"])
        self.assertNotIn("EXT_meshopt_compression", gltf.get("extensionsUsed", []))
        json.dumps(result)

        self.set_compression({"geometry": "none"})
        measured = export.export_scene(bake="off", compress=True)["size"]["compressed"]
        self.assertEqual(measured["bytes_after"], measured["bytes_before"])


if __name__ == "__main__":
    unittest.main()
