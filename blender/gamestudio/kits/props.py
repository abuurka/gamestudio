# SPDX-License-Identifier: GPL-3.0-or-later
"""Prop kits: crate, barrel, collectible."""

import math

import bmesh
from mathutils import Matrix, Vector

from .base import boolean, choice, integer, kit, material, number
from .geometry import bounds


@kit("crate", label="Crate",
     description="A wooden crate with a frame and recessed panels on every side. Stacks cleanly.",
     params={
         "width": number(1.0, 0.2, 5.0, "Size along X, metres"),
         "depth": number(1.0, 0.2, 5.0, "Size along Y, metres"),
         "height": number(1.0, 0.2, 5.0, "Size along Z, metres"),
         "frame": number(0.12, 0.0, 0.3, "Frame board width as a fraction of the smallest side (0 = plain box)"),
         "inset": number(0.03, 0.0, 0.15, "How deep the panels are recessed, metres"),
         "braces": boolean(True, "A diagonal brace across each side panel"),
         "material": material("wood"),
         "frame_material": material("wood_dark"),
     }, max_tris=6 * 9 * 2 + 4 * 12)
def crate(m, p, rng):
    w, d, h = p["width"], p["depth"], p["height"]
    panel, frame = m.slot(p["material"]), m.slot(p["frame_material"])
    faces = m.box((-w / 2, -d / 2, 0.0), (w / 2, d / 2, h), panel)
    smallest = min(w, d, h)
    t = p["frame"] * smallest
    inset = min(p["inset"], 0.25 * smallest)
    if t <= 1e-4:
        return
    bm = m.bm
    bm.normal_update()
    for f in faces:
        normal = f.normal.copy()
        center = f.calc_center_median()
        rim = bmesh.ops.inset_individual(bm, faces=[f], thickness=t, depth=0.0)["faces"]
        for r in rim:
            r.material_index = frame
        if inset > 1e-4:
            walls = bmesh.ops.inset_individual(bm, faces=[f], thickness=0.0, depth=-inset)["faces"]
            for r in walls:
                r.material_index = frame
        if p["braces"] and abs(normal.z) < 0.5 and inset > 0.004:
            # a board from corner to corner of the recessed panel, a little below the frame
            span = (w if abs(normal.y) > 0.5 else d) - 2 * t
            rise = h - 2 * t
            length, angle = math.hypot(span, rise), math.atan2(rise, span)
            bw = 0.7 * t
            side = normal.cross(Vector((0, 0, 1)))  # panel's horizontal axis
            basis = Matrix((side, normal, (0, 0, 1))).transposed()  # local X along the panel, Y out
            rot = basis @ Matrix.Rotation(angle if rng.random() < 0.5 else -angle, 3, "Y")
            mat = Matrix.Translation(center - normal * inset) @ rot.to_4x4()
            m.box((-length / 2 + bw / 2, 0.0, -bw / 2), (length / 2 - bw / 2, inset * 0.8, bw / 2), frame, matrix=mat)


@kit("barrel", label="Barrel",
     description="A bulging wooden barrel with metal hoops and a recessed lid.",
     params={
         "height": number(1.0, 0.3, 3.0, "Height in metres"),
         "radius": number(0.38, 0.1, 1.5, "Radius at the middle, metres"),
         "bulge": number(0.15, 0.0, 0.4, "How much narrower the ends are than the middle (fraction)"),
         "segments": integer(14, 6, 24, "Sides around"),
         "hoops": integer(2, 0, 4, "Metal hoops"),
         "material": material("wood"),
         "hoop_material": material("iron"),
     }, max_tris=24 * 2 * 30 + 2 * 24)
