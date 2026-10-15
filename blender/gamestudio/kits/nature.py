# SPDX-License-Identifier: GPL-3.0-or-later
"""Nature kits: rock, bush, tree, terrain."""

import math

from mathutils import Vector

from .base import boolean, choice, integer, kit, material, number
from .geometry import bounds, fbm, seed_offset, smoothstep


def _fit(faces, width, height, cut=None):
    """Flatten everything below `cut` (a 0-1 fraction of the height), then scale the faces' verts
    to `width` x `width` (longer side) x `height` with the bottom centre at the origin."""
    verts = {v for f in faces for v in f.verts}
    lo, hi = bounds(faces)
    if cut is not None:
        z_cut = lo.z + cut * (hi.z - lo.z)
        for v in verts:
            v.co.z = max(v.co.z, z_cut)
        lo.z = z_cut
    s = width / max(hi.x - lo.x, hi.y - lo.y, 1e-6)
    sz = height / max(hi.z - lo.z, 1e-6)
    cx, cy = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2
    for v in verts:
        v.co = Vector(((v.co.x - cx) * s, (v.co.y - cy) * s, (v.co.z - lo.z) * sz))


def _blob(rng, amount):
    """A deform function for icospheres: lumpy, seeded."""
    off = seed_offset(rng)
    spin = rng.uniform(0, math.tau)

    def deform(co):
        d = co.normalized()
        d = Vector((d.x * math.cos(spin) - d.y * math.sin(spin), d.x * math.sin(spin) + d.y * math.cos(spin), d.z))
        return d * (1.0 + amount * fbm(d * 1.4 + off, octaves=3, persistence=0.55))
    return deform


@kit("rock", label="Rock",
     description=("A faceted, lumpy boulder with a flat base, from pebbles to cliffs. Scatter several with "
                  "different seeds and sizes."),
     params={
         "size": number(1.0, 0.1, 40.0, "Width in metres (the longer horizontal side)"),
         "height": number(0.7, 0.2, 1.5, "Height as a fraction of the width"),
         "roughness": number(0.45, 0.0, 1.0, "How lumpy the surface is"),
         "detail": integer(2, 1, 4, "Facets: 1 = 80, 2 = 320, 3 = 1280, 4 = 5120 triangles (4 for cliffs over ~15 m)"),
         "moss": number(0.0, 0.0, 1.0, "How much of the upward-facing surface is mossy (0 = none)"),
         "material": material("stone"),
     }, max_tris=5120)
def rock(m, p, rng):
    stretch = (rng.uniform(0.8, 1.25), rng.uniform(0.7, 1.0))
    deform = _blob(rng, 0.25 * p["roughness"])
    # Chisel: clip the blob with random planes spread around it, which leaves the large flat
    # facets of a stylized rock. Rougher rocks get more, deeper cuts.
    cuts = []
    count = 7 + round(6 * p["roughness"])
    spin = rng.uniform(0, math.tau)
    for i in range(count):
        z = rng.uniform(-0.15, 0.95)
        a = spin + math.tau * i / count + rng.uniform(-0.4, 0.4)
        n = Vector((math.cos(a) * math.sqrt(1 - z * z), math.sin(a) * math.sqrt(1 - z * z), z))
        cuts.append((n, rng.uniform(0.5, 0.72) + 0.15 * (1 - p["roughness"])))

    def chisel(co):
        v = deform(co)
        for n, d in cuts:
            over = v.dot(n) - d
            if over > 0:
                v = v - n * over
        return _scaled(v, stretch[0], stretch[1], 1.0)

    faces = m.icosphere(p["detail"] + 1, m.slot(p["material"]), deform=chisel)
    _fit(faces, p["size"], p["size"] * p["height"], cut=0.15)
    if p["moss"] > 0:
        moss = m.slot("grass")
        for f in faces:
            f.normal_update()
            if f.normal.z > 1.0 - 0.8 * p["moss"]:
                f.material_index = moss


def _scaled(v, sx, sy, sz):
    return Vector((v.x * sx, v.y * sy, v.z * sz))


