# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive props: door, key, switch (lever), pickup.

Doors and switches are assemblies: a fixed root (frame, base) and a moving child (<name>_Leaf,
<name>_Handle) whose origin is on its hinge, with door_open/door_close style clips on it.
"""

import math

from mathutils import Matrix, Vector

from .base import Part, boolean, choice, color, kit, material, number
from .geometry import bounds

TOWARD_MINUS_Y = Matrix.Rotation(math.pi / 2, 4, "X")   # a lathe's +Z turned to -Y
TOWARD_PLUS_Y = Matrix.Rotation(-math.pi / 2, 4, "X")
ALONG_X = Matrix.Rotation(math.pi / 2, 4, "Y")
KEY_COLORS = ("#E8382F", "#2F7DE8", "#F2B705", "#2FBF71", "#8E5CD6", "#F28C28")
GLASS = "#1F2A36"


def _pick(value, palette, seed):
    return value if value != "auto" else palette[seed % len(palette)]


def _transform(faces, matrix):
    for v in {v for f in faces for v in f.verts}:
        v.co = matrix @ v.co


def _rounded_rect(w, h, r, steps=3):
    """(x, z) outline of a w x h rectangle centred on the origin with rounded corners."""
    r = min(r, w / 2 - 1e-4, h / 2 - 1e-4)
    pts = []
    for cx, cz, a0 in ((w / 2 - r, h / 2 - r, 0.0), (-w / 2 + r, h / 2 - r, 90.0),
                       (-w / 2 + r, -h / 2 + r, 180.0), (w / 2 - r, -h / 2 + r, 270.0)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90.0 * i / steps)
            pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    return pts


def _centre(m):
    lo, hi = bounds(list(m.bm.faces))
    c = (lo + hi) / 2
    for v in m.bm.verts:
        v.co -= c


# ------------------------------------------------------------------ door

DOOR_STYLES = {  # leaf thickness, leaf material, frame material, light colour
    "plain": (0.05, "wood", "wood_dark"),
    "planked": (0.06, "wood", "wood_dark"),
    "scifi": (0.08, "metal", "concrete"),
}
GAP, BOTTOM_GAP = 0.006, 0.012


def door_layout(p) -> dict:
    """The opening, the leaf's size and where its hinge is (the leaf's origin, in the frame's axes)."""
    W, H, D = p["width"], p["height"], p["depth"]
    hinge = -1.0 if p["hinge"] == "left" else 1.0         # the hinge's side along X
    swing = 1.0 if p["open_angle"] >= 0 else -1.0          # it opens toward +Y (or -Y)
    t = min(DOOR_STYLES[p["style"]][0], 0.8 * D)
    return {"W": W, "H": H, "D": D, "t": t, "hinge": hinge, "swing": swing,
            "leaf_w": W - 2 * GAP, "leaf_h": H - GAP - BOTTOM_GAP,
            "pivot": (hinge * (W / 2 - GAP), swing * t / 2, BOTTOM_GAP),
            "chamfer": 0.12 * min(W, H) if p["style"] == "scifi" else 0.0,
            "angle": -hinge * p["open_angle"]}


def _door_parts(p):
    d = door_layout(p)
    clips = ()
    if p["clips"]:
        cp = {"angle": d["angle"], "seconds": p["open_seconds"]}
        clips = (("door_open", cp, "door_open"), ("door_close", cp, "door_close"))
    return [Part("Leaf", d["pivot"], build=_leaf, clips=clips)]


