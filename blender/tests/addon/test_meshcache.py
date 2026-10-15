# SPDX-License-Identifier: GPL-3.0-or-later
"""The cross-export cache of the glTF exporter's mesh extraction (core/meshcache.py, docs/SPEC.md
§4.6): exports with the cache cold, warm and after edits are byte for byte the exports without it,
every edit a mesh's result depends on misses the cache (only for that mesh), calls the cache cannot
key run the exporter's own code, and the exporter's function is always put back."""

import contextlib
import importlib
import os
import pathlib
import tempfile
from unittest import mock

import bpy

from gamestudio import kits
from gamestudio.core import export, meshcache

import helpers

EXTRACT = importlib.import_module(meshcache.EXTRACTOR)


@contextlib.contextmanager
def cache_off():
    meshcache.set_enabled(False)
    try:
        yield
    finally:
        meshcache.set_enabled(True)


def material(name, color=(0.8, 0.8, 0.8, 1.0), color_attribute=None, uv_map=None):
    """A Principled material; color_attribute: its base colour reads that attribute; uv_map: an
    image texture on its emission reads that UV map."""
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = color
    if color_attribute:
        node = tree.nodes.new("ShaderNodeVertexColor")
        node.layer_name = color_attribute
        tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    if uv_map:
        image = bpy.data.images.get("Grid") or bpy.data.images.new("Grid", 8, 8)
        image.generated_type = "COLOR_GRID"
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.uv_map = uv_map
        tree.links.new(uv.outputs["UV"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], bsdf.inputs["Emission Color"])
    return mat


def primitive(kind, name, location, **kwargs):
    getattr(bpy.ops.mesh, f"primitive_{kind}_add")(location=location, **kwargs)
    obj = bpy.context.active_object
    obj.name = obj.data.name = name
    return obj


def shape_keys(obj, mute_one=True):
    obj.shape_key_add(name="Basis")
    up = obj.shape_key_add(name="Up", from_mix=False)
    for i, point in enumerate(up.points):
        if i % 3 == 0:
            point.co.z += 0.3
    side = obj.shape_key_add(name="Side", from_mix=False)
    side.relative_key = up
    for i, point in enumerate(side.points):
        if i % 2 == 0:
            point.co.x += 0.2
    if mute_one:
        obj.shape_key_add(name="Muted", from_mix=False).mute = True


def build_level() -> dict:
    """One object per case the cache must key: skinning, shape keys (with and without modifiers),
    several UV maps, colour attributes, custom normals, modifiers, several material slots and a mesh
    two objects share with object-linked materials."""
    objs = {}
    rig, body = kits.create("character_rigged", {"name": "Hero"})
    objs.update(rig=rig, body=body)

    objs["blob"] = blob = primitive("uv_sphere", "Blob", (3, 0, 0), segments=16, ring_count=8)
    shape_keys(blob)
    blob.data.materials.append(material("BlobMat", (0.2, 0.6, 0.9, 1)))

    objs["morphed"] = morphed = primitive("cube", "Morphed", (6, 0, 0))
    shape_keys(morphed, mute_one=False)
    morphed.modifiers.new("Mirror", "MIRROR").use_axis = (True, False, False)

    objs["slots"] = slots = primitive("cube", "Slots", (0, 3, 0))
    for i, color in enumerate(((1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1))):
        slots.data.materials.append(material(f"Slot{i}", color))
    for face in slots.data.polygons:
        face.material_index = face.index % 3

    objs["uvs"] = uvs = primitive("cube", "TwoUVs", (3, 3, 0))
    second = uvs.data.uv_layers.new(name="Second")
    for loop in second.uv:
        loop.vector = (loop.vector.x * 0.5 + 0.25, loop.vector.y * 0.5)
    uvs.data.materials.append(material("SecondUV", uv_map="Second"))

    objs["paint"] = paint = primitive("cube", "Painted", (6, 3, 0))
    corner = paint.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
    for i, value in enumerate(corner.data):
        value.color = ((i % 4) / 4, 0.5, 1 - (i % 3) / 3, 1.0)
    point = paint.data.color_attributes.new("Tint", "FLOAT_COLOR", "POINT")
    for i, value in enumerate(point.data):
        value.color = (0.1 * i, 0.2, 0.3, 0.5)
    paint.data.materials.append(material("Painted", color_attribute="Col"))

    objs["smooth"] = smooth = primitive("uv_sphere", "Smooth", (0, 6, 0), segments=12, ring_count=6)
    normals = [tuple(loop.normal) for loop in smooth.data.loops]
    smooth.data.normals_split_custom_set([(n[0], n[1] + 0.3, n[2]) for n in normals])

    # Bevel is left out: its UVs come out one float ULP apart from one evaluation to the next, so
    # its exports differ (with the cache too: the key follows the evaluated mesh).
    objs["arrayed"] = arrayed = primitive("cube", "Arrayed", (3, 6, 0))
    arrayed.modifiers.new("Array", "ARRAY").count = 3
    arrayed.modifiers.new("Triangulate", "TRIANGULATE")
    objs["subdivided"] = subdivided = primitive("cube", "Subdivided", (6, 6, 0))
    subdivided.modifiers.new("Subdivision", "SUBSURF").levels = 1

    objs["shared_a"] = shared_a = primitive("cube", "SharedA", (0, 9, 0))
    shared_a.data.name = "Shared"
    shared_a.data.materials.append(material("MeshMat", (0.5, 0.5, 0.5, 1)))
    shared_a.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
    objs["shared_b"] = shared_b = helpers.new_object("SharedB", shared_a.data, location=(3, 9, 0))
    for obj, mat in ((shared_a, material("Red", (1, 0, 0, 1))),
                     (shared_b, material("VertexColour", color_attribute="Col"))):
        obj.material_slots[0].link = "OBJECT"
        obj.material_slots[0].material = mat
    bpy.context.view_layer.update()
    return objs


