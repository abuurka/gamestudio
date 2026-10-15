# SPDX-License-Identifier: GPL-3.0-or-later
"""Baking procedural materials (core/bake.py, docs/SPEC.md §4.6): analysis, cache, restore."""

import json
import os
import shutil
import unittest
from unittest import mock

import bpy

from gamestudio.core import bake, export
from gamestudio.core.util import any_window

import helpers


def principled(mat):
    return mat.node_tree.nodes["Principled BSDF"]


def noise_ramp(name="Lava", bump=False):
    """Noise -> Color Ramp -> Base Color (and Bump -> Normal): the classic procedural material."""
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.05, 0.0, 0.0, 1.0)
    ramp.color_ramp.elements[1].color = (1.0, 0.6, 0.0, 1.0)
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], principled(mat).inputs["Base Color"])
    if bump:
        node = tree.nodes.new("ShaderNodeBump")
        node.inputs["Distance"].default_value = 0.2
        tree.links.new(noise.outputs["Fac"], node.inputs["Height"])
        tree.links.new(node.outputs["Normal"], principled(mat).inputs["Normal"])
    return mat


def image_material(name="Tex", size=8):
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.new(name + "Image", size, size)
    uv = tree.nodes.new("ShaderNodeUVMap")
    tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])
    tree.links.new(tex.outputs["Color"], principled(mat).inputs["Base Color"])
    return mat