@kit("door", label="Door",
     description=("A door in its frame: plain (raised panels and a knob), planked (boards, iron straps, a ring "
                  "handle) or scifi (chamfered metal with light strips). The frame is the root, sized for a wall "
                  "`depth` thick, its opening centred on the origin. The leaf is a child <name>_Leaf with its origin "
                  "on the hinge line (bottom of the hinge edge, on the face it opens toward), so it swings about its "
                  "local Z. It has door_open and door_close clips: put an Animator (playOnStart off) and a Collider "
                  "on the leaf and play them with ctx.animation.play(leaf, 'door_open', {loop: false}). Give the "
                  "frame a mesh Collider."),
     params={
         "style": choice("plain", tuple(DOOR_STYLES), "plain, planked or scifi"),
         "width": number(1.0, 0.5, 4.0, "Opening width, metres"),
         "height": number(2.1, 1.0, 5.0, "Opening height, metres"),
         "depth": number(0.2, 0.06, 1.0, "Frame depth along Y (the wall's thickness), metres"),
         "frame": number(0.1, 0.03, 0.5, "Frame width around the opening, metres"),
         "hinge": choice("left", ("left", "right"), "Hinge side seen from the front (-Y): left = -X"),
         "open_angle": number(100.0, -180.0, 180.0, "How far door_open swings it, degrees; positive swings it away "
                                                    "from the front (toward +Y)"),
         "open_seconds": number(1.0, 0.1, 10.0, "Duration of door_open and door_close"),
         "clips": boolean(True, "Add the door_open and door_close clips to the leaf"),
         "color": color("auto", "Paint for a plain leaf, or the light colour of a scifi door, as #RRGGBB; auto = "
                                "the style's usual look"),
         "material": material("auto", "Leaf material; auto = wood (plain, planked) or metal (scifi)", auto=True),
         "frame_material": material("auto", "Frame material; auto = wood_dark or concrete (scifi)", auto=True),
     }, max_tris=1500, parts=_door_parts)
def door(m, p, rng):
    d = door_layout(p)
    W, H, D, f, c = d["W"], d["H"], d["D"], p["frame"], d["chamfer"]
    style = p["style"]
    frame = m.slot(p["frame_material"] if p["frame_material"] != "auto" else DOOR_STYLES[style][2])
    if style == "planked":
        o = 0.6 * f
        profile = [(-W / 2 - f, 0.0), (-W / 2, 0.0), (-W / 2, H), (W / 2, H), (W / 2, 0.0), (W / 2 + f, 0.0),
                   (W / 2 + f, H), (W / 2 + f + o, H), (W / 2 + f + o, H + f), (-W / 2 - f - o, H + f),
                   (-W / 2 - f - o, H), (-W / 2 - f, H)]
    else:
        inner = [(-W / 2, H - c), (-W / 2 + c, H), (W / 2 - c, H), (W / 2, H - c)] if c else [(-W / 2, H), (W / 2, H)]
        profile = [(-W / 2 - f, 0.0), (-W / 2, 0.0), *inner, (W / 2, 0.0), (W / 2 + f, 0.0), (W / 2 + f, H + f),
                   (-W / 2 - f, H + f)]
    m.prism(profile, "Y", -D / 2, D / 2, frame)
    if style == "scifi":
        light = m.slot("emissive", color=p["color"] if p["color"] != "auto" else "#3FE0FF", emission_strength=1.5)
        e = min(0.3 * f, 0.03)
        for y0, y1 in ((-D / 2 - 0.004, -D / 2 + 0.01), (D / 2 - 0.01, D / 2 + 0.004)):
            for side in (-1, 1):
                x0, x1 = sorted((side * (W / 2 + 0.3 * f), side * (W / 2 + 0.3 * f + e)))
                m.box((x0, y0, 0.1 * H), (x1, y1, 0.85 * H), light)
            m.box((-0.3 * W, y0, H + 0.3 * f), (0.3 * W, y1, H + 0.3 * f + e), light)


