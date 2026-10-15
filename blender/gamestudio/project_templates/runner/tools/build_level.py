# SPDX-License-Identifier: MIT-0
"""Builds scenes/main.blend, the endless runner's level "Sunset Skyway", from GameStudio kits.

New Project runs build(gs, root) once. Run it again to get the level back as this file makes it.
It replaces everything in the file, so put changes you want to keep into this file:

  Coding agent (MCP): run_python with
                        import runpy; runpy.run_path(r"<project>/tools/build_level.py")["build"](gs)
  A terminal:         blender --background --python tools/build_level.py
                      (GameStudio enabled in that Blender, the project set up once by opening it there)

Blender space: metres, Z up. The track runs toward +Y along x = 0, with lanes at x = -2.2, 0 and 2.2
(LANE; src/lib/lanes.ts has the same) and the road's top at z = 0. The file has three scenes:

  main      the start deck, the PlayerStart and Track empties, the camera, the sky, the backdrop and
            the Music empty
  Chunks    the track pieces, side by side: one collection marked gs_prefab per chunk, whose root
            empty (TrackChunk) sits at the chunk's start, with floors, decor, Socket empties and
            lamp posts (collection instances of the LampPost prefab) under it
  Prefabs   what the game spawns: the Runner, obstacles, coin lines, pickups, auras and debris

The Chunk class below builds a chunk; the add-chunk skill uses it to add one.
"""

import math
import pathlib

import bpy
from mathutils import Matrix, Vector

PROJECT = pathlib.Path(__file__).resolve().parents[1]
SKY = (1.0, 0.62, 0.42)
LANE = 2.2  # metres between lane centres
LANES = (-LANE, 0.0, LANE)
HALF_ROAD = 3.8  # the road is 7.6 m wide
CHUNK_SPACING = 50.0  # metres between chunks laid side by side in the Chunks scene
PREFAB_SPACING = 6.0  # metres between prefabs in the Prefabs scene
CLOUDS = "#F2A882"  # the cloud sea, the fog and the sky below the horizon: one colour, no seams
ROAD = {"top": ("stone", {"color": "#D2B08A"}), "side": ("stone_dark", {"color": "#7B5A4B"}),
        "line": ("emissive", {"color": "#FFE9C7", "emission_strength": 0.25}),
        "joint": ("stone_dark", {"color": "#A5845F"}), "curb": ("stone", {"color": "#9C7760"}),
        "warn": ("emissive", {"color": "#FF6A1F", "emission_strength": 3.0}),
        "plank": ("wood", {"color": "#9A6A43"})}


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    scene, path = new_scene(gs, root, SKY)
    add = gs.core.components.add_component
    level, backdrop, actors = (collection(scene.collection, n) for n in ("Level", "Backdrop", "Actors"))

    # --- Prefabs first: chunks place instances of LampPost and CoinArc ----------------------------
    prefabs = bpy.data.scenes.new("Prefabs").collection
    bpy.data.scenes.new("Chunks")
    runner(gs, prefabs)
    hurdle(gs, prefabs)
    crates(gs, prefabs)
    boulder(gs, prefabs)
    sweeper(gs, prefabs)
    coin_line(gs, prefabs)
    coin_arc(gs, prefabs)
    power_up(gs, prefabs, "Magnet", "magnet", "MagnetAura", "#FF4F9A")
    power_up(gs, prefabs, "Shield", "shield", "ShieldBubble", "#4DD4FF")
    magnet_aura(gs, prefabs)
    shield_bubble(gs, prefabs)
    debris_wood(gs, prefabs)
    debris_stone(gs, prefabs)
    lamp_post(gs, prefabs)

    # --- The chunks ---------------------------------------------------------------------------------
    chunk_straight(gs)
    chunk_gap(gs)
    chunk_slalom(gs)
    chunk_ramp(gs)
    chunk_narrow(gs)
    chunk_sweeper(gs)

    # --- The level: a start deck under an arch, then the streamed track ---------------------------------
    deck = slab(gs, "StartDeck", level, -5.0, 5.0, -12.0, 12.0, depth=1.2)
    add(deck, "Collider")
    start_decor(gs, level)
    start = empty("PlayerStart", level, (0, 0, 0), "ARROWS", 1.0)
    start.rotation_euler.z = math.pi  # face +Y, down the track (kits face -Y)
    track = empty("Track", level, (0, 12, 0), "ARROWS", 2.0)  # the first chunk starts here
    add(track, "Track", {"ahead": 160, "behind": 20, "warmup": 60})
    for i, (x, y, z, size, rot) in enumerate([(-16, 4, -6, 7, 20), (19, 20, -3, 5, -30), (-24, 40, 2, 4, 60)]):
        floating_island(gs, f"StartIsland{i + 1}", level, (x, y, z), size, rot, seed=40 + i)

    # --- The backdrop travels with the runner (Backdrop): clouds, far islands, wind --------------------
    sea = gs.kits.create("terrain", {"name": "CloudSea", "size": 200, "resolution": 48, "height": 5,
                                     "feature_size": 28, "skirt": 0.5, "seed": 5}, location=(0, 70, -18),
                         collection=backdrop)[0]
    gs.kits.materials.apply(sea, "snow", color=CLOUDS, roughness=0.9, name="CloudSea")
    add(sea, "Backdrop")
    for i, (x, y, z, size, rot) in enumerate([(-75, 90, 4, 26, 15), (85, 130, -2, 30, -40), (-95, 175, 10, 22, 70),
                                              (60, 60, 14, 14, 110)]):
        add(floating_island(gs, f"FarIsland{i + 1}", backdrop, (x, y, z), size, rot, seed=10 + i, trees=3), "Backdrop")
    wind = emitter(gs, "WindMotes", (0, 30, 2), backdrop, shape="box", box=[16, 6, 36], rate=45, lifetime=[1.0, 1.8],
                   speed=[0.1, 0.4], spread=math.pi, size=[0.07, 0.02], colorStart=[1, 0.92, 0.78, 0.9],
                   colorEnd=[1, 0.7, 0.5, 0], brightness=2.2)
    add(wind, "Backdrop")

    # --- Camera and light --------------------------------------------------------------------------------
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 24
    cam.data.clip_end = 400
    actors.objects.link(cam)
    cam.location = (0, -6.8, 3.4)  # PlayerStart + the FollowCamera offset (runtime [0, 3.4, 6.8])
    cam.rotation_euler = (math.radians(81), 0, 0)
    scene.camera = cam
    # RunSystem points the target at the Runner it spawns; looking 6 m ahead shows the track coming.
    add(cam, "FollowCamera", {"offset": [0, 3.4, 6.8], "smoothing": 7, "lookAtOffset": [0, 1.4, -6]})
    # A touch of bloom: lamps, warning strips, auras and sparkles glow; the road and the clouds don't.
    add(cam, "PostEffects", {"bloom": True, "bloomStrength": 0.5, "bloomThreshold": 1.6, "bloomRadius": 0.3})

    # A low sun from the side (+X): long shadows across the lanes. From the side, not ahead, because
    # the game's shadows follow the camera along the track, and they keep working for any distance
    # only while the light has no part along it.
    gs.kits.create("sky", {"name": "Sun", "preset": "sunset", "horizon": "#FFB36B", "zenith": "#4E5FA6",
                           "ground": CLOUDS, "sun_color": "#FFC08A", "sun_strength": 3.0, "sun_elevation": 30,
                           "sun_azimuth": 90}, collection=actors)
    fog(scene, start=35, depth=120, color=CLOUDS)  # Track.ahead (160) is past the fog's end: no pop-in

    # The music: the starter's theme, looping on the music channel (it plays on under the menus, and the
    # Options screen's Music slider sets its volume). In the level, not a prefab: it plays from the start.
    music = empty("Music", actors, (0, -3, 2), "SPHERE", 0.4)
    add(music, "AudioSource", {"clip": "assets/music/theme.ogg", "loop": True, "autoplay": True, "spatial": False,
                               "channel": "music", "volume": 0.5})

    return save_and_export(gs, scene, path)