def barrel(m, p, rng):
    h, big = p["height"], p["radius"]
    end = big * (1.0 - p["bulge"])
    wood, hoop, lid = m.slot(p["material"]), m.slot(p["hoop_material"]), m.slot("wood_dark")

    def r_at(z):
        return end + (big - end) * math.sin(math.pi * z / h)

    hoop_at = {0: [], 1: [0.5], 2: [0.16, 0.84], 3: [0.12, 0.5, 0.88], 4: [0.1, 0.3, 0.7, 0.9]}[p["hoops"]]
    half = min(0.035 * h, 0.06)
    out = 0.01 + 0.03 * big
    zs = sorted({h * i / 8 for i in range(9)})
    bands = [(h * t - half, h * t + half) for t in hoop_at]
    zs = [z for z in zs if not any(lo - 1e-4 <= z <= hi + 1e-4 for lo, hi in bands)]
    profile, slots, smooth = [], [], []
    events = sorted([(z, None) for z in zs] + [(lo, (lo, hi)) for lo, hi in bands])
    for z, band in events:
        if band is None:
            profile.append((r_at(z), z))
            slots.append(wood)
            smooth.append(True)
            continue
        lo, hi = band
        profile += [(r_at(lo), lo), (r_at(lo) + out, lo), (r_at(hi) + out, hi), (r_at(hi), hi)]
        slots += [hoop, hoop, hoop, wood]
        smooth += [False, True, False, True]
    rim = min(0.06 * end + 0.01, 0.4 * end)
    dip = min(0.04 * h, 0.05)
    profile += [(end - rim, h), (end - rim, h - dip)]
    slots[-1], smooth[-1] = wood, False  # the band from the last body point to the rim top
    slots += [wood, wood]
    smooth += [False, False]
    m.lathe(profile, p["segments"], wood, band_slots=slots[:len(profile) - 1],
            band_smooth=smooth[:len(profile) - 1], cap_slots=(lid, lid))


@kit("collectible", label="Collectible",
     description="A pickup: a coin (standing, facing -Y), a cut gem, or a puffy star. Spin it around Z.",
     params={
         "style": choice("coin", ("coin", "gem", "star"), "Shape"),
         "size": number(0.6, 0.1, 3.0, "Diameter in metres"),
         "thickness": number(0.15, 0.05, 0.5, "Thickness as a fraction of the size (coin and star)"),
         "segments": integer(24, 8, 48, "Sides around (coin); a gem uses a quarter of it"),
         "material": material("auto", "Material; auto = gold for coins and stars, gem for gems", auto=True),
     }, pivot="center", max_tris=48 * 2 * 8 + 4)
def collectible(m, p, rng):
    style, r = p["style"], p["size"] / 2
    name = p["material"] if p["material"] != "auto" else ("gem" if style == "gem" else "gold")
    slot = m.slot(name)
    if style == "coin":
        t = p["thickness"] * p["size"]
        a, b = 0.3 * t, 0.5 * t
        profile = [(0.0, -a), (0.7 * r, -a), (0.78 * r, -b), (0.92 * r, -b), (r, -0.3 * t),
                   (r, 0.3 * t), (0.92 * r, b), (0.78 * r, b), (0.7 * r, a), (0.0, a)]
        faces = m.lathe(profile, p["segments"], slot, smooth=True, matrix=Matrix.Rotation(math.pi / 2, 4, "X"))
    elif style == "gem":
        n = max(6, p["segments"] // 4)
        w = 0.62 * r  # an upright crystal: a faceted band with pointed ends, `size` tall
        profile = [(0.0, -r), (w, -0.2 * r), (w, 0.2 * r), (0.0, r)]
        faces = m.lathe(profile, n, slot, smooth=False)
    else:
        t = max(p["thickness"], 0.12) * p["size"]
        ring = []
        for i in range(10):
            a = math.pi / 2 + math.tau * i / 10
            rr = r if i % 2 == 0 else 0.45 * r
            ring.append((rr * math.cos(a), 0.0, rr * math.sin(a)))
        verts = ring + [(0.0, -t, 0.0), (0.0, t, 0.0)]
        tris = [(10, i, (i + 1) % 10) for i in range(10)] + [(11, (i + 1) % 10, i) for i in range(10)]
        faces = m.add(verts, tris, slot)
    lo, hi = bounds(faces)
    c = (lo + hi) / 2
    for v in m.bm.verts:
        v.co -= c