def _leaf(m, p, rng):
    """The leaf, built with the hinge edge on x = 0 (extending to +X), centred on y = 0, then moved so
    the origin is on the hinge line and mirrored for a right-hand hinge."""
    d = door_layout(p)
    w, h, t, style = d["leaf_w"], d["leaf_h"], d["t"], p["style"]
    name = p["material"] if p["material"] != "auto" else DOOR_STYLES[style][1]
    paint = p["color"] if style == "plain" and p["color"] != "auto" else None
    leaf = m.slot(name, color=paint) if paint else m.slot(name)
    knob_z = min(1.0, 0.48 * h)
    if style == "plain":
        m.box((0.0, -t / 2, 0.0), (w, t / 2, h), leaf)
        brass = m.slot("gold")
        for face in (-1, 1):
            y0, y1 = sorted((face * (t / 2 - 0.002), face * (t / 2 + 0.008)))
            for z0, z1 in ((0.1 * h, 0.42 * h), (0.5 * h, 0.9 * h)):
                m.box((0.16 * w, y0, z0), (0.84 * w, y1, z1), leaf)
            turn = TOWARD_MINUS_Y if face < 0 else TOWARD_PLUS_Y
            m.lathe([(0.032, 0.0), (0.032, 0.006), (0.011, 0.012), (0.011, 0.035), (0.026, 0.045), (0.028, 0.058),
                     (0.0, 0.066)], 10, brass, smooth=True,
                    matrix=Matrix.Translation((w - 0.075, face * t / 2, knob_z)) @ turn)
    elif style == "planked":
        iron, dark = m.slot("iron"), m.slot("wood_dark")
        m.box((0.0, -t / 2 + 0.012, 0.0), (w, t / 2, h), dark)
        n = max(3, round(w / 0.15))
        pitch = w / n
        for i in range(n):
            m.box((i * pitch + 0.003, -t / 2, 0.0), ((i + 1) * pitch - 0.003, -t / 2 + 0.014, h - 0.004 * (i % 2)),
                  leaf)
        for z in (0.18 * h, 0.82 * h):
            m.box((0.0, -t / 2 - 0.006, z - 0.03), (0.72 * w, -t / 2 + 0.006, z + 0.03), iron)
            m.box((0.0, t / 2 - 0.004, z - 0.06), (w, t / 2 + 0.03, z + 0.06), leaf)  # battens on the back
        length = math.hypot(0.8 * w, 0.64 * h)
        brace = (Matrix.Translation((0.5 * w, t / 2 + 0.013, 0.5 * h))
                 @ Matrix.Rotation(-math.atan2(0.64 * h, 0.8 * w), 4, "Y"))
        m.box((-length / 2, -0.012, -0.05), (length / 2, 0.012, 0.05), leaf, matrix=brace)
        at = Matrix.Translation((w - 0.12, -t / 2 - 0.012, knob_z))
        m.box((-0.035, 0.0, -0.035), (0.035, 0.012, 0.035), iron, matrix=at)
        m.torus(0.045, 0.008, 12, 4, iron, matrix=at @ Matrix.Translation((0.0, -0.004, -0.045)) @ TOWARD_MINUS_Y)
    else:
        c = d["chamfer"]
        light = m.slot("emissive", color=p["color"] if p["color"] != "auto" else "#3FE0FF", emission_strength=1.5)
        dark, glass = m.slot("rubber"), m.slot("plastic", color=GLASS, roughness=0.1)
        m.prism([(0.0, 0.0), (w, 0.0), (w, h - c), (w - c, h), (c, h), (0.0, h - c)], "Y", -t / 2, t / 2, leaf)
        for face in (-1, 1):
            y0, y1 = sorted((face * (t / 2 - 0.002), face * (t / 2 + 0.005)))
            for x in (0.09 * w, 0.88 * w):
                m.box((x, y0, 0.1 * h), (x + 0.03 * w, y1, 0.82 * h - c), light)
            m.box((0.17 * w, y0, 0.45 * h), (0.83 * w, y1, 0.53 * h), dark)
            y2 = face * (t / 2 + 0.008)
            m.box((0.45 * w, min(y0, y2), 0.465 * h), (0.55 * w, max(y1, y2), 0.515 * h), light)
            m.box((0.3 * w, y0, 0.66 * h), (0.7 * w, y1, 0.74 * h), glass)
    flip = Matrix.Diagonal((-d["hinge"], 1.0, 1.0, 1.0))
    _transform(list(m.bm.faces), Matrix.Translation((0.0, -d["swing"] * t / 2, 0.0)) @ flip)


