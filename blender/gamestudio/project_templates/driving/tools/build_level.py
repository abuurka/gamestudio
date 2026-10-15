# SPDX-License-Identifier: MIT-0
"""Builds scenes/main.blend, the time trial's level "Pine Valley Circuit", from GameStudio kits.

New Project runs build(gs, root) once. Run it again to get the level back as this file makes it.
It replaces everything in the scene, so put changes you want to keep into this file:

  Coding agent (MCP): run_python with
                        import runpy; runpy.run_path(r"<project>/tools/build_level.py")["build"](gs)
  A terminal:         blender --background --python tools/build_level.py
                      (GameStudio enabled in that Blender, the project set up once by opening it there)

The track is a closed spline through CONTROL (metres, Blender X/Y): change the points to change the
circuit, then rebuild. The road, kerbs and barriers are swept along it with MeshBuilder; gates,
trees, stands and tyre stacks come from the kits. Checkpoints are empties whose scaled 1 m cube is
the volume the car must enter, in index order (0 is the start/finish line). The menus and the HUD are
not here: they are in ui/ui.blend, which tools/build_ui.py builds.
"""

import math
import pathlib
import random

import bpy
from mathutils import Matrix

PROJECT = pathlib.Path(__file__).resolve().parents[1]
SKY = (0.5, 0.7, 0.95)

CONTROL = [(0, -42), (0, -10), (0, 25), (4, 50), (22, 63), (44, 61), (57, 47), (57, 30), (47, 18), (46, 0),
           (57, -14), (61, -34), (50, -55), (28, -64), (8, -60)]
