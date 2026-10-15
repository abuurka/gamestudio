# SPDX-License-Identifier: MIT-0
"""Builds scenes/main.blend, the arena shooter's level "The Pit", from GameStudio kits.

New Project runs build(gs, root) once. Run it again to get the level back as this file makes it.
It replaces everything in the scene, so put changes you want to keep into this file:

  Coding agent (MCP): run_python with
                        import runpy; runpy.run_path(r"<project>/tools/build_level.py")["build"](gs)
  A terminal:         blender --background --python tools/build_level.py
                      (GameStudio enabled in that Blender, the project set up once by opening it there)

Blender space: metres, Z up. The arena is 30 x 30 m around the origin; enemies enter at the four
SpawnPoint pads. The enemies and the bullet are prefabs: collections marked gs_prefab in the
"Prefabs" scene, so they are templates for ctx.spawn() and not part of the level.
"""

import math
import pathlib

import bpy
from mathutils import Vector

PROJECT = pathlib.Path(__file__).resolve().parents[1]
SKY = (0.36, 0.45, 0.6)
HALF = 15  # half the arena's size, metres


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    scene, path = new_scene(gs, root, SKY)
    add = gs.core.components.add_component
    level, props, actors = (collection(scene, n) for n in ("Level", "Props", "Actors"))

    def kit(kind, name, location, coll, rotation=0.0, **params):
        obj = gs.kits.create(kind, {"name": name, **params}, location=location, collection=coll)[0]
        obj.rotation_euler.z = math.radians(rotation)
        return obj

    def block(name, location, size, coll=level, top="concrete", side="stone_dark", bevel=0.08):
        width, depth, height = size
        obj = kit("platform", name, location, coll, width=width, depth=depth, height=height, bevel=bevel,
                  top_material=top, side_material=side)
        add(obj, "Collider")
        return obj

    # --- The arena: a tiled floor, brick walls, pads where enemies come in ----------------------------
    arena_floor = floor(gs, "ArenaFloor", level)
    add(arena_floor, "Collider")
    # The navmesh the enemies' NavAgents walk on: built from the level's colliders when it loads.
    add(arena_floor, "NavMesh", {"agentRadius": 0.4})
    centre = gs.kits.geometry.MeshBuilder()
    annulus(centre, 2.6, 3.0, 0.0, 0.02, centre.slot("emissive", color="#FF922B", emission_strength=0.5))
    centre.to_object("CentreRing", collection=level)
    for name, x, y, w, d in [("Wall_North", 0, HALF + 0.5, 2 * HALF + 2, 1), ("Wall_South", 0, -HALF - 0.5, 2 * HALF + 2, 1),
                             ("Wall_East", HALF + 0.5, 0, 1, 2 * HALF), ("Wall_West", -HALF - 0.5, 0, 1, 2 * HALF)]:
        block(name, (x, y, -0.6), (w, d, 2.2), top="stone_dark", side="brick")
    for i, (x, y) in enumerate([(0, 12.5), (12.5, 0), (0, -12.5), (-12.5, 0)]):
        add(spawn_pad(gs, f"SpawnPad{i + 1}", (x, y, 0.0), level), "SpawnPoint")
        # Each pad pulses: red embers rise off it (brightness above 1 glows with bloom).
        emitter(gs, f"SpawnPadGlow{i + 1}", (x, y, 0.06), props, shape="cone", radius=1.15, spread=0.1, rate=14,
                lifetime=[0.5, 1.0], speed=[0.6, 1.4], size=[0.24, 0.0], colorStart=[1, 0.18, 0.08, 1],
                colorEnd=[1, 0.05, 0.02, 0], brightness=2.0)

    # Cover: pillars with lamps, low walls, crates and barrels (static: a Collider and no RigidBody).
    for i, (x, y) in enumerate([(7.5, 7.5), (-7.5, 7.5), (7.5, -7.5), (-7.5, -7.5)]):
        block(f"Pillar{i + 1}", (x, y, 0), (1.8, 1.8, 3.2), top="stone", side="stone", bevel=0.12)
        lamp = kit("collectible", f"Lamp{i + 1}", (x, y, 3.6), props, style="gem", size=0.7)
        gs.kits.materials.apply(lamp, "emissive", color="#FF922B", emission_strength=1.0)
        add(lamp, "Spin", {"speed": 1})
    block("LowWall1", (4, 5.5, 0), (4.5, 1, 1.1), top="stone_dark", side="brick")
    block("LowWall2", (-4, -5.5, 0), (4.5, 1, 1.1), top="stone_dark", side="brick")
    for i, (x, y, z, r) in enumerate([(-8.5, 2.5, 0, 0), (-8.4, 3.6, 0, 12), (-8.45, 3.0, 1, 30), (9, -3.5, 0, 5),
                                      (10.1, -3.7, 0, -8)]):
        add(kit("crate", f"Crate{i + 1}", (x, y, z), props, rotation=r, seed=i), "Collider")
    for i, (x, y) in enumerate([(3, -9.5), (4.1, -9.8), (-10.5, -10.5), (10.5, 10.5), (-3.5, 10)]):
        add(kit("barrel", f"Barrel{i + 1}", (x, y, 0), props, seed=i, material="wood_dark"), "Collider")

    # --- Outside the walls: dunes, rocks, pines and two bunkers (scenery, no colliders) -----------------
    kit("terrain", "Dunes", (0, 0, -0.6), level, size=120, resolution=40, height=4, feature_size=16,
        flatten_center=0.42, top_material="sand", cliff_material="stone", side_material="dirt", seed=3)
    for i in range(14):
        a = i / 14 * math.tau + 0.3
        r = 25 + 7 * ((i * 37) % 10) / 10
        kit("tree", f"Pine{i + 1}", (r * math.cos(a), r * math.sin(a), -0.4), props, style="pine",
            height=6 + (i % 4), seed=i)
    for i, (x, y, size) in enumerate([(-22, -6, 3.5), (21, 9, 2.8), (-6, 23, 3), (8, -24, 4)]):
        kit("rock", f"Boulder{i + 1}", (x, y, -0.6), props, size=size, seed=i)
    kit("building", "BunkerWest", (-27, 14, -0.6), props, rotation=70, width=8, depth=6, roof="flat",
        wall_material="concrete", windows=1, side_windows=0)
    kit("building", "BunkerEast", (26, -18, -0.6), props, rotation=-110, width=7, depth=6, roof="flat",
        wall_material="concrete", windows=1, side_windows=0)

    # --- The player, its aim marker, the wave director --------------------------------------------------
    player = kit("character", "Player", (0, -3, 0), actors, rotation=180, color="#3B82F6")
    add(player, "RigidBody", {"type": "kinematic"})
    add(player, "Collider", {"shape": "capsule"})
    add(player, "CharacterController", {"speed": 7, "jumpSpeed": 0, "turnSpeed": 0})  # AimSystem turns it
    add(player, "Shooter", {"fireRate": 8, "projectile": "Bullet", "muzzleHeight": 1.06})
    add(player, "Health", {"max": 100})
    gun(gs, player, actors)

    marker = ring(gs, "AimMarker", (0, 0, 0.03), actors, 0.42, 0.52, "#4DD4FF")
    add(marker, "AimMarker")

    waves = bpy.data.objects.new("Waves", None)
    waves.empty_display_type = "SPHERE"
    actors.objects.link(waves)
    add(waves, "WaveSpawner", {"enemies": "Grunt, Runner@3", "firstWave": 4, "perWave": 2})

    # The music: the starter's looping theme on the music channel, which plays on under the menus (the
    # Options screen's Music slider sets that channel's volume). Like every sound, it starts on the first input.
    music = bpy.data.objects.new("Music", None)
    music.empty_display_type, music.empty_display_size = "SPHERE", 0.3
    actors.objects.link(music)
    add(music, "AudioSource", {"clip": "assets/music/theme.ogg", "volume": 0.5, "loop": True, "autoplay": True,
                               "spatial": False, "channel": "music"})

    # --- Prefabs: spawned by the systems (ctx.spawn). They live in their own scene, out of the level. ----
    prefabs = bpy.data.scenes.new("Prefabs").collection
    grunt = enemy(gs, prefabs, "Grunt", height=1.7, girth=0.2, color="#E03131")
    add(grunt, "Enemy", {"speed": 3.2, "damage": 10, "points": 10})
    add(grunt, "Health", {"max": 3})
    runner = enemy(gs, prefabs, "Runner", height=1.25, girth=0.15, color="#F59F00")
    add(runner, "Enemy", {"speed": 5.6, "damage": 5, "reach": 1.1, "attackInterval": 0.6, "points": 15})
    add(runner, "Health", {"max": 1})
    shot = bullet(gs, prefab_collection(gs, prefabs, "Bullet"))
    add(shot, "Projectile", {"speed": 32, "damage": 1, "lifetime": 1.2})

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 30
    actors.objects.link(cam)
    cam.location = (0, -14, 19)
    cam.rotation_euler = (math.radians(30), 0, 0)
    scene.camera = cam
    add(cam, "FollowCamera", {"target": "Player", "offset": [0, 19, 11], "smoothing": 6, "lookAtOffset": [0, 0.5, 0]})
    # A touch of bloom: only bright things glow (the bullets, the lamps, the pads' embers).
    add(cam, "PostEffects", {"bloom": True, "bloomStrength": 0.45, "bloomThreshold": 1.0, "bloomRadius": 0.3})

    # The sky kit: a dusty desert sky (the game draws its gradient too) and its sun, in front and to the right.
    gs.kits.create("sky", {"name": "Sun", "preset": "day", "horizon": "#E3CDAE", "zenith": "#4F7FBF",
                           "ground": "#B39B7C", "sun_strength": 3.2, "sun_elevation": 47, "sun_azimuth": 131},
                   collection=actors)
    fog(scene, start=28, depth=60)  # the dunes fade into a sandy haze

    return save_and_export(gs, scene, path)