# ------------------------------------------------------------------ key


@kit("key", label="Key",
     description=("A key to pick up: a classic key with a jewelled ring (bow) and a toothed bit, or a key card. "
                  "It stands upright facing -Y with its long side along X, centred on its origin, so it spins "
                  "around Z; colour-code keys to their doors with `color`."),
     params={
         "style": choice("classic", ("classic", "card"), "classic: a metal key; card: a sci-fi key card"),
         "size": number(0.3, 0.05, 2.0, "Length along X in metres"),
         "color": color("auto", "The gem's (classic) or the card's colour as #RRGGBB, or auto from the seed"),
         "material": material("auto", "Metal of a classic key; auto = gold", auto=True),
     }, pivot="center", max_tris=800)
def key(m, p, rng):
    L, tint = p["size"], _pick(p["color"], KEY_COLORS, p["seed"])
    if p["style"] == "card":
        body, white = m.slot("plastic", color=tint, roughness=0.35), m.slot("plastic", color="#F4F4F4")
        gold, dark = m.slot("gold"), m.slot("rubber")
        glow = m.slot("emissive", color=tint, emission_strength=1.5)
        w, h, t = L, 0.63 * L, 0.035 * L
        m.prism(_rounded_rect(w, h, 0.08 * L), "Y", -t / 2, t / 2, body)
        f = -t / 2
        m.box((-0.45 * w, f - 0.004 * L, 0.2 * h), (0.45 * w, f + 0.002 * L, 0.38 * h), white)
        m.box((-0.38 * w, f - 0.004 * L, -0.12 * h), (-0.18 * w, f + 0.002 * L, 0.08 * h), gold)
        m.box((0.05 * w, f - 0.004 * L, -0.36 * h), (0.42 * w, f + 0.002 * L, -0.28 * h), glow)
        m.box((-0.5 * w + 0.02 * L, t / 2 - 0.002 * L, 0.12 * h), (0.5 * w - 0.02 * L, t / 2 + 0.004 * L, 0.3 * h),
              dark)
    else:
        metal = m.slot(p["material"] if p["material"] != "auto" else "gold")
        gem = m.slot("gem", color=tint)
        rb, tube = 0.15 * L, 0.035 * L
        cx = -0.5 * L + rb + tube
        ring = Matrix.Translation((cx, 0.0, 0.0)) @ TOWARD_MINUS_Y
        m.torus(rb, tube, 16, 4, metal, matrix=ring)
        for angle in (0.0, math.pi / 2):
            bar = Matrix.Translation((cx, 0.0, 0.0)) @ Matrix.Rotation(angle, 4, "Y")
            m.box((-rb, -0.4 * tube, -0.4 * tube), (rb, 0.4 * tube, 0.4 * tube), metal, matrix=bar)
        g = 0.07 * L
        m.icosphere(1, gem, deform=lambda co: Vector((co.x * g + cx, co.y * 0.55 * g, co.z * g)))
        shaft = 0.028 * L
        x0 = cx + rb
        m.lathe([(shaft, 0.0), (shaft, 0.04 * L), (1.5 * shaft, 0.04 * L), (1.5 * shaft, 0.075 * L),
                 (shaft, 0.075 * L), (shaft, 0.5 * L - x0)], 8, metal, smooth=False,
                matrix=Matrix.Translation((x0, 0.0, 0.0)) @ ALONG_X)
        # the bit: teeth hanging below the end of the shaft, a pattern from the seed
        teeth = rng.sample((0.5, 0.65, 0.8, 1.0), 3)  # distinct: no three corners in a line
        x, step, depth = 0.5 * L - 0.012 * L, 0.06 * L, 0.2 * L
        bit = [(x, 0.0), (x, -depth * teeth[0])]
        for i, tooth in enumerate(teeth):
            bit += [(x - step * (i + 0.5), -depth * tooth), (x - step * (i + 1), -depth * tooth * 0.75)]
        bit += [(x - 3 * step, -0.4 * depth), (x - 3 * step - 0.02 * L, 0.0)]
        m.prism(list(reversed(bit)), "Y", -0.012 * L, 0.012 * L, metal)
    _centre(m)