@kit("bush", label="Bush",
     description="A cluster of leafy blobs sitting on the ground.",
     params={
         "size": number(1.2, 0.3, 6.0, "Width in metres"),
         "height": number(0.75, 0.3, 1.5, "Height as a fraction of the width"),
         "clumps": integer(4, 1, 7, "Number of leafy blobs"),
         "material": material("leaves"),
     }, max_tris=7 * 80)
def bush(m, p, rng):
    slot = m.slot(p["material"])
    faces = []
    n = p["clumps"]
    for i in range(n):
        if i == 0:
            r, x, y = 0.5, 0.0, 0.0
        else:
            a = math.tau * i / (n - 1) + rng.uniform(-0.4, 0.4)
            r = rng.uniform(0.3, 0.42)
            d = rng.uniform(0.35, 0.5)
            x, y = d * math.cos(a), d * math.sin(a)
        z = r * rng.uniform(0.45, 0.7)
        deform = _blob(rng, 0.18)
        faces += m.icosphere(2, slot, deform=lambda co, r=r, x=x, y=y, z=z: deform(co) * r + Vector((x, y, z)))
    for v in {v for f in faces for v in f.verts}:
        v.co.z = max(v.co.z, 0.0)
    _fit(faces, p["size"], p["size"] * p["height"])


@kit("tree", label="Tree",
     description="A low-poly tree: a tapered trunk with a round canopy of blobs, or a pine of stacked cones.",
     params={
         "style": choice("round", ("round", "pine"), "round: broadleaf blobs; pine: stacked cones"),
         "height": number(4.0, 1.0, 20.0, "Total height in metres"),
         "trunk_radius": number(0.2, 0.03, 1.0, "Trunk radius at the base, metres"),
         "canopy_radius": number(1.3, 0.3, 6.0, "Canopy radius in metres (capped at 40% of the height)"),
         "layers": integer(3, 1, 5, "Canopy blobs (round) or cone tiers (pine)"),
         "lean": number(0.0, 0.0, 0.25, "How far the top leans, as a fraction of the height (direction from the seed)"),
         "trunk_material": material("bark"),
         "leaf_material": material("auto", "Canopy material; auto = leaves (round) or pine (pine)", auto=True),
     }, max_tris=500)
def tree(m, p, rng):
    h = p["height"]
    radius = min(p["canopy_radius"], 0.4 * h)
    r0 = min(p["trunk_radius"], radius * 0.5)
    bark = m.slot(p["trunk_material"])
    leaf_name = p["leaf_material"] if p["leaf_material"] != "auto" else ("leaves" if p["style"] == "round" else "pine")
    leaves = m.slot(leaf_name)
    twist = rng.uniform(0, math.tau)
    if p["style"] == "round":
        top = h - radius
        m.lathe([(r0 * 1.35, 0.0), (r0, 0.12 * top), (r0 * 0.6, top)], 6, bark, smooth=False, angle=twist)
        blobs = [(radius, 0.0, 0.0, top)]
        n = p["layers"]
        for i in range(1, n):
            a = math.tau * i / max(n - 1, 1) + rng.uniform(-0.5, 0.5) + twist
            r = radius * rng.uniform(0.55, 0.72)
            d = radius * rng.uniform(0.7, 0.85)
            z = min(top - radius * rng.uniform(0.25, 0.5), h - r)
            blobs.append((r, d * math.cos(a), d * math.sin(a), z))
        for r, x, y, z in blobs:
            deform = _blob(rng, 0.16)
            m.icosphere(2, leaves, deform=lambda co, r=r, x=x, y=y, z=z: deform(co) * r + Vector((x, y, z)))
    else:
        z0 = 0.22 * h
        n = p["layers"]
        tier = (h - z0) * 1.6 / (n + 0.6)
        m.lathe([(r0 * 1.25, 0.0), (r0, 0.1 * h), (r0 * 0.5, z0 + tier * 0.5)], 6, bark, smooth=False, angle=twist)
        for k in range(n):
            t = k / max(n - 1, 1)
            zb = z0 + t * (h - z0 - tier) if n > 1 else h - tier
            r = radius * (1.0 - 0.55 * t) * rng.uniform(0.92, 1.05)
            m.lathe([(r * 0.8, zb), (r, zb + 0.1 * tier), (0.0, zb + tier)], 8, leaves, smooth=False,
                    angle=rng.uniform(0, math.tau))
    if p["lean"] > 0:
        a = rng.uniform(0, math.tau)
        dx, dy = math.cos(a) * p["lean"], math.sin(a) * p["lean"]
        for v in m.bm.verts:
            v.co.x += v.co.z * dx
            v.co.y += v.co.z * dy