# --- Chunks --------------------------------------------------------------------------------------------


class Chunk:
    """A chunk prefab under construction, in the Chunks scene.

    Coordinates are local to the root (the chunk's start): x across the track (LANES), y along it
    from 0 to `length`, z up with the road's top at 0. Floors get a Collider; decor does not. Call
    finish() at the end.

        c = Chunk(gs, "ChunkSpikes", length=30, weight=1, min_distance=300)
        c.floor(0, 30)
        c.socket("obstacle", (0, 10), any_lane=True)
        c.socket("coins", (0, 14), any_lane=True, chance=0.8)
        c.lamp(-1, 5)
        c.finish()
    """

    def __init__(self, gs, name, *, length=30.0, weight=1.0, min_distance=0.0):
        self.gs, self.name, self.length = gs, name, length
        scene = bpy.data.scenes.get("Chunks") or bpy.data.scenes.new("Chunks")
        slot = len(scene.collection.children)
        self.coll = prefab_collection(gs, scene.collection, name)
        self.root = empty(name, self.coll, (slot * CHUNK_SPACING, 0, 0), "ARROWS", 2.0)
        self.coll.instance_offset = self.root.location
        gs.core.components.add_component(self.root, "TrackChunk", {"length": length, "weight": weight,
                                                                   "minDistance": min_distance})
        self.decor = gs.kits.geometry.MeshBuilder()
        self.count = {}

    def _name(self, kind):
        self.count[kind] = self.count.get(kind, 0) + 1
        return f"{self.name}_{kind}{self.count[kind]}"

    def attach(self, obj, at, rotation=0.0):
        """Parents obj to the root at local `at` (x, y[, z]), turned `rotation` degrees about Z."""
        if obj.name not in self.coll.objects:
            for other in list(obj.users_collection):
                other.objects.unlink(obj)
            self.coll.objects.link(obj)
        obj.parent = self.root
        obj.matrix_parent_inverse = Matrix()  # `at` is relative to the root
        x, y, *z = at
        obj.location = (x, y, z[0] if z else 0.0)
        obj.rotation_euler.z = math.radians(rotation)
        return obj

    def floor(self, y0, y1, x0=-HALF_ROAD, x1=HALF_ROAD, top=0.0, planks=False):
        """A floor slab (with a Collider) from y0 to y1, its top at `top`, with lane lines and curbs."""
        obj = slab(self.gs, self._name("Floor"), self.coll, x0, x1, y0, y1, top=top, planks=planks)
        self.attach(obj, (0, 0))
        self.gs.core.components.add_component(obj, "Collider")
        if not planks:
            road_marks(self.decor, x0, x1, y0, y1, top)
        return obj

    def ramp(self, y0, y1, y2, rise):
        """A ramp from y0 (road level) up to `rise` at y1, a deck at that height to y2, then a drop."""
        m = self.gs.kits.geometry.MeshBuilder()
        side, top = slot(m, "side"), slot(m, "top")
        m.prism([(y0, -1.0), (y2, -1.0), (y2, rise - 0.06), (y1, rise - 0.06), (y0, -0.06)], "X", -HALF_ROAD,
                HALF_ROAD, side)
        m.prism([(y0, -0.06), (y1, rise - 0.06), (y2, rise - 0.06), (y2, rise), (y1, rise), (y0, 0.0)], "X",
                -HALF_ROAD, HALF_ROAD, top)
        obj = self.attach(m.to_object(self._name("Ramp"), collection=self.coll), (0, 0))
        self.gs.core.components.add_component(obj, "Collider", {"shape": "convex"})  # a wedge: convex is exact
        road_marks(self.decor, -HALF_ROAD, HALF_ROAD, y1, y2, rise)
        warn_strip(self.decor, -HALF_ROAD, HALF_ROAD, y2 - 0.35, y2, rise)
        return obj

    def socket(self, kind, at, *, chance=1.0, any_lane=False, options=""):
        """A Socket empty: an obstacle, coins or a pickup spawns here when the chunk does."""
        shape = {"obstacle": "CUBE", "coins": "SPHERE", "pickup": "CONE"}[kind]
        obj = empty(self._name(f"Socket_{kind.capitalize()}"), self.coll, (0, 0, 0), shape, 0.45)
        self.attach(obj, at)
        self.gs.core.components.add_component(obj, "Socket", {"kind": kind, "chance": chance, "anyLane": any_lane,
                                                               "options": options})
        return obj

    def instance(self, prefab, at, rotation=0.0):
        """A collection instance of another prefab: at runtime it becomes a full instance, components included."""
        obj = bpy.data.objects.new(self._name(prefab), None)
        obj.instance_type = "COLLECTION"
        obj.instance_collection = bpy.data.collections[prefab]
        obj.empty_display_size = 0.5
        return self.attach(obj, at, rotation)

    def lamp(self, side, y):
        """A LampPost (a nested prefab) beside the road: side -1 left, 1 right; its lamp hangs over the road."""
        return self.instance("LampPost", (side * (HALF_ROAD + 0.45), y), 0 if side < 0 else 180)

    def warn(self, y0, y1, x0=-HALF_ROAD, x1=HALF_ROAD, top=0.0):
        """A glowing strip that warns of a drop or a gap."""
        warn_strip(self.decor, x0, x1, y0, y1, top)

    def pillar(self, y, top=0.0):
        """A stone pillar under the road, down into the clouds."""
        m = self.decor
        m.box((-1.1, y - 1.1, -16.0), (1.1, y + 1.1, top - 1.0), slot(m, "side"))
        m.box((-1.5, y - 1.5, top - 1.6), (1.5, y + 1.5, top - 1.0), slot(m, "side"))

    def rock(self, at, size, seed, rotation=0.0):
        """Scenery: a floating island with trees beside the track (no collider)."""
        obj = floating_island(self.gs, self._name("Island"), self.coll, (0, 0, 0), size, rotation, seed=seed)
        return self.attach(obj, at, rotation)

    def finish(self):
        """The decor mesh (lines, curbs, warning strips, pillars) as one object, without a Collider."""
        if self.decor.bm.faces:
            self.attach(self.decor.to_object(f"{self.name}_Decor", collection=self.coll), (0, 0))
        return self.root


