# SPDX-License-Identifier: GPL-3.0-or-later
"""Environment kits: a water body, and the sky (the world's gradient plus a matching sun lamp).

What of the sky reaches the game: the sun lamp (glTF light: direction, colour, strength), the
gradient (ground, horizon and zenith colours) as the background, which also tints the fill light,
and the world's mist as distance fog. The sun disc and the stars render in Blender only.
"""

import math

import bpy
from mathutils import Vector

from . import materials
from .base import boolean, choice, color, integer, kit, number
from .geometry import fbm, seed_offset

# ------------------------------------------------------------------ water

WATER = next(x["color"] for x in materials.presets() if x["name"] == "water")


def _water_clips(p):
    return [("bob", {"height": 0.03, "speed": 0.2}, "bob")] if p["bob"] else []


@kit("water", label="Water",
     description=("A body of water: a see-through surface with low, faceted ripples (static, so it stays cheap) "
                  "over sides and a bottom `depth` below it, square or round. The origin is the centre of the surface "
                  "(z = 0, the ripples rise a little above it). Sink it into terrain or a basin; a box Collider with "
                  "sensor on fills the volume (swimming, splashes). Optional gentle 'bob' clip (needs an Animator)."),
     params={
         "shape": choice("square", ("square", "round"), "square patch or round pond"),
         "size": number(10.0, 0.5, 400.0, "Width in metres (round: the diameter)"),
         "depth": number(1.0, 0.05, 50.0, "How far the water reaches below its surface, metres"),
         "resolution": integer(12, 1, 64, "Grid cells per side for the ripples (1 = a flat quad)"),
         "waves": number(0.08, 0.0, 2.0, "Height of the ripples in metres (0 = flat)"),
         "wave_size": number(2.5, 0.2, 50.0, "Typical distance between ripples in metres"),
         "bob": boolean(False, "Add a gentle looping 'bob' clip (3 cm up and down every 5 s)"),
         "color": color("auto", "Water colour as #RRGGBB; auto = the water preset's blue"),
     }, pivot="ground", max_tris=2 * 64 * 64 + 12 * 64, clips=_water_clips)