@kit("terrain", label="Terrain",
     description=("Noise-displaced ground. shape square: a tile with skirts down to a flat bottom; round: a disc "
                  "whose hills fall off to the rim; island: a round, ragged top over a tapered rocky underside "
                  "(floating islands). Heights rise from z=0 to `height`, plus `relief_height` where a relief "
                  "shapes the land: a hill, a ramp (a hill climb in one call), a valley or a ridge, in any direction. "
                  "Steep faces get the cliff material. "
                  "The origin is the centre at ground level (z=0); the skirt or underside hangs below it."),
     params={
         "shape": choice("square", ("square", "round", "island"),
                         "square tile, round disc, or island (round top, tapered underside)"),
         "size": number(20.0, 2.0, 200.0, "Width and depth in metres (round and island: the diameter)"),
         "resolution": integer(24, 4, 128, "Grid cells per side (triangles = 2 x resolution^2; round shapes "
                                           "use a similar count). Big terrains that rivers and roads carve "
                                           "want fine cells: 128 is about 34,000 triangles"),
         "height": number(2.0, 0.0, 40.0, "Height of the highest hills in metres"),
         "feature_size": number(8.0, 1.0, 100.0, "Typical distance between hills in metres"),
         "roughness": number(0.5, 0.0, 1.0, "How much small detail is added to the big shapes"),
         "flatten_center": number(0.0, 0.0, 0.9,
                                  "Radius of a flat area at z=0 in the middle, as a fraction of the half-size "
                                  "(it flattens the noise, not the relief)"),
         "relief": choice("none", ("none", "hill", "ramp", "valley", "ridge"),
                          "A landform under the noise: hill (a round mound in the middle), ramp (climbs toward "
                          "relief_direction), valley (a trough along relief_direction), ridge (a crest along it)"),
         "relief_height": number(5.0, 0.0, 100.0, "Height the relief adds in metres: the hill's peak, the ramp's "
                                                  "top, the valley's rims, the ridge's crest"),
         "relief_direction": number(0.0, 0.0, 360.0, "Degrees clockwise from +Y seen from above: the way the ramp "
                                                     "climbs, or the line a valley or ridge runs along"),
         "relief_size": number(0.6, 0.1, 1.0, "Hill or ridge radius and valley half-width as a fraction of the "
                                              "half-size; for a ramp, the share of the length it climbs over "
                                              "(flat at both ends; 1 = edge to edge)"),
         "skirt": number(1.0, 0.1, 20.0, "Depth of the sides below z=0, metres (square and round)"),
         "underside": number(0.6, 0.1, 2.0, "Island: depth of the tapered underside as a fraction of the size"),
         "cliff_angle": number(35.0, 5.0, 90.0, "Faces steeper than this (degrees) use the cliff material; 90 = never"),
         "smooth": boolean(False, "Smooth shading instead of flat facets"),
         "top_material": material("grass"),
         "cliff_material": material("stone", "Steep slopes, and the island's underside"),
         "side_material": material("dirt", "The skirt, and the island's rim just under the top"),
     }, pivot="ground", max_tris=2 * 128 * 128 + 8 * 128 + 4 * 128)
