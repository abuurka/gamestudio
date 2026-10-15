# SPDX-License-Identifier: MIT-0
"""Builds scenes/main.blend, the platformer's level "Sky Garden", from GameStudio kits.

New Project runs build(gs, root) once. Run it again to get the level back as this file makes it.
It replaces everything in the scene, so put changes you want to keep into this file:

  Coding agent (MCP): run_python with
                        import runpy; runpy.run_path(r"<project>/tools/build_level.py")["build"](gs)
  A terminal:         blender --background --python tools/build_level.py
                      (GameStudio enabled in that Blender, the project set up once by opening it there)

Blender space: metres, Z up. The course runs toward +Y, the player's forward. Jump metrics for the
player below (speed 6, jumpSpeed 7.5, gravity 20): 1.4 m high, about 4.5 m long. Keep gaps under 3 m.
"""

import math
import pathlib

import bpy

PROJECT = pathlib.Path(__file__).resolve().parents[1]
SKY = (0.42, 0.66, 0.95)


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    scene, path = new_scene(gs, root, SKY)
    add = gs.core.components.add_component
    level, props, pickups, actors = (collection(scene, n) for n in ("Level", "Props", "Pickups", "Actors"))

    def kit(kind, name, location, coll, rotation=0.0, **params):
        obj = gs.kits.create(kind, {"name": name, **params}, location=location, collection=coll)[0]
        obj.rotation_euler.z = math.radians(rotation)
        return obj

    def island(name, x, y, width, depth, top, thickness=3.0, side="dirt"):
        obj = kit("platform", name, (x, y, top - thickness), level, width=width, depth=depth, height=thickness,
                  bevel=0.25, top_material="grass", side_material=side)
        add(obj, "Collider")
        return obj

    # --- The course, start to goal ---------------------------------------------------------
    island("Meadow", 0, -3, 14, 14, 0.0)
    for i, (x, y, top) in enumerate([(0, 7, 0.4), (2, 11, 0.8), (0, 15, 1.2)]):
        island(f"Stone{i + 1}", x, y, 2.6, 2.6, top, thickness=1.2, side="stone")
    island("Crossroads", 0, 22.5, 12, 10, 1.2)
    island("HighLedge", -4, 30.5, 4, 3, 5.5, thickness=1.0, side="stone")
    island("LavaIsland", 0, 46, 8, 13, 1.2)
    stairs = kit("stairs", "SummitStairs", (0, 54.3, 1.2), level, steps=8, width=3, step_height=0.3, step_depth=0.45)
    add(stairs, "Collider", {"shape": "mesh"})
    island("Summit", 0, 60, 10, 8, 3.6)

    ferry = kit("platform", "Ferry", (0, 29.5, 0.8), level, width=3, depth=3, height=0.4, bevel=0.05,
                top_material="wood", side_material="wood_dark")
    add(ferry, "RigidBody", {"type": "kinematic"})
    add(ferry, "Collider")
    add(ferry, "MovingPlatform", {"offset": [0, 0, -8], "period": 7})  # runtime -Z is Blender +Y

    for i, y in enumerate((43.5, 48.5)):
        lava = kit("platform", f"Lava{i + 1}", (0, y, 1.14), level, width=8.02, depth=1.6, height=0.12, bevel=0)
        gs.kits.materials.apply(lava, "emissive", color="#FF5A1F", emission_strength=3.0)
        add(lava, "KillZone")  # no Collider: the player walks into it

    pad = bounce_pad(gs, "BouncePad", (-4, 25, 1.2), props)
    add(pad, "BouncePad", {"strength": 14})
    add(pad, "Collider")

    for i, (x, y, z) in enumerate([(3.5, 20, 1.2), (-2.5, 41, 1.2)]):
        pole, flag = checkpoint(gs, f"Checkpoint{i + 1}", (x, y, z), props, scene)
        add(pole, "Checkpoint", {"radius": 2.5})
        add(flag, "Animator", {"clip": "FlagRaise", "loop": False, "playOnStart": False})

    pedestal = kit("platform", "GoalPedestal", (0, 61.5, 3.6), level, width=1.6, depth=1.6, height=0.6, bevel=0.08,
                   top_material="stone", side_material="stone_dark")
    add(pedestal, "Collider")
    star = kit("collectible", "GoalStar", (0, 61.5, 5.1), pickups, style="star", size=1.4)
    gs.kits.materials.apply(star, "gold", metallic=0.6, emission="#FFC83D", emission_strength=3.5, name="StarGold")
    add(star, "Goal", {"radius": 1.8})
    gs.kits.animation.add_clip(star, ["spin", "bob"], {"spin": {"speed": 0.3}, "bob": {"height": 0.2, "speed": 0.5}})
    add(star, "Animator", {"clip": "spin_bob"})
    # Ambient particles: twinkles around the star, embers over the lava (bright enough to glow with bloom).
    emitter(gs, "StarSparkles", (0, 61.5, 5.3), props, texture="star", rate=8, lifetime=[0.8, 1.6], speed=[0.2, 0.6],
            spread=math.pi, shape="sphere", radius=1.0, gravity=-0.05, drag=0.5, size=[0.3, 0.0], spin=2,
            colorStart=[1, 0.75, 0.25, 1], colorEnd=[1, 0.45, 0.1, 0], brightness=3.0)
    for i, y in enumerate((43.5, 48.5)):
        emitter(gs, f"LavaEmbers{i + 1}", (0, y, 1.3), props, shape="box", box=[7.6, 0.1, 1.4], rate=6,
                lifetime=[1.0, 2.0], speed=[0.4, 1.1], spread=0.35, gravity=-0.08, size=[0.14, 0.02],
                colorStart=[1, 0.4, 0.08, 1], colorEnd=[1, 0.12, 0.02, 0], brightness=4.5)

    coins = [(0, -2, 1), (0, 0.5, 1), (0, 3, 1), (0, 7, 1.4), (2, 11, 1.8), (0, 15, 2.2), (-2, 20, 2.2),
             (2, 25, 2.2), (-5, 30.5, 6.5), (-4, 30.5, 6.5), (-3, 30.5, 6.5), (0, 43.5, 2.6), (0, 48.5, 2.6),
             (0, 54.3, 3.6), (-3, 59, 4.6)]
    for i, location in enumerate(coins):
        coin = kit("collectible", f"Coin{i + 1}", location, pickups, style="coin", size=0.6)
        gs.kits.materials.apply(coin, "gold", metallic=0.6, emission="#FFB01F", emission_strength=0.6, name="CoinGold")
        add(coin, "Coin", {"value": 1})

    # --- Dressing: kits with colliders where the player can bump into them ----------------------
    for i, (style, x, y, z, height) in enumerate([("round", -5, -8, 0, 4.5), ("round", 5.5, -8, 0, 4),
                                                   ("pine", -5.8, 1.5, 0, 5), ("pine", -5, 19.5, 1.2, 5.5),
                                                   ("round", -4, 62.5, 3.6, 4.5), ("round", 4, 62.5, 3.6, 4)]):
        tree = kit("tree", f"Tree{i + 1}", (x, y, z), props, style=style, height=height, seed=i)
        add(tree, "Collider")
        gs.kits.animation.add_clip(tree, "sway", {"angle": 2.5})  # one shared action; each plays at its own pace
        add(tree, "Animator", {"clip": "sway", "speed": 0.8 + 0.08 * i})
    for i, (x, y, z) in enumerate([(-3, -8.6, 0), (-4.5, -3.5, 0), (5.5, 2.5, 0), (5, 18.5, 1.2),
                                   (-4, 57.5, 3.6), (4.2, 58, 3.6)]):
        kit("bush", f"Bush{i + 1}", (x, y, z), props, size=1.3, seed=i)
    for i, (x, y, z, size) in enumerate([(6.2, -6.8, 0, 1.2), (-6, -6.5, 0, 0.9), (-3.2, 40.4, 1.2, 0.8),
                                         (3.3, 51.6, 1.2, 0.9), (4.3, 21.5, 1.2, 0.7)]):
        add(kit("rock", f"Rock{i + 1}", (x, y, z), props, size=size, moss=0.4, seed=i), "Collider", {"shape": "convex"})
    for i, y in enumerate((-8, -4)):  # along the meadow's left edge, toward the start
        add(kit("fence", f"Fence{i + 1}", (-6.6, y, 0), props, rotation=90, length=4,
                posts="both" if i == 1 else "start"), "Collider")
    hut = kit("building", "Hut", (4.6, -2.6, 0), props, rotation=-90, width=4, depth=3.4, floor_height=2.6,
              windows=1, side_windows=1, roof_pitch=35)
    add(hut, "Collider", {"shape": "mesh"})
    for i, (x, y, z) in enumerate([(4.3, 25.6, 1.2), (5.3, 25.4, 1.2), (4.8, 25.5, 2.2)]):
        add(kit("crate", f"Crate{i + 1}", (x, y, z), props, rotation=8 * i, seed=i), "Collider")
    for i, (x, y) in enumerate([(4.6, 23.2), (-1.8, 26.6)]):
        add(kit("barrel", f"Barrel{i + 1}", (x, y, 1.2), props, seed=i), "Collider")

    # Distant islands frame the view; nothing to do there.
    for i, (x, y, w, d, top) in enumerate([(-20, 18, 10, 9, -1), (21, 34, 12, 9, 0.5), (-17, 52, 9, 10, 2)]):
        kit("platform", f"FarIsland{i + 1}", (x, y, top - 4), level, width=w, depth=d, height=4, bevel=0.3,
            top_material="grass", side_material="dirt")
        kit("tree", f"FarTree{i + 1}", (x - 3, y - 2, top), props, style="pine", height=6, seed=10 + i)
        kit("tree", f"FarTreeB{i + 1}", (x + 3, y - 2.5, top), props, style="round", height=4, seed=20 + i)
    kit("building", "FarTower", (21, 36.5, 0.5), props, rotation=200, width=4.5, depth=4.5, floors=3)
    kit("terrain", "Water", (0, 20, -6), level, size=200, resolution=4, height=0, skirt=0.5, cliff_angle=90,
        top_material="water", side_material="water")

    # --- Player, camera, light -------------------------------------------------------------------
    # A rigged character: the components go on its armature (the root); Player_Mesh is its skinned body.
    player = kit("character_rigged", "Player", (0, -6, 0), actors, rotation=180, color="#3B82F6")
    add(player, "RigidBody", {"type": "kinematic"})
    add(player, "Collider", {"shape": "capsule"})
    add(player, "CharacterController", {"speed": 6, "jumpSpeed": 7.5, "gravity": 20, "turnSpeed": 12})
    add(player, "Animator", {"graph": "Player"})  # the animation graph in src/systems/player-animation.ts

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 26
    actors.objects.link(cam)
    cam.location = (0, -15, 4.5)
    cam.rotation_euler = (math.radians(78), 0, 0)
    scene.camera = cam
    add(cam, "FollowCamera", {"target": "Player", "offset": [0, 4.5, 9], "smoothing": 5, "lookAtOffset": [0, 1.2, 0]})
    # A touch of bloom: only what is brighter than the threshold glows (the star, its sparkles, the embers;
    # not the big lava strips, which would wash out the island).
    add(cam, "PostEffects", {"bloom": True, "bloomStrength": 0.5, "bloomThreshold": 2.0, "bloomRadius": 0.25})

    # The sky kit: a gradient sky (the game draws it too) and its sun, in front and to the right.
    gs.kits.create("sky", {"name": "Sun", "preset": "day", "ground": "#9CC7E0", "sun_strength": 3.5,
                           "sun_elevation": 48, "sun_azimuth": 128}, collection=actors)
    fog(scene, start=60, depth=160)  # far islands and the sea fade into the horizon

    # Music: the starter's loop (assets/music/theme.ogg), everywhere at once. The music channel keeps playing
    # under the menus, and the Options screen's Music slider sets its volume. It starts on the first key press.
    music = bpy.data.objects.new("Music", None)
    music.empty_display_type, music.empty_display_size = "SPHERE", 0.5
    music.location = (0, -9, 2)
    actors.objects.link(music)
    add(music, "AudioSource", {"clip": "assets/music/theme.ogg", "volume": 0.5, "loop": True, "autoplay": True,
                               "spatial": False, "channel": "music"})

    return save_and_export(gs, scene, path)


