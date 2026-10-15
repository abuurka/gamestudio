# SPDX-License-Identifier: GPL-3.0-or-later
"""Character kits: a capsule blockout for prototyping, and a rigged humanoid with animation clips.

character_rigged is a separate kit so `character` keeps its one-mesh contract: scenes that
regenerate a character rebuild exactly what they had, and a walk cycle needs legs the capsule lacks.
"""

import math

from mathutils import Matrix, Vector

from . import animation
from .base import boolean, color, kit, material, number
from .geometry import capsule_profile, smoothstep
from .rig import Bone

# Body colours picked by seed when color is "auto" (seed 0 is the first).
BODY_COLORS = ("#3F7FD9", "#E0533D", "#3FAE6A", "#E8B53A", "#8E5CD6", "#2BB3B3", "#E46FA8", "#5E6B7D")
LEGS_COLORS = ("#2E3A55", "#4A3B2F", "#3D4148", "#2B2B30", "#4B5A3A", "#5A4A6E", "#6B5B45")
HAIR_COLORS = ("#3B2A1E", "#1E1B1A", "#C9A15A", "#8A4B2A", "#5B3B2A", "#D8D2C8", "#2A2F3A")
A_POSE = math.radians(8)  # rest pose: the arms hang 8 degrees out from the sides
HAIR = 0.06  # hair cap thickness, in head radii


@kit("character", label="Character",
     description=("A prototyping character: a capsule body, a head with two eyes on the front (-Y) and "
                  "stubby arms. For the player add CharacterController, a kinematic RigidBody and a capsule Collider."),
     params={
         "height": number(1.8, 0.5, 4.0, "Total height in metres, head included"),
         "girth": number(0.17, 0.1, 0.35, "Body radius as a fraction of the height"),
         "head": number(0.13, 0.07, 0.25, "Head radius as a fraction of the height"),
         "arms": boolean(True, "Stubby arms at the sides (they widen the capsule collider a little)"),
         "color": color("auto", "Body colour as #RRGGBB, or auto to pick one from the seed"),
         "material": material("plastic", "Body material (tinted by color)"),
         "head_material": material("skin"),
     }, max_tris=600)
def character(m, p, rng):
    h = p["height"]
    br, hr = p["girth"] * h, p["head"] * h
    hex_color = p["color"] if p["color"] != "auto" else BODY_COLORS[p["seed"] % len(BODY_COLORS)]
    body = m.slot(p["material"], color=hex_color)
    skin, eyes = m.slot(p["head_material"]), m.slot("rubber")
    body_top = h - 1.9 * hr
    m.lathe(capsule_profile(br, body_top, rings=3), 12, body)
    hc = h - hr
    sphere = [(hr * math.cos(a), hc + hr * math.sin(a))
              for a in (-math.pi / 2 + math.pi * i / 6 for i in range(7))]
    sphere[0], sphere[-1] = (0.0, hc - hr), (0.0, h)
    m.lathe(sphere, 12, skin, angle=math.pi / 12)
    er = 0.17 * hr  # two dark eyes on the front show which way it faces
    for side in (-1, 1):
        at = Vector((side * 0.36 * hr, -0.86 * hr, hc + 0.12 * hr))
        m.icosphere(2, eyes, deform=lambda co, at=at: Vector((co.x * er, co.y * er * 0.6, co.z * er * 1.25)) + at)
    if p["arms"]:
        ar = 0.32 * br
        arm_h = 0.42 * body_top
        z0 = body_top - br * 0.55 - arm_h
        for side in (-1, 1):
            x = side * (br + ar * 0.3)
            m.lathe(capsule_profile(ar, arm_h, rings=2), 8, body, matrix=Matrix.Translation((x, 0.0, z0)))


# ------------------------------------------------------------------ rigged humanoid


