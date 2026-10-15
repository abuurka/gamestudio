# SPDX-License-Identifier: GPL-3.0-or-later
"""Live-transform conversion must equal the real glTF exporter's node TRS (§4.3)."""

import math
import tempfile

import bpy
from mathutils import Quaternion, Vector

from gamestudio.core import convert, export, ids

import helpers

TOLERANCE = 1e-4


def _close(a, b, tol=TOLERANCE) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _quat_close(a, b, tol=TOLERANCE) -> bool:
    return _close(a, b, tol) or _close(a, [-v for v in b], tol)


class ConvertMatchesExporterTest(helpers.ProjectTestCase):
    def build_scene(self):
        scene = bpy.context.scene
        mesh = helpers.new_mesh()

        rotated = helpers.new_object("Rotated", mesh, location=(1, 2, 3))
        rotated.rotation_euler = (0.3, 0.4, 0.5)
        rotated.scale = (1, 2, 3)

        child = helpers.new_object("Child", mesh, parent=rotated, location=(0.5, 0, 1))
        child.rotation_euler = (0.1, -0.7, 1.2)

        bpy.context.view_layer.update()
        with_inverse = helpers.new_object("ChildWithInverse", mesh, location=(4, -1, 2))
        with_inverse.rotation_euler = (0.5, 0.2, -0.3)
        with_inverse.scale = (0.5, 0.5, 2)
        with_inverse.parent = rotated
        with_inverse.matrix_parent_inverse = rotated.matrix_world.inverted()

        grandchild = helpers.new_object("MirroredGrandchild", mesh, parent=with_inverse, location=(0, 1, 0))
        grandchild.scale = (-1, 2, 1)

        quat = helpers.new_object("Quaternion", mesh, location=(-2, 5, 0.5))
        quat.rotation_mode = "QUATERNION"
        quat.rotation_quaternion = Quaternion((0.7, 0.1, -0.5, 0.3)).normalized()

        camera = helpers.new_object("Camera", bpy.data.cameras.new("Camera"), location=(0, -12, 7))
        camera.rotation_euler = (Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.camera = camera
        cam_child = helpers.new_object("CameraChild", None, parent=camera, location=(1, 1, -2))
        cam_child.rotation_euler = (0.2, 0.3, 0.4)

        sun = helpers.new_object("Sun", bpy.data.lights.new("Sun", "SUN"))
        sun.rotation_euler = (math.radians(40), math.radians(15), math.radians(30))
        spot = helpers.new_object("Spot", bpy.data.lights.new("Spot", "SPOT"), location=(3, 3, 3))
        spot.rotation_euler = (1.0, 0.0, 2.0)
        point = helpers.new_object("PointOnMesh", bpy.data.lights.new("Point", "POINT"),
                                   parent=rotated, location=(0, 0, 2))
        light_child = helpers.new_object("SpotChild", mesh, parent=spot, location=(0, 0, -1))
        light_child.scale = (2, 1, 1)

        prefab = bpy.data.collections.new("Prefab")
        part = bpy.data.objects.new("PrefabPart", mesh)
        prefab.objects.link(part)
        prefab.instance_offset = (0.5, -0.25, 1)
        instancer = helpers.new_object("Instancer", None, location=(3, 0, 0))
        instancer.instance_type = "COLLECTION"
        instancer.instance_collection = prefab
        instancer.rotation_euler = (0, 0, 0.7)
        helpers.new_object("InstancerChild", None, parent=instancer, location=(0, 0, 2))

        bpy.context.view_layer.update()
        self.expected_entities = len(scene.objects)
        return camera

    def test_trs_equals_glb_nodes(self):
        camera = self.build_scene()
        with tempfile.TemporaryDirectory(dir=helpers.tmp_base()) as out:
            result = export.export_scene_to(out)
            gltf, _chunks = export.read_glb(result["glb"])

        checked = 0
        for node in gltf["nodes"]:
            gs_id = (node.get("extras") or {}).get("gs_id")
            if gs_id is None:
                continue
            obj = ids.find_by_id(gs_id)
            self.assertIsNotNone(obj, node["name"])
            self.assertEqual(obj.name, node["name"])
            p, r, s = convert.to_runtime_trs(obj)
            node_p = node.get("translation", [0.0, 0.0, 0.0])
            node_r = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
            node_s = node.get("scale", [1.0, 1.0, 1.0])
            self.assertTrue(_close(p, node_p), f"{obj.name} translation {p} != {node_p}")
            self.assertTrue(_quat_close(r, node_r), f"{obj.name} rotation {r} != {node_r}")
            self.assertTrue(_close(s, node_s), f"{obj.name} scale {s} != {node_s}")
            checked += 1
        self.assertEqual(checked, self.expected_entities)

        # The reference values from runtime/fixtures/basic/basic.glb.
        p, r, _s = convert.to_runtime_trs(camera)
        self.assertTrue(_close(p, [0, 7, 12]), p)
        self.assertTrue(_quat_close(r, [-0.2297, 0, 0, 0.9732], 1e-3), r)

    def test_transform_item_shape(self):
        obj = helpers.new_object("Thing", None, location=(1, 2, 3))
        bpy.context.view_layer.update()
        item = convert.transform_item(obj, "gs_000000000001")
        self.assertEqual(item["id"], "gs_000000000001")
        self.assertEqual(item["p"], [1.0, 3.0, -2.0])
        self.assertEqual(item["r"], [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(item["s"], [1.0, 1.0, 1.0])
        self.assertEqual(convert.vector_to_runtime((0, 0, -9.81)), [0.0, -9.81, 0.0])
