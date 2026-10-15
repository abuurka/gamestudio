# SPDX-License-Identifier: GPL-3.0-or-later
"""The bundled glTF exporter: parameters and TRS conventions GameStudio depends on.

core/export.py runs `bpy.ops.export_scene.gltf` with `GLTF_SETTINGS` and then matches glTF nodes
to objects by name. core/convert.py re-implements the exporter's node transforms for the live
link, so the runtime gets the same TRS from a live edit as from a fresh export (docs/SPEC.md
section 4.3). The golden values below were recorded from Blender 5.2.2 (io_scene_gltf2 5.2.40)
and agree with the convention documented in core/convert.py.
"""

import json
import math
import pathlib
import shutil
import struct
import unittest

import bpy

import contract_support as cs

TOLERANCE = 1e-5
EXPORT_PY = cs.EXTENSION_SRC / "core" / "export.py"
FALLBACK_SETTINGS = {
    "export_format": "GLB", "export_extras": False, "export_yup": True, "export_apply": True,
    "export_cameras": True, "export_lights": True, "use_visible": False, "export_animations": True,
    "use_active_scene": True, "use_selection": False,
}

# Camera at (1, 2, 3), rotation_euler (60, 0, 30) degrees XYZ.
CAMERA_GOLDEN_T = [1.0, 3.0, -2.0]
CAMERA_GOLDEN_R = [-0.25, 0.25, 0.0669873, 0.9330127]          # glTF (x, y, z, w)
# Sun at (0, 0, 5), rotation_euler (40, 15, 30) degrees XYZ.
SUN_GOLDEN_R = [-0.4353430, 0.2858461, -0.0058202, 0.8536596]
# Empty parented to the camera at local (0, 0, -2), no rotation: the exporter undoes the
# camera's -90 degree X correction in the child's local transform.
CAMERA_CHILD_GOLDEN_T = [0.0, 0.0, -2.0]
CAMERA_CHILD_GOLDEN_R = [0.7071068, 0.0, 0.0, 0.7071068]
TRICKY_NAMES = ("Crate.001", "Space Name", "dot.and-dash", "Ünïcode_名前")


def gltf_settings() -> dict:
    """GLTF_SETTINGS from core/export.py, so the test follows the code."""
    if EXPORT_PY.is_file():
        return cs.module_constant(EXPORT_PY, "GLTF_SETTINGS")
    return dict(FALLBACK_SETTINGS)


def read_glb_json(path) -> dict:
    data = pathlib.Path(path).read_bytes()
    length, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A, "first GLB chunk is not JSON"
    return json.loads(data[20:20 + length].decode("utf-8"))


def _close(a, b, tol=TOLERANCE) -> bool:
    return a is not None and len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _quat_close(a, b, tol=TOLERANCE) -> bool:
    return _close(a, b, tol) or (a is not None and _close([-v for v in a], b, tol))


class GltfParametersTest(unittest.TestCase):
    def test_export_parameters_exist(self):
        """Every keyword in core/export.py GLTF_SETTINGS is still an exporter property, with valid enum values.

        Breaks if changed: core/export.py `_run_gltf_export` (the operator raises TypeError on an
        unknown keyword, so Export Scene, Play and the live link's geometry rebuilds all fail).
        """
        op = bpy.ops.export_scene.gltf
        props = op.get_rna_type().properties
        for key, value in gltf_settings().items():
            with self.subTest(param=key):
                self.assertIn(key, props.keys(), f"export_scene.gltf has no '{key}' parameter")
                prop = props[key]
                if prop.type == "ENUM" and isinstance(value, str):
                    self.assertIn(value, cs.operator_enum_items(op, key))
                elif prop.type == "BOOLEAN":
                    self.assertIsInstance(value, bool)


class GltfConventionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cs.factory_reset(use_empty=True)
        scene = bpy.context.scene

        def add(name, data=None, location=(0, 0, 0), rotation=(0, 0, 0), parent=None):
            obj = bpy.data.objects.new(name, data)
            scene.collection.objects.link(obj)
            obj.location = location
            obj.rotation_euler = rotation
            if parent is not None:
                obj.parent = parent
            return obj

        rad = math.radians
        camera = add("Camera", bpy.data.cameras.new("Camera"), (1, 2, 3), (rad(60), 0, rad(30)))
        add("CameraChild", None, (0, 0, -2), parent=camera)
        add("Sun", bpy.data.lights.new("Sun", "SUN"), (0, 0, 5), (rad(40), rad(15), rad(30)))
        mesh = bpy.data.meshes.new("Tri")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        for name in TRICKY_NAMES:
            add(name, mesh, (1, 2, 3), (0.3, 0.4, 0.5))
        bpy.context.view_layer.update()
        cls.object_names = sorted(obj.name for obj in scene.objects)

        cls.folder = cs.make_tmp_dir("gs_contract_gltf_")
        path = cls.folder / "contract.glb"
        window = cs.any_window()
        with bpy.context.temp_override(window=window, scene=scene, view_layer=bpy.context.view_layer):
            result = bpy.ops.export_scene.gltf(filepath=str(path), **gltf_settings())
        assert "FINISHED" in result, result
        cls.gltf = read_glb_json(path)
        cls.nodes = {node.get("name"): node for node in cls.gltf.get("nodes", [])}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)
        cs.factory_reset(use_empty=True)

    def test_camera_node_rotation_golden(self):
        """A camera's node rotation (Y-up, with the -90 degree local X correction) matches the golden value.

        Breaks if changed: core/convert.py `CORRECTION` and `to_runtime_trs` (live camera moves
        would no longer agree with exported scenes), and the runtime's camera orientation.
        """
        node = self.nodes["Camera"]
        self.assertTrue(_quat_close(node.get("rotation"), CAMERA_GOLDEN_R), node.get("rotation"))

    def test_light_node_rotation_golden(self):
        """A light's node rotation gets the same correction as a camera (golden value).

        Breaks if changed: core/convert.py `_CORRECTED_TYPES` (live edits of sun and spot lights
        would point them in a different direction from an export).
        """
        node = self.nodes["Sun"]
        self.assertTrue(_quat_close(node.get("rotation"), SUN_GOLDEN_R), node.get("rotation"))

    def test_child_of_camera_compensates_correction(self):
        """A camera's child node is local to the *corrected* camera node (golden TRS).

        Breaks if changed: core/convert.py `local_matrix` (children of cameras and lights would
        jump when moved live).
        """
        node = self.nodes["CameraChild"]
        self.assertTrue(_close(node.get("translation", [0, 0, 0]), CAMERA_CHILD_GOLDEN_T), node)
        self.assertTrue(_quat_close(node.get("rotation", [0, 0, 0, 1]), CAMERA_CHILD_GOLDEN_R), node)

    def test_yup_translation_swizzle(self):
        """With export_yup, Blender (x, y, z) becomes glTF (x, z, -y).

        Breaks if changed: core/convert.py `vector_to_runtime` and `to_runtime_trs` translation.
        """
        self.assertTrue(_close(self.nodes["Camera"].get("translation"), CAMERA_GOLDEN_T),
                        self.nodes["Camera"].get("translation"))

    def test_node_names_equal_object_names(self):
        """Every object becomes a node named exactly like the object (dots, spaces, Unicode kept).

        Breaks if changed: core/export.py `inject_ids`, which matches nodes to objects by name to
        write `gs_id` extras; renamed nodes lose their entity IDs and components in the runtime.
        """
        self.assertEqual(sorted(self.nodes), self.object_names)