def terrain(m, p, rng):
    n, size = p["resolution"], p["size"]
    half = size / 2
    off = seed_offset(rng)
    octaves = 1 + round(4 * p["roughness"])
    flat_r = p["flatten_center"] * half
    relief = _relief(p, half)

    def height_at(x, y):
        h = fbm(Vector((x, y, 0.0)) / p["feature_size"] + off, octaves=octaves, persistence=0.45)
        h = p["height"] * min(1.0, max(0.0, h * 0.85 + 0.5))
        if flat_r > 0:
            h *= smoothstep(flat_r, flat_r + 0.5 * half + 1e-6, math.hypot(x, y))
        if relief is not None:
            h += relief(x, y)
        return h

    if p["shape"] != "square":
        _round_terrain(m, p, rng, height_at)
        return
    step = size / n
    verts = [(-half + i * step, -half + j * step, 0.0) for j in range(n + 1) for i in range(n + 1)]
    verts = [(x, y, height_at(x, y)) for x, y, _ in verts]
    idx = lambda i, j: j * (n + 1) + i  # noqa: E731
    faces = [(idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)) for j in range(n) for i in range(n)]
    top, cliff, side = m.slot(p["top_material"]), m.slot(p["cliff_material"]), m.slot(p["side_material"])
    # the boundary loop, counter-clockwise seen from above
    ring = ([idx(i, 0) for i in range(n)] + [idx(n, j) for j in range(n)]
            + [idx(i, n) for i in range(n, 0, -1)] + [idx(0, j) for j in range(n, 0, -1)])
    base = len(verts)
    verts += [(verts[k][0], verts[k][1], -p["skirt"]) for k in ring]
    center = len(verts)
    verts.append((0.0, 0.0, -p["skirt"]))
    count = len(ring)
    skirt = [(ring[k], base + k, base + (k + 1) % count, ring[(k + 1) % count]) for k in range(count)]
    bottom = [(center, base + (k + 1) % count, base + k) for k in range(count)]
    slots = [top] * len(faces) + [side] * (len(skirt) + len(bottom))
    smooth = [p["smooth"]] * len(faces) + [False] * (len(skirt) + len(bottom))
    if p["cliff_angle"] < 90:
        # Slope from a slightly blurred gradient (two vertices each way), so the cliff material
        # forms patches instead of speckles on rough ground.
        z = lambda i, j: verts[idx(min(max(i, 0), n), min(max(j, 0), n))][2]  # noqa: E731

        def grad(i, j):
            i0, i1, j0, j1 = max(i - 2, 0), min(i + 2, n), max(j - 2, 0), min(j + 2, n)
            return (z(i1, j) - z(i0, j)) / ((i1 - i0) * step), (z(i, j1) - z(i, j0)) / ((j1 - j0) * step)

        limit = math.tan(math.radians(p["cliff_angle"]))
        g = {(i, j): grad(i, j) for j in range(n + 1) for i in range(n + 1)}
        for k, (i, j) in enumerate((i, j) for j in range(n) for i in range(n)):
            corners = (g[i, j], g[i + 1, j], g[i + 1, j + 1], g[i, j + 1])
            gx, gy = sum(c[0] for c in corners) / 4, sum(c[1] for c in corners) / 4
            if math.hypot(gx, gy) > limit:
                slots[k] = cliff
    m.add(verts, faces + skirt + bottom, slots, smooth)
    m.smooth_angle = math.radians(60)


def _relief(p, half):
    """The relief's height at (x, y), from 0 to relief_height; None for no relief."""
    kind, top, size = p["relief"], p["relief_height"], p["relief_size"]
    if kind == "none" or top <= 0:
        return None
    a = math.radians(p["relief_direction"])
    dx, dy = math.sin(a), math.cos(a)
    if kind == "ramp":
        reach = half * (abs(dx) + abs(dy)) if p["shape"] == "square" else half  # corner to corner on a tile

        def ramp(x, y):
            u = (x * dx + y * dy) / reach  # -1 at the low end, 1 at the high end
            return top * min(1.0, max(0.0, (u + size) / (2 * size)))
        return ramp
    if kind == "hill":
        return lambda x, y: top * 0.5 * (1 + math.cos(math.pi * min(1.0, math.hypot(x, y) / (size * half))))

    def crest(x, y):  # 1 on the line through the centre along the direction, 0 from size * half away
        d = abs(x * dy - y * dx) / (size * half)
        return 0.5 * (1 + math.cos(math.pi * min(1.0, d)))
    if kind == "ridge":
        return lambda x, y: top * crest(x, y)
    return lambda x, y: top * (1.0 - crest(x, y))


