# SPDX-License-Identifier: MIT
"""Builds runtime/fixtures/basic/{basic.glb, basic.scene.json} with Blender.

This fixture lets the runtime be tested without the add-on. It mimics the
add-on's export (docs/SPEC.md section 4), using custom properties for gs_id.

Run:
  blender --background --factory-startup --python runtime/fixtures/make_basic_fixture.py -- runtime/fixtures/basic
"""

import json
import math
import os
import sys

import bpy
from mathutils import Vector

out_dir = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "basic"

_ids = iter(f"gs_{i:012x}" for i in range(0xA00001, 0xA00100))


def material(name, rgba):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
    return mat


def finish(obj, name, mat=None):
    obj.name = name
    obj["gs_id"] = next(_ids)
    if mat is not None and obj.data is not None and hasattr(obj.data, "materials"):
        obj.data.materials.append(mat)
    return obj


bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.25))
ground = finish(bpy.context.object, "Ground", material("GroundMat", (0.25, 0.3, 0.25, 1)))
ground.scale = (20, 20, 0.5)

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 4))
crate = finish(bpy.context.object, "Crate", material("CrateMat", (0.8, 0.5, 0.2, 1)))
crate.rotation_euler = (0.3, 0.2, 0.5)

bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, 4.8))
lamp = finish(bpy.context.object, "CrateLamp", material("LampMat", (1, 1, 0.3, 1)))
lamp.parent = crate
lamp.matrix_parent_inverse = crate.matrix_world.inverted()

bpy.ops.mesh.primitive_cube_add(size=1, location=(5, 0, 0.6))
ramp = finish(bpy.context.object, "Ramp", material("RampMat", (0.4, 0.4, 0.5, 1)))
ramp.scale = (4, 3, 0.3)
ramp.rotation_euler = (0, math.radians(-15), 0)

bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=1.8, location=(-3, 0, 0.9))
player = finish(bpy.context.object, "Player", material("PlayerMat", (0.2, 0.4, 0.9, 1)))

bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=0.1, location=(2, 3, 1))
coin = finish(bpy.context.object, "Coin", material("CoinMat", (1.0, 0.8, 0.1, 1)))
coin.rotation_euler = (math.radians(90), 0, 0)

cam_data = bpy.data.cameras.new("Camera")
camera = bpy.data.objects.new("Camera", cam_data)
scene.collection.objects.link(camera)
camera.location = (0, -12, 7)
direction = Vector((0, 0, 1)) - camera.location
camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
finish(camera, "Camera")
scene.camera = camera

sun_data = bpy.data.lights.new("Sun", type="SUN")
sun_data.energy = 3
sun = bpy.data.objects.new("Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(40), math.radians(15), math.radians(30))
finish(sun, "Sun")

glb_path = os.path.join(out_dir, "basic.glb")
bpy.ops.export_scene.gltf(
    filepath=glb_path,
    export_format="GLB",
    export_extras=True,
    export_yup=True,
    export_apply=True,
    export_cameras=True,
    export_lights=True,
    use_visible=False,
)

RIGID_DEFAULTS = {"type": "dynamic", "mass": 1, "linearDamping": 0, "angularDamping": 0.05,
                  "gravityScale": 1, "lockRotation": False, "ccd": False}
COLLIDER_DEFAULTS = {"shape": "auto", "friction": 0.5, "restitution": 0, "sensor": False}


def entity(obj, components=None):
    return {
        "id": obj["gs_id"],
        "name": obj.name,
        "type": obj.type,
        "parent": obj.parent["gs_id"] if obj.parent else None,
        "components": components or {},
    }


entities = [
    entity(ground, {"Collider": dict(COLLIDER_DEFAULTS)}),
    entity(crate, {"RigidBody": dict(RIGID_DEFAULTS), "Collider": dict(COLLIDER_DEFAULTS)}),
    entity(lamp),
    entity(ramp, {"Collider": dict(COLLIDER_DEFAULTS)}),
    entity(player, {
        "RigidBody": dict(RIGID_DEFAULTS, type="kinematic"),
        "Collider": dict(COLLIDER_DEFAULTS, shape="capsule"),
        "CharacterController": {"speed": 5, "jumpSpeed": 6, "gravity": 20, "turnSpeed": 10},
    }),
    entity(coin, {"Spin": {"speed": 2, "axis": [0, 1, 0]},
                  "Collider": dict(COLLIDER_DEFAULTS, shape="sphere", sensor=True)}),
    entity(camera, {"FollowCamera": {"target": player["gs_id"], "offset": [0, 4, 8],
                                     "smoothing": 5, "lookAtOffset": [0, 1, 0]}}),
    entity(sun),
]

manifest = {
    "format": "gamestudio.scene",
    "formatVersion": 1,
    "scene": "basic",
    "rev": 1,
    "exportedAt": "2026-10-09T00:00:00.000Z",
    "blenderVersion": bpy.app.version_string,
    "glb": "basic.glb",
    "settings": {
        "gravity": [0, -9.81, 0],
        "background": [0.05, 0.06, 0.09],
        "activeCamera": camera["gs_id"],
    },
    "entities": entities,
}
with open(os.path.join(out_dir, "basic.scene.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print("FIXTURE_OK", glb_path)
