# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender half of runtime/scripts/features.mjs: animation, audio, prefabs, particles and levels end to end.

  blender --background --factory-startup --python runtime/scripts/features_scene.py -- author <project>
  blender --background --factory-startup --python runtime/scripts/features_scene.py -- live <project> <sentinels>
  blender --background --factory-startup --python runtime/scripts/features_scene.py -- levels <project> <sentinels>

author: builds scenes/main.blend with the stock add-on API and plain bpy, then exports it.
  Flag     object actions: active "Wave" + NLA strip "Spin"; Animator plays Wave
  Rig      armature with a skinned mesh; Animator loops "Bend" (bone rotation)
  Door     Animator plays "Open" once (an end event)
  Speaker  AudioSource: assets/sfx/music.wav, looping, autoplay, positional
  Coin     a collection marked as a prefab (not in the scene): CoinRoot (Spin) > CoinBody (Animator "CoinBob")
  CoinInstance  a collection instance of Coin
  Spawner  the project's Spawner component: spawns Coin prefabs from code
  Fountain ParticleEmitter authored here: a steady stream of blue stars
  Mascot   carried into level2 by ctx.loadScene(..., { keep: ['Mascot'] })
level2 (scenes/level2.blend, its scene keeps the default name "Scene"): a sunset sky with mist (fog),
  its own Mascot (replaced by the kept one), a Portal ParticleEmitter, PostEffects (bloom, vignette)
  on the camera. author exports both levels with export.export_all() (level2 in a background Blender).
live: connects to the running dev server, edits Animator.clip on Flag (sent as entity.components),
  then adds another Coin instance, which the live link turns into a re-export + scene.load.
levels: opens level2 while a game plays it (after a ctx.loadScene) and moves the Portal live.