# ------------------------------------------------------------------ switch


def switch_layout(p) -> dict:
    s = p["size"]
    if p["style"] == "lever":
        return {"pivot": (0.0, 0.0, 0.19 * s), "angle": p["throw"]}
    return {"pivot": (0.0, -0.05 * s, 0.18 * s), "angle": -p["throw"]}  # the handle reaches 0.16 s


def _switch_parts(p):
    d = switch_layout(p)
    clips = ()
    if p["clips"]:
        cp = {"angle": d["angle"], "seconds": p["seconds"], "axis": "X"}
        clips = (("door_open", cp, "switch_on"), ("door_close", cp, "switch_off"))
    return [Part("Handle", d["pivot"], build=_handle, clips=clips)]


@kit("switch", label="Switch",
     description=("A lever switch: a floor lever on a stone-and-iron base, or a wall switch on a plate (its back "
                  "at y = 0, to sit on a wall facing -Y). The handle is a child <name>_Handle with its origin on the "
                  "pivot axle; it rests in the off position and turns about its local X axis. Clips on the handle: "
                  "switch_on (off to on) and switch_off (back); play them once with ctx.animation.play(handle, "
                  "'switch_on', {loop: false}). Add a sensor Collider on the root to detect the player."),
     params={
         "style": choice("lever", ("lever", "wall"), "lever: a floor lever; wall: a wall-mounted switch"),
         "size": number(1.0, 0.25, 4.0, "Scale: 1 = a 0.8 m floor lever or a 0.36 m wall plate"),
         "throw": number(70.0, 10.0, 160.0, "Degrees the handle turns between off and on"),
         "seconds": number(0.35, 0.1, 5.0, "Duration of switch_on and switch_off"),
         "clips": boolean(True, "Add the switch_on and switch_off clips to the handle"),
         "color": color("auto", "Knob colour as #RRGGBB, or auto (red)"),
         "material": material("iron", "Base and plate material"),
     }, max_tris=900, parts=_switch_parts)
def switch(m, p, rng):
    s = p["size"]
    base, metal = m.slot(p["material"]), m.slot("metal")
    if p["style"] == "lever":
        stone = m.slot("stone_dark")
        m.prism(_rounded_rect(0.4 * s, 0.1 * s, 0.03 * s, 2), "Y", -0.15 * s, 0.15 * s, stone,
                matrix=Matrix.Translation((0.0, 0.0, 0.05 * s)))
        m.box((-0.15 * s, -0.11 * s, 0.1 * s), (0.15 * s, 0.11 * s, 0.13 * s), base)
        for side in (-1, 1):
            x0, x1 = sorted((side * 0.035 * s, side * 0.075 * s))
            m.prism([(-0.07 * s, 0.13 * s), (0.07 * s, 0.13 * s), (0.04 * s, 0.24 * s), (-0.04 * s, 0.24 * s)],
                    "X", x0, x1, base)
        m.lathe([(0.012 * s, -0.08 * s), (0.012 * s, 0.08 * s)], 8, metal, smooth=False,
                matrix=Matrix.Translation((0.0, 0.0, 0.19 * s)) @ ALONG_X)
    else:
        red = m.slot("emissive", color="#FF3B30", emission_strength=1.5)
        green = m.slot("emissive", color="#3BFF6B", emission_strength=1.5)
        m.prism(_rounded_rect(0.2 * s, 0.36 * s, 0.025 * s, 2), "Y", -0.03 * s, 0.0, base,
                matrix=Matrix.Translation((0.0, 0.0, 0.18 * s)))
        m.box((-0.045 * s, -0.06 * s, 0.14 * s), (0.045 * s, -0.025 * s, 0.22 * s), metal)
        for z, slot in ((0.04 * s, red), (0.32 * s, green)):
            m.box((-0.02 * s, -0.036 * s, z - 0.012 * s), (0.02 * s, -0.025 * s, z + 0.012 * s), slot)