def humanoid_layout(p) -> dict:
    """Joint positions and part sizes of character_rigged, in metres (feet at z=0, facing -Y)."""
    h = p["height"]
    hr = p["head"] * h
    tw = p["girth"] * h                                    # torso half-width
    lr = 0.4 * tw                                          # thigh radius
    head_c = h - hr - (HAIR * hr if p["hair"] else 0.0)    # the hair cap stays within the height
    head_bottom = head_c - hr
    torso_top = head_bottom - 0.3 * hr
    leg = min(p["legs"] * h, torso_top - 0.18 * h + 0.55 * lr)  # hip joint height; leaves a torso
    ah = max(0.1 * leg, 0.32 * tw)                         # ankle above the sole; the shin end stays above it
    bottom = leg - 0.55 * lr                               # crotch: hides the tops of the thighs
    torso = torso_top - bottom
    ar, hand = 0.27 * tw, 0.36 * tw
    shoulder_z = bottom + 0.84 * torso
    reach = shoulder_z - 1.6 * hand - 0.04 * h             # the hands stay off the ground
    arm = min(p["arms"] * h, reach) / math.cos(A_POSE)
    fl = 0.22 * leg + 0.04 * h                             # foot length
    return {
        "h": h, "hr": hr, "tw": tw, "td": 0.72 * tw, "lr": lr, "sr": 0.84 * lr, "ar": ar, "fr": 0.88 * ar,
        "hand": hand, "nr": min(0.3 * hr, 0.55 * tw), "head_c": head_c, "head_bottom": head_bottom,
        "torso_top": torso_top, "bottom": bottom, "torso": torso, "leg": leg, "ah": ah,
        "knee": ah + 0.5 * (leg - ah), "hip_x": 0.52 * tw, "shoulder": (0.96 * tw + 0.2 * ar, shoulder_z),
        "arm": arm, "heel": 0.28 * fl, "toe": 0.72 * fl, "fw": 0.42 * tw, "fl": fl,
        "spine_z": bottom + 0.34 * torso, "chest_z": bottom + 0.62 * torso,
        "neck_z": torso_top - 0.04 * torso, "head_z": head_bottom + 0.3 * hr,
    }


def _arm_points(d, side):
    sx, sz = d["shoulder"]
    s = 1 if side == "L" else -1
    shoulder = Vector((s * sx, 0.0, sz))
    direction = Vector((s * math.sin(A_POSE), 0.0, -math.cos(A_POSE)))
    return shoulder, shoulder + direction * (d["arm"] / 2), shoulder + direction * d["arm"], direction


def _humanoid_rig(p):
    d = humanoid_layout(p)
    bones = [
        Bone("root", (0, 0, 0), (0, -0.12 * d["h"], 0), deform=False),
        Bone("hips", (0, 0, d["leg"]), (0, 0, d["spine_z"]), "root"),
        Bone("spine", (0, 0, d["spine_z"]), (0, 0, d["chest_z"]), "hips"),
        Bone("chest", (0, 0, d["chest_z"]), (0, 0, d["neck_z"]), "spine"),
        Bone("neck", (0, 0, d["neck_z"]), (0, 0, d["head_z"]), "chest"),
        Bone("head", (0, 0, d["head_z"]), (0, 0, d["h"]), "neck"),
    ]
    for side in "LR":
        shoulder, elbow, wrist, _ = _arm_points(d, side)
        x = d["hip_x"] if side == "L" else -d["hip_x"]
        bones += [
            Bone(f"upper_arm_{side}", shoulder, elbow, "chest"),
            Bone(f"lower_arm_{side}", elbow, wrist, f"upper_arm_{side}"),
            Bone(f"upper_leg_{side}", (x, 0, d["leg"]), (x, 0, d["knee"]), "hips"),
            Bone(f"lower_leg_{side}", (x, 0, d["knee"]), (x, 0, d["ah"]), f"upper_leg_{side}"),
            Bone(f"foot_{side}", (x, 0, d["ah"]), (x, -0.8 * d["toe"], d["ah"]), f"lower_leg_{side}"),
        ]
    layout = {"type": "humanoid", "height": d["h"], "ankle": d["ah"], "heel": d["heel"], "toe": d["toe"]}
    return bones, layout


