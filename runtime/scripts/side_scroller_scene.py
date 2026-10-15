# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender half of runtime/scripts/side_scroller.mjs: a 2.5D side-scroller level, authored and exported.

  blender --background --factory-startup --python runtime/scripts/side_scroller_scene.py -- <project>

Blender is Z-up and the game is Y-up: the level runs along Blender X (game X), up is Blender Z (game Y)
and the depth is Blender Y (game Z, the axis a side-scroller locks).
  Ground, Step_1..4   platforms with Colliders
  Player              CharacterController {plane: 'side'}, a kinematic RigidBody and a capsule Collider
  Crate_1, Crate_2    dynamic RigidBody {lockZ: true}: the player pushes them, they stay in the plane
  Backdrop_*          scenery behind the level (no collider)
  LevelBounds         a Cube empty over the level: SideCamera.bounds
  Camera              orthographic (ortho scale 16 at 1280 x 720, so 9 m high), at Blender -Y looking at +Y
                      (rotation 90°, 0, 0); SideCamera follows the Player

Prints "SIDE <json>" lines for the Node side and exits non-zero on failure.
"""

import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "blender"))
sys.dont_write_bytecode = True

import bpy  # noqa: E402

import gamestudio  # noqa: E402
from gamestudio.core import components, export, ids, project, registry  # noqa: E402

PROJECT = pathlib.Path(sys.argv[sys.argv.index("--") + 1])
BLEND = PROJECT / "scenes" / "main.blend"


def say(kind, **data):
    print("SIDE " + json.dumps({"kind": kind, **data}), flush=True)


def fail(message):
    say("error", message=message)
    sys.exit(1)


def material(name, rgba):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
    return mat


def box(name, lo, hi, mat):
    """A box mesh from corner lo to corner hi (Blender coordinates), with its origin at the bottom centre."""
    sx, sy, sz = ((hi[i] - lo[i]) / 2 for i in range(3))
    h = hi[2] - lo[2]
    mesh = bpy.data.meshes.new(name + "Mesh")
    verts = [(-sx, -sy, 0), (sx, -sy, 0), (sx, sy, 0), (-sx, sy, 0),
             (-sx, -sy, h), (sx, -sy, h), (sx, sy, h), (-sx, sy, h)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(verts, [], faces)
    mesh.materials.append(mat)
    o = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(o)
    o.location = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2])
    return o


def author():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    BLEND.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    root = project.project_root()
    if root is None or not registry.reload_registry(root):
        fail(f"no project or registry at {PROJECT}")
    missing = [n for n in ("SideCamera", "CharacterController", "RigidBody") if registry.schema_ref(n) is None]
    if missing:
        fail(f"registry lacks {missing}")

    scene = bpy.context.scene
    scene.name = "main"
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720

    stone = material("Stone", (0.32, 0.36, 0.42, 1))
    grass = material("Grass", (0.25, 0.6, 0.3, 1))
    wood = material("Wood", (0.65, 0.4, 0.18, 1))
    hero = material("Hero", (0.95, 0.3, 0.2, 1))
    hills = material("Hills", (0.55, 0.65, 0.85, 1))

    ground = box("Ground", (-12, -2, -1), (40, 2, 0), grass)
    components.add_component(ground, "Collider")
    for i, (x0, x1, top) in enumerate(((4, 7, 1.3), (9, 12, 2.6), (15, 18, 1.6), (21, 25, 3.0)), start=1):
        step = box(f"Step_{i}", (x0, -1.5, top - 0.4), (x1, 1.5, top), stone)
        components.add_component(step, "Collider")
    box("Wall_L", (-13, -2, -1), (-12, 2, 12), stone)
    components.add_component(bpy.data.objects["Wall_L"], "Collider")
    for i, x in enumerate(range(-10, 40, 7)):  # scenery well behind the plane
        box(f"Backdrop_{i}", (x, 5, -1), (x + 4, 6, 2 + (i % 3) * 1.5), hills)

    player = box("Player", (-8.3, -0.3, 0), (-7.7, 0.3, 1.6), hero)
    components.add_component(player, "RigidBody", {"type": "kinematic"})
    components.add_component(player, "Collider", {"shape": "capsule"})
    components.add_component(player, "CharacterController", {"plane": "side", "speed": 5, "jumpSpeed": 8})

    for i, (x, z) in enumerate(((0, 0), (0.1, 0.8)), start=1):
        crate = box(f"Crate_{i}", (x - 0.4, -0.4, z), (x + 0.4, 0.4, z + 0.8), wood)
        components.add_component(crate, "RigidBody", {"type": "dynamic", "mass": 1, "lockZ": True})
        components.add_component(crate, "Collider", {"shape": "box", "friction": 0.4})

    bounds = bpy.data.objects.new("LevelBounds", None)
    bounds.empty_display_type = "CUBE"
    scene.collection.objects.link(bounds)
    bounds.location = (14, 0, 6)  # x -13..41, z -1..13
    bounds.scale = (27, 4, 7)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 16
    camera = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(camera)
    camera.location = (-8, -20, 3)
    camera.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = camera
    components.add_component(camera, "SideCamera", {
        "target": player, "bounds": bounds, "offset": [0, 1.5], "deadZone": [1, 2], "lookAhead": 2.5})

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    scene.collection.objects.link(sun)
    sun.data.energy = 3
    sun.rotation_euler = (math.radians(50), math.radians(-25), 0)

    result = export.export_scene()
    bpy.ops.wm.save_mainfile()
    say("export", result=result, ids={o.name: ids.get_id(o) for o in bpy.data.objects if ids.get_id(o)})


gamestudio.register()
try:
    author()
except SystemExit:
    raise
except Exception as error:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    fail(f"{type(error).__name__}: {error}")