START = (0, -20)  # the start line: the centre-line point nearest this point of CONTROL
SCALE = 1.15  # of CONTROL
STEP = 2.0  # metres between samples of the centre line
ROAD = 6.0  # half the road's width
KERB = 1.2  # kerb width outside the road
WALL = 9.5  # barrier distance from the centre line
CHECKPOINTS = (0.0, 0.1, 0.42, 0.7)  # where the gates are, as fractions of the lap from the start line


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    scene, path = new_scene(gs, root, SKY)
    add = gs.core.components.add_component
    level, props, actors = (collection(scene, n) for n in ("Level", "Props", "Actors"))

    def kit(kind, name, location, coll, rotation=0.0, **params):
        obj = gs.kits.create(kind, {"name": name, **params}, location=location, collection=coll)[0]
        obj.rotation_euler.z = rotation
        return obj

    points, (ox, oy) = centre_line()
    n = len(points)
    start = min(range(n), key=lambda i: math.dist(points[i], (START[0] * SCALE - ox, START[1] * SCALE - oy)))
    points = points[start:] + points[:start]  # sample 0 is the start line
    normals = [right_of(points, i) for i in range(n)]

    # --- The circuit ------------------------------------------------------------------------------
    track = road_mesh(gs, points, normals).to_object("Track", collection=level)
    add(track, "Collider", {"shape": "mesh"})
    add(track, "Surface", {"speedFactor": 1, "grip": 1})
    barriers = barrier_mesh(gs, points, normals).to_object("Barriers", collection=level)
    add(barriers, "Collider", {"shape": "mesh"})
    ground = kit("terrain", "Ground", (0, 0, 0), level, size=200, resolution=48, height=8, feature_size=30,
                 flatten_center=0.9, top_material="grass", cliff_material="stone", side_material="dirt", seed=5)
    add(ground, "Collider", {"shape": "mesh"})
    add(ground, "Surface", {"speedFactor": 0.45, "grip": 0.7})

    for k, fraction in enumerate(CHECKPOINTS):
        i = round(fraction * n) % n
        (x, y), (tx, ty) = points[i], tangent(points, i)
        angle = math.atan2(-tx, ty)  # turns local +Y onto the track's direction
        gate = bpy.data.objects.new("StartLine" if k == 0 else f"Checkpoint{k}", None)
        gate.empty_display_type, gate.empty_display_size = "CUBE", 0.5  # a 1 m cube: the scale is the volume
        gate.location, gate.rotation_euler.z, gate.scale = (x, y, 3), angle, (2 * WALL, 4, 6)
        level.objects.link(gate)
        add(gate, "Checkpoint", {"index": k})
        if k == 0:
            add(gate, "Race", {"laps": 3})
        post_color, banner_color = ("#E03131", "#F8F9FA") if k == 0 else ("#F8F9FA", "#1C7ED6")
        for side in (-1, 1):
            px, py = x + normals[i][0] * side * (WALL + 1.1), y + normals[i][1] * side * (WALL + 1.1)
            post = kit("platform", f"{gate.name}_Post{'LR'[side > 0]}", (px, py, 0), props, width=0.7, depth=0.7,
                       height=5.8, bevel=0.05)
            gs.kits.materials.apply(post, "plastic", color=post_color)
            add(post, "Collider")
        banner = kit("platform", f"{gate.name}_Banner", (x, y, 4.8), props, rotation=angle, width=2 * WALL + 3,
                     depth=0.5, height=1.0, bevel=0.05)
        gs.kits.materials.apply(banner, "plastic", color=banner_color)
    start_strip(gs, points, normals).to_object("StartStrip", collection=level)
    (x, y), (tx, ty) = points[0], tangent(points, 0)
    # A haze of dust drifts over the grid (box: across the road, up, along it; alpha, not glowing).
    emitter(gs, "StartLineDust", (x, y, 0.2), props, rotation=math.atan2(-tx, ty), shape="box", box=[11, 0.2, 3],
            texture="smoke", blending="alpha", rate=5, lifetime=[1.5, 3.0], speed=[0.2, 0.6], spread=0.6,
            gravity=-0.02, drag=0.3, size=[0.6, 1.8], spin=0.5, colorStart=[0.72, 0.64, 0.5, 0.35],
            colorEnd=[0.8, 0.74, 0.62, 0])

    marker = arrow(gs).to_object("NextCheckpoint", location=(0, 0, 8), collection=actors)
    add(marker, "CheckpointMarker")

    # --- Scenery: stands, tyre stacks, cones to knock over, trees and rocks off the track ----------------
    (sx, sy), (nx, ny) = points[3], normals[3]
    side_angle = math.atan2(nx, -ny) + math.pi  # the stand's front (-Y) faces the track
    kit("building", "Grandstand", (sx - nx * 17, sy - ny * 17, 0), props, rotation=side_angle - math.pi, width=26,
        depth=6, floor_height=3.2, roof="flat", windows=6, side_windows=1, wall_material="plaster",
        roof_material="roof")
    kit("building", "PitGarage", (sx + nx * 17, sy + ny * 17, 0), props, rotation=side_angle, width=18, depth=7,
        floor_height=4, roof="flat", windows=0, door_width=4, door_height=3.4, wall_material="concrete")
    rng = random.Random(11)
    for g, frac in enumerate((0.2, 0.33, 0.55, 0.62, 0.85)):
        i = round(frac * n) % n
        side = 1 if g % 2 else -1
        bx, by = points[i][0] + normals[i][0] * side * (WALL + 2), points[i][1] + normals[i][1] * side * (WALL + 2)
        for t, (dx, dy, dz) in enumerate([(0, 0, 0), (0.85, 0, 0), (0.42, 0, 0.9)]):
            kit("barrel", f"Tyres{g + 1}_{t + 1}", (bx + dx, by + dy, dz), props, height=0.9, radius=0.42,
                bulge=0, hoops=0, material="rubber", seed=t)
    i = round(0.16 * n)
    for c in range(5):
        j = (i + c * 2) % n
        offset = -(ROAD - 1.5)
        cone_obj = cone(gs).to_object(f"Cone{c + 1}", location=(points[j][0] + normals[j][0] * offset,
                                                                points[j][1] + normals[j][1] * offset, 0.02),
                                      collection=props)
        add(cone_obj, "RigidBody", {"type": "dynamic", "mass": 3})
        add(cone_obj, "Collider", {"shape": "convex", "friction": 0.8})
    placed = 0
    for attempt in range(1500):
        if placed >= 80:
            break
        x, y = rng.uniform(-92, 92), rng.uniform(-92, 92)
        if math.hypot(x, y) > 90 or min(math.dist((x, y), p) for p in points) < WALL + 4:
            continue
        style = "pine" if rng.random() < 0.55 else "round"
        tree = kit("tree", f"Tree{placed + 1}", (x, y, 0), props, style=style, height=rng.uniform(4, 9), seed=placed)
        gs.kits.animation.add_clip(tree, "sway", {"angle": 2})  # one shared action; each plays at its own pace
        add(tree, "Animator", {"clip": "sway", "speed": rng.uniform(0.7, 1.3)})
        placed += 1
    for r in range(10):
        x, y = rng.uniform(-80, 80), rng.uniform(-80, 80)
        if min(math.dist((x, y), p) for p in points) > WALL + 3:
            kit("rock", f"Rock{r + 1}", (x, y, 0), props, size=rng.uniform(1.5, 3.5), moss=0.3, seed=r)

    # --- The car, the camera, the sun ------------------------------------------------------------------
    i = n - 6  # 12 m before the start line
    (cx, cy), (tx, ty) = points[i], tangent(points, i)
    car = car_body(gs).to_object("Car", location=(cx, cy, 0.05), collection=actors)
    car.rotation_euler.z = math.atan2(tx, -ty)  # the car's front (-Y) along the track
    add(car, "RigidBody", {"type": "kinematic"})
    add(car, "Collider", {"shape": "box"})
    add(car, "Car")
    for name, x, y, steers in [("WheelFL", -0.98, -1.3, True), ("WheelFR", 0.98, -1.3, True),
                               ("WheelRL", -0.98, 1.35, False), ("WheelRR", 0.98, 1.35, False)]:
        wheel = wheel_mesh(gs).to_object(f"Car_{name}", collection=actors)
        wheel.parent, wheel.location = car, (x, y, 0.36)
        add(wheel, "Wheel", {"radius": 0.36, "steers": steers})

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 28
    actors.objects.link(cam)
    cam.location = (cx - tx * 8, cy - ty * 8, 3.5)
    cam.rotation_euler = (math.radians(80), 0, math.atan2(-tx, ty))
    scene.camera = cam
    add(cam, "ChaseCamera", {"target": "Car", "distance": 8, "height": 3.2, "lookAhead": 5, "stiffness": 5})

    # The sky kit: a clear day (the game draws its gradient too) and its sun, behind the start.
    gs.kits.create("sky", {"name": "Sun", "preset": "day", "sun_strength": 3.5, "sun_elevation": 44,
                           "sun_azimuth": 200}, collection=actors)
    fog(scene, start=70, depth=150)  # far hills and trees fade into the horizon

    # The music: the starter's looping theme on the music channel, which plays on under the menus (the
    # Options screen's Music slider sets that channel's volume). Not spatial, so where it stands does not matter.
    music = bpy.data.objects.new("Music", None)
    music.empty_display_type, music.empty_display_size = "SPHERE", 0.3
    music.location = (cx, cy, 2)
    actors.objects.link(music)
    add(music, "AudioSource", {"clip": "assets/music/theme.ogg", "volume": 0.5, "loop": True, "autoplay": True,
                               "spatial": False, "channel": "music"})

    return save_and_export(gs, scene, path)