def chunk_straight(gs):
    """Open road: two obstacles in random lanes, a coin line between them, sometimes a pickup."""
    c = Chunk(gs, "ChunkStraight", length=30, weight=3)
    c.floor(0, 30)
    c.pillar(15)
    c.socket("pickup", (0, 3), any_lane=True, chance=0.15)
    c.socket("obstacle", (0, 9), any_lane=True, chance=0.9)
    c.socket("coins", (0, 12.5), any_lane=True, chance=0.8)
    c.socket("obstacle", (0, 24), any_lane=True, chance=0.7)
    c.lamp(-1, 6)
    c.lamp(1, 21)
    c.rock((-17, 12, -5), 6, seed=1, rotation=30)
    c.rock((20, 24, 2), 3, seed=2)
    return c.finish()


def chunk_gap(gs):
    """A 3.5 m gap across the road to jump, with a coin arc over it (a nested CoinArc prefab)."""
    c = Chunk(gs, "ChunkGap", length=30, weight=2, min_distance=80)
    c.floor(0, 11)
    c.floor(14.5, 30)
    c.warn(10.6, 11)
    c.warn(14.5, 14.9)
    c.pillar(5)
    c.pillar(22)
    c.socket("obstacle", (0, 3), any_lane=True, chance=0.5)
    c.instance("CoinArc", (0, 8.7))
    c.socket("coins", (0, 20), any_lane=True, chance=0.7)
    c.lamp(1, 4)
    c.lamp(-1, 18)
    c.rock((18, 9, -7), 7, seed=3, rotation=-20)
    return c.finish()