def water(m, p, rng):
    tint = p["color"] if p["color"] != "auto" else None
    top = m.slot("water", color=tint) if tint else m.slot("water")
    deep = m.slot("water", color=_darker(tint or WATER), alpha=0.85)
    off = seed_offset(rng)
    n, half, depth = p["resolution"], p["size"] / 2, p["depth"]

    def height(x, y):
        if p["waves"] <= 0:
            return 0.0
        return p["waves"] * fbm(Vector((x, y, 0.0)) / p["wave_size"] + off, octaves=2, persistence=0.5)

    verts, faces, slots = [], [], []
    if p["shape"] == "square":
        step = p["size"] / n
        idx = lambda i, j: j * (n + 1) + i  # noqa: E731
        for j in range(n + 1):
            for i in range(n + 1):
                x, y = -half + i * step, -half + j * step
                edge = i in (0, n) or j in (0, n)
                verts.append((x, y, 0.0 if edge else height(x, y)))
        faces = [(idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)) for j in range(n) for i in range(n)]
        ring = ([idx(i, 0) for i in range(n)] + [idx(n, j) for j in range(n)]
                + [idx(i, n) for i in range(n, 0, -1)] + [idx(0, j) for j in range(n, 0, -1)])
    else:
        segs = max(12, 4 * round(1.5 * n / 4))
        rings = max(1, n // 2)
        verts.append((0.0, 0.0, height(0.0, 0.0) if rings > 1 else 0.0))
        rows = []
        for k in range(1, rings + 1):
            row = []
            for s in range(segs):
                a = math.tau * s / segs
                x, y = half * k / rings * math.cos(a), half * k / rings * math.sin(a)
                row.append(len(verts))
                verts.append((x, y, height(x, y) if k < rings else 0.0))
            rows.append(row)
        faces = [(0, rows[0][s], rows[0][(s + 1) % segs]) for s in range(segs)]
        for lo, hi in zip(rows, rows[1:]):
            faces += [(lo[s], lo[(s + 1) % segs], hi[(s + 1) % segs], hi[s]) for s in range(segs)]
        ring = rows[-1]
    slots = [top] * len(faces)
    base = len(verts)
    verts += [(verts[v][0], verts[v][1], -depth) for v in ring]
    centre = len(verts)
    verts.append((0.0, 0.0, -depth))
    count = len(ring)
    faces += [(ring[k], base + k, base + (k + 1) % count, ring[(k + 1) % count]) for k in range(count)]
    faces += [(centre, base + (k + 1) % count, base + k) for k in range(count)]
    slots += [deep] * (2 * count)
    m.add(verts, faces, slots)


def _darker(hex_color, amount=0.55):
    return "#" + "".join(f"{round(int(hex_color[i:i + 2], 16) * amount):02X}" for i in (1, 3, 5))


# ------------------------------------------------------------------ sky

# preset: horizon, zenith, ground (below the horizon), sun colour, sun strength (W/m2), sun elevation
SKIES = {
    "day": ("#BFE3F2", "#3C8DDB", "#8A9A88", "#FFF4E0", 3.0, 50.0),
    "sunset": ("#FFB36B", "#4A5FA8", "#5A4640", "#FF9A52", 2.2, 6.0),
    "night": ("#1E2D4F", "#05070F", "#0B0F18", "#9DB4FF", 0.5, 35.0),
    "overcast": ("#C9CED3", "#8E99A6", "#6B6F70", "#E6E9EE", 1.4, 60.0),
}
SKY_PROP = "gs_sky"  # world[SKY_PROP] = the name of the sun that set it up
SKY_COLORS_PROP = "gs_sky_colors"  # world[...] = {horizon, zenith, ground, sun} as sRGB hex, for exporters


@kit("sky", label="Sky", default_name="Sun", root="sun",
     description=("Sets the scene's world to a stylized gradient sky (ground, horizon and zenith colours, a sun or "
                  "moon disc, stars at night) and creates the matching sun lamp, which is the object this returns "
                  "(regenerate it to change the sky). Presets: day, sunset, night, overcast; any colour can be set. "
                  "In the game you get the sun (direction, colour, strength, shadows) and the gradient (ground, "
                  "horizon and zenith colours) as the background, which also tints the fill light. Turn on the "
                  "world's mist (world.mist_settings: use_mist, start, depth) for distance fog in the game, in the "
                  "horizon colour (world['gs_fog_color'] = '#RRGGBB' picks another). The sun disc and the stars "
                  "show in Blender only. One sky per scene: a new one takes over the world."),
     params={
         "preset": choice("day", tuple(SKIES), "Starting colours and sun: day, sunset, night or overcast"),
         "horizon": color("auto", "Sky colour at the horizon as #RRGGBB (also the game's background colour)"),
         "zenith": color("auto", "Sky colour straight up as #RRGGBB"),
         "ground": color("auto", "Colour below the horizon as #RRGGBB"),
         "sun_color": color("auto", "Sunlight colour as #RRGGBB"),
         "sun_strength": number(-1.0, -1.0, 20.0, "Sunlight strength in W/m2 (3 is a bright day); -1 = the "
                                                  "preset's"),
         "sun_elevation": number(-1.0, -1.0, 90.0, "Sun height above the horizon in degrees; -1 = the preset's "
                                                   "(day 50, sunset 6, night 35, overcast 60)"),
         "sun_azimuth": number(210.0, 0.0, 360.0, "Where the sun is, degrees clockwise from +Y seen from above "
                                                  "(180 = in front, at -Y, lighting the fronts of kit assets)"),
         "sun_disc": boolean(True, "Draw the sun (or moon) disc in the sky"),
     })
def sky(obj, p, rng):
    horizon, zenith, ground, sun_hex, strength, elevation = SKIES[p["preset"]]
    horizon = _given(p["horizon"], horizon)
    zenith = _given(p["zenith"], zenith)
    ground = _given(p["ground"], ground)
    sun_hex = _given(p["sun_color"], sun_hex)
    strength = strength if p["sun_strength"] < 0 else p["sun_strength"]
    elevation = elevation if p["sun_elevation"] < 0 else p["sun_elevation"]
    el, az = math.radians(elevation), math.radians(p["sun_azimuth"])
    to_sun = Vector((math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)))

    light = obj.data
    light.energy = strength
    light.color = materials.hex_to_linear(sun_hex)
    light.angle = math.radians(2.0)
    if hasattr(light, "use_shadow"):
        light.use_shadow = True
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = to_sun.to_track_quat("Z", "Y").to_euler()  # a sun shines along its local -Z

    scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
    _world(scene, obj.name, p, horizon, zenith, ground, sun_hex, to_sun)


def _given(value, preset):
    return preset if value == "auto" else value


def _socket(sockets, name, kind="RGBA"):
    return next(s for s in sockets if s.name == name and s.type == kind)