def cube(name, mat=None, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    if mat is not None:
        obj.data.materials.append(mat)
    return obj


def snapshot():
    """What must not change: datablock names, and every material's node tree."""
    return {
        "materials": sorted(m.name for m in bpy.data.materials),
        "digests": {m.name: bake.material_digest(m) for m in bpy.data.materials},
        "images": sorted(i.name for i in bpy.data.images),
        "meshes": sorted(m.name for m in bpy.data.meshes),
        "objects": sorted(o.name for o in bpy.data.objects),
        "scenes": sorted(s.name for s in bpy.data.scenes),
        "data": {o.name: o.data.name if o.data else None for o in bpy.data.objects},
        "slots": {o.name: [(s.link, s.material.name if s.material else None) for s in o.material_slots]
                  for o in bpy.data.objects},
        "modifiers": {o.name: [m.show_viewport for m in o.modifiers] for o in bpy.data.objects},
        "hidden": {o.name: (o.hide_viewport, o.hide_render, o.hide_get()) for o in bpy.data.objects},
    }


class AnalysisTest(helpers.ProjectTestCase):
    def test_gltf_safe_materials_are_skipped(self):
        plain = bpy.data.materials.new("Plain")
        self.assertEqual(bake.analyze_material(plain)["status"], "safe")
        self.assertEqual(bake.analyze_material(image_material())["status"], "safe")
        normal = image_material("Normal")
        tree = normal.node_tree
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.new("NormalImage", 4, 4)
        tree.links.new(tex.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], principled(normal).inputs["Normal"])
        separate = tree.nodes.new("ShaderNodeSeparateColor")
        tree.links.new(tex.outputs["Color"], separate.inputs["Color"])
        tree.links.new(separate.outputs["Green"], principled(normal).inputs["Roughness"])
        self.assertEqual(bake.analyze_material(normal)["status"], "safe")
        self.assertEqual(bake.analyze_material(None)["status"], "safe")

    def test_procedural_inputs_need_baking(self):
        info = bake.analyze_material(noise_ramp(bump=True))
        self.assertEqual(info["status"], "bake")
        self.assertEqual(info["surface"], "principled")
        self.assertEqual(info["channels"], ["base_color", "normal"])
        self.assertFalse(info["world"])

        mat = bpy.data.materials.new("Rough")
        tree = mat.node_tree
        math_node = tree.nodes.new("ShaderNodeMath")
        voronoi = tree.nodes.new("ShaderNodeTexVoronoi")
        tree.links.new(voronoi.outputs["Distance"], math_node.inputs[0])
        tree.links.new(math_node.outputs[0], principled(mat).inputs["Roughness"])
        tree.links.new(voronoi.outputs["Distance"], principled(mat).inputs["Emission Strength"])
        geometry = tree.nodes.new("ShaderNodeNewGeometry")
        tree.links.new(geometry.outputs["Position"], voronoi.inputs["Vector"])
        info = bake.analyze_material(mat)
        self.assertEqual(info["channels"], ["roughness", "emission"])
        self.assertTrue(info["strength_linked"])
        self.assertTrue(info["world"])  # world-space position: the bake depends on where the object is

        # An image on generated coordinates is not what the exporter writes: bake it.
        generated = image_material("Generated")
        coords = generated.node_tree.nodes.new("ShaderNodeTexCoord")
        image = next(n for n in generated.node_tree.nodes if n.type == "TEX_IMAGE")
        generated.node_tree.links.new(coords.outputs["Generated"], image.inputs["Vector"])
        self.assertEqual(bake.analyze_material(generated)["channels"], ["base_color"])

    def test_other_surfaces(self):
        mixed = bpy.data.materials.new("Mixed")
        tree = mixed.node_tree
        mix = tree.nodes.new("ShaderNodeMixShader")
        second = tree.nodes.new("ShaderNodeBsdfDiffuse")
        tree.links.new(principled(mixed).outputs["BSDF"], mix.inputs[1])
        tree.links.new(second.outputs["BSDF"], mix.inputs[2])
        output = bake.output_node(tree)
        tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
        info = bake.analyze_material(mixed)
        self.assertEqual((info["surface"], info["channels"]), ("generic", ["base_color", "roughness"]))
        self.assertIn("not a Principled BSDF", info["notes"][0])

        glass = bpy.data.materials.new("Fade")
        tree = glass.node_tree
        mix = tree.nodes.new("ShaderNodeMixShader")
        clear = tree.nodes.new("ShaderNodeBsdfTransparent")
        tree.links.new(clear.outputs["BSDF"], mix.inputs[1])
        tree.links.new(principled(glass).outputs["BSDF"], mix.inputs[2])
        tree.links.new(mix.outputs["Shader"], bake.output_node(tree).inputs["Surface"])
        self.assertEqual(bake.analyze_material(glass)["status"], "safe")  # the exporter writes alpha

        alpha = noise_ramp("Alpha")
        noise = next(n for n in alpha.node_tree.nodes if n.type == "TEX_NOISE")
        alpha.node_tree.links.new(noise.outputs["Fac"], principled(alpha).inputs["Alpha"])
        self.assertIn("procedural Alpha is not baked", bake.analyze_material(alpha)["notes"])

    def test_material_digest_follows_what_renders(self):
        mat = noise_ramp()
        first = bake.material_digest(mat)
        self.assertEqual(bake.material_digest(mat.copy()), first)  # same nodes, same digest
        mat.node_tree.nodes["Color Ramp"].color_ramp.elements[1].color = (0, 0, 1, 1)
        second = bake.material_digest(mat)
        self.assertNotEqual(second, first)
        mat.node_tree.nodes["Noise Texture"].inputs["Scale"].default_value = 7.0
        third = bake.material_digest(mat)
        self.assertNotEqual(third, second)
        mat.node_tree.nodes["Noise Texture"].location = (500, 500)  # layout only
        self.assertEqual(bake.material_digest(mat), third)

    def test_uv_maps_that_cannot_hold_a_bake(self):
        mat = noise_ramp()
        box = cube("Box", mat)
        arrayed = cube("Arrayed", mat, location=(3, 0, 0))
        arrayed.modifiers.new("Array", "ARRAY").count = 3
        bare = cube("Bare", mat, location=(-3, 0, 0))
        bare.data.uv_layers.remove(bare.data.uv_layers[0])
        tiled = cube("Tiled", mat, location=(0, 3, 0))
        for loop in tiled.data.uv_layers[0].uv:
            loop.vector = (loop.vector[0] * 3, loop.vector[1])
        depsgraph = bpy.context.evaluated_depsgraph_get()
        problems = {o.name: bake._Geometry(o, depsgraph).uv_problem(bake._Geometry(o, depsgraph).mask({0}))
                    for o in (box, arrayed, bare, tiled)}
        self.assertEqual(problems["Box"], "")
        self.assertIn("overlapping", problems["Arrayed"])
        self.assertIn("no UV map", problems["Bare"])
        self.assertIn("outside the 0-1 square", problems["Tiled"])

    def test_plan_sizes_and_budget(self):
        mat = noise_ramp()
        small = cube("Small", mat)
        small.scale = (0.2, 0.2, 0.2)
        big = cube("Big", mat, location=(5, 0, 0))
        big.scale = (20, 20, 1)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        units = {u.name: u for u in bake.plan([small, big], depsgraph, {"bakeSize": 512, "bakeDensity": 128})}
        self.assertEqual(units["Big"].size, 512)  # capped by bakeSize
        self.assertLess(units["Small"].size, 512)
        self.assertGreaterEqual(units["Small"].size, bake.MIN_SIZE)
        self.assertEqual(units["Small"].strategy, "uv")
        tight = {u.name: u for u in bake.plan([small, big], depsgraph, {"bakeSize": 512, "bakeBudgetMB": 0.5})}
        self.assertLessEqual(sum(bake.unit_bytes(u) for u in tight.values()), 0.5 * 1024 * 1024)
        self.assertNotEqual(tight["Big"].key, units["Big"].key)  # the size is part of the cache key

    def test_shared_meshes_keep_their_own_size(self):
        mat = noise_ramp()
        small = cube("Small", mat)
        big = small.copy()  # a linked duplicate: one evaluated mesh, read once
        big.name = "Big"
        big.scale = (4, 4, 4)
        bpy.context.scene.collection.objects.link(big)
        bpy.context.view_layer.update()
        units = {u.name: u for u in bake.plan([small, big], bpy.context.evaluated_depsgraph_get(), {})}
        self.assertGreater(units["Big"].size, units["Small"].size)
        self.assertNotEqual(units["Big"].key, units["Small"].key)