def chunk_slalom(gs):
    """Three rows of tall obstacles, each with one lane free; the coins show the way."""
    c = Chunk(gs, "ChunkSlalom", length=36, weight=2, min_distance=160)
    c.floor(0, 36)
    c.pillar(18)
    for y, blocked in ((9, (0, 1)), (20, (0, 2)), (31, (1, 2))):
        for lane in blocked:
            c.socket("obstacle", (LANES[lane], y), options="Crates:2, Boulder:1")
    c.socket("coins", (LANES[1], 12), options="CoinLine")
    c.socket("coins", (LANES[0], 22.5), options="CoinLine")
    c.lamp(-1, 12)
    c.lamp(1, 28)
    c.rock((-19, 20, 3), 4, seed=4, rotation=80)
    return c.finish()


def chunk_ramp(gs):
    """A ramp up to a raised deck that ends in a 2.2 m drop: jump off it through the coins."""
    c = Chunk(gs, "ChunkRamp", length=36, weight=1.5, min_distance=240)
    c.floor(0, 6)
    c.ramp(6, 14, 24, 2.2)
    c.floor(24, 36)
    c.pillar(12)
    c.pillar(30)
    c.socket("obstacle", (0, 19, 2.2), any_lane=True, chance=0.8)
    c.socket("coins", (0, 23.5, 2.2), any_lane=True, options="CoinArc")
    c.socket("coins", (0, 28), any_lane=True, chance=0.6)
    c.lamp(-1, 3)
    c.lamp(1, 33)
    c.rock((21, 16, -2), 5, seed=5, rotation=45)
    return c.finish()


def chunk_narrow(gs):
    """The road narrows to a plank bridge in the middle lane: keep to it and jump its hurdle."""
    c = Chunk(gs, "ChunkNarrow", length=30, weight=1.5, min_distance=320)
    c.floor(0, 6)
    c.floor(6, 24, x0=-1.3, x1=1.3, planks=True)
    c.floor(24, 30)
    for x0, x1 in ((-HALF_ROAD, -1.3), (1.3, HALF_ROAD)):
        c.warn(5.6, 6, x0, x1)
        c.warn(24, 24.4, x0, x1)
    c.pillar(15)
    c.socket("coins", (0, 7), options="CoinLine")
    c.socket("obstacle", (0, 16), chance=0.8, options="Hurdle")
    c.socket("coins", (0, 19), chance=0.6, options="CoinLine")
    c.lamp(-1, 3)
    c.lamp(1, 27)
    c.rock((-15, 15, -9), 8, seed=6, rotation=-60)
    return c.finish()


def chunk_sweeper(gs):
    """A drum rolls from side to side across the road (the Sweeper prefab); pass it where it isn't."""
    c = Chunk(gs, "ChunkSweeper", length=30, weight=1.5, min_distance=400)
    c.floor(0, 30)
    c.warn(13.2, 13.5)
    c.warn(16.5, 16.8)
    c.pillar(15)
    c.socket("coins", (0, 2), any_lane=True, chance=0.8)
    c.socket("obstacle", (0, 15), options="Sweeper")
    c.socket("coins", (0, 20), any_lane=True, chance=0.8)
    c.lamp(-1, 10)
    c.lamp(1, 10)
    c.rock((22, 14, 0), 4, seed=7, rotation=10)
    return c.finish()


# --- Prefabs (the Prefabs scene) ---------------------------------------------------------------------