def _world(scene, owner, p, horizon, zenith, ground, sun_hex, to_sun):
    world = scene.world if scene.world is not None and scene.world.get(SKY_PROP) else None
    if world is None:
        world = bpy.data.worlds.new("GS_Sky")
        scene.world = world
    world[SKY_PROP] = owner
    world[SKY_COLORS_PROP] = {"horizon": horizon, "zenith": zenith, "ground": ground, "sun": sun_hex}
    world.color = materials.hex_to_linear(horizon)  # what the exporter sends as the game's background
    if world.node_tree is None and hasattr(world, "use_nodes"):  # Blender < 5.0
        world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    nodes, links = tree.nodes, tree.links

    def node(kind, x, y, **inputs):
        n = nodes.new(kind)
        n.location = (x, y)
        for name, value in inputs.items():
            n.inputs[name].default_value = value
        return n

    coords = node("ShaderNodeTexCoord", -1200, 0)
    direction = node("ShaderNodeVectorMath", -1000, 0)
    direction.operation = "NORMALIZE"
    links.new(coords.outputs["Generated"], direction.inputs[0])
    xyz = node("ShaderNodeSeparateXYZ", -800, 120)
    links.new(direction.outputs["Vector"], xyz.inputs[0])
    height = node("ShaderNodeMapRange", -600, 120)
    height.inputs["From Min"].default_value = -1.0
    links.new(xyz.outputs["Z"], height.inputs["Value"])
    ramp = node("ShaderNodeValToRGB", -380, 120)
    links.new(height.outputs["Result"], ramp.inputs["Fac"])
    stops = ramp.color_ramp.elements
    lin = [(*materials.hex_to_linear(c), 1.0) for c in (ground, horizon, zenith)]
    mid = tuple(0.55 * a + 0.45 * b for a, b in zip(lin[1], lin[2]))
    stops[0].position, stops[0].color = 0.0, lin[0]
    stops[1].position, stops[1].color = 1.0, lin[2]
    for position, value in ((0.47, lin[0]), (0.5, lin[1]), (0.6, mid)):
        stop = stops.new(position)
        stop.color = value
    colour = ramp.outputs["Color"]

    if p["preset"] == "night":  # stars: a dot in one Voronoi cell of five, above the horizon
        stars = node("ShaderNodeTexVoronoi", -600, -260, Scale=60.0)
        links.new(direction.outputs["Vector"], stars.inputs["Vector"])
        pin = node("ShaderNodeMapRange", -380, -200)
        pin.inputs["From Min"].default_value, pin.inputs["From Max"].default_value = 0.14, 0.04
        links.new(stars.outputs["Distance"], pin.inputs["Value"])
        pick = node("ShaderNodeSeparateColor", -380, -360)
        links.new(stars.outputs["Color"], pick.inputs["Color"])
        lucky = node("ShaderNodeMath", -200, -360)
        lucky.operation = "GREATER_THAN"
        lucky.inputs[1].default_value = 0.8
        links.new(pick.outputs[0], lucky.inputs[0])
        up = node("ShaderNodeMath", -200, -500)
        up.operation = "GREATER_THAN"
        up.inputs[1].default_value = 0.05
        links.new(xyz.outputs["Z"], up.inputs[0])
        star = pin.outputs["Result"]
        for mask in (lucky, up):
            both = node("ShaderNodeMath", 0, -400)
            both.operation = "MULTIPLY"
            links.new(star, both.inputs[0])
            links.new(mask.outputs["Value"], both.inputs[1])
            star = both.outputs["Value"]
        add = node("ShaderNodeMix", 200, -100)
        add.data_type, add.blend_type = "RGBA", "ADD"
        links.new(star, add.inputs["Factor"])
        links.new(colour, _socket(add.inputs, "A"))
        _socket(add.inputs, "B").default_value = (0.9, 0.92, 1.0, 1.0)
        colour = _socket(add.outputs, "Result")

    if p["sun_disc"]:
        facing = node("ShaderNodeVectorMath", -600, -600)
        facing.operation = "DOT_PRODUCT"
        facing.inputs[1].default_value = tuple(to_sun)
        links.new(direction.outputs["Vector"], facing.inputs[0])
        sun = (*materials.hex_to_linear(sun_hex), 1.0)
        size = 3.5 if p["preset"] != "night" else 2.5
        for radius, strength, bright in ((size * 4.0, 0.35, 1.0), (size, 1.0, 4.0)):
            rim = node("ShaderNodeMapRange", -380, -600)
            rim.interpolation_type = "SMOOTHSTEP"
            rim.inputs["From Min"].default_value = math.cos(math.radians(radius * 1.15))
            rim.inputs["From Max"].default_value = math.cos(math.radians(radius))
            rim.inputs["To Max"].default_value = strength
            links.new(facing.outputs["Value"], rim.inputs["Value"])
            mix = node("ShaderNodeMix", 200, -300)
            mix.data_type = "RGBA"
            links.new(rim.outputs["Result"], mix.inputs["Factor"])
            links.new(colour, _socket(mix.inputs, "A"))
            _socket(mix.inputs, "B").default_value = tuple(c * bright for c in sun[:3]) + (1.0,)
            colour = _socket(mix.outputs, "Result")

    background = node("ShaderNodeBackground", 400, 0, Strength=1.0)
    links.new(colour, background.inputs["Color"])
    output = node("ShaderNodeOutputWorld", 600, 0)
    links.new(background.outputs["Background"], output.inputs["Surface"])