def _round_terrain(m, p, rng, height_at):
    """Round and island terrain on a polar grid (rings around a centre vertex). The hills fall off to
    z=0 at the rim; a round patch then has a skirt and a flat bottom, an island a rocky underside
    that tapers to a point."""
    n, half = p["resolution"], p["size"] / 2
    island = p["shape"] == "island"
    segs = max(12, 4 * round(1.5 * n / 4))  # a multiple of 4 keeps a round patch centred
    rings = max(2, n // 2)
    edge_noise, rock_noise = seed_offset(rng), seed_offset(rng)
    tip_x, tip_y = rng.uniform(-0.06, 0.06) * half, rng.uniform(-0.06, 0.06) * half

    def rim(a):
        if not island:
            return half
        return half * (1.0 + 0.08 * fbm(Vector((math.cos(a), math.sin(a), 0.0)) * 1.3 + edge_noise, octaves=2))

    def surface(x, y):
        edge = math.hypot(x, y) / rim(math.atan2(y, x))
        return height_at(x, y) * (1.0 - smoothstep(0.72, 1.0, edge))

    angles = [math.tau * s / segs for s in range(segs)]
    rims = [rim(a) for a in angles]
    top, cliff, side = m.slot(p["top_material"]), m.slot(p["cliff_material"]), m.slot(p["side_material"])
    verts, faces, slots, smooth = [(0.0, 0.0, surface(0.0, 0.0))], [], [], []

    def ring(points):
        start = len(verts)
        verts.extend(points)
        return list(range(start, len(verts)))

    def band(lo, hi, slot, shade):
        for s in range(segs):
            t = (s + 1) % segs
            faces.append((lo[s], lo[t], hi[t], hi[s]))
            slots.append(slot)
            smooth.append(shade)

    def fan(centre, row, slot, shade):
        for s in range(segs):
            faces.append((centre, row[s], row[(s + 1) % segs]))
            slots.append(slot)
            smooth.append(shade)

    rows = []
    for k in range(1, rings + 1):
        points = []
        for a, r in zip(angles, rims):
            x, y = r * k / rings * math.cos(a), r * k / rings * math.sin(a)
            points.append((x, y, surface(x, y) if k < rings else 0.0))
        rows.append(ring(points))
    fan(0, rows[0], top, p["smooth"])
    for lo, hi in zip(rows, rows[1:]):
        band(lo, hi, top, p["smooth"])
    if p["cliff_angle"] < 90:
        limit = math.tan(math.radians(p["cliff_angle"]))
        e = 2 * half / rings  # a slightly blurred slope, so cliffs form patches
        for i, face in enumerate(faces):
            cx = sum(verts[v][0] for v in face) / len(face)
            cy = sum(verts[v][1] for v in face) / len(face)
            gx = (surface(cx + e, cy) - surface(cx - e, cy)) / (2 * e)
            gy = (surface(cx, cy + e) - surface(cx, cy - e)) / (2 * e)
            if math.hypot(gx, gy) > limit:
                slots[i] = cliff

    edge = rows[-1]
    if not island:
        low = ring([(verts[v][0], verts[v][1], -p["skirt"]) for v in edge])
        band(edge, low, side, False)
        fan(ring([(0.0, 0.0, -p["skirt"])])[0], low, side, False)
    else:
        depth = p["underside"] * p["size"]
        steps = max(3, n // 6)
        # (depth fraction, radius fraction): a thin lip of side material, then rock tapering down.
        levels = [(0.05, 0.97)] + [(0.05 + 0.95 * (j / steps) ** 1.1, 0.95 * (1 - (j / steps) ** 1.5) ** 0.8)
                                   for j in range(1, steps)]
        previous = edge
        for i, (dz, frac) in enumerate(levels):
            points = []
            for a, r in zip(angles, rims):
                d = Vector((math.cos(a), math.sin(a), 2.0 * dz))
                lumpy = 0.0 if i == 0 else 1.0
                rr = r * frac * (1.0 + 0.3 * lumpy * fbm(d * 1.8 + rock_noise, octaves=3))
                z = -depth * dz * (1.0 + 0.12 * lumpy * fbm(d * 2.6 + rock_noise, octaves=2))
                points.append((rr * math.cos(a), rr * math.sin(a), z))
            row = ring(points)
            band(previous, row, side if i == 0 else cliff, False)
            previous = row
        fan(ring([(tip_x, tip_y, -depth)])[0], previous, cliff, False)
        # The ragged outline is off-centre: centre the bounds on the origin.
        xs, ys = [v[0] for v in verts], [v[1] for v in verts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        verts = [(x - cx, y - cy, z) for x, y, z in verts]
    m.add(verts, faces, slots, smooth)
    m.smooth_angle = math.radians(60)