# --- Pieces ------------------------------------------------------------------------------------------------


def prefab_collection(gs, parent, name):
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    gs.core.export.set_prefab(coll)
    return coll


def enemy(gs, parent, name, height, girth, color):
    """An enemy prefab: one character (the prefab's root) that moves as a kinematic body."""
    coll = prefab_collection(gs, parent, name)
    body = gs.kits.create("character", {"name": name, "height": height, "girth": girth, "color": color,
                                        "head_material": "plastic"}, collection=coll)[0]
    add = gs.core.components.add_component
    add(body, "RigidBody", {"type": "kinematic"})
    add(body, "Collider", {"shape": "capsule"})
    # Walks the navmesh around cover; EnemySystem sets its target, speed and stopping distance.
    add(body, "NavAgent", {"acceleration": 30, "separation": 0.9})
    return body


def bullet(gs, coll):
    m = gs.kits.geometry.MeshBuilder()
    glow = m.slot("emissive", color="#FFD43B", emission_strength=1.5)
    m.icosphere(1, glow, smooth=True, deform=lambda co: Vector((co.x * 0.09, co.y * 0.3, co.z * 0.09)))  # long along Y
    return m.to_object("Bullet", collection=coll)


def gun(gs, player, coll):
    """A stubby blaster in the player's hands, pointing along its front (-Y)."""
    m = gs.kits.geometry.MeshBuilder()
    m.box((0.14, -0.68, 0.98), (0.3, -0.18, 1.14), m.slot("rubber"))
    m.box((0.17, -0.74, 1.01), (0.27, -0.68, 1.11), m.slot("emissive", color="#4DD4FF", emission_strength=1.5))
    obj = m.to_object("PlayerGun", collection=coll)
    obj.parent = player  # child entities move with the player; its collider ignores them
    return obj


