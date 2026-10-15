# SPDX-License-Identifier: GPL-3.0-or-later
"""What Blender shows is what exports (docs/SPEC.md §4.6): baked materials, evaluated geometry,
geometry-nodes instances, texture budgets, the size report and the export warnings."""

import json
import os
import pathlib
import unittest
from unittest import mock

import bpy
import numpy as np

from gamestudio.core import bake, compare, components, export, fidelity, ids

import helpers
from test_bake import cube, noise_ramp, principled, snapshot

ANIMATOR = {
    "name": "Animator", "category": "Animation", "builtin": True,
    "fields": [{"name": "clip", "type": "string", "default": ""}, {"name": "loop", "type": "bool", "default": True}],
}
AUDIO_SOURCE = {
    "name": "AudioSource", "category": "Audio", "builtin": True,
    "fields": [{"name": "clip", "type": "asset", "default": "", "extensions": ["ogg", "mp3", "wav"]}],
}
HAS_CYCLES = bake.cycles_available()


def read(path):
    gltf, chunks = export.read_glb(path)
    return gltf, fidelity.Glb(gltf, chunks)


def nodes_named(gltf):
    return {n.get("name"): n for n in gltf["nodes"]}


def scatter(ground, source, density=6.0):
    """A geometry-nodes modifier that scatters instances of source on ground's faces (and keeps ground)."""
    tree = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    points = nodes.new("GeometryNodeDistributePointsOnFaces")
    points.inputs["Density"].default_value = density
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    info = nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = source
    info.inputs["As Instance"].default_value = True
    join = nodes.new("GeometryNodeJoinGeometry")
    links.new(group_in.outputs[0], points.inputs["Mesh"])
    links.new(points.outputs["Points"], instance.inputs["Points"])
    links.new(info.outputs["Geometry"], instance.inputs["Instance"])
    links.new(instance.outputs["Instances"], join.inputs[0])
    links.new(group_in.outputs[0], join.inputs[0])
    links.new(join.outputs[0], group_out.inputs[0])
    modifier = ground.modifiers.new("Scatter", "NODES")
    modifier.node_group = tree
    return modifier


def save_image(root, name, width, height, color=(0.8, 0.2, 0.1, 1.0)):
    image = bpy.data.images.new(name, width, height)
    image.generated_type = "COLOR_GRID"
    image.filepath_raw = str(root / f"{name}.png")
    image.file_format = "PNG"
    image.save()
    return image


def evaluated_triangles(obj) -> int:
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return len(mesh.loop_triangles)
    finally:
        evaluated.to_mesh_clear()


def gltf_triangles(gltf, mesh_index) -> int:
    total = 0
    for prim in gltf["meshes"][mesh_index]["primitives"]:
        total += gltf["accessors"][prim["indices"]]["count"] // 3
    return total


def unused(gltf) -> dict:
    """What a GLB holds that its scene does not use: {kind: [indices]}, empty when nothing."""
    nodes = gltf.get("nodes", [])
    reach = {i for i, _drawn in fidelity.scene_nodes(gltf)}
    reach |= {j for s in gltf.get("skins", []) for j in s.get("joints", [])}
    meshes = {nodes[i]["mesh"] for i in reach if "mesh" in nodes[i]}
    prims = [p for m in meshes for p in gltf["meshes"][m]["primitives"]]
    materials = {p["material"] for p in prims if "material" in p}
    accessors = {a for p in prims for a in [*p["attributes"].values(), *([p["indices"]] if "indices" in p else [])]}
    for i in reach:
        accessors |= set(((nodes[i].get("extensions") or {}).get(fidelity.INSTANCING) or {}).get("attributes", {})
                         .values())
    accessors |= {s[k] for a in gltf.get("animations", []) for s in a["samplers"] for k in ("input", "output")}
    accessors |= {s["inverseBindMatrices"] for s in gltf.get("skins", []) if "inverseBindMatrices" in s}
    views = {gltf["accessors"][a]["bufferView"] for a in accessors}
    views |= {i["bufferView"] for i in gltf.get("images", []) if "bufferView" in i}
    out = {}
    for kind, used in (("nodes", reach), ("meshes", meshes), ("materials", materials), ("accessors", accessors),
                       ("bufferViews", views)):
        extra = sorted(set(range(len(gltf.get(kind, [])))) - used)
        if extra:
            out[kind] = extra
    return out


