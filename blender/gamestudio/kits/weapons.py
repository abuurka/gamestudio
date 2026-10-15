# SPDX-License-Identifier: GPL-3.0-or-later
"""Weapon kits: hand-held weapons (blaster, sword, bow) and the projectiles they fire.

Weapons have their origin at the grip, the business end toward -Y (blaster, bow) or +Z (sword).
Blasters and bows get a child empty <name>_Muzzle where projectiles spawn, aimed along -Y.
"""

import math

from mathutils import Matrix, Vector

from .base import Part, choice, color, kit, material, number
from .geometry import bounds

PAINT = ("#E8382F", "#2F7DE8", "#F2B705", "#2FBF71", "#F28C28", "#8E5CD6", "#22B8CF", "#5E6B7D")
GLOW = ("#1EB4FF", "#FF2E2E", "#38F23C", "#FF9A1F", "#A64DFF", "#FF3DC8")
TOWARD_MINUS_Y = Matrix.Rotation(math.pi / 2, 4, "X")  # a lathe's +Z axis turned to point along -Y


def _pick(value, palette, seed):
    return value if value != "auto" else palette[seed % len(palette)]


# ------------------------------------------------------------------ weapon


def _blaster_layout(p):
    s, k = p["size"], p["length"]
    barrel = 0.15 * k * s
    axis_z = 0.075 * s
    front = -0.07 * s
    grip = Vector((0.0, 0.043 * s, -0.03 * s))  # where the hand holds it: becomes the origin
    return s, barrel, axis_z, front, grip


def _bow_layout(p):
    s, k = p["size"], p["length"]
    half = 0.6 * k * s
    curve = 0.36 * half
    return s, half, curve


def _muzzle(p):
    if p["style"] == "blaster":
        s, barrel, axis_z, front, grip = _blaster_layout(p)
        return Vector((0.0, front - barrel, axis_z)) - grip
    s, half, curve = _bow_layout(p)
    return Vector((0.0, -0.03 * s, 0.06 * s))  # the arrow rest, just above the hand


def _weapon_parts(p):
    if p["style"] == "sword":
        return []
    return [Part("Muzzle", tuple(_muzzle(p)), display="PLAIN_AXES", size=0.06 * p["size"])]


@kit("weapon", label="Weapon",
     description=("A hand-held weapon: a sci-fi blaster pistol, a sword or a bow. The origin is at the grip, where "
                  "a hand holds it: parent it to a hand bone or the camera. Blasters and bows fire along -Y and get "
                  "a child empty <name>_Muzzle at the barrel tip (blaster) or the arrow rest (bow): spawn projectiles "
                  "at its world position, moving along its -Y. Swords point up (+Z) with the edges to the sides."),
     params={
         "style": choice("blaster", ("blaster", "sword", "bow"), "blaster: a pistol with glowing coils; sword: a "
                                                                  "longsword; bow: a recurve bow with its string"),
         "size": number(1.0, 0.25, 4.0, "Scale: 1 suits a 1.8 m character (blaster 0.3 m long, sword 1 m, bow 1.2 m)"),
         "length": number(1.0, 0.5, 2.0, "Barrel, blade or bow length relative to the usual"),
         "color": color("auto", "Accent colour as #RRGGBB (blaster body, sword and bow grip), or auto from the seed"),
         "material": material("auto", "Main material; auto = plastic in the accent colour (blaster), metal (sword "
                                      "blade), wood (bow)", auto=True),
     }, pivot="grip", max_tris=1200, parts=_weapon_parts)
def weapon(m, p, rng):
    accent = _pick(p["color"], PAINT, p["seed"])
    {"blaster": _blaster, "sword": _sword, "bow": _bow}[p["style"]](m, p, accent)