# --- The track's shape ------------------------------------------------------------------------------------


def centre_line():
    """CONTROL as a smooth closed curve: Catmull-Rom, resampled every STEP metres, then relaxed so the
    tightest corner is wide enough for the road and barriers, and centred on the origin. Returns the
    points and the shift that centred them."""
    pts = [(x * SCALE, y * SCALE) for x, y in CONTROL]
    dense = []
    for i in range(len(pts)):
        p0, p1, p2, p3 = (pts[(i + k - 1) % len(pts)] for k in range(4))
        steps = max(2, int(math.dist(p1, p2) / 0.5))
        for j in range(steps):
            t = j / steps
            dense.append(tuple(0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t
                                      + (-a + 3 * b - 3 * c + d) * t ** 3) for a, b, c, d in zip(p0, p1, p2, p3)))
    lengths = [0.0]
    for a, b in zip(dense, dense[1:] + dense[:1]):
        lengths.append(lengths[-1] + math.dist(a, b))
    count = int(lengths[-1] / STEP)
    out, k = [], 0
    for i in range(count):
        s = lengths[-1] * i / count
        while lengths[k + 1] < s:
            k += 1
        a, b = dense[k], dense[(k + 1) % len(dense)]
        f = (s - lengths[k]) / (lengths[k + 1] - lengths[k])
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    for _ in range(20):
        out = [tuple(0.5 * out[i][c] + 0.25 * (out[i - 1][c] + out[(i + 1) % len(out)][c]) for c in (0, 1))
               for i in range(len(out))]
    cx = (min(p[0] for p in out) + max(p[0] for p in out)) / 2
    cy = (min(p[1] for p in out) + max(p[1] for p in out)) / 2
    return [(x - cx, y - cy) for x, y in out], (cx, cy)