def bin_is_packed(gltf, glb) -> bool:
    """The BIN chunk holds the buffer views back to back (4-byte aligned) and nothing else."""
    end = 0
    for view in gltf.get("bufferViews", []):
        if view["byteOffset"] != end + (-end % 4):
            return False
        end = view["byteOffset"] + view["byteLength"]
    return len(glb.bin) == gltf["buffers"][0]["byteLength"] == end + (-end % 4)


class FidelityTestCase(helpers.ProjectTestCase):
    registry_data = helpers.modified_registry(lambda d: d["components"].extend([ANIMATOR, AUDIO_SOURCE]))

    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        self.export_dir = self.root / ".gamestudio" / "export"

    def configure(self, **settings):
        path = self.root / "gamestudio.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["export"] = settings
        path.write_text(json.dumps(config), encoding="utf-8")
        stamp = path.stat().st_mtime + 2 * (1 + getattr(self, "_writes", 0))
        self._writes = getattr(self, "_writes", 0) + 1
        os.utime(path, (stamp, stamp))  # read_config caches by mtime


@unittest.skipUnless(HAS_CYCLES, "this Blender has no Cycles")
class BakedExportTest(FidelityTestCase):
    def test_procedural_materials_export_as_baked_textures(self):
        mat = noise_ramp(bump=True)
        box = cube("Box", mat)
        arrayed = cube("Arrayed", mat, location=(0, 3, 0))
        arrayed.modifiers.new("Array", "ARRAY").count = 3
        before = snapshot()

        result = export.export_scene()
        gltf, _glb = read(result["glb"])
        lava = [m for m in gltf["materials"] if m["name"] == "Lava"]  # copies keep the original's name
        self.assertEqual(len(lava), 2)
        for material in lava:
            self.assertIn("baseColorTexture", material["pbrMetallicRoughness"])
            self.assertIn("normalTexture", material)
        self.assertEqual(len(gltf["images"]), 4)
        units = {u["object"]: u for u in result["bake"]["units"]}
        self.assertEqual((units["Box"]["uv"], units["Arrayed"]["uv"]), ("existing", "generated"))
        self.assertEqual(result["bake"]["baked"], 2)
        # The arrayed copy reads its texture through the generated UV map (the second TEXCOORD).
        arrayed_node = nodes_named(gltf)["Arrayed"]
        prim = gltf["meshes"][arrayed_node["mesh"]]["primitives"][0]
        material = gltf["materials"][prim["material"]]
        texcoord = material["pbrMetallicRoughness"]["baseColorTexture"].get("texCoord", 0)
        self.assertIn(f"TEXCOORD_{texcoord}", prim["attributes"])
        self.assertEqual(gltf_triangles(gltf, arrayed_node["mesh"]), evaluated_triangles(arrayed))
        self.assertEqual(snapshot(), before)  # materials, meshes, slots and modifiers are back as they were

        again = export.export_scene(force=True)  # unforced, nothing changed: the last export is reused
        self.assertEqual((again["bake"]["baked"], again["bake"]["cached"]), (0, 2))
        self.assertEqual(snapshot(), before)

    def test_bake_modes(self):
        box = cube("Box", noise_ramp())
        self.configure(bake="cached")
        result = export.export_scene()
        gltf, _glb = read(result["glb"])
        self.assertNotIn("baseColorTexture", gltf["materials"][0].get("pbrMetallicRoughness", {}))
        self.assertTrue(any("has not been baked yet" in w for w in result["warnings"]), result["warnings"])

        bake.bake_objects([box], bpy.context.scene, export.bake_cache_dir(self.export_dir),
                          export.export_settings(self.root))
        result = export.export_scene()
        gltf, _glb = read(result["glb"])
        self.assertIn("baseColorTexture", gltf["materials"][0]["pbrMetallicRoughness"])
        self.assertEqual(result["bake"]["cached"], 1)

        self.configure(bake="off")
        result = export.export_scene()
        gltf, _glb = read(result["glb"])
        self.assertNotIn("images", gltf)
        self.assertEqual(result["warnings"], [])
        self.assertIn("baseColorTexture",  # the bake argument overrides the setting
                      read(export.export_scene(bake="auto")["glb"])[0]["materials"][0]["pbrMetallicRoughness"])

    def test_geometry_nodes_instances_get_baked_materials(self):
        rock = cube("Rock", noise_ramp("RockSkin"), location=(0, 10, 0))
        rock.scale = (0.2, 0.2, 0.2)
        rock.hide_set(True)
        ground = cube("Ground", bpy.data.materials.new("Plain"))
        scatter(ground, rock, density=2.0)
        result = export.export_scene()
        gltf, _glb = read(result["glb"])
        holder = next(n for n in gltf["nodes"] if fidelity.INSTANCING in (n.get("extensions") or {}))
        material = gltf["materials"][gltf["meshes"][holder["mesh"]]["primitives"][0]["material"]]
        self.assertEqual(material["name"], "RockSkin")
        self.assertIn("baseColorTexture", material["pbrMetallicRoughness"])
        self.assertIn("Rock", [u["object"] for u in result["bake"]["units"]])