def _handle(m, p, rng):
    s = p["size"]
    knob = m.slot("plastic", color=p["color"] if p["color"] != "auto" else "#E8382F", roughness=0.35)
    metal, dark = m.slot("metal"), m.slot("rubber")
    if p["style"] == "lever":
        tilt = Matrix.Rotation(math.radians(-p["throw"] / 2), 4, "X")  # off: leaning back, toward +Y
        m.lathe([(0.032 * s, -0.03 * s), (0.032 * s, 0.03 * s)], 10, dark, smooth=False, matrix=ALONG_X)
        m.lathe([(0.016 * s, 0.0), (0.016 * s, 0.5 * s), (0.022 * s, 0.5 * s), (0.022 * s, 0.53 * s)], 8, metal,
                smooth=False, matrix=tilt)
        m.icosphere(2, knob, smooth=True,
                    matrix=tilt @ Matrix.Translation((0.0, 0.0, 0.57 * s)) @ Matrix.Scale(0.05 * s, 4))
    else:
        down = Matrix.Rotation(math.radians(p["throw"] / 2), 4, "X") @ TOWARD_MINUS_Y  # off: pointing down
        m.lathe([(0.02 * s, -0.02 * s), (0.02 * s, 0.02 * s)], 10, dark, smooth=False, matrix=ALONG_X)
        m.lathe([(0.009 * s, 0.0), (0.009 * s, 0.12 * s)], 8, metal, smooth=False, matrix=down)
        m.icosphere(2, knob, smooth=True,
                    matrix=down @ Matrix.Translation((0.0, 0.0, 0.135 * s)) @ Matrix.Scale(0.024 * s, 4))


# ------------------------------------------------------------------ pickup

PICKUPS = {"health": "#E23B3B", "ammo": "#5B6B3A", "energy": "#7CFF6B", "shield": "#2F7DE8"}


@kit("pickup", label="Pickup",
     description=("A power-up to pick up: a health kit (white case, red cross), an ammo box with bullets, an energy "
                  "cell with a glowing band, or a shield. Centred on its origin and facing -Y, so it spins around Z "
                  "like a collectible. Keys and key cards are the key kit."),
     params={
         "style": choice("health", tuple(PICKUPS), "health, ammo, energy or shield"),
         "size": number(0.5, 0.1, 3.0, "Size in metres: the width of a health kit, ammo box or shield, the height "
                                       "of an energy cell"),
         "color": color("auto", "Main colour as #RRGGBB (the cross, the ammo box, the glow, the shield), or auto "
                                "for the style's usual colour"),
     }, pivot="center", max_tris=1000)