def floor(gs, name, coll, tile=2.5, gap=0.06, depth=0.6):
    """One mesh: a dark base with raised tiles in a soft checker and a brick border, top at z = 0."""
    m = gs.kits.geometry.MeshBuilder()
    grout, light, dark = m.slot("stone_dark"), m.slot("stone", color="#B3AC9E"), m.slot("stone", color="#958F83")
    border = m.slot("brick")
    m.box((-HALF, -HALF, -depth), (HALF, HALF, -0.04), grout)
    count = round(2 * HALF / tile)
    for i in range(count):
        for j in range(count):
            x0, y0 = -HALF + i * tile + gap / 2, -HALF + j * tile + gap / 2
            edge = i in (0, count - 1) or j in (0, count - 1)
            slot = border if edge else (light if (i + j) % 2 else dark)
            m.box((x0, y0, -0.1), (x0 + tile - gap, y0 + tile - gap, 0.0), slot)
    return m.to_object(name, collection=coll)


def annulus(m, inner, outer, z0, z1, slot, segments=32):
    """A closed flat ring (a washer) around Z."""
    verts, faces = [], []
    for s in range(segments):
        c, si = math.cos(math.tau * s / segments), math.sin(math.tau * s / segments)
        verts += [(inner * c, inner * si, z0), (outer * c, outer * si, z0), (outer * c, outer * si, z1),
                  (inner * c, inner * si, z1)]
    for s in range(segments):
        t = (s + 1) % segments
        faces += [(4 * s + k, 4 * t + k, 4 * t + (k + 1) % 4, 4 * s + (k + 1) % 4) for k in range(4)]
    m.add(verts, faces, slot)