def _blaster(m, p, accent):
    s, barrel, axis_z, front, grip = _blaster_layout(p)
    body = m.slot(p["material"]) if p["material"] != "auto" else m.slot("plastic", color=accent, roughness=0.35)
    dark, metal = m.slot("rubber"), m.slot("metal")
    glow = m.slot("emissive", color=GLOW[p["seed"] % len(GLOW)], emission_strength=1.6)
    hw = 0.03 * s
    m.prism([(front - 0.015 * s, 0.035 * s), (0.1 * s, 0.035 * s), (0.115 * s, 0.06 * s), (0.1 * s, 0.112 * s),
             (0.02 * s, 0.122 * s), (-0.035 * s, 0.112 * s), (front - 0.015 * s, 0.09 * s)], "X", -hw, hw, body)
    m.prism([(-0.004 * s, 0.04 * s), (0.065 * s, 0.04 * s), (0.092 * s, -0.1 * s), (0.026 * s, -0.1 * s)],
            "X", -0.022 * s, 0.022 * s, dark)
    m.prism([(-0.048 * s, 0.037 * s), (-0.038 * s, 0.037 * s), (-0.03 * s, 0.004 * s), (0.002 * s, 0.0),
             (0.002 * s, -0.01 * s), (-0.034 * s, -0.006 * s)], "X", -0.006 * s, 0.006 * s, dark)
    m.box((-0.004 * s, -0.018 * s, 0.005 * s), (0.004 * s, -0.009 * s, 0.037 * s), metal)
    # the barrel with two glowing coils and a muzzle collar
    r, ring, b = 0.019 * s, 0.024 * s, barrel
    profile = [(r, -0.012 * s), (r, 0.25 * b), (ring, 0.25 * b), (ring, 0.34 * b), (r, 0.34 * b), (r, 0.47 * b),
               (ring, 0.47 * b), (ring, 0.56 * b), (r, 0.56 * b), (r, 0.84 * b), (0.024 * s, 0.86 * b),
               (0.024 * s, b), (0.011 * s, b), (0.011 * s, b - 0.008 * s), (0.0, b - 0.008 * s)]
    slots = [metal, metal, glow, metal, metal, metal, glow, metal, metal, metal, metal, metal, dark, dark]
    m.lathe(profile, 12, metal, band_slots=slots, band_smooth=[False] * len(slots),
            matrix=Matrix.Translation((0.0, front, axis_z)) @ TOWARD_MINUS_Y)
    # an energy cell on top, glowing strips on the sides, a sight
    m.lathe([(0.0, 0.0), (0.011 * s, 0.005 * s), (0.012 * s, 0.015 * s), (0.012 * s, 0.05 * s),
             (0.011 * s, 0.06 * s), (0.0, 0.065 * s)], 8, glow, smooth=False,
            matrix=Matrix.Translation((0.0, 0.09 * s, 0.124 * s)) @ TOWARD_MINUS_Y)
    for side in (-1, 1):
        x0, x1 = sorted((side * hw * 0.98, side * (hw + 0.003 * s)))
        m.box((x0, -0.05 * s, 0.072 * s), (x1, 0.07 * s, 0.084 * s), glow)
    m.box((-0.006 * s, -0.05 * s, 0.118 * s), (0.006 * s, -0.03 * s, 0.14 * s), dark)
    for v in m.bm.verts:
        v.co -= grip


def _sword(m, p, accent):
    s, k = p["size"], p["length"]
    blade = m.slot(p["material"]) if p["material"] != "auto" else m.slot("metal")
    wrap, gold = m.slot("rubber", color=accent), m.slot("gold")
    gem = m.slot("gem", color=accent)
    m.lathe([(0.0, -0.075 * s), (0.016 * s, -0.072 * s), (0.019 * s, 0.0), (0.016 * s, 0.072 * s),
             (0.0, 0.075 * s)], 8, wrap, smooth=False)
    m.lathe([(0.0, -0.13 * s), (0.02 * s, -0.122 * s), (0.032 * s, -0.1 * s), (0.02 * s, -0.078 * s),
             (0.0, -0.07 * s)], 8, gold, smooth=False)
    m.prism([(-0.13 * s, 0.09 * s), (-0.1 * s, 0.07 * s), (0.1 * s, 0.07 * s), (0.13 * s, 0.09 * s),
             (0.135 * s, 0.105 * s), (0.1 * s, 0.11 * s), (-0.1 * s, 0.11 * s), (-0.135 * s, 0.105 * s)],
            "Y", -0.02 * s, 0.02 * s, gold)
    for side in (-1, 1):
        at = Vector((0.0, side * 0.02 * s, 0.09 * s))
        m.icosphere(1, gem, deform=lambda co, at=at: Vector((co.x * 0.016 * s, co.y * 0.008 * s,
                                                                co.z * 0.016 * s)) + at)
    length = 0.78 * k * s
    m.lathe([(0.8, 0.1 * s), (1.0, 0.1 * s + 0.06 * length), (0.88, 0.1 * s + 0.86 * length),
             (0.0, 0.1 * s + length)], 4, blade, smooth=False,
            matrix=Matrix.Diagonal((0.036 * s, 0.008 * s, 1.0, 1.0)))