Prints "FEATURES <json>" lines for the Node side and exits non-zero on failure.
"""

import json
import math
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "blender"))
sys.dont_write_bytecode = True

import bpy  # noqa: E402

import gamestudio  # noqa: E402
from gamestudio.core import components, export, ids, link, live, project, registry  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE, PROJECT = ARGS[0], pathlib.Path(ARGS[1])
BLEND = PROJECT / "scenes" / "main.blend"
LEVEL2 = PROJECT / "scenes" / "level2.blend"


def say(kind, **data):
    print("FEATURES " + json.dumps({"kind": kind, **data}), flush=True)


def fail(message):
    say("error", message=message)
    sys.exit(1)


def mesh_data(name, size=0.5, height=1.0):
    mesh = bpy.data.meshes.new(name)
    s = size
    verts = [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0), (-s, -s, height), (s, -s, height), (s, s, height),
             (-s, s, height)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(verts, [], faces)
    return mesh


def material(name, rgba):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
    return mat


def obj(name, data=None, location=(0, 0, 0), parent=None, collection=None, mat=None):
    o = bpy.data.objects.new(name, data)
    (collection or bpy.context.scene.collection).objects.link(o)
    o.location = location
    if parent is not None:
        o.parent = parent
    if mat is not None and data is not None and hasattr(data, "materials"):
        data.materials.append(mat)
    return o


def keys(target, path, index, frames):
    for frame, value in frames:
        getattr(target, path)[index] = value
        target.keyframe_insert(path, index=index, frame=frame)


def build_level2():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    LEVEL2.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(LEVEL2))
    registry.reload_registry(project.project_root())
    scene = bpy.context.scene  # keeps the default name: the file's name names the level
    scene.render.fps = 24
    ground = obj("Ground", mesh_data("GroundMesh", 8, 0.2), (0, 0, -0.2), mat=material("Dusk", (0.35, 0.28, 0.32, 1)))
    components.add_component(ground, "Collider")
    for i, x in enumerate((-4.5, 4.5, -3, 3)):
        obj(f"Pillar_{i}", mesh_data(f"PillarMesh_{i}", 0.35, 2.2 + i * 0.4), (x, 3 + i * 2, 0),
            mat=material(f"Stone_{i}", (0.45, 0.42, 0.5, 1)))
    camera = obj("Camera", bpy.data.cameras.new("Camera"), (0, -9, 2.6))
    camera.rotation_euler = (math.radians(84), 0, 0)
    scene.camera = camera
    components.add_component(camera, "PostEffects", {"bloom": True, "bloomStrength": 0.7, "vignette": True})
    sun = obj("Sun", bpy.data.lights.new("Sun", "SUN"), (3, -4, 6))
    sun.data.energy = 2.5
    sun.data.color = (1.0, 0.75, 0.55)
    sun.rotation_euler = (math.radians(60), math.radians(-30), 0)
    obj("Mascot", mesh_data("MascotStandIn", 0.3, 0.6), (-2, -1.5, 0), mat=material("Grey", (0.5, 0.5, 0.5, 1)))
    portal = obj("Portal", None, (1.5, 0.5, 1.2))
    components.add_component(portal, "ParticleEmitter", {
        "rate": 70, "shape": "sphere", "radius": 0.7, "speed": [0.2, 0.7], "lifetime": [0.8, 1.6], "gravity": -0.08,
        "size": [0.3, 0.0], "colorStart": [0.55, 0.12, 1.0, 1.0], "colorEnd": [0.1, 0.35, 1.0, 0.0], "brightness": 1.2,
        "texture": "dot", "maxParticles": 400})
    world = bpy.data.worlds.new("Sunset")
    world["gs_sky_colors"] = {"horizon": "#FFB36B", "zenith": "#4A5FA8", "ground": "#5A4640", "sun": "#FF9A52"}
    world.color = (1.0, 0.45, 0.15)
    world.mist_settings.use_mist = True
    world.mist_settings.start = 8
    world.mist_settings.depth = 40
    scene.world = world
    bpy.ops.wm.save_mainfile()


def author():
    build_level2()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    BLEND.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    root = project.project_root()
    if root is None or not registry.reload_registry(root):
        fail(f"no project or registry at {PROJECT}")
    missing = [n for n in ("Animator", "AudioSource", "Spawner") if registry.schema_ref(n) is None]
    if missing:
        fail(f"registry lacks {missing}")

    scene = bpy.context.scene
    scene.name = "main"
    scene.render.fps = 24

    ground = obj("Ground", mesh_data("GroundMesh", 6, 0.2), (0, 0, -0.2), mat=material("Grass", (0.2, 0.35, 0.2, 1)))
    components.add_component(ground, "Collider")

    camera = obj("Camera", bpy.data.cameras.new("Camera"), (0, -9, 4.5))
    camera.rotation_euler = (math.radians(68), 0, 0)
    scene.camera = camera
    sun = obj("Sun", bpy.data.lights.new("Sun", "SUN"), (2, -3, 6))
    sun.data.energy = 3
    sun.rotation_euler = (math.radians(40), math.radians(20), 0)

    # Object actions: the active one and one on an NLA track.
    flag = obj("Flag", mesh_data("FlagMesh", 0.3, 1.6), (0, 0, 0), mat=material("Red", (0.8, 0.1, 0.1, 1)))
    flag.animation_data_create()
    spin = bpy.data.actions.new("Spin")
    flag.animation_data.action = spin
    keys(flag, "rotation_euler", 2, ((1, 0), (49, math.tau)))
    track = flag.animation_data.nla_tracks.new()
    track.name = "SpinTrack"
    track.strips.new("Spin", 1, spin)
    flag.animation_data.action = bpy.data.actions.new("Wave")
    keys(flag, "rotation_euler", 1, ((1, -0.4), (13, 0.4), (25, -0.4)))
    components.add_component(flag, "Animator", {"clip": "Wave"})

    # A skinned mesh on an armature, bending at the middle bone.
    rig = obj("Rig", bpy.data.armatures.new("RigData"), (2.5, 0, 0))
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone_root = rig.data.edit_bones.new("Root")
    bone_root.head, bone_root.tail = (0, 0, 0), (0, 0, 1)
    bone_tip = rig.data.edit_bones.new("Tip")
    bone_tip.head, bone_tip.tail = (0, 0, 1), (0, 0, 2)
    bone_tip.parent = bone_root
    bpy.ops.object.mode_set(mode="OBJECT")
    skin = obj("RigSkin", mesh_data("RigSkinMesh", 0.25, 2.0), parent=rig, mat=material("Blue", (0.1, 0.3, 0.9, 1)))
    skin.modifiers.new("Armature", "ARMATURE").object = rig
    skin.vertex_groups.new(name="Root").add([0, 1, 2, 3], 1.0, "REPLACE")
    skin.vertex_groups.new(name="Tip").add([4, 5, 6, 7], 1.0, "REPLACE")
    rig.animation_data_create()
    rig.animation_data.action = bpy.data.actions.new("Bend")
    tip = rig.pose.bones["Tip"]
    tip.rotation_mode = "XYZ"
    keys(tip, "rotation_euler", 0, ((1, 0), (13, 0.9), (25, 0)))
    components.add_component(rig, "Animator", {"clip": "Bend"})

    door = obj("Door", mesh_data("DoorMesh", 0.4, 1.2), (-2.5, 1, 0), mat=material("Wood", (0.5, 0.3, 0.1, 1)))
    door.animation_data_create()
    door.animation_data.action = bpy.data.actions.new("Open")
    keys(door, "rotation_euler", 2, ((1, 0), (13, math.radians(90))))
    components.add_component(door, "Animator", {"clip": "Open", "loop": False})

    speaker = obj("Speaker", None, (0, 0, 1))
    components.add_component(speaker, "AudioSource", {
        "clip": "assets/sfx/music.wav", "loop": True, "autoplay": True, "refDistance": 6, "volume": 0.8})

    # The prefab: a collection that is not linked into the scene.
    coin = bpy.data.collections.new("Coin")
    export.set_prefab(coin)
    coin_root = obj("CoinRoot", None, collection=coin)
    components.add_component(coin_root, "Spin", {"speed": 2})
    coin_body = obj("CoinBody", mesh_data("CoinMesh", 0.3, 0.1), (0, 0, 0.5), parent=coin_root, collection=coin,
                    mat=material("Gold", (1.0, 0.75, 0.1, 1)))
    coin_body.animation_data_create()
    coin_body.animation_data.action = bpy.data.actions.new("CoinBob")
    keys(coin_body, "location", 2, ((1, 0.5), (13, 0.9), (25, 0.5)))
    components.add_component(coin_body, "Animator", {"clip": "CoinBob"})

    placed = obj("CoinInstance", None, (-3, -1.5, 0))
    placed.instance_type = "COLLECTION"
    placed.instance_collection = coin

    spawner = obj("Spawner", None, (0, 2, 0))
    components.add_component(spawner, "Spawner", {"prefab": "Coin", "every": 0.6, "max": 3,
                                                  "sound": "assets/sfx/pop.wav"})

    fountain = obj("Fountain", mesh_data("FountainBase", 0.35, 0.3), (-1.2, -2.2, 0),
                   mat=material("Marble", (0.85, 0.85, 0.9, 1)))
    components.add_component(fountain, "ParticleEmitter", {
        "rate": 40, "texture": "star", "lifetime": [0.8, 1.4], "speed": [2.2, 3.2], "spread": 0.3,
        "direction": [0, 1, 0], "gravity": 0.45, "size": [0.24, 0.05], "colorStart": [0.05, 0.45, 1.0, 1.0],
        "colorEnd": [0.02, 0.15, 0.9, 0.0]})
    obj("Mascot", mesh_data("MascotMesh", 0.25, 0.5), (1.4, -2.4, 0), mat=material("Teal", (0.1, 0.7, 0.6, 1)))

    scene.frame_set(1)
    levels = export.export_all()  # every level: this file here, level2.blend in a background Blender
    result = export.export_scene()
    bpy.ops.wm.save_mainfile()  # after the export, so the file keeps the export revision
    say("export", result=result, ids={o.name: ids.get_id(o) for o in bpy.data.objects if ids.get_id(o)})
    say("levels", result=levels)


def pump(seconds=0.0, until=None, timeout=20.0):
    deadline = time.monotonic() + (timeout if until else seconds)
    while True:
        link.pump()
        live.pump()
        if until is not None and until():
            return True
        if time.monotonic() > deadline:
            return until is None
        time.sleep(0.01)


def wait_for_node(step, sentinels):
    say("wait", step=step)
    marker = sentinels / f"{step}.ok"
    if not pump(until=marker.exists, timeout=60):
        fail(f"Node never confirmed '{step}'")


def live_phase(sentinels):
    bpy.ops.wm.open_mainfile(filepath=str(BLEND))
    registry.reload_registry(project.project_root())
    client = link.get_link()
    if not pump(until=lambda: client.connected and client.game_peers(), timeout=40):
        fail(f"link not connected: {client.status_text}")
    pump(0.5)
    if not live.is_active():
        fail("live link did not activate")

    flag = bpy.data.objects["Flag"]
    components.set_field(flag, "Animator", "clip", "Spin")
    bpy.context.view_layer.update()
    pump(0.6)
    box = []
    client.request({"t": "state.request", "filter": {"ids": [ids.get_id(flag)]}}, box.append, timeout=5)
    pump(until=lambda: box, timeout=10)
    entities = (box[0] or {}).get("entities", []) if box else []
    clip = entities[0]["components"]["Animator"]["clip"] if entities else None
    say("live-edit", clip=clip, stats=live.stats())
    wait_for_node("live-clip", sentinels)

    rebuilds = live.stats()["rebuilds"]
    second = bpy.data.objects.new("CoinInstance2", None)
    second.instance_type = "COLLECTION"
    second.instance_collection = bpy.data.collections["Coin"]
    second.location = (3, -1.5, 0)
    bpy.context.scene.collection.objects.link(second)
    bpy.context.view_layer.update()
    if not pump(until=lambda: live.stats()["rebuilds"] > rebuilds, timeout=20):
        fail("adding an instance did not trigger a live re-export")
    say("reexport", rev=export.last_export().get("rev"), instance=ids.get_id(second), stats=live.stats())
    wait_for_node("reexport", sentinels)


def levels_phase(sentinels):
    bpy.ops.wm.open_mainfile(filepath=str(LEVEL2))
    registry.reload_registry(project.project_root())
    client = link.get_link()
    if not pump(until=lambda: client.connected and client.game_peers(), timeout=40):
        fail(f"link not connected: {client.status_text}")
    pump(0.5)
    if not live.is_active():
        fail("live link did not activate")
    portal = bpy.data.objects["Portal"]
    portal.location.x += 1.5
    bpy.context.view_layer.update()
    pump(0.6)
    say("moved", id=ids.get_id(portal), scene=export.export_name(bpy.context.scene), stats=live.stats())
    wait_for_node("moved", sentinels)


gamestudio.register()
if PHASE == "author":
    author()
elif PHASE == "live":
    live_phase(pathlib.Path(ARGS[2]))
elif PHASE == "levels":
    levels_phase(pathlib.Path(ARGS[2]))
else:
    fail(f"unknown phase {PHASE}")
say("done")