def place_prefab(gs, parent, name):
    """A prefab collection in the Prefabs scene and the spot for its root, next to the others."""
    coll = prefab_collection(gs, parent, name)
    at = Vector((len(parent.children) * PREFAB_SPACING, 0, 0))
    coll.instance_offset = at
    return coll, at


def runner(gs, parent):
    """The player: a rigged character with the Runner components; RunSystem spawns it at PlayerStart."""
    coll, at = place_prefab(gs, parent, "Runner")
    rig = gs.kits.create("character_rigged", {"name": "Runner", "height": 1.75, "color": "#2F80ED",
                                              "legs_color": "#27304A", "hair_color": "#3B2A1E"},
                         location=at, collection=coll)[0]
    add = gs.core.components.add_component
    add(rig, "Runner")
    add(rig, "RigidBody", {"type": "kinematic"})
    add(rig, "Collider", {"shape": "capsule"})
    return rig  # its clips: Run (played faster with the speed), JumpStart, JumpAir and Fall; see runner.ts


def obstacle_root(gs, obj, debris):
    gs.core.components.add_component(obj, "Obstacle", {"debris": debris})
    return obj


def hurdle(gs, parent):
    """A low striped barrier across one lane: jump it or change lane."""
    coll, at = place_prefab(gs, parent, "Hurdle")
    m = gs.kits.geometry.MeshBuilder()
    post, red, white = m.slot("wood_dark"), m.slot("plastic", color="#E03131"), m.slot("plastic", color="#F4EFE6")
    for side in (-1, 1):
        x = side * 0.88
        m.box((x - 0.07, -0.07, 0.0), (x + 0.07, 0.07, 0.75), post)
        m.box((x - 0.1, -0.32, 0.0), (x + 0.1, 0.32, 0.06), post)
    for i in range(5):
        x0 = -0.81 + i * 1.62 / 5
        m.box((x0, -0.05, 0.47), (x0 + 1.62 / 5, 0.05, 0.75), red if i % 2 == 0 else white)
    m.box((-0.81, -0.04, 0.2), (0.81, 0.04, 0.3), post)
    return obstacle_root(gs, m.to_object("Hurdle", location=at, collection=coll), "DebrisWood")


def crates(gs, parent):
    """Two stacked crates, 1.85 m: too tall to jump."""
    coll, at = place_prefab(gs, parent, "Crates")
    bottom = gs.kits.create("crate", {"name": "Crates", "width": 1.5, "depth": 1.2, "height": 1.0, "seed": 3},
                            location=at, collection=coll)[0]
    top = gs.kits.create("crate", {"name": "CratesTop", "width": 1.15, "depth": 1.0, "height": 0.85, "seed": 4},
                         collection=coll)[0]
    top.parent = bottom
    top.location = (0.08, 0.05, 1.0)
    top.rotation_euler.z = math.radians(12)
    return obstacle_root(gs, bottom, "DebrisWood")


def boulder(gs, parent):
    """A boulder, 1.5 m: too tall to jump."""
    coll, at = place_prefab(gs, parent, "Boulder")
    rock = gs.kits.create("rock", {"name": "Boulder", "size": 1.8, "height": 0.85, "moss": 0.2, "seed": 9},
                          location=at, collection=coll)[0]
    return obstacle_root(gs, rock, "DebrisStone")


def sweeper(gs, parent):
    """The moving hazard: an empty that slides across the lanes, with a drum that rolls as it goes."""
    coll, at = place_prefab(gs, parent, "Sweeper")
    root = empty("Sweeper", coll, at, "PLAIN_AXES", 1.0)
    add = gs.core.components.add_component
    add(root, "Sweeper", {"travel": LANE, "speed": 1.6, "radius": 0.85})
    obstacle_root(gs, root, "DebrisStone")
    m = gs.kits.geometry.MeshBuilder()
    stone, glow, dark = m.slot("stone", color="#8D7B6A"), m.slot("emissive", color="#FF6A1F", emission_strength=3.0), \
        m.slot("stone_dark")
    lay = Matrix.Rotation(math.pi / 2, 4, "X")  # the drum's axis along Y, the way the track runs
    m.lathe([(0.0, -0.9), (0.72, -0.9), (0.85, -0.8), (0.85, 0.8), (0.72, 0.9), (0.0, 0.9)], 18, stone, matrix=lay)
    for z in (-0.5, 0.5):
        m.torus(0.86, 0.05, 24, 4, glow, matrix=lay @ Matrix.Translation((0, 0, z)))
    for k in range(6):  # slats along it, so the roll shows
        turn = Matrix.Rotation(k * math.pi / 3, 4, "Y")
        m.box((-0.1, -0.78, 0.8), (0.1, 0.78, 0.9), dark, matrix=turn)
    drum = m.to_object("SweeperDrum", collection=coll)
    drum.parent = root
    drum.location = (0, 0, 0.85)  # its axis; the root is on the road
    return root