def pickup(m, p, rng):
    w, style = p["size"], p["style"]
    tint = p["color"] if p["color"] != "auto" else PICKUPS[style]
    dark, metal = m.slot("rubber"), m.slot("metal")
    if style == "health":
        case, cross = m.slot("plastic", color="#F4F4F4"), m.slot("plastic", color=tint, roughness=0.35)
        h, d = 0.72 * w, 0.36 * w
        m.prism(_rounded_rect(w, h, 0.08 * w, 2), "Y", -d / 2, d / 2, case)
        a, b = 0.24 * h, 0.08 * h
        for face in (-1, 1):
            y0, y1 = sorted((face * (d / 2 - 0.002 * w), face * (d / 2 + 0.02 * w)))
            m.box((-a, y0, -b), (a, y1, b), cross)
            m.box((-b, y0, -a), (b, y1, a), cross)
        m.prism([(-0.2 * w, h / 2 - 0.01 * w), (-0.13 * w, h / 2 - 0.01 * w), (-0.13 * w, h / 2 + 0.08 * w),
                 (0.13 * w, h / 2 + 0.08 * w), (0.13 * w, h / 2 - 0.01 * w), (0.2 * w, h / 2 - 0.01 * w),
                 (0.2 * w, h / 2 + 0.14 * w), (-0.2 * w, h / 2 + 0.14 * w)], "Y", -0.05 * w, 0.05 * w, dark)
    elif style == "ammo":
        box, band = m.slot("plastic", color=tint, roughness=0.6), m.slot("plastic", color="#E8B53A")
        brass, tip = m.slot("gold"), m.slot("copper")
        h, d = 0.45 * w, 0.42 * w
        m.box((-w / 2, -d / 2, -h / 2), (w / 2, d / 2, h / 2), box)
        m.box((-w / 2 - 0.01 * w, -d / 2 - 0.01 * w, h / 2 - 0.1 * h), (w / 2 + 0.01 * w, d / 2 + 0.01 * w,
                                                                         h / 2 - 0.02 * h), dark)
        m.box((-0.3 * w, -d / 2 - 0.008 * w, -0.2 * h), (0.3 * w, -d / 2 + 0.002 * w, 0.08 * h), band)
        m.box((-0.06 * w, -d / 2 - 0.02 * w, 0.18 * h), (0.06 * w, -d / 2 + 0.002 * w, 0.32 * h), metal)
        count = 3 + p["seed"] % 3
        r = 0.045 * w
        for i in range(count):
            x = (i - (count - 1) / 2) * 2.5 * r
            at = Matrix.Translation((x, 0.0, h / 2 - 0.01 * w))
            m.lathe([(r, 0.0), (r, 0.16 * w), (0.0, 0.16 * w + 0.001)], 8, brass, smooth=True, matrix=at,
                    band_slots=[brass, brass])
            m.lathe([(0.98 * r, 0.16 * w), (0.8 * r, 0.21 * w), (0.0, 0.25 * w)], 8, tip, smooth=True, matrix=at)
    elif style == "energy":
        glow = m.slot("emissive", color=tint, emission_strength=1.6)
        r, h = 0.2 * w, w
        profile = [(0.0, 0.0), (0.9 * r, 0.0), (r, 0.03 * h), (r, 0.16 * h), (0.92 * r, 0.18 * h), (0.92 * r, 0.82 * h),
                   (r, 0.84 * h), (r, 0.97 * h), (0.9 * r, h), (0.35 * r, h), (0.35 * r, 1.08 * h), (0.0, 1.08 * h)]
        slots = [metal, metal, metal, metal, glow, metal, metal, metal, metal, metal, metal]
        m.lathe(profile, 12, metal, band_slots=slots, band_smooth=[False, True, True, False, True, False, True,
                                                                   True, False, False, False])
        for z in (0.32 * h, 0.5 * h, 0.68 * h):
            m.lathe([(0.95 * r, z - 0.025 * h), (r * 1.04, z - 0.02 * h), (r * 1.04, z + 0.02 * h),
                     (0.95 * r, z + 0.025 * h)], 12, dark, smooth=False)
    else:
        face, rim, white = m.slot("plastic", color=tint, roughness=0.4), m.slot("metal"), m.slot("plastic",
                                                                                                color="#F4F4F4")
        h, t = 1.15 * w, 0.12 * w
        outline = [(-0.5 * w, 0.5 * h), (0.0, 0.46 * h), (0.5 * w, 0.5 * h), (0.5 * w, 0.05 * h),
                   (0.38 * w, -0.25 * h), (0.0, -0.5 * h), (-0.38 * w, -0.25 * h), (-0.5 * w, 0.05 * h)]
        m.prism(outline, "Y", -t / 2, t / 2, rim)
        m.prism([(0.88 * x, 0.88 * z - 0.01 * h) for x, z in outline], "Y", -t / 2 - 0.02 * w, t / 2 + 0.02 * w, face)
        for y in (-1, 1):
            y0, y1 = sorted((y * (t / 2 + 0.015 * w), y * (t / 2 + 0.035 * w)))
            m.box((-0.06 * w, y0, -0.36 * h), (0.06 * w, y1, 0.36 * h), white)
            m.box((-0.32 * w, y0, 0.08 * h), (0.32 * w, y1, 0.2 * h), white)
    _centre(m)
