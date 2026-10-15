# SPDX-License-Identifier: GPL-3.0-or-later
"""Structure kits: fence, stairs, platform, building."""

import math

from mathutils import Matrix

from .base import boolean, choice, integer, kit, material, number


@kit("fence", label="Fence",
     description=("A fence segment along X: posts with rails, or pickets in front of rails. Chain segments "
                  "every `length` metres; use posts=start on all but the last to avoid doubled posts."),
     params={
         "length": number(2.0, 0.5, 8.0, "Distance between the end posts' centres, metres"),
         "height": number(1.0, 0.3, 3.0, "Height in metres"),
         "style": choice("pickets", ("pickets", "rails"),
                         "pickets: upright pointed boards; rails: horizontal bars only"),
         "posts": choice("both", ("both", "start"), "both: a post at each end; start: only at -X (for chaining)"),
         "post_size": number(0.12, 0.04, 0.4, "Post thickness, metres"),
         "rails": integer(2, 1, 4, "Horizontal rails"),
         "material": material("wood"),
         "post_material": material("wood_dark"),
     }, max_tris=1000)
def fence(m, p, rng):
    length, h, s = p["length"], p["height"], p["post_size"]
    boards, posts = m.slot(p["material"]), m.slot(p["post_material"])
    rr = s / math.sqrt(2)  # a 4-sided lathe at 45 degrees is an axis-aligned square post
    for x in ([-length / 2, length / 2] if p["posts"] == "both" else [-length / 2]):
        m.lathe([(rr, 0.0), (rr, h - 0.45 * s), (0.0, h)], 4, posts, smooth=False, angle=math.pi / 4,
                matrix=Matrix.Translation((x, 0.0, 0.0)))
    n = p["rails"]
    rail_h = min(0.1, h / (3 * n))
    rail_d = min(0.045, s * 0.45)
    y0 = 0.0 if p["style"] == "pickets" else -rail_d / 2
    for k in range(n):
        z = h * (0.28 + 0.55 * (k / (n - 1) if n > 1 else 0.5))
        m.box((-length / 2, y0, z - rail_h / 2), (length / 2, y0 + rail_d, z + rail_h / 2), boards)
    if p["style"] == "pickets":
        pw, gap = 0.085, 0.06
        free = length - s
        count = max(1, int((free + gap) // (pw + gap)))
        pitch = free / count
        for i in range(count):
            x = -free / 2 + pitch * (i + 0.5)
            top = h * 0.94 + rng.uniform(-0.025, 0.025) * h
            prof = [(x - pw / 2, 0.04), (x + pw / 2, 0.04), (x + pw / 2, top - pw / 2), (x, top),
                    (x - pw / 2, top - pw / 2)]
            m.prism(prof, "Y", -0.025, 0.0, boards)


@kit("stairs", label="Stairs",
     description="A straight, solid flight of stairs. The bottom step is at the front (-Y); it climbs toward +Y.",
     params={
         "steps": integer(6, 1, 40, "Number of steps"),
         "width": number(1.5, 0.3, 10.0, "Width along X, metres"),
         "step_height": number(0.18, 0.05, 0.5, "Rise per step, metres (characters step up 0.35 m at most)"),
         "step_depth": number(0.3, 0.1, 1.5, "Run per step, metres"),
         "material": material("stone"),
     }, max_tris=8 * 40 + 4)
def stairs(m, p, rng):
    n, w, rise, run = p["steps"], p["width"], p["step_height"], p["step_depth"]
    depth = n * run
    y0 = -depth / 2
    profile = [(y0, 0.0)]
    for i in range(n):
        profile += [(y0 + i * run, (i + 1) * rise), (y0 + (i + 1) * run, (i + 1) * rise)]
    profile.append((depth / 2, 0.0))
    m.prism(profile, "X", -w / 2, w / 2, m.slot(p["material"]))


@kit("platform", label="Platform",
     description=("A floor tile or floating platform: a block with chamfered top edges, the top in one material "
                  "and the sides in another."),
     params={
         "width": number(4.0, 0.5, 60.0, "Size along X, metres"),
         "depth": number(4.0, 0.5, 60.0, "Size along Y, metres"),
         "height": number(0.5, 0.05, 10.0, "Thickness, metres (the top is at z = height)"),
         "bevel": number(0.1, 0.0, 1.0, "Chamfer on the top edges, metres"),
         "top_material": material("grass"),
         "side_material": material("stone"),
     }, max_tris=20)
def platform(m, p, rng):
    w, d, h = p["width"] / 2, p["depth"] / 2, p["height"]
    b = min(p["bevel"], 0.5 * h, 0.25 * min(w, d) * 2)
    top, side = m.slot(p["top_material"]), m.slot(p["side_material"])
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    verts = [(sx * w, sy * d, 0.0) for sx, sy in corners]
    verts += [(sx * w, sy * d, h - b) for sx, sy in corners]
    faces = [(3, 2, 1, 0)] + [(i, (i + 1) % 4, 4 + (i + 1) % 4, 4 + i) for i in range(4)]
    slots = [side] * 5
    if b > 1e-4:
        verts += [(sx * (w - b), sy * (d - b), h) for sx, sy in corners]
        faces += [(4 + i, 4 + (i + 1) % 4, 8 + (i + 1) % 4, 8 + i) for i in range(4)] + [(8, 9, 10, 11)]
        slots += [top] * 5
    else:
        faces.append((4, 5, 6, 7))
        slots.append(top)
    m.add(verts, faces, slots)


def _lines(values, lo, hi):
    out = []
    for v in sorted(min(hi, max(lo, v)) for v in values):
        if not out or v - out[-1] > 1e-4:
            out.append(v)
    return out


def _wall(m, length, height, thickness, openings, matrix, wall_slot, base_slot, plinth):
    """A wall slab with rectangular openings (u0, u1, z0, z1). Local axes: u along the wall from 0
    to `length`, v = 0 on the outer face and `thickness` inward, z up. Closed and manifold."""
    us = _lines([0.0, length] + [u for o in openings for u in o[:2]], 0.0, length)
    zs = _lines([0.0, height, plinth] + [z for o in openings for z in o[2:]], 0.0, height)
    nu, nz = len(us) - 1, len(zs) - 1
    solid = {(i, j) for i in range(nu) for j in range(nz)
             if not any(u0 < (us[i] + us[i + 1]) / 2 < u1 and z0 < (zs[j] + zs[j + 1]) / 2 < z1
                        for u0, u1, z0, z1 in openings)}
    index, coords, faces, slots = {}, [], [], []

    def vid(i, j, back=0):
        if (i, j, back) not in index:
            index[i, j, back] = len(coords)
            coords.append((us[i], thickness * back, zs[j]))
        return index[i, j, back]

    def slot(z):
        return base_slot if z < plinth - 1e-6 else wall_slot

    for i in range(nu):
        for j in range(nz):
            if (i, j) not in solid:
                continue
            cz = (zs[j] + zs[j + 1]) / 2
            ring = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]  # counter-clockwise from outside
            faces.append(tuple(vid(*c) for c in ring))
            faces.append(tuple(vid(*c, 1) for c in reversed(ring)))
            slots += [slot(cz), slot(cz)]
            # a side face on every edge that borders an opening or the wall's outline
            for k, neighbour in enumerate(((i, j - 1), (i + 1, j), (i, j + 1), (i - 1, j))):
                if neighbour in solid:
                    continue
                a, b = ring[k], ring[(k + 1) % 4]
                faces.append((vid(*b), vid(*a), vid(*a, 1), vid(*b, 1)))
                slots.append(slot((zs[a[1]] + zs[b[1]]) / 2))
    m.add(coords, faces, slots, matrix=matrix)


@kit("building", label="Building",
     description=("A modular building blockout: walls with a front door and window openings, floors, and a "
                  "gable or flat roof. Walk inside it with a mesh Collider. The door is in the front (-Y) wall."),
     params={
         "width": number(6.0, 3.0, 40.0, "Size along X (the front), metres"),
         "depth": number(5.0, 3.0, 40.0, "Size along Y, metres"),
         "floors": integer(1, 1, 5, "Storeys"),
         "floor_height": number(3.0, 2.4, 6.0, "Height of each storey, metres"),
         "wall_thickness": number(0.25, 0.1, 0.8, "Wall thickness, metres"),
         "door": boolean(True, "A door opening in the middle of the front wall"),
         "door_width": number(1.2, 0.6, 4.0, "Door width, metres"),
         "door_height": number(2.2, 1.6, 5.0, "Door height, metres (kept below the first ceiling)"),
         "windows": integer(2, 0, 10, "Windows per storey on the front and back walls (fewer if they do not fit)"),
         "side_windows": integer(1, 0, 10, "Windows per storey on each side wall"),
         "window_width": number(1.0, 0.3, 3.0, "Window width, metres"),
         "window_height": number(1.2, 0.3, 3.0, "Window height, metres (kept below the ceiling)"),
         "sill_height": number(0.9, 0.2, 2.0, "Height of the window bottoms above each floor, metres"),
         "roof": choice("gable", ("gable", "flat", "none"), "Roof shape; the gable ridge runs along X"),
         "roof_pitch": number(30.0, 10.0, 60.0, "Gable roof slope, degrees"),
         "overhang": number(0.3, 0.0, 1.5, "How far the roof reaches past the walls, metres"),
         "wall_material": material("plaster"),
         "base_material": material("stone_dark", "Material of the plinth along the bottom of the walls"),
         "roof_material": material("roof"),
         "floor_material": material("wood"),
     }, max_tris=6000)
def building(m, p, rng):
    W, D, F, fh, t = p["width"], p["depth"], p["floors"], p["floor_height"], p["wall_thickness"]
    H = F * fh
    wall, base = m.slot(p["wall_material"]), m.slot(p["base_material"])
    roof, floor = m.slot(p["roof_material"]), m.slot(p["floor_material"])
    plinth = min(0.4, 0.15 * fh)
    door = None
    if p["door"]:
        dw = min(p["door_width"], W - 2 * t - 0.6)
        dh = min(p["door_height"], fh - 0.3)
        door = (W / 2 - dw / 2, W / 2 + dw / 2, 0.0, dh)

    def windows(length, count, storey, avoid=None):
        ww = min(p["window_width"], length - 0.8)
        sill = min(p["sill_height"], fh - 0.55)
        z0 = storey * fh + sill
        z1 = min(z0 + p["window_height"], (storey + 1) * fh - 0.25)
        n = count
        while n > 0 and n * ww + (n + 1) * 0.4 > length:
            n -= 1
        if n == 0 or ww < 0.3 or z1 - z0 < 0.3:
            return []
        out = []
        for k in range(n):
            c = length * (k + 0.5) / n
            if avoid and abs(c - (avoid[0] + avoid[1]) / 2) < (avoid[1] - avoid[0] + ww) / 2 + 0.3:
                continue
            out.append((c - ww / 2, c + ww / 2, z0, z1))
        return out

    walls = [  # (length, transform, windows per storey, has the door)
        (W, Matrix.Translation((-W / 2, -D / 2, 0.0)), p["windows"], True),
        (W, Matrix.Translation((W / 2, D / 2, 0.0)) @ Matrix.Rotation(math.pi, 4, "Z"), p["windows"], False),
        (D - 2 * t, Matrix.Translation((-W / 2, D / 2 - t, 0.0)) @ Matrix.Rotation(-math.pi / 2, 4, "Z"),
         p["side_windows"], False),
        (D - 2 * t, Matrix.Translation((W / 2, -D / 2 + t, 0.0)) @ Matrix.Rotation(math.pi / 2, 4, "Z"),
         p["side_windows"], False),
    ]
    for length, matrix, count, front in walls:
        openings = [door] if front and door else []
        for storey in range(F):
            openings += windows(length, count, storey, door if front and storey == 0 else None)
        _wall(m, length, H, t, openings, matrix, wall, base, plinth)

    inner = (-W / 2 + t, -D / 2 + t), (W / 2 - t, D / 2 - t)
    m.box((*inner[0], 0.0), (*inner[1], 0.08), floor)
    for storey in range(1, F):
        m.box((*inner[0], storey * fh - 0.15), (*inner[1], storey * fh), floor)

    o = p["overhang"]
    if p["roof"] == "flat":
        m.box((-W / 2 - o, -D / 2 - o, H), (W / 2 + o, D / 2 + o, H + 0.25), roof)
    elif p["roof"] == "gable":
        k = math.tan(math.radians(p["roof_pitch"]))
        o = min(o, 0.3 * fh / k)  # steep roofs: keep the eaves above the top windows
        ridge = H + (D / 2) * k
        m.prism([(-D / 2, H), (D / 2, H), (0.0, ridge - 0.02)], "X", -W / 2, W / 2, wall)
        eave, rt = H - o * k, 0.18
        shell = [(-D / 2 - o, eave), (0.0, ridge), (D / 2 + o, eave),
                 (D / 2 + o, eave + rt), (0.0, ridge + rt), (-D / 2 - o, eave + rt)]
        m.prism(shell, "X", -W / 2 - o, W / 2 + o, roof)