# --- Pieces made with MeshBuilder (no kit fits) --------------------------------------------------------


def bounce_pad(gs, name, location, coll):
    """A round spring pad: a dark rim around a glowing magenta top."""
    m = gs.kits.geometry.MeshBuilder()
    rim, top = m.slot("rubber"), m.slot("emissive", color="#F03E9B", emission_strength=1.5)
    m.lathe([(0.0, 0.0), (1.0, 0.0), (1.0, 0.2), (0.88, 0.25), (0.0, 0.25)], 24, rim,
            band_slots=[rim, rim, rim, top], cap_slots=(rim, top))
    return m.to_object(name, location=location, collection=coll)


def checkpoint(gs, name, location, coll, scene):
    """A flag pole beside the respawn point (the object's origin) and a flag that rises up it.

    The flag's rise is a Blender action (FlagRaise) that its Animator plays when the player passes."""
    m = gs.kits.geometry.MeshBuilder()
    metal, gold = m.slot("metal"), m.slot("gold")
    m.lathe([(0.0, 0.0), (0.22, 0.0), (0.22, 0.08), (0.07, 0.1), (0.07, 2.9), (0.0, 2.9)], 12, metal,
            matrix=translation(1.0, 0, 0))
    m.icosphere(2, gold, smooth=True, deform=lambda co: co * 0.13, matrix=translation(1.0, 0, 3.0))
    pole = m.to_object(name, location=location, collection=coll)

    f = gs.kits.geometry.MeshBuilder()
    f.prism([(0.0, 0.0), (0.9, 0.28), (0.0, 0.56)], "Y", -0.02, 0.02, f.slot("plastic", color="#FF6B6B"))
    flag = f.to_object(f"{name}_Flag", collection=coll)
    flag.parent = pole
    flag.location = (1.07, 0, 0.3)
    flag.keyframe_insert("location", index=2, frame=1)
    flag.location.z = 2.3
    flag.keyframe_insert("location", index=2, frame=16)
    flag.animation_data.action.name = "FlagRaise"  # the second flag's becomes FlagRaise.001: same clip name
    scene.frame_set(1)  # export the lowered flag
    return pole, flag


def translation(x, y, z):
    from mathutils import Matrix

    return Matrix.Translation((x, y, z))


def emitter(gs, name, location, coll, **fields):
    """An empty with a ParticleEmitter: an effect that plays from the start (fields in runtime space, Y up)."""
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type, obj.empty_display_size = "SPHERE", 0.3
    obj.location = location
    coll.objects.link(obj)
    gs.core.components.add_component(obj, "ParticleEmitter", fields)
    return obj


# --- Scene plumbing, the same in every template --------------------------------------------------------


def collection(scene, name):
    coll = bpy.data.collections.new(name)
    scene.collection.children.link(coll)
    return coll


def new_scene(gs, root, sky):
    """Empty the current scene, save it as the project's start scene and load its components."""
    scene = bpy.context.scene
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