class MeshCacheTestCase(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        # The exporter encodes images through a temporary folder; an earlier test may have removed TEMP.
        os.makedirs(tempfile.gettempdir(), exist_ok=True)
        bpy.context.scene.name = "main"
        meshcache.set_enabled(True)
        meshcache.clear()
        self.count = 0
        self.objs = build_level()

    def tearDown(self):
        meshcache.set_enabled(True)
        meshcache.clear()
        super().tearDown()

    def glb(self, cached=True) -> pathlib.Path:
        """Run the exporter the way export_scene_to() does (export._gltf()) into a new file."""
        self.count += 1
        path = self.root / f"run{self.count}.glb"
        with contextlib.nullcontext() if cached else cache_off():
            export._run_gltf_export(bpy.context.scene, str(path))
        return path

    def counted(self) -> tuple:
        """(a cached export, the hits, misses and uncached calls it counted)."""
        before = meshcache.stats()
        path = self.glb()
        after = meshcache.stats()
        return path, {name: after[name] - before[name] for name in ("hits", "misses", "uncached")}

    def assertSameGlb(self, expected: pathlib.Path, actual: pathlib.Path, label=""):
        if expected.read_bytes() == actual.read_bytes():
            return
        gltf_a, chunks_a = export.read_glb(expected)
        gltf_b, chunks_b = export.read_glb(actual)
        for section in sorted(set(gltf_a) | set(gltf_b)):
            self.assertEqual(gltf_a.get(section), gltf_b.get(section), f"{label}: {section} differs")
        self.assertEqual(chunks_a, chunks_b, f"{label}: the binary chunk differs")
        self.fail(f"{label}: the GLB bytes differ")

    def assertEditMisses(self, edit, label, misses=1):
        """After edit(), a cached export misses for the edited mesh only and is the uncached one."""
        edit()
        bpy.context.view_layer.update()
        cached, counts = self.counted()
        self.assertEqual(counts["misses"], misses, f"{label}: {counts}")
        self.assertEqual(counts["uncached"], 0, label)
        self.assertSameGlb(self.glb(cached=False), cached, label)


class MeshCacheTest(MeshCacheTestCase):
    def test_exporter_is_the_checked_version(self):
        # Fails on a Blender whose glTF exporter this cache was not checked against (it then runs
        # uncached): compare primitive_extract.py with meshcache._key() and add the fingerprint.
        self.assertIsNone(meshcache.supported())

    def test_cold_warm_and_edited_exports_are_the_exporters(self):
        stock = self.glb(cached=False)
        self.assertEqual(meshcache.stats()["misses"] + meshcache.stats()["hits"], 0)  # off: not wrapped
        cold, counts = self.counted()
        meshes = counts["misses"]
        self.assertGreaterEqual(meshes, 10)  # each mesh once; the shared mesh once for both objects
        self.assertEqual((counts["hits"], counts["uncached"]), (0, 0))
        self.assertEqual(meshcache.stats()["entries"], meshes)
        warm, counts = self.counted()
        self.assertEqual(counts, {"hits": meshes, "misses": 0, "uncached": 0})
        self.assertSameGlb(stock, cold, "cold")
        self.assertSameGlb(stock, warm, "warm")
        self.assertGreater(pathlib.Path(stock).stat().st_size, 10_000)

        mesh = self.objs["slots"].data
        self.assertEditMisses(lambda: setattr(mesh.vertices[0], "co", (0.9, 0.7, 0.6)), "one vertex moved")
        _again, counts = self.counted()
        self.assertEqual(counts, {"hits": meshes, "misses": 0, "uncached": 0})

    def test_mesh_edits_miss_the_cache(self):
        self.glb()  # fill the cache
        objs = self.objs
        second = objs["uvs"].data.uv_layers["Second"]
        self.assertEditMisses(lambda: setattr(second.uv[2], "vector", (0.9, 0.1)), "a UV of the second map")
        self.assertEditMisses(lambda: setattr(objs["slots"].data.polygons[0], "material_index", 2),
                              "a face moved to another slot")
        corner = objs["paint"].data.color_attributes["Col"]
        self.assertEditMisses(lambda: setattr(corner.data[3], "color", (0.0, 1.0, 0.0, 1.0)), "a corner colour")
        self.assertEditMisses(lambda: setattr(objs["paint"].data.color_attributes, "render_color_index", 1),
                              "the render colour attribute")
        smooth = objs["smooth"].data
        self.assertEditMisses(lambda: smooth.normals_split_custom_set([(0.0, 0.0, 1.0)] * len(smooth.loops)),
                              "custom normals")
        self.assertEditMisses(lambda: setattr(objs["arrayed"].modifiers["Array"], "count", 2), "a modifier")
        face = objs["blob"].data.polygons[1]
        self.assertEditMisses(lambda: setattr(face, "use_smooth", not face.use_smooth), "a face's shading")

    def test_shape_key_edits_miss_the_cache(self):
        self.glb()
        blocks = self.objs["blob"].data.shape_keys.key_blocks
        self.assertEditMisses(lambda: setattr(blocks["Up"].points[4], "co", (1.0, 2.0, 3.0)), "a shape key point")
        self.assertEditMisses(lambda: setattr(blocks["Muted"], "mute", False), "a shape key unmuted")
        self.assertEditMisses(lambda: setattr(blocks["Side"], "relative_key", blocks["Basis"]), "a relative key")
        morphed = self.objs["morphed"].data.shape_keys.key_blocks
        self.assertEditMisses(lambda: setattr(morphed["Side"].points[0], "co", (0.0, 0.0, 2.0)),
                              "a shape key under a modifier")
        # A shape key's value is an animated weight: it is not part of the extracted mesh.
        blocks["Up"].value = 0.7
        cached, counts = self.counted()
        self.assertEqual(counts["misses"], 0)
        self.assertSameGlb(self.glb(cached=False), cached, "a shape key value")

    def test_skin_edits_miss_the_cache(self):
        self.glb()
        body, rig = self.objs["body"], self.objs["rig"]
        group = body.vertex_groups[1]
        members = [v.index for v in body.data.vertices if any(g.group == group.index for g in v.groups)]
        self.assertGreaterEqual(len(members), 2)
        self.assertEditMisses(lambda: group.add(members[:1], 0.123, "REPLACE"), "a vertex weight")
        self.assertEditMisses(lambda: group.remove(members[1:2]), "a vertex taken out of a group")
        self.assertEditMisses(lambda: setattr(group, "name", "NotABone"), "a vertex group renamed")
        self.assertEditMisses(lambda: setattr(rig, "location", (0.0, 1.0, 0.0)), "the rig moved")

    def test_material_edits_miss_only_when_extraction_reads_them(self):
        self.glb()
        slot = self.objs["slots"].material_slots[1]
        self.assertEditMisses(lambda: setattr(slot, "material", material("Reads", color_attribute="Col")),
                              "a slot's material now reads a colour attribute")
        principled = bpy.data.materials["Slot0"].node_tree.nodes["Principled BSDF"]
        principled.inputs["Base Color"].default_value = (0.3, 0.3, 0.3, 1.0)
        _path, counts = self.counted()
        self.assertEqual(counts["misses"], 0, "a material colour is not part of the extracted mesh")

    def test_ui_state_and_object_moves_hit(self):
        self.glb()
        for vertex in self.objs["slots"].data.vertices:
            vertex.select = not vertex.select
        self.objs["slots"].data.polygons[2].hide = True
        self.objs["blob"].location.x += 1.0  # not skinned: the mesh is in object space
        bpy.context.view_layer.update()
        cached, counts = self.counted()
        self.assertEqual(counts["misses"], 0)
        self.assertSameGlb(self.glb(cached=False), cached, "selection, hiding and a move")


class MeshCacheFallbackTest(MeshCacheTestCase):
    def test_calls_the_cache_cannot_key_run_uncached(self):
        stock = self.glb(cached=False)
        with mock.patch.dict(meshcache.VALUES):
            del meshcache.VALUES["FLOAT2"]  # UV maps can no longer be read: every mesh has one
            cached, counts = self.counted()
        self.assertEqual(counts["hits"] + counts["misses"], 0)
        self.assertGreaterEqual(counts["uncached"], 10)
        self.assertEqual(meshcache.stats()["entries"], 0)
        self.assertSameGlb(stock, cached, "uncached")

    def test_an_unexpected_exporter_is_left_alone(self):
        original = EXTRACT.extract_primitives

        def extract_primitives(materials, blender_mesh, export_settings):
            return original(materials, blender_mesh, None, None, None, export_settings)

        with mock.patch.object(meshcache, "_logged", set()), mock.patch.object(meshcache, "_verdict", None):
            with mock.patch.object(EXTRACT, "extract_primitives", extract_primitives):
                with self.assertLogs("gamestudio.meshcache", "INFO") as logs, meshcache.armed():
                    self.assertIs(EXTRACT.extract_primitives, extract_primitives)
                with self.assertNoLogs("gamestudio.meshcache", "INFO"), meshcache.armed():  # logged once
                    self.assertIs(EXTRACT.extract_primitives, extract_primitives)
            self.assertIn("replaced by another function", logs.output[0])
            with mock.patch.object(meshcache, "KNOWN_SOURCES", {}):
                stock = self.glb(cached=False)
                with self.assertLogs("gamestudio.meshcache", "INFO") as logs:
                    cached, counts = self.counted()
                self.assertIn("not a version this cache was checked against", logs.output[0])
                self.assertEqual(counts, {"hits": 0, "misses": 0, "uncached": 0})
                self.assertSameGlb(stock, cached, "unknown exporter")
        self.assertIs(EXTRACT.extract_primitives, original)

    def test_the_exporters_function_is_put_back_after_errors(self):
        original = EXTRACT.extract_primitives
        with self.assertRaises(ZeroDivisionError), meshcache.armed():
            self.assertIsNot(EXTRACT.extract_primitives, original)
            self.assertIs(EXTRACT.extract_primitives.__wrapped__, original)
            1 / 0
        self.assertIs(EXTRACT.extract_primitives, original)

        def fail(_self):
            raise RuntimeError("extraction failed")

        with mock.patch.object(EXTRACT.PrimitiveCreator, "prepare_data", fail):
            with self.assertRaises(Exception):
                self.glb()
        self.assertIs(EXTRACT.extract_primitives, original)
        self.assertEqual(meshcache.stats()["entries"], 0)
        self.glb()  # and the next export works
        self.assertGreaterEqual(meshcache.stats()["entries"], 10)

    def test_the_cache_is_bounded(self):
        self.glb()
        largest = max(entry.nbytes for entry in meshcache._entries.values())
        self.assertLess(largest, meshcache.stats()["bytes"])
        with mock.patch.object(meshcache, "MAX_BYTES", largest + 1):  # room for any one entry, not all
            meshcache.clear()
            stock = self.glb(cached=False)
            self.glb()
            self.assertLessEqual(meshcache.stats()["bytes"], meshcache.MAX_BYTES)
            self.assertGreater(meshcache.stats()["evicted"], 0)
            cached, counts = self.counted()
            self.assertGreater(counts["misses"], 0)
            self.assertSameGlb(stock, cached, "bounded")


class MeshCacheFullExportTest(MeshCacheTestCase):
    def test_full_exports_with_prefabs_are_the_exporters(self):
        prefab = bpy.data.collections.new("Crate")
        crate = primitive("cube", "CrateMesh", (0, -4, 0))
        for coll in crate.users_collection:
            coll.objects.unlink(crate)
        prefab.objects.link(crate)
        crate.data.materials.append(material("CrateMat", (0.6, 0.4, 0.2, 1)))
        export.set_prefab(prefab)
        instance = helpers.new_object("CrateInstance", None, location=(2, -4, 0))
        instance.instance_type, instance.instance_collection = "COLLECTION", prefab
        bpy.context.view_layer.update()

        def full(label, cached=True):
            with contextlib.nullcontext() if cached else cache_off():
                result = export.export_scene_to(self.root / label / "export", bake="off")
            glb = pathlib.Path(result["glb"])
            return glb, glb.with_suffix(".prefabs.glb")

        stock = full("stock", cached=False)
        cold = full("cold")
        warm = full("warm")
        self.assertGreater(meshcache.stats()["hits"], 10)
        for label, run in (("cold", cold), ("warm", warm)):
            for expected, actual in zip(stock, run):
                self.assertTrue(expected.exists(), expected)
                self.assertSameGlb(expected, actual, f"{label} {actual.name}")