def _bow(m, p, accent):
    s, half, curve = _bow_layout(p)
    wood = m.slot(p["material"]) if p["material"] != "auto" else m.slot("wood")
    wrap, string = m.slot("rubber", color=accent), m.slot("plastic", color="#EDE6D6")
    n = 15
    path, scales, slots = [], [], []
    for i in range(n):
        t = -1.0 + 2.0 * i / (n - 1)
        flick = max(0.0, (abs(t) - 0.8) / 0.2)
        path.append((0.0, curve * (t * t - 0.3 * flick * flick), half * t))
        taper = 1.0 - 0.55 * abs(t)
        grip = max(0.0, 1.0 - abs(t) / 0.2)
        scales.append(((0.04 * taper + 0.008 * grip) * s, (0.022 * taper + 0.014 * grip) * s))
    for i in range(n - 1):
        t = -1.0 + 2.0 * (i + 0.5) / (n - 1)
        slots.append(wrap if abs(t) < 0.15 else wood)
    m.sweep(path, [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], wood, scales=scales, band_slots=slots)
    tip = path[-1][1]
    m.box((-0.003 * s, tip - 0.003 * s, -half), (0.003 * s, tip + 0.003 * s, half), string)


# ------------------------------------------------------------------ projectile


@kit("projectile", label="Projectile",
     description=("A projectile flying toward -Y: a glowing energy bolt, a glowing orb with a see-through halo, or "
                  "an arrow. Small, centred on its origin and meant to be spawned: put it in its own collection, mark "
                  "the collection as a prefab (gamestudio.core.export.set_prefab), keep the collection out of the "
                  "level (exclude it from the view layer) and ctx.spawn() it at a weapon's Muzzle, then move it "
                  "along its -Y from code. A sphere Collider with sensor on detects hits."),
     params={
         "style": choice("bolt", ("bolt", "orb", "arrow"), "bolt: a glowing teardrop; orb: a glowing ball; "
                                                           "arrow: shaft, head and fletching"),
         "length": number(0.6, 0.05, 5.0, "Length along Y in metres (orb: its diameter)"),
         "thickness": number(0.15, 0.04, 0.5, "Width as a fraction of the length (bolt; arrow: the fletching)"),
         "color": color("auto", "Glow colour (bolt, orb) or fletching colour (arrow) as #RRGGBB, or auto from the "
                                "seed"),
         "glow": number(1.5, 0.0, 50.0, "Emission strength of the bolt and orb (0 = no glow)"),
     }, pivot="center", max_tris=900)
def projectile(m, p, rng):
    tint = _pick(p["color"], GLOW, p["seed"])
    L, style = p["length"], p["style"]
    if style == "bolt":
        r = p["thickness"] * L / 2
        core = m.slot("emissive", color=tint, emission_strength=p["glow"])
        halo = m.slot("emissive", color=tint, emission_strength=p["glow"] * 0.3, alpha=0.25)
        profile = [(0.0, 0.0), (0.55 * r, 0.04 * L), (0.88 * r, 0.14 * L), (r, 0.32 * L), (0.86 * r, 0.6 * L),
                   (0.5 * r, 0.84 * L), (0.0, L)]
        m.lathe(profile, 10, core, smooth=True, matrix=TOWARD_MINUS_Y)
        m.lathe([(w * 1.45, z * 1.08 - 0.04 * L) for w, z in profile], 10, halo, smooth=True, matrix=TOWARD_MINUS_Y)
    elif style == "orb":
        core = m.slot("emissive", color=_lighter(tint, 0.15), emission_strength=p["glow"])
        halo = m.slot("emissive", color=tint, emission_strength=p["glow"] * 0.3, alpha=0.3)
        R = L / 2
        m.icosphere(2, core, smooth=True, matrix=Matrix.Scale(0.62 * R, 4))
        m.icosphere(2, halo, smooth=True, matrix=Matrix.Scale(R, 4))
    else:
        wood, head, fletch = m.slot("wood"), m.slot("iron"), m.slot("plastic", color=tint)
        r = 0.012 * L
        m.lathe([(r, 0.0), (r, 0.86 * L)], 6, wood, smooth=False, matrix=TOWARD_MINUS_Y)
        hw = max(2.4 * r, 0.25 * p["thickness"] * L)
        m.lathe([(0.7 * r, 0.82 * L), (hw, 0.86 * L), (0.0, L)], 4, head, smooth=False, matrix=TOWARD_MINUS_Y)
        fin = max(3 * r, 0.6 * p["thickness"] * L)
        for i in range(3):  # three swept-back fins around the shaft near the tail (+Y end)
            m.prism([(r * 0.5, -0.02 * L), (r * 0.5 + fin, -0.01 * L), (r * 0.5 + 0.8 * fin, -0.2 * L),
                     (r * 0.5, -0.26 * L)], "Z", -0.004 * L, 0.004 * L, fletch,
                    matrix=Matrix.Rotation(math.pi / 2 + math.tau * i / 3, 4, "Y"))
        m.box((-1.4 * r, -0.01 * L, -1.4 * r), (1.4 * r, 0.02 * L, 1.4 * r), wood)
    lo, hi = bounds(list(m.bm.faces))
    c = (lo + hi) / 2
    for v in m.bm.verts:
        v.co -= c


def _lighter(hex_color, amount=0.55):
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * amount):02X}" for c in rgb)