@unittest.skipUnless(bake.cycles_available(), "this Blender has no Cycles")
class BakeTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.cache = self.root / ".gamestudio" / "bake"
        self.settings = {"bakeSize": 128, "bakeSamples": 1}

    def bake(self, objects, **kwargs):
        return bake.bake_objects(objects, bpy.context.scene, self.cache, self.settings, **kwargs)

    def test_bake_caches_and_leaves_the_file_alone(self):
        mat = noise_ramp(bump=True)
        box = cube("Box", mat)
        safe = cube("Safe", bpy.data.materials.new("Plain"), location=(3, 0, 0))
        before = snapshot()
        window_scene = any_window().scene

        result = self.bake([box, safe])
        self.assertEqual((result["baked"], result["cached"], result["problems"]), (1, 0, []))
        (unit,) = result["units"]
        self.assertEqual((unit["object"], unit["channels"], unit["uv"]), ("Box", ["base_color", "normal"], "existing"))
        entries = [p for p in self.cache.iterdir() if p.is_dir()]
        self.assertEqual(len(entries), 1)
        meta = json.loads((entries[0] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(set(meta["channels"]), {"base_color", "normal"})
        for channel in meta["channels"].values():
            self.assertTrue((entries[0] / channel["file"]).is_file())
        self.assertEqual(snapshot(), before)
        self.assertEqual(any_window().scene, window_scene)

        again = self.bake([box])
        self.assertEqual((again["baked"], again["cached"]), (0, 1))
        self.assertEqual(self.bake([box], force=True)["baked"], 1)

        # Changing the material or the geometry makes a new bake.
        mat.node_tree.nodes["Noise Texture"].inputs["Scale"].default_value = 3.0
        self.assertEqual(self.bake([box])["baked"], 1)
        box.data.vertices[0].co.x -= 0.2
        self.assertEqual(self.bake([box])["baked"], 1)
        self.assertEqual(len([p for p in self.cache.iterdir() if p.is_dir()]), 3)  # force rewrote the first

    def test_overlapping_uvs_bake_on_a_generated_layout(self):
        mat = noise_ramp()
        arrayed = cube("Arrayed", mat)
        arrayed.modifiers.new("Array", "ARRAY").count = 3
        mirror = arrayed.modifiers.new("Mirror", "MIRROR")
        mirror.use_axis = (False, True, False)
        arrayed.location = (0, 2, 0)
        hidden_source = cube("Hidden", mat, location=(0, -4, 0))
        hidden_source.hide_viewport = True
        before = snapshot()
        result = self.bake([arrayed, hidden_source])
        units = {u["object"]: u for u in result["units"]}
        self.assertEqual(units["Arrayed"]["uv"], "generated")
        self.assertEqual(units["Hidden"]["uv"], "existing")
        entry = next(p for p in self.cache.iterdir() if (p / "uv.npy").is_file())
        self.assertTrue((entry / "meta.json").is_file())
        self.assertEqual(snapshot(), before)

    def test_problems_are_reported(self):
        mat = noise_ramp()
        keyed = cube("Keyed", mat)
        keyed.shape_key_add(name="Basis")
        keyed.modifiers.new("Array", "ARRAY").count = 2
        result = self.bake([keyed])
        self.assertEqual(result["units"], [])
        self.assertIn("Material 'Lava' on 'Keyed' is procedural and was not baked", result["problems"][0])
        self.assertIn("shape keys", result["problems"][0])

    def test_without_cycles_bake_fails_clearly(self):
        box = cube("Box", noise_ramp())
        with mock.patch.object(bake, "cycles_available", return_value=False):
            with self.assertRaises(bake.BakeError) as caught:
                self.bake([box])
        self.assertIn("Cycles", str(caught.exception))

    def test_prune_removes_old_entries(self):
        self.bake([cube("Box", noise_ramp())])
        entry = next(self.cache.iterdir())
        self.assertEqual(bake.prune(self.cache, max_age_days=1), 0)
        old = (entry / "meta.json").stat().st_mtime - 3 * 86400
        os.utime(entry / "meta.json", (old, old))
        self.assertEqual(bake.prune(self.cache, max_age_days=1), 1)
        self.assertFalse(entry.exists())


def placed_material(name="Placed"):
    """A noise on world-space positions: the bake depends on where the object is."""
    mat = noise_ramp(name)
    tree = mat.node_tree
    geometry = tree.nodes.new("ShaderNodeNewGeometry")
    tree.links.new(geometry.outputs["Position"], tree.nodes["Noise Texture"].inputs["Vector"])
    return mat


def glb_summary(result) -> dict:
    """What a cached export must write as the baking one did: materials, images and who uses which."""
    gltf, _chunks = export.read_glb(result["glb"])
    names = [m.get("name") for m in gltf.get("materials", [])]
    used = {node["name"]: [names[p["material"]] for p in gltf["meshes"][node["mesh"]]["primitives"] if "material" in p]
            for node in gltf["nodes"] if "mesh" in node}
    return {"materials": sorted(names), "images": sorted(str(i.get("name")) for i in gltf.get("images", [])),
            "used": used}


@unittest.skipUnless(bake.cycles_available(), "this Blender has no Cycles")
class CachedExportTest(helpers.ProjectTestCase):
    """An export whose bakes are all cached makes no bake scene (bake._Evaluation) and writes what the
    baking export wrote; edits still make new bakes."""

    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        path = self.root / "gamestudio.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["export"] = {"bakeSize": 128, "bakeSamples": 1}
        path.write_text(json.dumps(config), encoding="utf-8")
        stamp = path.stat().st_mtime + 2
        os.utime(path, (stamp, stamp))  # read_config caches by mtime
        self.out = self.root / ".gamestudio" / "export"

    def level(self):
        """A box with its twin (a linked duplicate), an arrayed box (a generated UV layout), a box whose
        material depends on where it is and one whose material does not."""
        lava = noise_ramp(bump=True)
        box = cube("Box", lava)
        twin = box.copy()
        twin.name = "Twin"
        twin.location = (3, 0, 0)
        bpy.context.scene.collection.objects.link(twin)
        arrayed = cube("Arrayed", lava, location=(0, 3, 0))
        arrayed.modifiers.new("Array", "ARRAY").count = 3
        placed = cube("Placed", placed_material(), location=(-3, 0, 0))
        still = cube("Still", noise_ramp("Moss"), location=(0, -3, 0))
        return box, twin, arrayed, placed, still

    def export(self):
        """A full export, counting the bake and evaluation scenes it makes."""
        made = []
        real_bake, real_eval = bake._BakeScene, bake._EvalScene

        def spy(kind, cls):
            return lambda *args, **kwargs: made.append(kind) or cls(*args, **kwargs)

        with mock.patch.object(bake, "_BakeScene", side_effect=spy("bake", real_bake)), \
                mock.patch.object(bake, "_EvalScene", side_effect=spy("eval", real_eval)):
            result = export.export_scene_to(self.out)
        return result, made

    @staticmethod
    def baked(result) -> list:
        return sorted(u["object"] for u in result["bake"]["units"] if not u["cached"])

    @staticmethod
    def units(result) -> list:
        """The bake report's units without what differs between baking and finding in the cache."""
        return sorted((u["object"], u["materials"], u["channels"], u["size"], u["uv"]) for u in result["bake"]["units"])

    def test_cached_export_makes_no_bake_scene_and_writes_the_same_glb(self):
        self.level()
        before = snapshot()
        window_scene = any_window().scene
        first, made = self.export()
        self.assertEqual(made, ["bake"])
        self.assertEqual((first["bake"]["baked"], first["bake"]["cached"]), (4, 1))  # the twin shares the box's bake
        again, made = self.export()
        self.assertEqual(made, [])
        self.assertEqual((again["bake"]["baked"], again["bake"]["cached"]), (0, 5))
        self.assertEqual(self.units(again), self.units(first))
        self.assertEqual(glb_summary(again), glb_summary(first))
        self.assertTrue(any(name.endswith("_base_color") for name in glb_summary(again)["images"]))
        self.assertEqual(snapshot(), before)
        self.assertEqual(any_window().scene, window_scene)

    def test_edits_still_bake_again(self):
        box, twin, _arrayed, placed, still = self.level()
        self.export()
        self.assertEqual(self.baked(self.export()[0]), [])
        # A node change: the objects with that material (the box and its twin share one bake).
        box.active_material.node_tree.nodes["Noise Texture"].inputs["Scale"].default_value = 3.0
        result, made = self.export()
        self.assertEqual((self.baked(result), made), (["Arrayed", "Box"], ["bake"]))
        self.assertEqual(self.baked(self.export()[0]), [])
        # A mesh edit: the box and its twin.
        box.data.vertices[0].co.x -= 0.2
        self.assertEqual(self.baked(self.export()[0]), ["Box"])
        # Moving objects: only the one whose material depends on where it is.
        placed.location.x -= 1.0
        still.location.x += 1.0
        twin.location.y += 1.0
        self.assertEqual(self.baked(self.export()[0]), ["Placed"])
        self.assertEqual(self.baked(self.export()[0]), [])

    def test_hidden_objects_are_evaluated_without_a_bake_scene(self):
        lava = noise_ramp()
        source = cube("Source", lava, location=(0, 6, 0))   # hidden with the eye (a scatter's source, say)
        source.modifiers.new("Subdivision", "SUBSURF").levels = 1
        source.hide_set(True)
        disabled = cube("Disabled", lava, location=(0, -6, 0))
        disabled.modifiers.new("Array", "ARRAY").count = 2
        disabled.hide_viewport = True
        cube("Shown", lava)
        before = snapshot()
        self.assertEqual(self.export()[1], ["eval", "bake"])
        result, made = self.export()
        self.assertEqual(made, ["eval"])  # for the two hidden objects; nothing to bake
        self.assertEqual((result["bake"]["baked"], result["bake"]["cached"]), (0, 3))
        self.assertEqual(snapshot(), before)  # hidden as before

    def test_planning_finds_the_keys_the_bake_scene_finds(self):
        objects = list(self.level())
        objects[2].modifiers.new("Subdivision", "SUBSURF").levels = 1
        eye = cube("Eye", noise_ramp("Rust"), location=(6, 0, 0))
        eye.modifiers.new("Subdivision", "SUBSURF").levels = 1
        eye.hide_set(True)
        off = cube("Off", eye.active_material, location=(-6, 0, 0))
        off.hide_viewport = True
        bevelled = objects[1].copy()  # the box's mesh too, but a modifier makes its evaluated mesh its own
        bevelled.name = "Bevelled"
        # Triangulate, not Bevel: Bevel's UVs differ by a float step between two evaluations.
        bevelled.modifiers.new("Triangulate", "TRIANGULATE")
        bpy.context.scene.collection.objects.link(bevelled)
        objects += [eye, off, bevelled]
        scene, settings = bpy.context.scene, {"bakeSize": 128, "bakeSamples": 1}
        bs = bake._BakeScene(any_window(), 1, scene.frame_current)  # how planning read geometry before
        try:
            for obj in objects:
                bs.link(obj)
            old = {u.name: (u.key, u.strategy, u.size) for u in bake.plan(objects, bs.depsgraph(), settings)}
        finally:
            bs.close()
        evaluation = bake._Evaluation(scene, any_window(), objects, 1)
        try:
            new = {u.name: (u.key, u.strategy, u.size) for u in bake.plan(objects, evaluation.depsgraph, settings)}
        finally:
            evaluation.close()
        self.assertEqual(new, old)
        self.assertEqual(len(new), 8)
        self.assertTrue(all(key for key, _strategy, _size in new.values()))
        self.assertEqual(new["Box"], new["Twin"])
        self.assertNotEqual(new["Bevelled"], new["Box"])

    def tearDown(self):
        shutil.rmtree(self.root / ".gamestudio" / "bake", ignore_errors=True)
        super().tearDown()


class SettingsTest(helpers.ProjectTestCase):
    def test_export_settings_from_gamestudio_json(self):
        config = json.loads((self.root / "gamestudio.json").read_text(encoding="utf-8"))
        config["export"] = {"bake": "cached", "maxTextureSize": 1024, "webp": True, "bakeSize": "big"}
        (self.root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertLogs("gamestudio.export", level="WARNING") as logs:
            settings = export.export_settings(self.root)
        self.assertEqual((settings["bake"], settings["maxTextureSize"], settings["webp"]), ("cached", 1024, True))
        self.assertEqual(settings["bakeSize"], export.EXPORT_DEFAULTS["bakeSize"])
        self.assertIn("export.bakeSize", logs.output[0])
        self.assertEqual(export.export_settings(None), export.EXPORT_DEFAULTS)

    def tearDown(self):
        shutil.rmtree(self.root / ".gamestudio" / "bake", ignore_errors=True)
        super().tearDown()


if __name__ == "__main__":
    unittest.main()