def tangent(points, i):
    (ax, ay), (bx, by) = points[i - 1], points[(i + 1) % len(points)]
    length = math.hypot(bx - ax, by - ay)
    return (bx - ax) / length, (by - ay) / length


def right_of(points, i):
    tx, ty = tangent(points, i)
    return ty, -tx


def sweep(m, points, normals, profile, slot_of):
    """A closed cross-section (offset to the right, height) swept around the closed centre line."""
    n, k = len(points), len(profile)
    verts = [(p[0] + nx * u, p[1] + ny * u, z) for p, (nx, ny) in zip(points, normals) for u, z in profile]
    faces, slots = [], []
    for i in range(n):
        j = (i + 1) % n
        for a in range(k):
            b = (a + 1) % k
            faces.append((i * k + a, i * k + b, j * k + b, j * k + a))
            slots.append(slot_of(i))
    m.add(verts, faces, slots)


def road_mesh(gs, points, normals):
    """Asphalt with a dashed centre line and red and white kerbs."""
    m = gs.kits.geometry.MeshBuilder()
    asphalt = m.slot("concrete", color="#4A4D52", roughness=0.85)
    red, white = m.slot("plastic", color="#E03131"), m.slot("plastic", color="#F1F3F5")
    sweep(m, points, normals, [(-ROAD, -0.3), (ROAD, -0.3), (ROAD, 0.05), (-ROAD, 0.05)], lambda i: asphalt)
    for side in (-1, 1):
        inner, outer = sorted((side * ROAD, side * (ROAD + KERB)))
        sweep(m, points, normals, [(inner, -0.3), (outer, -0.3), (outer, 0.06), (inner, 0.06)],
              lambda i: red if (i // 2) % 2 else white)
    for i in range(0, len(points), 4):  # centre dashes: 4 m painted, 4 m gap
        a, b = points[i], points[(i + 1) % len(points)]
        (nx, ny) = normals[i]
        corners = [(a[0] - nx * 0.15, a[1] - ny * 0.15), (a[0] + nx * 0.15, a[1] + ny * 0.15),
                   (b[0] + nx * 0.15, b[1] + ny * 0.15), (b[0] - nx * 0.15, b[1] - ny * 0.15)]
        m.add([(x, y, 0.055) for x, y in corners], [(0, 1, 2, 3)], white)
    return m


def barrier_mesh(gs, points, normals):
    """Low red and white walls on both sides of the track."""
    m = gs.kits.geometry.MeshBuilder()
    red, white = m.slot("plastic", color="#C92A2A"), m.slot("plastic", color="#F8F9FA")
    for side in (-1, 1):
        inner, outer = sorted((side * WALL, side * (WALL + 0.5)))
        sweep(m, points, normals, [(inner, -0.3), (outer, -0.3), (outer, 0.9), (inner, 0.9)],
              lambda i: red if (i // 3) % 2 else white)
    return m


def start_strip(gs, points, normals):
    """A chequered strip across the road at the start line."""
    m = gs.kits.geometry.MeshBuilder()
    black, white = m.slot("rubber"), m.slot("plastic", color="#F8F9FA")
    (x, y), (nx, ny) = points[0], normals[0]
    frame = Matrix(((nx, -ny, 0, x), (ny, nx, 0, y), (0, 0, 1, 0), (0, 0, 0, 1)))  # local X across the road
    size = 0.6
    for col in range(int(2 * ROAD / size)):
        for row in range(3):
            u0, v0 = -ROAD + col * size, (row - 1.5) * size
            m.box((u0, v0, 0.05), (u0 + size, v0 + size, 0.06), black if (col + row) % 2 else white, matrix=frame)
    return m


# --- Props made with MeshBuilder ----------------------------------------------------------------------------


def car_body(gs):
    """A small sports car, front toward -Y, origin on the ground; the wheels are separate objects."""
    m = gs.kits.geometry.MeshBuilder()
    paint = m.slot("plastic", color="#E8382F", roughness=0.3)
    stripe, dark = m.slot("plastic", color="#F8F9FA", roughness=0.3), m.slot("rubber")
    glass = m.slot("plastic", color="#1F2A36", roughness=0.1)
    head, tail = m.slot("emissive", color="#FFF3BF", emission_strength=2), m.slot("emissive", color="#FF2020",
                                                                                   emission_strength=2)
    m.box((-0.75, -1.9, 0.0), (0.75, 1.9, 0.3), dark)  # under-tray: the collider reaches the ground
    m.prism([(-2.1, 0.3), (2.1, 0.3), (2.1, 0.78), (1.9, 0.86), (-1.75, 0.86), (-2.15, 0.62)], "X", -0.95, 0.95,
            paint)
    m.prism([(-0.95, 0.86), (1.25, 0.86), (0.95, 1.32), (-0.35, 1.32)], "X", -0.8, 0.8, glass)
    m.box((-0.62, -0.35, 1.32), (0.62, 0.95, 1.36), paint)  # roof
    m.box((-0.2, -2.12, 0.62), (0.2, -1.0, 0.875), stripe)
    m.box((-0.9, 1.9, 1.02), (0.9, 2.2, 1.08), dark)  # spoiler
    for x in (-0.6, 0.6):
        m.box((x - 0.05, 1.95, 0.86), (x + 0.05, 2.1, 1.02), dark)
        m.box((x - 0.22, -2.17, 0.5), (x + 0.22, -2.1, 0.6), head)
        m.box((x - 0.25, 2.1, 0.6), (x + 0.25, 2.14, 0.7), tail)
    return m


def wheel_mesh(gs):
    """A tyre with a metal hub, axle along X, origin at its centre."""
    m = gs.kits.geometry.MeshBuilder()
    tyre, hub = m.slot("rubber"), m.slot("metal")
    axle = Matrix.Rotation(math.pi / 2, 4, "Y")  # lathe around Z, then lay it along X
    m.lathe([(0.0, -0.14), (0.3, -0.14), (0.36, -0.1), (0.36, 0.1), (0.3, 0.14), (0.0, 0.14)], 16, tyre,
            matrix=axle)
    for side in (-1, 1):
        m.lathe([(0.0, 0.0), (0.2, 0.0), (0.2, 0.02), (0.0, 0.02)], 12, hub, smooth=False,
                matrix=Matrix.Translation((side * 0.145, 0, 0)) @ Matrix.Rotation(side * math.pi / 2, 4, "Y"))
    return m


def cone(gs):
    m = gs.kits.geometry.MeshBuilder()
    orange, white = m.slot("plastic", color="#FF6B1A"), m.slot("plastic", color="#F8F9FA")
    m.box((-0.22, -0.22, 0.0), (0.22, 0.22, 0.04), orange)
    m.lathe([(0.0, 0.04), (0.17, 0.04), (0.12, 0.3), (0.09, 0.42), (0.03, 0.7), (0.0, 0.7)], 12, orange,
            band_slots=[orange, orange, white, orange, orange])
    return m


def arrow(gs):
    """A glowing arrow pointing down at the next checkpoint (its tip at the origin)."""
    m = gs.kits.geometry.MeshBuilder()
    glow = m.slot("emissive", color="#FFD43B", emission_strength=1.5)
    m.lathe([(0.0, 0.0), (0.6, 0.8), (0.27, 0.8), (0.27, 1.6), (0.0, 1.6)], 4, glow, smooth=False)
    return m


def emitter(gs, name, location, coll, rotation=0.0, **fields):
    """An empty with a ParticleEmitter: an effect that plays from the start (fields in runtime space, Y up)."""
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type, obj.empty_display_size = "SPHERE", 0.3
    obj.location, obj.rotation_euler.z = location, rotation
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