def coin(gs, coll, name, parent, location):
    obj = gs.kits.create("collectible", {"name": name, "style": "coin", "size": 0.6}, collection=coll)[0]
    gs.kits.materials.apply(obj, "gold", metallic=0.6, emission="#FFB01F", emission_strength=0.6, name="CoinGold")
    gs.core.components.add_component(obj, "Coin", {"value": 1})
    obj.parent = parent
    obj.location = location
    return obj


def coin_line(gs, parent):
    """Five coins in a row along the lane, chest high."""
    coll, at = place_prefab(gs, parent, "CoinLine")
    root = empty("CoinLine", coll, at, "PLAIN_AXES", 0.5)
    for i in range(5):
        coin(gs, coll, f"CoinLine_Coin{i + 1}", root, (0, 2.0 * i, 1.0))
    return root


def coin_arc(gs, parent):
    """Seven coins along a jump's arc, 8 m long and 1.4 m high: jump at its start to take them all."""
    coll, at = place_prefab(gs, parent, "CoinArc")
    root = empty("CoinArc", coll, at, "PLAIN_AXES", 0.5)
    for i in range(7):
        y = 8.0 * i / 6
        coin(gs, coll, f"CoinArc_Coin{i + 1}", root, (0, y, 1.0 + 1.4 * (1 - ((y - 4) / 4) ** 2)))
    return root


def power_up(gs, parent, name, kind, aura, color):
    """A pickup: an empty on the road with PowerUp, and a spinning icon above it."""
    coll, at = place_prefab(gs, parent, name)
    root = empty(name, coll, at, "PLAIN_AXES", 0.5)
    gs.core.components.add_component(root, "PowerUp", {"kind": kind, "duration": 8, "aura": aura})
    if kind == "shield":
        icon = gs.kits.create("pickup", {"name": f"{name}Icon", "style": "shield", "size": 0.75, "color": color},
                              collection=coll)[0]
    else:
        m = gs.kits.geometry.MeshBuilder()
        body, tips = m.slot("plastic", color=color, emission=color, emission_strength=0.4), m.slot("metal")
        for side in (-1, 1):  # a horseshoe magnet, open at the top
            m.box((side * 0.2 - 0.09, -0.08, -0.25), (side * 0.2 + 0.09, 0.08, 0.15), body)
            m.box((side * 0.2 - 0.09, -0.085, 0.15), (side * 0.2 + 0.09, 0.085, 0.32), tips)
        m.box((-0.29, -0.08, -0.36), (0.29, 0.08, -0.2), body)
        icon = m.to_object(f"{name}Icon", collection=coll)
    icon.parent = root
    icon.location = (0, 0, 1.2)
    gs.core.components.add_component(icon, "Spin", {"speed": 2.5})
    rgb = [c / 255 for c in bytes.fromhex(color[1:])]
    sparkles = emitter(gs, f"{name}Sparkles", (0, 0, 1.2), coll, texture="star", rate=8, lifetime=[0.5, 1.0],
                       speed=[0.2, 0.5], spread=math.pi, shape="sphere", radius=0.5, size=[0.18, 0.0],
                       colorStart=[*rgb, 1], colorEnd=[*rgb, 0], brightness=2.5)
    sparkles.parent = root
    sparkles.location = (0, 0, 1.2)
    return root


def magnet_aura(gs, parent):
    """Two glowing rings that spin around the runner while it has the magnet."""
    coll, at = place_prefab(gs, parent, "MagnetAura")
    m = gs.kits.geometry.MeshBuilder()
    glow = m.slot("emissive", color="#FF4F9A", emission_strength=3.0)
    m.torus(0.7, 0.035, 32, 4, glow, matrix=Matrix.Translation((0, 0, 0.08)))
    m.torus(0.55, 0.03, 32, 4, glow, matrix=Matrix.Translation((0, 0, 1.0)) @ Matrix.Rotation(0.3, 4, "X"))
    ring = m.to_object("MagnetAura", location=at, collection=coll)
    gs.core.components.add_component(ring, "Spin", {"speed": 3})
    return ring


def shield_bubble(gs, parent):
    """A see-through bubble with sparkles around the runner while it has the shield."""
    coll, at = place_prefab(gs, parent, "ShieldBubble")
    m = gs.kits.geometry.MeshBuilder()
    glass = m.slot("glass", color="#8FE3FF", alpha=0.22, emission="#4DD4FF", emission_strength=0.5)
    m.icosphere(3, glass, smooth=True, deform=lambda co: co * 1.05, matrix=Matrix.Translation((0, 0, 0.95)))
    bubble = m.to_object("ShieldBubble", location=at, collection=coll)
    sparkles = emitter(gs, "ShieldBubbleSparkles", (0, 0, 0), coll, texture="star", rate=14, lifetime=[0.4, 0.8],
                       speed=[0.1, 0.3], spread=math.pi, shape="sphere", radius=1.0, size=[0.14, 0.0],
                       colorStart=[0.3, 0.85, 1, 1], colorEnd=[0.3, 0.85, 1, 0], brightness=2.5, space="local")
    sparkles.parent = bubble
    sparkles.location = (0, 0, 0.95)
    return bubble