def _limb(m, a, b, r0, r1, slot, segments=8, rings=3):
    """A capsule from point a to point b with its round ends centred on them (radius r0 at a, r1 at
    b): a ball joint that looks the same however the bone turns."""
    a, b = Vector(a), Vector(b)
    length = (b - a).length
    pts = [(0.0, -r0)]
    pts += [(r0 * math.cos(t), r0 * math.sin(t))
            for t in (-math.pi / 2 + math.pi / 2 * i / rings for i in range(1, rings + 1))]
    pts += [(r1 * math.cos(t), length + r1 * math.sin(t)) for t in (math.pi / 2 * i / rings for i in range(rings))]
    pts.append((0.0, length + r1))
    rot = (b - a).to_track_quat("Z", "Y").to_matrix().to_4x4()
    return m.lathe(pts, segments, slot, matrix=Matrix.Translation(a) @ rot)


TORSO = [(0.0, 0.0), (0.55, 0.015), (0.82, 0.07), (0.94, 0.17), (0.97, 0.28), (0.93, 0.40), (0.92, 0.50),
         (0.97, 0.63), (1.0, 0.75), (0.97, 0.86), (0.86, 0.94), (0.6, 0.985), (0.0, 1.0)]  # (half-width, height)
BELT = 0.40  # trousers below this fraction of the torso, shirt above


@kit("character_rigged", label="Rigged Character",
     description=("A humanoid with a skeleton (root, hips, spine, chest, neck, head, upper/lower arms, upper/lower "
                  "legs, feet) and in-place animation clips: Idle, Walk, Run, Jump, JumpStart, JumpAir, Land, Wave, "
                  "Fall (add more with gs.kits.animation.add_clip). Limbs are rigid ball-jointed segments and the "
                  "torso bends smoothly, so poses never twist or tear. Play clips with an Animator on the armature."),
     params={
         "height": number(1.8, 0.5, 4.0, "Total height in metres, head included"),
         "girth": number(0.11, 0.07, 0.16, "Torso half-width as a fraction of the height; limbs scale with it"),
         "head": number(0.12, 0.07, 0.22, "Head radius as a fraction of the height"),
         "legs": number(0.42, 0.25, 0.55, "Hip height as a fraction of the height (shortened if the head needs room)"),
         "arms": number(0.32, 0.2, 0.42, "Shoulder-to-wrist length as a fraction of the height"),
         "hair": boolean(True, "A hair cap on the head"),
         "color": color("auto", "Shirt colour as #RRGGBB, or auto to pick one from the seed"),
         "legs_color": color("auto", "Trousers colour as #RRGGBB, or auto to pick one from the seed"),
         "hair_color": color("auto", "Hair colour as #RRGGBB, or auto to pick one from the seed"),
         "material": material("plastic", "Clothes material (tinted by color and legs_color)"),
         "head_material": material("skin", "Head, forearms and hands"),
         "clips": boolean(True, "Add the standard clips (Idle, Walk, Run, Jump, JumpStart, JumpAir, Land, Wave, Fall)"),
     }, max_tris=2400,
     rig=_humanoid_rig,
     clips=lambda p: list(animation.HUMANOID_SET) if p["clips"] else [])