class ExportFidelityTest(FidelityTestCase):
    def test_object_linked_materials_survive_modifiers(self):
        red, blue = bpy.data.materials.new("Red"), bpy.data.materials.new("Blue")
        box = cube("Box", red)
        box.modifiers.new("Bevel", "BEVEL")
        box.material_slots[0].link = "OBJECT"
        box.material_slots[0].material = blue  # what Blender draws
        before = snapshot()
        result = export.export_scene(bake="off")
        gltf, _glb = read(result["glb"])
        self.assertEqual([m["name"] for m in gltf["materials"]], ["Blue"])
        self.assertEqual(result["bake"]["fixed"], ["Box"])
        self.assertEqual(snapshot(), before)

    def test_geometry_nodes_scatter_exports_as_gpu_instances(self):
        rock = cube("Rock", bpy.data.materials.new("Stone"), location=(0, 10, 0))
        rock.scale = (0.2, 0.2, 0.2)
        rock.hide_set(True)  # the usual setup: the source is hidden, only its instances show
        ground = cube("Ground", bpy.data.materials.new("Grass"))
        ground.scale = (4, 4, 0.1)
        scatter(ground, rock)
        decor = bpy.data.collections.new("Decor")
        helpers.new_object("Plant", helpers.new_mesh("PlantMesh"), collection=decor)
        instancer = helpers.new_object("DecorInstance", None, location=(5, 0, 0))
        instancer.instance_type = "COLLECTION"
        instancer.instance_collection = decor
        bpy.context.view_layer.update()
        counts, _sources = export.instance_counts(bpy.context.evaluated_depsgraph_get())
        self.assertGreater(counts["Ground"], 10)

        result = export.export_scene()
        self.assertEqual(result["instances"], {"Ground": counts["Ground"]})
        self.assertEqual(result["instancing"], [{"parent": "Ground", "mesh": "Cube", "count": counts["Ground"]}])
        self.assertIn("Rock", result["hidden_meshes"])
        gltf, _glb = read(result["glb"])
        nodes = nodes_named(gltf)
        holder = next(n for n in gltf["nodes"] if fidelity.INSTANCING in (n.get("extensions") or {}))
        attributes = holder["extensions"][fidelity.INSTANCING]["attributes"]
        self.assertEqual(gltf["accessors"][attributes["TRANSLATION"]]["count"], counts["Ground"])
        self.assertIn(gltf["nodes"].index(holder), nodes["Ground"]["children"])
        self.assertIn(fidelity.INSTANCING, gltf["extensionsUsed"])
        self.assertNotIn("GN Instance", nodes)
        self.assertNotIn("mesh", nodes["Rock"])  # the hidden source is not drawn
        self.assertEqual([gltf["nodes"][c]["name"] for c in nodes["DecorInstance"]["children"]], ["Plant"])
        manifest = json.loads(pathlib.Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual({e["name"] for e in manifest["entities"]}, {"Rock", "Ground", "DecorInstance"})
        for name in ("Rock", "Ground"):
            self.assertEqual(nodes[name]["extras"], {"gs_id": ids.get_id(bpy.data.objects[name])})
        self.assertGreater(result["size"]["triangles"], counts["Ground"] * 12 - 1)

        self.configure(instancing=0)
        gltf, _glb = read(export.export_scene()["glb"])
        self.assertEqual(sum(1 for n in gltf["nodes"] if n.get("name") == "GN Instance"), counts["Ground"])
        self.assertNotIn("extensionsUsed", gltf)

    def test_modifiers_apply_and_hidden_cutters_disappear(self):
        mirrored = cube("Mirrored", location=(4, 0, 0))
        for vertex in mirrored.data.vertices:
            vertex.co.y += 1.5
        mirrored.modifiers.new("Array", "ARRAY").count = 3
        mirrored.modifiers.new("Mirror", "MIRROR").use_axis = (False, True, False)
        wall = cube("Wall")
        wall.scale = (2, 2, 2)
        cutter = cube("Cutter")
        cutter.scale = (0.4, 0.4, 3)
        cutter.hide_set(True)
        wall.modifiers.new("Bool", "BOOLEAN").object = cutter
        trigger = cube("Trigger", location=(-4, 0, 0))
        trigger.display_type = "WIRE"
        trigger.hide_render = True
        components.add_component(trigger, "Collider")
        wall.modifiers["Bool"].operand_type = "OBJECT"
        bpy.context.view_layer.update()

        result = export.export_scene()
        gltf, _glb = read(result["glb"])
        nodes = nodes_named(gltf)
        for obj in (mirrored, wall):
            self.assertEqual(gltf_triangles(gltf, nodes[obj.name]["mesh"]), evaluated_triangles(obj))
        self.assertEqual(gltf_triangles(gltf, nodes["Mirrored"]["mesh"]), 12 * 6)
        self.assertNotIn("mesh", nodes["Cutter"])
        self.assertIn("mesh", nodes["Trigger"])  # it has a Collider: its mesh stays for it, not drawn
        self.assertEqual(result["hidden_meshes"], ["Cutter"])
        self.assertEqual(result["not_drawn"], ["Trigger"])
        manifest = json.loads(pathlib.Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual({e["name"]: e.get("drawn", True) for e in manifest["entities"]}["Trigger"], False)
        self.assertFalse(any("Trigger" in w for w in result["warnings"]), result["warnings"])
        self.assertTrue(cutter.hide_get())

    def test_collision_only_objects_keep_a_bare_mesh_and_are_not_drawn(self):
        wall = cube("InvisibleWall", bpy.data.materials.new("Glass"), location=(0, 4, 0))
        wall.display_type = "WIRE"
        components.add_component(wall, "Collider")
        zone = cube("Zone", location=(4, 0, 0))
        zone.display_type = "BOUNDS"
        components.add_component(zone, "Collider", {"sensor": True})
        crate = cube("Crate", bpy.data.materials.new("Wood"), location=(-4, 0, 0))
        components.add_component(crate, "Collider")
        lamp = cube("Lamp", bpy.data.materials.new("Brass"), location=(0, 4, 2))
        lamp.parent = wall  # a child entity of a collision-only object is drawn
        sketch = cube("Sketch", location=(0, -4, 0))
        sketch.display_type = "WIRE"  # no Collider: drawn as before

        result = export.export_scene(bake="off")
        manifest = json.loads(pathlib.Path(result["manifest"]).read_text(encoding="utf-8"))
        drawn = {e["name"]: e.get("drawn", True) for e in manifest["entities"]}
        self.assertEqual(drawn, {"InvisibleWall": False, "Zone": False, "Crate": True, "Lamp": True, "Sketch": True})
        self.assertEqual(sorted(result["not_drawn"]), ["InvisibleWall", "Zone"])
        gltf, glb = read(result["glb"])
        nodes = nodes_named(gltf)
        for name in ("InvisibleWall", "Zone"):
            prims = gltf["meshes"][nodes[name]["mesh"]]["primitives"]
            self.assertEqual([sorted(p) for p in prims], [["attributes", "indices"]])  # no material
            self.assertEqual(list(prims[0]["attributes"]), ["POSITION"])
            position = gltf["accessors"][prims[0]["attributes"]["POSITION"]]
            self.assertEqual(position["count"], 8)  # the cube's corners, welded
            self.assertEqual((position["min"], position["max"]), ([-0.5] * 3, [0.5] * 3))
            self.assertEqual(gltf["accessors"][prims[0]["indices"]]["count"], 36)
        self.assertEqual(sorted(m["name"] for m in gltf["materials"]), ["Brass", "Wood"])  # Glass went
        self.assertEqual(result["size"]["draw_calls"], 3)  # Crate, Lamp, Sketch
        self.assertEqual(result["size"]["triangles"], 3 * 12)
        self.assertEqual(unused(gltf), {})
        self.assertTrue(bin_is_packed(gltf, glb))
        self.assertEqual(result["warnings"], [])

        # In a prefab template too.
        barrier = bpy.data.collections.new("Barrier")
        export.set_prefab(barrier)
        post = helpers.new_object("Post", helpers.new_mesh("PostMesh"), collection=barrier)
        post.display_type = "WIRE"
        components.add_component(post, "Collider")
        helpers.new_object("Sign", helpers.new_mesh("SignMesh"), collection=barrier, parent=post)
        result = export.export_scene(bake="off")
        manifest = json.loads(pathlib.Path(result["manifest"]).read_text(encoding="utf-8"))
        template = {e["name"]: e.get("drawn", True) for e in manifest["prefabs"][0]["entities"]}
        self.assertEqual(template, {"Post": False, "Sign": True})
        prefab_gltf, _glb = read(self.export_dir / "main.prefabs.glb")
        post_node = nodes_named(prefab_gltf)["Post"]
        self.assertEqual(list(prefab_gltf["meshes"][post_node["mesh"]]["primitives"][0]["attributes"]), ["POSITION"])
        self.assertEqual(unused(prefab_gltf), {})

    def test_what_the_scene_does_not_use_is_pruned(self):
        stash = bpy.data.collections.new("Stash")
        bpy.context.scene.collection.children.link(stash)
        rock = cube("Rock", bpy.data.materials.new("Stone"), location=(0, 10, 0))
        rock.scale = (0.2, 0.2, 0.2)
        spare = cube("Spare", bpy.data.materials.new("Unused"), location=(0, -10, 0))
        for obj in (rock, spare):  # instance sources live in an excluded collection (the procedural toolkit)
            stash.objects.link(obj)
            bpy.context.scene.collection.objects.unlink(obj)
        bpy.context.view_layer.layer_collection.children["Stash"].exclude = True
        ground = cube("Ground", bpy.data.materials.new("Grass"))
        ground.scale = (4, 4, 0.1)
        scatter(ground, rock)
        bpy.context.view_layer.update()

        with mock.patch.object(fidelity, "prune", return_value={}):
            unpruned = export.export_scene(bake="off")
        loose, _glb = read(unpruned["glb"])
        self.assertIn("Rock", nodes_named(loose))  # the exporter writes the excluded objects ...
        self.assertEqual(unused(loose)["nodes"], [i for i, n in enumerate(loose["nodes"]) if n["name"] in
                                                  ("Rock", "Spare")])
        self.assertEqual(unpruned["size"]["draw_calls"], 2)  # ... but the size report counts what the scene draws

        result = export.export_scene(bake="off", force=True)  # the scene did not change: force a new export
        gltf, glb = read(result["glb"])
        self.assertEqual(unused(gltf), {})
        self.assertTrue(bin_is_packed(gltf, glb))
        self.assertFalse({"Rock", "Spare"} & set(nodes_named(gltf)))
        self.assertEqual(sorted(m["name"] for m in gltf["materials"]), ["Grass", "Stone"])
        self.assertEqual(len(gltf["meshes"]), 2)  # the ground, and the rock once (instanced)
        self.assertEqual(result["pruned"]["nodes"], 2)
        self.assertGreaterEqual(result["pruned"]["meshes"], 1)
        self.assertGreater(result["pruned"]["bytes"], 0)
        self.assertLessEqual(result["size"]["glb"], unpruned["size"]["glb"] - result["pruned"]["bytes"])
        self.assertEqual(result["size"]["draw_calls"], 2)
        self.assertEqual(result["size"]["triangles"], unpruned["size"]["triangles"])
        self.assertEqual(result["instancing"], unpruned["instancing"])

    def test_textures_are_downscaled_and_colour_maps_become_webp(self):
        color = save_image(self.root, "Albedo", 1024, 512)
        normal = save_image(self.root, "Bumps", 1024, 1024)
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
        self.configure(maxTextureSize=256, webp=True)

        result = export.export_scene()
        gltf, glb = read(result["glb"])
        images = {i["name"]: i for i in gltf["images"]}
        self.assertEqual(images["Albedo"]["mimeType"], "image/webp")
        self.assertEqual(fidelity.image_size(glb.view(images["Albedo"]["bufferView"])), (256, 128))
        self.assertEqual(images["Bumps"]["mimeType"], "image/png")  # data maps stay lossless
        self.assertEqual(fidelity.image_size(glb.view(images["Bumps"]["bufferView"])), (256, 256))
        self.assertIn(fidelity.WEBP, gltf["extensionsRequired"])
        webp_textures = [t for t in gltf["textures"] if fidelity.WEBP in (t.get("extensions") or {})]
        self.assertEqual(len(webp_textures), 1)
        self.assertNotIn("source", webp_textures[0])
        self.assertTrue(any("'Albedo' is 1024x512: exported at 256x128" in w for w in result["warnings"]))
        self.assertEqual(sorted(t["name"] for t in result["textures"]), ["Albedo", "Bumps"])
        cached = list((self.root / ".gamestudio" / "bake" / "textures").iterdir())
        self.assertEqual(len(cached), 2)
        stamps = {p.name: p.stat().st_mtime_ns for p in cached}
        again, again_glb = read(export.export_scene()["glb"])
        again_images = {i["name"]: i for i in again["images"]}
        self.assertEqual(again_glb.view(again_images["Albedo"]["bufferView"]), glb.view(images["Albedo"]["bufferView"]))
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in cached[0].parent.iterdir()}, stamps)  # cache hits
        self.assertEqual(tuple(bpy.data.images["Albedo"].size), (1024, 512))  # the file's image is untouched

        self.configure(maxTextureSize=0, webp=False)
        gltf, glb = read(export.export_scene()["glb"])
        images = {i["name"]: i for i in gltf["images"]}
        self.assertEqual(fidelity.image_size(glb.view(images["Albedo"]["bufferView"])), (1024, 512))
        self.assertNotIn("extensionsRequired", gltf)

    def test_size_report_and_collected_warnings(self):
        box = cube("Box", bpy.data.materials.new("Plain"))
        box.animation_data_create()
        track = box.animation_data.nla_tracks.new()
        for start, name in ((1, "A"), (30, "B")):
            action = bpy.data.actions.new(name)
            box.animation_data.action = action
            box.location.z = 0
            box.keyframe_insert("location", index=2, frame=1)
            box.location.z = 1
            box.keyframe_insert("location", index=2, frame=10)
            box.animation_data.action = None
            track.strips.new(name, start, action)
        speaker = helpers.new_object("Speaker", None)
        components.add_component(speaker, "AudioSource", {"clip": "assets/missing.ogg"})
        self.configure(warnSizeMB=0.0001)

        result = export.export_scene()
        size = result["size"]
        self.assertEqual(size["bytes"], os.path.getsize(result["glb"]))
        self.assertEqual(size["glb"], os.path.getsize(result["glb"]))
        self.assertEqual((size["prefabs"], size["files"]), (0, 0))
        self.assertEqual(size["triangles"], 12)
        self.assertEqual(size["draw_calls"], 1)
        self.assertEqual(size["top"][0]["kind"], "mesh")
        warnings = result["warnings"]
        self.assertTrue(any("NLA track" in w and "2 strips" in w for w in warnings), warnings)
        self.assertTrue(any("Asset file not found" in w for w in warnings), warnings)
        self.assertTrue(any("over export.warnSizeMB" in w for w in warnings), warnings)
        self.assertEqual(export.last_export()["warnings"], warnings)
        json.dumps(result)  # the result goes over the agent bridge as JSON

    def test_hidden_objects_and_instances_without_cycles(self):
        hidden = cube("Stashed")
        hidden.hide_set(True)
        cube("Lavaish", noise_ramp(), location=(3, 0, 0))
        with mock.patch.object(bake, "cycles_available", return_value=False):
            result = export.export_scene()
        warnings = result["warnings"]
        self.assertTrue(any("hidden in the viewport are exported" in w and "Stashed" in w for w in warnings), warnings)
        self.assertTrue(any("Cycles is not available" in w and "Lava" in w for w in warnings), warnings)
        self.assertEqual(result["bake"]["units"], [])

    def test_object_clips(self):
        rig = helpers.new_object("Rig", None)
        rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions.new("Idle")
        one = rig.animation_data.nla_tracks.new()
        one.strips.new("Walk", 1, bpy.data.actions.new("Walk"))
        two = rig.animation_data.nla_tracks.new()
        two.strips.new("ComboA", 1, bpy.data.actions.new("ComboA"))
        two.strips.new("ComboB", 40, bpy.data.actions.new("ComboB"))
        child = helpers.new_object("Hand", None, parent=rig)
        child.animation_data_create()
        child.animation_data.action = bpy.data.actions.new("Wave")
        self.assertEqual(export.object_clips(rig), ["Idle", "Walk", "Wave"])  # the 2-strip track exports nothing
        self.assertEqual(export.object_clips(child), ["Wave"])
        self.assertEqual(export.object_clips(None), [])