def debris(gs, parent, name, pieces):
    """A debris prefab: an empty with Debris, and pieces with dynamic bodies that DebrisSystem throws apart."""
    coll, at = place_prefab(gs, parent, name)
    root = empty(name, coll, at, "SPHERE", 0.4)
    add = gs.core.components.add_component
    add(root, "Debris", {"force": 6, "lifetime": 2.5})
    for i, (piece, location, rotation) in enumerate(pieces(coll)):
        piece.parent = root
        piece.location = location
        piece.rotation_euler = [math.radians(r) for r in rotation]
        add(piece, "RigidBody", {"type": "dynamic", "mass": 0.5})
        add(piece, "Collider", {"shape": "convex"})
    return root


def debris_wood(gs, parent):
    def pieces(coll):
        spots = [((-0.4, 0, 0.3), (0, 0, 20)), ((0.4, 0.1, 0.4), (0, 30, -15)), ((-0.2, -0.1, 0.8), (40, 0, 60)),
                 ((0.25, 0.05, 0.9), (0, -20, 100)), ((0, 0.15, 1.3), (15, 45, 0)), ((0.1, -0.2, 0.55), (-30, 10, 140))]
        for i, (location, rotation) in enumerate(spots):
            m = gs.kits.geometry.MeshBuilder()
            m.box((-0.28, -0.06, -0.04), (0.28, 0.06, 0.04), m.slot("wood", color="#B07A4A"))
            yield m.to_object(f"DebrisWood_Plank{i + 1}", collection=coll), location, rotation
    return debris(gs, parent, "DebrisWood", pieces)


def debris_stone(gs, parent):
    def pieces(coll):
        spots = [((-0.35, 0, 0.3), (0, 0, 0)), ((0.35, 0.1, 0.4), (20, 0, 40)), ((0, -0.2, 0.8), (0, 30, 90)),
                 ((0.15, 0.2, 1.1), (45, 0, 10)), ((-0.2, 0.1, 1.0), (0, 0, 130))]
        for i, (location, rotation) in enumerate(spots):
            rock = gs.kits.create("rock", {"name": f"DebrisStone_Chunk{i + 1}", "size": 0.45 - 0.04 * i,
                                           "detail": 1, "seed": 20 + i}, collection=coll)[0]
            yield rock, location, rotation
    return debris(gs, parent, "DebrisStone", pieces)


def lamp_post(gs, parent):
    """A street lamp with its lantern over the road (+X). Chunks place it as a collection instance."""
    coll, at = place_prefab(gs, parent, "LampPost")
    m = gs.kits.geometry.MeshBuilder()
    iron, glow = m.slot("iron"), m.slot("emissive", color="#FFC46B", emission_strength=1.8)
    m.lathe([(0.0, 0.0), (0.18, 0.0), (0.18, 0.15), (0.08, 0.25), (0.06, 4.3), (0.0, 4.35)], 8, iron)
    m.box((0.0, -0.04, 4.1), (1.15, 0.04, 4.18), iron)
    m.box((1.05, -0.03, 3.95), (1.11, 0.03, 4.1), iron)
    m.icosphere(2, glow, smooth=True, deform=lambda co: co * 0.17, matrix=Matrix.Translation((1.08, 0, 3.8)))
    return m.to_object("LampPost", location=at, collection=coll)


# --- Level pieces -------------------------------------------------------------------------------------


def slot(m, role):
    preset, overrides = ROAD[role]
    return m.slot(preset, **overrides)


def slab(gs, name, coll, x0, x1, y0, y1, top=0.0, depth=1.0, planks=False):
    """A floor block: its top at `top`, `depth` thick (the box Collider comes from its bounds)."""
    m = gs.kits.geometry.MeshBuilder()
    m.box((x0, y0, top - depth), (x1, y1, top - 0.06), slot(m, "side"))
    if planks:  # a bridge of boards across, with gaps between them
        y = y0
        while y < y1 - 1e-6:
            m.box((x0, y, top - 0.06), (x1, min(y + 0.45, y1), top), slot(m, "plank"))
            y += 0.5
    else:
        m.box((x0, y0, top - 0.06), (x1, y1, top), slot(m, "top"))
    return m.to_object(name, collection=coll)