def character_rigged(m, p, rng):
    d = humanoid_layout(p)
    seed = p["seed"]

    def pick(value, palette):
        return value if value != "auto" else palette[seed % len(palette)]

    shirt = m.slot(p["material"], color=pick(p["color"], BODY_COLORS))
    pants = m.slot(p["material"], color=pick(p["legs_color"], LEGS_COLORS))
    skin, dark = m.slot(p["head_material"]), m.slot("rubber")
    m.smooth_angle = math.radians(60)

    # Torso: hips, spine and chest blend into each other along the height.
    bottom, torso, tw = d["bottom"], d["torso"], d["tw"]
    profile = [(r * tw, bottom + z * torso) for r, z in TORSO]
    bands = [pants if (TORSO[i][1] + TORSO[i + 1][1]) / 2 < BELT else shirt for i in range(len(TORSO) - 1)]
    faces = m.lathe(profile, 16, shirt, band_slots=bands, matrix=Matrix.Diagonal((1.0, d["td"] / tw, 1.0, 1.0)))
    band = 0.09 * torso

    def torso_weights(co):
        s1 = smoothstep(d["spine_z"] - band, d["spine_z"] + band, co.z)
        s2 = smoothstep(d["chest_z"] - band, d["chest_z"] + band, co.z)
        return {"hips": 1 - s1, "spine": s1 - s2, "chest": s2}
    m.weigh(faces, torso_weights)

    m.weigh(_limb(m, (0, 0, d["neck_z"] - 0.1 * torso), (0, 0, d["head_c"]), d["nr"], d["nr"], skin), {"neck": 1})

    # Head with eyes on the front (-Y) and a hair cap tilted back to show the forehead.
    hr, hc = d["hr"], d["head_c"]
    sphere = [(hr * math.cos(a), hc + hr * math.sin(a)) for a in (-math.pi / 2 + math.pi * i / 8 for i in range(9))]
    sphere[0], sphere[-1] = (0.0, hc - hr), (0.0, hc + hr)
    m.weigh(m.lathe(sphere, 14, skin, angle=math.pi / 14), {"head": 1})
    er = 0.17 * hr
    for side in (-1, 1):
        at = Vector((side * 0.36 * hr, -0.86 * hr, hc + 0.12 * hr))
        eye = m.icosphere(2, dark, smooth=True,
                          deform=lambda co, at=at: Vector((co.x * er, co.y * er * 0.6, co.z * er * 1.25)) + at)
        m.weigh(eye, {"head": 1})
    if p["hair"]:
        hair = m.slot("rubber", color=pick(p["hair_color"], HAIR_COLORS))
        cr = (1 + HAIR) * hr
        cap = [(cr * math.cos(a), cr * math.sin(a)) for a in (math.radians(5 + 17 * i) for i in range(5))]
        cap.append((0.0, cr))
        tilt = Matrix.Translation((0, 0.05 * hr, hc)) @ Matrix.Rotation(math.radians(-30), 4, "X")
        m.weigh(m.lathe(cap, 14, hair, angle=math.pi / 14, matrix=tilt), {"head": 1})

    for side in "LR":
        shoulder, elbow, wrist, direction = _arm_points(d, side)
        m.weigh(_limb(m, shoulder, elbow, d["ar"], d["ar"], shirt), {f"upper_arm_{side}": 1})
        m.weigh(_limb(m, elbow, wrist, d["fr"], 0.9 * d["fr"], skin), {f"lower_arm_{side}": 1})
        hand = d["hand"]
        m.weigh(_limb(m, wrist + direction * 0.3 * hand, wrist + direction * 1.05 * hand, hand, 0.85 * hand, skin),
                {f"lower_arm_{side}": 1})

        x = d["hip_x"] if side == "L" else -d["hip_x"]
        hip, knee, ankle = (x, 0, d["leg"]), (x, 0, d["knee"]), (x, 0, d["ah"])
        m.weigh(_limb(m, hip, knee, d["lr"], d["sr"], pants), {f"upper_leg_{side}": 1})
        m.weigh(_limb(m, knee, ankle, d["sr"], 0.85 * d["sr"], pants), {f"lower_leg_{side}": 1})
        ah, heel, toe, fl = d["ah"], d["heel"], d["toe"], d["fl"]
        shoe = [(heel, 0.0), (-toe + 0.4 * ah, 0.0), (-toe, 0.35 * ah), (-toe + 0.15 * ah, 0.7 * ah),
                (-toe + 0.6 * ah, 0.95 * ah), (-0.1 * fl, 1.15 * ah), (0.08 * fl, 1.45 * ah), (heel, 1.3 * ah)]
        m.weigh(m.prism(shoe, "X", x - d["fw"], x + d["fw"], dark), {f"foot_{side}": 1})