class PruneTest(unittest.TestCase):
    """fidelity.prune on a hand-made GLB: skins, animations, textures and shared meshes."""

    @staticmethod
    def blob(glb, data: bytes) -> int:
        views = glb.gltf.setdefault("bufferViews", [])
        views.append({"buffer": 0, "byteOffset": 0, "byteLength": len(data)})
        glb.replace(len(views) - 1, data)
        return len(views) - 1

    def make(self):
        glb = fidelity.Glb({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}]}, [])
        tri = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        acc = lambda values, kind="VEC3": glb.add_accessor(values, kind)  # noqa: E731
        index = acc(np.array([0, 1, 2], dtype=np.uint16), "SCALAR")
        meshes = [{"name": name, "primitives": [{"attributes": {"POSITION": acc(tri * (k + 1))}, "indices": index,
                                                 "material": k}]}
                  for k, name in enumerate(("Kept", "Shared", "Orphan"))]
        images = [{"name": name, "mimeType": "image/png", "bufferView": self.blob(glb, name.encode() * 5)}
                  for name in ("used", "unused")]
        times = acc(np.array([0.0, 1.0], dtype=np.float32), "SCALAR")
        moves = acc(np.zeros((2, 3), dtype=np.float32))
        glb.gltf.update({
            "nodes": [
                {"name": "Root", "mesh": 0, "children": [1]},
                {"name": "Body", "mesh": 1, "skin": 0},
                {"name": "Joint"},                            # a skin joint outside the scene tree: kept
                {"name": "Orphan", "mesh": 2, "children": [4]},  # not in the scene: removed with its mesh
                {"name": "OrphanChild", "mesh": 1},
            ],
            "meshes": meshes,
            "skins": [{"joints": [2], "inverseBindMatrices": acc(np.eye(4, dtype=np.float32).reshape(1, 16),
                                                                 "MAT4")}],
            "materials": [{"name": "A"}, {"name": "B", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                          {"name": "C", "emissiveTexture": {"index": 1}}],
            "textures": [{"source": 0, "sampler": 0}, {"source": 1, "sampler": 1}],
            "samplers": [{}, {"magFilter": 9728}],
            "images": images,
            "animations": [
                {"name": "Both", "samplers": [{"input": times, "output": moves}, {"input": times, "output": moves}],
                 "channels": [{"sampler": 0, "target": {"node": 3, "path": "translation"}},
                              {"sampler": 1, "target": {"node": 0, "path": "translation"}}]},
                {"name": "OrphanOnly", "samplers": [{"input": times, "output": moves}],
                 "channels": [{"sampler": 0, "target": {"node": 4, "path": "translation"}}]},
            ],
        })
        glb.gltf["accessors"][meshes[0]["primitives"][0]["attributes"]["POSITION"]].update(min=[0] * 3, max=[1] * 3)
        chunks = glb.finish()
        return fidelity.Glb(glb.gltf, chunks)

    def test_prune(self):
        glb = self.make()
        gltf = glb.gltf
        orphan_bytes = glb.view(gltf["images"][1]["bufferView"])
        before = len(glb.bin)
        removed = fidelity.prune(glb)
        self.assertEqual([n["name"] for n in gltf["nodes"]], ["Root", "Body", "Joint"])
        self.assertEqual(gltf["skins"][0]["joints"], [2])
        self.assertEqual([m["name"] for m in gltf["meshes"]], ["Kept", "Shared"])
        self.assertEqual([m["name"] for m in gltf["materials"]], ["A", "B"])
        self.assertEqual(gltf["textures"], [{"source": 0, "sampler": 0}])
        self.assertEqual([i["name"] for i in gltf["images"]], ["used"])
        self.assertEqual(gltf["samplers"], [{}])
        self.assertEqual([a["name"] for a in gltf["animations"]], ["Both"])
        self.assertEqual(gltf["animations"][0]["channels"], [{"sampler": 0, "target": {"node": 0, "path": "translation"}}])
        self.assertEqual(unused(gltf), {})
        self.assertTrue(bin_is_packed(gltf, glb))
        self.assertEqual(removed["nodes"], 2)
        self.assertEqual(removed["bytes"], before - len(glb.bin))
        self.assertNotIn(orphan_bytes, glb.bin)
        # The data moved with its views.
        shared = glb.read_accessor(gltf["meshes"][1]["primitives"][0]["attributes"]["POSITION"])
        self.assertEqual(shared.tolist(), [[0, 0, 0], [2, 0, 0], [0, 2, 0]])
        self.assertEqual(glb.read_accessor(gltf["meshes"][1]["primitives"][0]["indices"]).tolist(), [0, 1, 2])
        self.assertEqual(glb.view(gltf["images"][0]["bufferView"]), b"used" * 5)
        self.assertEqual(fidelity.prune(glb), {})  # nothing left to remove
        # A GLB with an extension prune does not know is left alone.
        other = self.make()
        other.gltf["extensionsUsed"] = ["KHR_animation_pointer"]
        self.assertEqual(fidelity.prune(other), {})
        self.assertEqual(len(other.gltf["nodes"]), 5)

    def test_size_report_counts_what_the_scene_draws(self):
        glb = self.make()
        report = fidelity.size_report(glb, "x.glb")
        self.assertEqual((report["draw_calls"], report["triangles"]), (2, 2))  # Root and Body, not the orphans
        glb.gltf["materials"][1].update(alphaMode="BLEND", doubleSided=True)  # three.js: back, then front faces
        self.assertEqual(fidelity.size_report(glb, "x.glb")["draw_calls"], 3)
        glb.gltf["nodes"][1]["extras"] = {"gs_id": "gs_body"}
        hidden = fidelity.size_report(glb, "x.glb", {"gs_body"})
        self.assertEqual((hidden["draw_calls"], hidden["triangles"]), (1, 1))


class CompareTest(FidelityTestCase):
    def test_compare_images_scores(self):
        rng = np.random.default_rng(1)
        detailed = (rng.random((60, 80, 3)) * 255).astype(np.uint8)
        flat = np.full((60, 80, 3), 128, dtype=np.uint8)
        same = compare.compare_images(compare.encode_png(detailed), compare.encode_png(detailed))
        self.assertEqual((same["mean_abs_diff"], same["verdict"]), (0.0, "match"))
        lost = compare.compare_images(compare.encode_png(detailed), compare.encode_png(flat))
        self.assertLess(lost["detail_ratio"], 0.05)
        self.assertEqual(lost["verdict"], "different")
        self.assertEqual(len(lost["worst_cells"]), 3)
        self.assertEqual(fidelity.image_size(lost["side_by_side_png"]), (80 * 3 + 2 * compare.GAP, 60))
        decoded = compare.decode_image(compare.encode_png(detailed))
        self.assertLess(np.abs(decoded * 255 - detailed).max(), 1.01)
        bigger = compare.compare_images(compare.encode_png(detailed), compare.encode_png(np.repeat(detailed, 2, 0)))
        self.assertEqual(bigger["size"], [80, 60])  # the game image is resized to Blender's

    def test_render_view_and_compare_views(self):
        cube("Box", bpy.data.materials.new("Plain"))
        camera = helpers.new_object("Cam", bpy.data.cameras.new("Cam"), location=(0, -6, 2))
        camera.rotation_euler = (1.3, 0, 0)
        scene = bpy.context.scene
        scene.camera = camera
        render = scene.render
        before = (render.engine, render.resolution_x, render.resolution_y, render.filepath, scene.camera)
        view = compare.render_view(engine="workbench", size=(64, 36))
        self.assertEqual(fidelity.image_size(view["png"]), (64, 36))
        self.assertEqual((view["camera"], view["engine"]), ("Cam", "BLENDER_WORKBENCH"))
        self.assertEqual((render.engine, render.resolution_x, render.resolution_y, render.filepath, scene.camera),
                         before)
        ids.ensure_id(camera)
        request = compare.game_request(size=(64, 36))
        self.assertEqual(request, {"scene": "main", "camera": "Cam", "camera_id": ids.get_id(camera),
                                   "active_camera": True, "size": [64, 36], "state": "stopped", "frames": 3})
        out = self.root / "compare"
        result = compare.compare_views(view["png"], engine="workbench", out_dir=out)
        self.assertEqual(result["verdict"], "match")
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["blender.png", "game.png", "side_by_side.png"])
        with self.assertRaises(compare.CompareError):
            compare.render_view(camera="Box", engine="workbench")
        # The render uses the scene's view transform, which the game follows (settings.toneMapping).
        scene.view_settings.view_transform = "Khronos PBR Neutral"
        neutral = compare.render_view(engine="workbench", size=(64, 36))
        standard = compare.render_view(engine="workbench", size=(64, 36), view_transform="Standard")
        self.assertEqual(scene.view_settings.view_transform, "Khronos PBR Neutral")
        explicit = compare.render_view(engine="workbench", size=(64, 36), view_transform="Khronos PBR Neutral")
        self.assertEqual(scene.view_settings.view_transform, "Khronos PBR Neutral")
        pixels = [compare.decode_image(v["png"]).tobytes() for v in (neutral, standard, explicit)]
        self.assertNotEqual(pixels[0], pixels[1])
        self.assertEqual(pixels[0], pixels[2])


if __name__ == "__main__":
    unittest.main()