def road_marks(m, x0, x1, y0, y1, top):
    """Dashed lane lines, joints across the road every 3 m, and curbs along a full-width road."""
    full = x1 - x0 > 2 * LANE
    if full:
        for x in (-LANE / 2, LANE / 2):
            y = y0 + 0.4
            while y + 1.4 < y1:
                m.box((x - 0.05, y, top), (x + 0.05, y + 1.4, top + 0.012), slot(m, "line"))
                y += 2.6
        for side in (-1, 1):
            a, b = sorted((side * (HALF_ROAD - 0.28), side * HALF_ROAD))
            m.box((a, y0, top), (b, y1, top + 0.16), slot(m, "curb"))
    y = math.ceil(y0 / 3.0) * 3.0
    while y < y1 - 0.1:
        if y > y0 + 0.1:
            m.box((x0 + (0.3 if full else 0.0), y - 0.04, top), (x1 - (0.3 if full else 0.0), y + 0.04, top + 0.006),
                  slot(m, "joint"))
        y += 3.0


def warn_strip(m, x0, x1, y0, y1, top):
    m.box((x0, y0, top), (x1, y1, top + 0.015), slot(m, "warn"))


def floating_island(gs, name, coll, location, size, rotation=0.0, seed=0, trees=2):
    """A rock turned upside down (flat top, jagged underside) with pine trees: scenery, no collider."""
    rock = gs.kits.create("rock", {"name": name, "size": size, "height": 0.8, "roughness": 0.6,
                                   "detail": 3 if size > 10 else 2, "material": "stone", "seed": seed},
                          collection=coll)[0]
    rock.data.transform(Matrix.Rotation(math.pi, 4, "Y"))  # the flat base on top, at z = 0
    rock.location = location
    rock.rotation_euler.z = math.radians(rotation)
    grass = gs.kits.create("platform", {"name": f"{name}_Grass", "width": size * 0.55, "depth": size * 0.45,
                                        "height": 0.25, "bevel": 0.1, "top_material": "grass",
                                        "side_material": "dirt"}, collection=coll)[0]
    grass.parent = rock
    grass.location = (0, 0, -0.05)
    for i in range(trees):
        tree = gs.kits.create("tree", {"name": f"{name}_Tree{i + 1}", "style": "pine",
                                       "height": size * (0.35 + 0.1 * (i % 2)), "seed": seed * 3 + i},
                              collection=coll)[0]
        tree.parent = rock
        tree.location = ((i - (trees - 1) / 2) * size * 0.22, (i % 2 - 0.5) * size * 0.12, 0.15)
    return rock


def start_decor(gs, coll):
    """Lane lines on the start deck, a stone arch over it with lanterns, and pillars under it."""
    m = gs.kits.geometry.MeshBuilder()
    road_marks(m, -HALF_ROAD, HALF_ROAD, -12, 12, 0.0)
    stone, glow = slot(m, "side"), m.slot("emissive", color="#FFC46B", emission_strength=4.0)
    for side in (-1, 1):
        x = side * 4.6
        m.box((x - 0.45, 7.55, 0.0), (x + 0.45, 8.45, 5.0), stone)
        m.box((x - 0.6, 7.4, 0.0), (x + 0.6, 8.6, 0.5), stone)
        m.icosphere(2, glow, smooth=True, deform=lambda co: co * 0.22, matrix=Matrix.Translation((x * 0.75, 8, 4.3)))
    m.box((-5.2, 7.5, 5.0), (5.2, 8.5, 5.8), stone)
    m.box((-4.1, 7.45, 4.92), (4.1, 8.55, 5.0), slot(m, "warn"))
    for y in (-6, 6):
        m.box((-1.4, y - 1.4, -16.0), (1.4, y + 1.4, -1.2), stone)
    return m.to_object("StartDecor", collection=coll)


# --- Scene plumbing ----------------------------------------------------------------------------------


def collection(parent, name):
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def prefab_collection(gs, parent, name):
    coll = collection(parent, name)
    gs.core.export.set_prefab(coll)
    return coll


def empty(name, coll, location, shape, size):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type, obj.empty_display_size = shape, size
    obj.location = location
    coll.objects.link(obj)
    return obj


def emitter(gs, name, location, coll, **fields):
    """An empty with a ParticleEmitter: an effect that plays from the start (fields in runtime space, Y up)."""
    obj = empty(name, coll, location, "SPHERE", 0.3)
    gs.core.components.add_component(obj, "ParticleEmitter", fields)
    return obj


def new_scene(gs, root, sky):
    """Empty the current scene, save it as the project's start scene and load its components."""
    scene = bpy.context.scene
    for other in [s for s in bpy.data.scenes if s != scene]:  # the Chunks and Prefabs scenes of an earlier build
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


def fog(scene, start, depth, color=None):
    """Distance fog: the world's mist, which the game draws from `start` metres, full `depth` metres
    further, in the sky's horizon colour or `color` (world["gs_fog_color"])."""
    mist = scene.world.mist_settings
    mist.use_mist, mist.start, mist.depth = True, start, depth
    if color:
        scene.world["gs_fog_color"] = color


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