def ring(gs, name, location, coll, inner, outer, color):
    m = gs.kits.geometry.MeshBuilder()
    annulus(m, inner, outer, 0.0, 0.02, m.slot("emissive", color=color, emission_strength=1.0))
    return m.to_object(name, location=location, collection=coll)


def emitter(gs, name, location, coll, rotation=0.0, **fields):
    """An empty with a ParticleEmitter: an effect that plays from the start (fields in runtime space, Y up)."""
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type, obj.empty_display_size = "SPHERE", 0.3
    obj.location, obj.rotation_euler.z = location, rotation
    coll.objects.link(obj)
    gs.core.components.add_component(obj, "ParticleEmitter", fields)
    return obj


def spawn_pad(gs, name, location, coll):
    """A dark disc in a glowing red ring: where enemies come in."""
    m = gs.kits.geometry.MeshBuilder()
    dark, glow = m.slot("rubber"), m.slot("emissive", color="#FF2D20", emission_strength=0.8)
    m.lathe([(0.0, 0.0), (1.0, 0.0), (1.0, 0.03), (0.0, 0.03)], 32, dark, smooth=False)
    annulus(m, 1.0, 1.35, 0.0, 0.05, glow)
    return m.to_object(name, location=location, collection=coll)


# --- Scene plumbing, the same in every template --------------------------------------------------------


def collection(scene, name):
    coll = bpy.data.collections.new(name)
    scene.collection.children.link(coll)
    return coll


def new_scene(gs, root, sky):
    """Empty the current scene, save it as the project's start scene and load its components."""
    scene = bpy.context.scene
    for other in [s for s in bpy.data.scenes if s != scene]:  # e.g. the Prefabs scene of an earlier build
        for obj in list(other.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for coll in list(other.collection.children_recursive):
            bpy.data.collections.remove(coll)
        bpy.data.scenes.remove(other)
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(scene.collection.children):
        scene.collection.children.unlink(coll)
        if coll.users == 0:
            bpy.data.collections.remove(coll)
    scene.name = gs.core.project.read_config(root).get("startScene") or "main"
    scene.render.fps, scene.render.fps_base = 30, 1.0  # kit animation clips are authored at 30 fps
    # The game tone-maps as the scene's Color Management says: change the exposure here, not in code.
    scene.view_settings.view_transform, scene.view_settings.look = "Khronos PBR Neutral", "None"
    scene.frame_start, scene.frame_end = 1, 120
    world = bpy.data.worlds.new("Sky")
    world.color = sky
    if world.node_tree and "Background" in world.node_tree.nodes:
        world.node_tree.nodes["Background"].inputs["Color"].default_value = (*sky, 1.0)
    scene.world = world
    path = root / "scenes" / f"{scene.name}.blend"
    path.parent.mkdir(exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))  # inside the project: components and export work now
    gs.core.project.invalidate_cache()
    if not gs.core.registry.reload_registry(root):
        raise RuntimeError(f"No component registry in {root} yet: GameStudio reads it when it sets the project up "
                           "(open the project in Blender and let the setup finish), then build again.")
    return scene, path


def fog(scene, start, depth):
    """Light distance fog: the world's mist, which the game draws from `start` metres, full `depth` metres
    further, in the sky's horizon colour (world["gs_fog_color"] = "#RRGGBB" picks another)."""
    mist = scene.world.mist_settings
    mist.use_mist, mist.start, mist.depth = True, start, depth


def save_and_export(gs, scene, path):
    bpy.ops.wm.save_mainfile()
    gs.core.export.export_scene(scene)
    bpy.ops.wm.save_mainfile()  # keep the ids and export revision assigned during export
    return path


def find_gamestudio():
    """The GameStudio package, when this file runs on its own (blender --python)."""
    import importlib
    import sys

    import addon_utils

    for name, module in list(sys.modules.items()):
        if name.split(".")[-1] == "gamestudio" and hasattr(module, "kits"):
            return module
    for module in addon_utils.modules():
        if module.__name__.split(".")[-1] == "gamestudio":
            addon_utils.enable(module.__name__, default_set=False)
            return importlib.import_module(module.__name__)
    raise SystemExit("The GameStudio extension is not installed in this Blender.")


if __name__ == "__main__":
    print("LEVEL_OK", build(find_gamestudio()))
