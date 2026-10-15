# SPDX-License-Identifier: GPL-3.0-or-later
"""Game shaders and tone mapping in the export (§4.2 "materials", settings.toneMapping and settings.exposure,
§4.5, §4.8): the materials the GLBs draw with and their shader values, shader asset files, the warnings, and
the journal's tiers for shader and Color Management changes."""

import copy
import json
from unittest import mock

import bpy

from gamestudio.core import export, ids, shaders

import helpers
from test_bake import cube, noise_ramp, principled

SHADERS = [
    {"name": "Triplanar", "description": "", "builtin": False, "source": "src/shaders/triplanar.ts", "fields": [
        {"name": "diffuse", "type": "asset", "default": "", "extensions": [".png"]},
        {"name": "tile", "type": "float", "default": 3.5, "min": 0.01},
        {"name": "wet", "type": "float", "default": 0}]},
    {"name": "Foliage", "description": "", "builtin": False, "source": "src/shaders/foliage.ts", "fields": [
        {"name": "sway", "type": "float", "default": 0.5}]},
]


def shader_registry() -> dict:
    data = helpers.fixture_registry()
    data["shaders"] = copy.deepcopy(SHADERS)
    return data


def read(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def mesh_with(name, mat, collection=None, location=(0, 0, 0)):
    mesh = helpers.new_mesh(f"{name}Mesh")
    mesh.materials.append(mat)
    return helpers.new_object(name, mesh, location=location, collection=collection)


def glb_materials(path) -> list:
    return [m.get("name") for m in export.read_glb(path)[0].get("materials", [])]


class ShaderExportCase(helpers.ProjectTestCase):
    registry_data = shader_registry()

    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        self.export_dir = self.root / ".gamestudio" / "export"

    def manifest(self) -> dict:
        return read(self.export_dir / "main.scene.json")


class MaterialsTest(ShaderExportCase):
    def test_materials_the_glbs_draw_with(self):
        (self.root / "assets" / "scans").mkdir(parents=True)
        (self.root / "assets" / "scans" / "sand.png").write_bytes(b"\x89PNG sand")
        sand = bpy.data.materials.new("GS_Sand")
        shaders.set_shader(sand, "Triplanar", {"diffuse": "assets/scans/sand.png", "wet": 0.5})
        leaves = bpy.data.materials.new("Leaves")
        shaders.set_shader(leaves, "Foliage", {"sway": 2})
        shaders.set_shader(bpy.data.materials.new("Unused"), "Foliage")  # no mesh draws with it
        mesh_with("Ground", sand)
        mesh_with("Wall", bpy.data.materials.new("Plain"))  # no game shader
        tree = bpy.data.collections.new("Tree")
        export.set_prefab(tree)
        mesh_with("Crown", leaves, collection=tree)

        result = export.export_scene()
        manifest = read(result["manifest"])
        self.assertEqual(manifest["materials"], {
            "GS_Sand": {"shader": "Triplanar", "values": {"diffuse": "assets/scans/sand.png", "tile": 3.5, "wet": 0.5}},
            "Leaves": {"shader": "Foliage", "values": {"sway": 2.0}},
        })
        keys = list(manifest)
        self.assertEqual(keys[keys.index("materials") + 1], "assets")
        self.assertEqual(result["materials"], 2)
        self.assertEqual(result["warnings"], [])
        # Keyed by the names in the GLBs: the ground's in the scene GLB, the prefab's in the prefab GLB.
        self.assertEqual(sorted(glb_materials(result["glb"])), ["GS_Sand", "Plain"])
        self.assertEqual(glb_materials(self.export_dir / "main.prefabs.glb"), ["Leaves"])
        # Asset values are copied like component asset fields (§4.5).
        self.assertTrue(manifest["assets"]["assets/scans/sand.png"].startswith("files/assets/scans/sand.png?v="))
        self.assertEqual((self.export_dir / "files" / "assets" / "scans" / "sand.png").read_bytes(), b"\x89PNG sand")

    def test_no_section_without_shaders_and_unknown_shaders_warn(self):
        mesh_with("Ground", bpy.data.materials.new("Plain"))
        result = export.export_scene()
        self.assertNotIn("materials", read(result["manifest"]))
        self.assertEqual(result["materials"], 0)

        water = bpy.data.materials.new("GS_Water")
        water.gs.shader = "Water"  # a shader whose file was deleted since
        mesh_with("Lake", water, location=(0, 4, 0))
        result = export.export_scene()
        self.assertEqual(read(result["manifest"])["materials"], {"GS_Water": {"shader": "Water", "values": {}}})
        self.assertIn('Material "GS_Water" uses game shader "Water", which no src/shaders file defines',
                      result["warnings"])

    def test_baked_copies_keep_the_name_and_the_shader(self):
        lava = noise_ramp("Lava")
        shaders.set_shader(lava, "Foliage", {"sway": 1.5})
        cube("Rock", lava)
        result = export.export_scene(bake="auto")
        self.assertEqual(result["bake"]["baked"], 1)
        gltf, _chunks = export.read_glb(result["glb"])
        self.assertEqual([m["name"] for m in gltf["materials"]], ["Lava"])
        self.assertIn("baseColorTexture", gltf["materials"][0]["pbrMetallicRoughness"])  # the baked copy
        self.assertEqual(read(result["manifest"])["materials"],
                         {"Lava": {"shader": "Foliage", "values": {"sway": 1.5}}})
        self.assertEqual([m.name for m in bpy.data.materials if m.name.startswith("Lava")], ["Lava"])  # copy gone

    def test_a_local_and_a_linked_material_with_one_name(self):
        library = self.root / "assets" / "rocks.blend"
        library.parent.mkdir()
        rock = bpy.data.materials.new("Rock")
        shaders.set_shader(rock, "Triplanar", {"tile": 5})
        boulder = mesh_with("Boulder", rock)
        ids.ensure_id(boulder)  # as a GameStudio level stores it (the journal knows linked objects by it)
        bpy.data.libraries.write(str(library), {boulder})
        helpers.reset_scene()
        bpy.context.scene.name = "main"
        with bpy.data.libraries.load(str(library), link=True) as (_src, dst):
            dst.objects = ["Boulder"]
        bpy.context.scene.collection.objects.link(dst.objects[0])
        local = bpy.data.materials.new("Rock")
        mesh_with("Pebble", local, location=(3, 0, 0))

        result = export.export_scene()
        self.assertEqual(set(glb_materials(result["glb"])), {"Rock"})
        self.assertNotIn("materials", read(result["manifest"]))  # the local material's settings: none
        warning = ("Materials 'Rock', 'Rock [rocks.blend]' are all exported as 'Rock' but their game shader "
                   "settings differ: the game applies those of 'Rock' to all of them")
        self.assertTrue(any(w.startswith(warning) for w in result["warnings"]), result["warnings"])

        shaders.set_shader(local, "Triplanar", {"tile": 5})  # now they agree
        result = export.export_scene()
        self.assertEqual(result["tier"], "layout", result.get("reason"))
        self.assertEqual(read(result["manifest"])["materials"]["Rock"],
                         {"shader": "Triplanar", "values": {"diffuse": "", "tile": 5.0, "wet": 0.0}})
        self.assertFalse(any(w.startswith("Materials ") for w in result["warnings"]), result["warnings"])


class ToneMappingTest(ShaderExportCase):
    def test_view_transforms(self):
        scene = bpy.context.scene
        view = scene.view_settings
        no_tone_mapping = "has no three.js equivalent; the game uses no tone mapping"
        for transform, mapping, warning in (
                ("Khronos PBR Neutral", "neutral", None), ("AgX", "agx", None), ("Standard", "none", None),
                ("ACES 1.3", "aces", None),
                ("Filmic", "aces", "View transform 'Filmic': Filmic has no three.js equivalent; the game uses ACES"),
                ("Filmic Log", "aces", "Filmic has no three.js equivalent; the game uses ACES"),
                ("ACES 2.0", "aces", "View transform 'ACES 2.0' has no three.js equivalent; the game uses ACES 1.x"),
                ("False Color", "none", no_tone_mapping), ("Raw", "none", no_tone_mapping)):
            with self.subTest(transform):
                view.view_transform = transform
                view.look = "None"
                if warning is None:
                    with self.assertNoLogs("gamestudio.export", "WARNING"):
                        got = export._tone_mapping(scene)
                else:
                    with self.assertLogs("gamestudio.export", "WARNING") as logs:
                        got = export._tone_mapping(scene)
                    self.assertTrue(any(warning in line for line in logs.output), logs.output)
                self.assertEqual(got, (mapping, 0.0))
        view.view_transform = "AgX"
        view.look = "AgX - Punchy"
        view.exposure = -1.25
        with self.assertLogs("gamestudio.export", "WARNING") as logs:
            self.assertEqual(export._tone_mapping(scene), ("agx", -1.25))
        self.assertTrue(any("Look 'AgX - Punchy' (Color Management) is not carried to the game" in line
                            for line in logs.output), logs.output)

    def test_settings_in_the_manifest(self):
        helpers.new_object("Ground", helpers.new_mesh())
        settings = read(export.export_scene()["manifest"])["settings"]
        self.assertEqual((settings["toneMapping"], settings["exposure"]), ("agx", 0.0))  # a new scene's
        bpy.context.scene.view_settings.view_transform = "Filmic"
        result = export.export_scene()
        self.assertEqual(read(result["manifest"])["settings"]["toneMapping"], "aces")
        self.assertIn("View transform 'Filmic': Filmic has no three.js equivalent; the game uses ACES",
                      result["warnings"])


class ShaderJournalTest(ShaderExportCase):
    def setUp(self):
        super().setUp()
        self.sand = bpy.data.materials.new("GS_Sand")
        self.ground = mesh_with("Ground", self.sand)
        mesh_with("Wall", bpy.data.materials.new("Plain"), location=(0, 3, 0))
        bpy.context.view_layer.update()

    def glbs(self) -> dict:
        return {p.name: p.read_bytes() for p in self.export_dir.glob("*.glb")}

    def export_without_exporter(self, tier: str) -> dict:
        """export_scene(), which must be a reuse or layout export (the glTF exporter does not run)."""
        with mock.patch.object(export, "_gltf", wraps=export._gltf) as gltf:
            result = export.export_scene()
        self.assertEqual(result["tier"], tier, result.get("reason"))
        self.assertFalse(gltf.called)
        return result

    def edit_in_ui(self, mat):
        """What editing a material's game shader in the Properties editor also does: tag the material."""
        mat.update_tag()
        bpy.context.view_layer.update()

    def test_shader_settings_rewrite_only_the_manifest(self):
        export.export_scene()
        self.assertNotIn("materials", self.manifest())
        glbs = self.glbs()
        shaders.set_shader(self.sand, "Triplanar", {"wet": 0.25})  # a material gets a shader
        self.edit_in_ui(self.sand)
        result = self.export_without_exporter("layout")
        self.assertEqual(self.glbs(), glbs)  # byte-identical
        self.assertEqual(self.manifest()["materials"],
                         {"GS_Sand": {"shader": "Triplanar", "values": {"diffuse": "", "tile": 3.5, "wet": 0.25}}})
        self.assertEqual(result["materials"], 1)

        (self.root / "assets").mkdir()
        (self.root / "assets" / "sand.png").write_bytes(b"\x89PNG")
        shaders.set_values(self.sand, {"tile": 2, "diffuse": "assets/sand.png"})  # through the API: no update
        self.export_without_exporter("layout")
        manifest = self.manifest()
        self.assertEqual(manifest["materials"]["GS_Sand"]["values"]["tile"], 2.0)
        self.assertTrue(manifest["assets"]["assets/sand.png"].startswith("files/assets/sand.png?v="))
        self.export_without_exporter("reuse")

        shaders.set_shader(self.sand, None)
        self.edit_in_ui(self.sand)
        result = self.export_without_exporter("layout")
        self.assertNotIn("materials", self.manifest())
        self.assertEqual((result["materials"], self.manifest()["assets"]), (0, {}))
        self.assertEqual(self.glbs(), glbs)

    def test_material_edits_still_need_a_full_export(self):
        shaders.set_shader(self.sand, "Triplanar")
        export.export_scene()

        def edit_nodes():
            principled(self.sand).inputs["Roughness"].default_value = 0.2

        def edit_nodes_and_shader():
            shaders.set_values(self.sand, {"wet": 1})
            principled(self.sand).inputs["Metallic"].default_value = 1.0

        def blend_mode():
            self.sand.surface_render_method = "BLENDED"
            self.edit_in_ui(self.sand)

        def rename():
            self.sand.name = "GS_Dune"

        for label, change in (("nodes", edit_nodes), ("nodes and shader", edit_nodes_and_shader),
                              ("blend mode", blend_mode), ("rename", rename)):
            with self.subTest(label):
                change()
                result = export.export_scene()
                self.assertEqual(result["tier"], "full", label)
                self.assertIn("GS_", result["reason"])
                self.export_without_exporter("reuse")
        self.assertEqual(list(self.manifest()["materials"]), ["GS_Dune"])
        self.assertEqual(self.manifest()["materials"]["GS_Dune"]["values"]["wet"], 1.0)

    def test_materials_of_a_prefab_off_the_scene(self):
        kit = bpy.data.collections.new("Kit")  # no depsgraph evaluates it: its materials are compared at plan()
        export.set_prefab(kit)
        leaves = bpy.data.materials.new("Leaves")
        mesh_with("Crown", leaves, collection=kit)
        export.export_scene()
        shaders.set_shader(leaves, "Foliage", {"sway": 3})
        self.export_without_exporter("layout")
        self.assertEqual(self.manifest()["materials"], {"Leaves": {"shader": "Foliage", "values": {"sway": 3.0}}})
        leaves.name = "Canopy"  # the prefab GLB names it
        self.assertEqual(export.export_scene()["tier"], "full")
        self.assertEqual(list(self.manifest()["materials"]), ["Canopy"])
        leaves.surface_render_method = "BLENDED"
        self.assertEqual(export.export_scene()["tier"], "full")

    def test_color_management_rewrites_only_the_manifest(self):
        export.export_scene()
        glbs = self.glbs()
        view = bpy.context.scene.view_settings
        view.view_transform = "Khronos PBR Neutral"
        view.exposure = 0.5
        self.export_without_exporter("layout")
        settings = self.manifest()["settings"]
        self.assertEqual((settings["toneMapping"], settings["exposure"]), ("neutral", 0.5))
        self.assertEqual(self.glbs(), glbs)

        view.look = "Medium High Contrast"  # not in the manifest: nothing to write, but a warning
        result = self.export_without_exporter("reuse")
        self.assertTrue(any(w.startswith("Look 'Medium High Contrast'") for w in result["warnings"]),
                        result["warnings"])
        view.look = "None"
        self.assertFalse(any(w.startswith("Look '") for w in self.export_without_exporter("reuse")["warnings"]))

        view.view_transform = "Filmic"
        result = self.export_without_exporter("layout")
        self.assertEqual(self.manifest()["settings"]["toneMapping"], "aces")
        self.assertTrue(any(w.startswith("View transform 'Filmic'") for w in result["warnings"]))
        view.view_transform = "AgX"
        result = self.export_without_exporter("layout")
        self.assertFalse(any(w.startswith("View transform") for w in result["warnings"]), result["warnings"])
