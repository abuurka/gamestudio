# SPDX-License-Identifier: GPL-3.0-or-later
"""MeshBuilder: the small modelling toolkit the kits share.

Geometry goes into one bmesh as closed shells (box, prism, lathe, sphere...), each face tagged
with a material slot and flat or smooth shading. finish() triangulates n-gons, makes the normals
point outward, marks sharp edges on smooth parts, projects UVs (box mapping, 1 UV unit per metre)
and returns a mesh. weigh() adds skin weights for rigged kits.
Nothing here uses bpy.ops, so kits work from timers, background mode and any context.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector, geometry, noise


class MeshBuilder:
    def __init__(self):
        self.bm = bmesh.new()
        self.slots: list[tuple[str, dict]] = []   # (material preset, overrides) per slot
        self.smooth_angle = math.radians(40)       # smooth faces meeting at a sharper angle get a hard edge
        self.uv_scale = 1.0                        # UV units per metre
        self.groups: list[str] = []                # vertex group (bone) names, in index order; see weigh()

    # ------------------------------------------------------------------ materials

    def slot(self, preset: str, **overrides) -> int:
        for i, (p, o) in enumerate(self.slots):
            if p == preset and o == overrides:
                return i
        self.slots.append((preset, dict(overrides)))
        return len(self.slots) - 1

    # ------------------------------------------------------------------ raw geometry

    def add(self, verts, faces, slot: int = 0, smooth=False, matrix: Matrix | None = None):
        """Add a shell: vertex positions and faces as index tuples. Returns the new faces.
        `slot` and `smooth` can also be per-face lists."""
        bm = self.bm
        if matrix is not None:
            verts = [matrix @ Vector(v) for v in verts]
        vs = [bm.verts.new(v) for v in verts]
        out = []
        for i, f in enumerate(faces):
            face = bm.faces.new([vs[j] for j in f])
            face.material_index = slot[i] if isinstance(slot, (list, tuple)) else slot
            face.smooth = smooth[i] if isinstance(smooth, (list, tuple)) else smooth
            out.append(face)
        return out

    def box(self, lo, hi, slot=0, matrix=None):
        """An axis-aligned box from corner `lo` to corner `hi` (then `matrix`, if given)."""
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                 (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        return self.add(verts, faces, slot, matrix=matrix)

    def prism(self, profile, axis: str, a0: float, a1: float, slot=0, cap_slot=None, matrix=None):
        """Extrude a 2D polygon along an axis from a0 to a1. The profile's two coordinates are
        (y, z) for axis "X", (x, z) for "Y" and (x, y) for "Z". Concave profiles are fine."""
        def to3(p, a):
            u, v = p
            return {"X": (a, u, v), "Y": (u, a, v), "Z": (u, v, a)}[axis]
        n = len(profile)
        verts = [to3(p, a0) for p in profile] + [to3(p, a1) for p in profile]
        faces = [tuple(reversed(range(n))), tuple(range(n, 2 * n))]
        faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        cap = slot if cap_slot is None else cap_slot
        return self.add(verts, faces, [cap, cap] + [slot] * n, matrix=matrix)

    def lathe(self, profile, segments: int, slot=0, smooth=True, angle=0.0, matrix=None,
              band_slots=None, band_smooth=None, cap_slots=(None, None)):
        """Revolve a profile of (radius, z) points, bottom to top, around Z. A point with radius 0
        becomes a pole; open ends get a flat cap. `band_slots`/`band_smooth` set the slot and
        shading of the band between point i and i + 1."""
        verts, faces, slots, smooths = [], [], [], []
        rings = []
        for r, z in profile:
            if r <= 1e-9:
                rings.append([len(verts)])
                verts.append((0.0, 0.0, z))
                continue
            ring = []
            for s in range(segments):
                a = angle + 2 * math.pi * s / segments
                ring.append(len(verts))
                verts.append((r * math.cos(a), r * math.sin(a), z))
            rings.append(ring)
        for i in range(len(rings) - 1):
            lo, hi = rings[i], rings[i + 1]
            band_slot = band_slots[i] if band_slots else slot
            band_sm = band_smooth[i] if band_smooth else smooth
            for s in range(segments):
                t = (s + 1) % segments
                if len(lo) == 1 and len(hi) == 1:
                    continue
                if len(lo) == 1:
                    faces.append((lo[0], hi[t], hi[s]))
                elif len(hi) == 1:
                    faces.append((lo[s], lo[t], hi[0]))
                else:
                    faces.append((lo[s], lo[t], hi[t], hi[s]))
                slots.append(band_slot)
                smooths.append(band_sm)
        for ring, cap_slot, reverse in ((rings[0], cap_slots[0], True), (rings[-1], cap_slots[1], False)):
            if len(ring) > 1:
                faces.append(tuple(reversed(ring)) if reverse else tuple(ring))
                slots.append(slot if cap_slot is None else cap_slot)
                smooths.append(False)
        return self.add(verts, faces, slots, smooths, matrix=matrix)

    def icosphere(self, subdivisions: int, slot=0, smooth=False, deform=None, matrix=None):
        """A unit icosphere (subdivisions 1 = 20 faces, 2 = 80, 3 = 320, 4 = 1280).
        `deform(co: Vector) -> Vector` reshapes each vertex before `matrix`."""
        tmp = bmesh.new()
        bmesh.ops.create_icosphere(tmp, subdivisions=subdivisions, radius=1.0)
        tmp.verts.index_update()
        verts = [v.co.copy() for v in tmp.verts]
        faces = [tuple(v.index for v in f.verts) for f in tmp.faces]
        tmp.free()
        if deform is not None:
            verts = [deform(v) for v in verts]
        return self.add(verts, faces, slot, smooth, matrix=matrix)

    def sweep(self, path, section, slot=0, smooth=False, scales=None, band_slots=None, matrix=None):
        """Sweep a closed 2D cross-section of (u, v) points along a 3D path, with flat caps at both
        ends (bow limbs, pipes, handles). `scales` gives a number or (su, sv) per path point;
        `band_slots` the slot of the segment between point i and i + 1. The section's u axis
        stays as level as the path allows (it starts along X, or Y for a path along X)."""
        path = [Vector(p) for p in path]
        n = len(path)
        tangents = [(path[min(i + 1, n - 1)] - path[max(i - 1, 0)]).normalized() for i in range(n)]
        ref = Vector((1.0, 0.0, 0.0)) if abs(tangents[0].x) < 0.9 else Vector((0.0, 1.0, 0.0))
        normal = (ref - tangents[0] * ref.dot(tangents[0])).normalized()
        verts, k = [], len(section)
        for i, (point, tangent) in enumerate(zip(path, tangents)):
            normal = (normal - tangent * normal.dot(tangent)).normalized()  # parallel transport
            binormal = tangent.cross(normal)
            s = 1.0 if scales is None else scales[i]
            su, sv = (s, s) if isinstance(s, (int, float)) else s
            verts += [point + normal * (u * su) + binormal * (v * sv) for u, v in section]
        faces, slots = [tuple(reversed(range(k))), tuple(range((n - 1) * k, n * k))], [slot, slot]
        for i in range(n - 1):
            for j in range(k):
                a, b = i * k + j, i * k + (j + 1) % k
                faces.append((a, b, b + k, a + k))
                slots.append(band_slots[i] if band_slots else slot)
        if band_slots:
            slots[0], slots[1] = band_slots[0], band_slots[-1]
        return self.add(verts, faces, slots, smooth, matrix=matrix)

    def torus(self, radius, thickness, segments=12, sides=4, slot=0, smooth=False, matrix=None):
        """A ring around Z through the origin: `radius` to the tube's centre, `thickness` the tube's
        radius. sides=4 gives a square-section ring (turned 45 degrees)."""
        verts, faces = [], []
        for i in range(segments):
            a = math.tau * i / segments
            for j in range(sides):
                b = math.tau * j / sides + (math.pi / sides if sides == 4 else 0.0)
                r = radius + thickness * math.cos(b)
                verts.append((r * math.cos(a), r * math.sin(a), thickness * math.sin(b)))
        for i in range(segments):
            for j in range(sides):
                i2, j2 = (i + 1) % segments, (j + 1) % sides
                faces.append((i * sides + j, i2 * sides + j, i2 * sides + j2, i * sides + j2))
        return self.add(verts, faces, slot, smooth, matrix=matrix)

    # ------------------------------------------------------------------ skinning

    def group(self, name: str) -> int:
        """Index of vertex group `name`, added on first use."""
        if name not in self.groups:
            self.groups.append(name)
        return self.groups.index(name)

    def weigh(self, faces, weights) -> None:
        """Skin weights for the vertices of `faces` (what box, lathe... return): {group: weight}
        for all of them, or a function(co: Vector) -> {group: weight}. Each vertex's weights are
        normalised to sum to 1; to_object() turns the groups into vertex groups."""
        layer = self.bm.verts.layers.deform.verify()
        seen = set()
        for face in faces:
            for v in face.verts:
                if v in seen:
                    continue
                seen.add(v)
                w = weights(v.co) if callable(weights) else weights
                w = {name: x for name, x in w.items() if x > 1e-6}
                total = sum(w.values())
                if total <= 0:
                    raise ValueError("weigh(): every vertex needs a positive weight")
                dv = v[layer]
                dv.clear()
                for name, x in w.items():
                    dv[self.group(name)] = x / total

    # ------------------------------------------------------------------ finishing

    def to_object(self, name: str, *, location=(0, 0, 0), collection=None) -> bpy.types.Object:
        """finish() plus the slot materials, an object at `location`, linked into `collection` (a
        Collection, a collection name or None for the active one). For modelling in run_python:

            m = gs.kits.geometry.MeshBuilder()
            m.box((-0.5, -0.3, 0.7), (0.5, 0.3, 0.75), m.slot("wood"))
            obj = m.to_object("Table", location=(2, 0, 0), collection="Props")
        """
        from . import base, materials  # base imports this module

        coll = base._collection(collection)
        loc = base._vec3(location)
        mesh = self.finish(name)
        for preset, overrides in self.slots:
            mesh.materials.append(materials.material(preset, **overrides))
        obj = bpy.data.objects.new(name, mesh)
        mesh.name = obj.name
        for group in self.groups:
            obj.vertex_groups.new(name=group)
        obj.location = loc
        coll.objects.link(obj)
        return obj

    def finish(self, name: str) -> bpy.types.Mesh:
        bm = self.bm
        # Flat caps and prism ends are n-gons; the exporter would triangulate them anyway, and as
        # triangles they shade predictably and keep validate_assets quiet. Quads stay.
        ngons = [f for f in bm.faces if len(f.verts) > 4]
        if ngons:
            # triangulate projects each n-gon along its stored normal, which new faces lack: a convex
            # outline still comes out right, a concave one (stairs, a door frame) needs the real normal
            for face in ngons:
                if _misfills(face):
                    face.normal_update()
            tris = bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")["faces"]
            _fix_slivers(tris)
        bm.normal_update()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.normal_update()
        for edge in bm.edges:
            faces = edge.link_faces
            if len(faces) == 2 and faces[0].smooth and faces[1].smooth:
                edge.smooth = faces[0].normal.angle(faces[1].normal, 0.0) <= self.smooth_angle
        box_uv(bm, self.uv_scale)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        return mesh


def _convex(face) -> bool:
    pts = [v.co for v in face.verts]
    normal = geometry.normal(pts)
    sign = 0.0
    for i, p in enumerate(pts):
        turn = (p - pts[i - 1]).cross(pts[(i + 1) % len(pts)] - p).dot(normal)
        if abs(turn) < 1e-12:
            continue
        if sign and (turn > 0) != (sign > 0):
            return False
        sign = turn
    return True


def _misfills(face) -> bool:
    """True if triangulating the n-gon as it is would put triangles outside its outline (their areas
    then add up to more than its own). Tried on a copy, so outlines that come out right keep exactly
    the triangles they always had."""
    if _convex(face):
        return False
    tmp = bmesh.new()
    copy = tmp.faces.new([tmp.verts.new(v.co) for v in face.verts])
    copy.normal = face.normal.copy()
    area = copy.calc_area()
    tris = bmesh.ops.triangulate(tmp, faces=[copy], quad_method="BEAUTY", ngon_method="BEAUTY")["faces"]
    total = sum(t.calc_area() for t in tris)
    tmp.free()
    return total > area * (1 + 1e-6) + 1e-12


def _fix_slivers(faces) -> None:
    """Flip away zero-area triangles that triangulating an outline with three corners in a line
    leaves: their longest edge is a diagonal of the same flat cap, so rotating it splits the
    neighbouring triangle instead."""
    faces = list(faces)
    for _ in range(8):
        slivers = [f for f in faces if f.is_valid and f.calc_area() < 1e-10]
        if not slivers:
            return
        for face in slivers:
            if not face.is_valid:
                continue
            edge = max(face.edges, key=lambda e: e.calc_length())
            if len(edge.link_faces) != 2:
                continue
            rotated = bmesh.utils.edge_rotate(edge, False)
            if rotated is not None:
                faces += list(rotated.link_faces)


def box_uv(bm, scale=1.0) -> None:
    """Box-project every face onto the plane its normal faces most (metres -> UV units)."""
    uv = bm.loops.layers.uv.verify()
    for face in bm.faces:
        n = face.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        for loop in face.loops:
            x, y, z = loop.vert.co
            if axis == 0:
                u, v = (y if n.x > 0 else -y), z
            elif axis == 1:
                u, v = (-x if n.y > 0 else x), z
            else:
                u, v = x, (y if n.z > 0 else -y)
            loop[uv].uv = (u * scale, v * scale)


# ------------------------------------------------------------------ helpers for kits


def fbm(p, octaves=3, persistence=0.5, lacunarity=2.0) -> float:
    """Fractal Perlin noise, roughly -1..1. Deterministic for a given point."""
    total, amp, norm = 0.0, 1.0, 0.0
    p = Vector(p)
    for _ in range(octaves):
        total += amp * noise.noise(p, noise_basis="PERLIN_ORIGINAL")
        norm += amp
        amp *= persistence
        p = p * lacunarity
    return total / norm


def seed_offset(rng) -> Vector:
    """A random point to sample noise around, so each seed sees a different patch of noise."""
    return Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)))


def smoothstep(e0, e1, x) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0))) if e1 != e0 else float(x >= e1)
    return t * t * (3 - 2 * t)


def bounds(faces):
    verts = {v for f in faces for v in f.verts}
    lo = Vector((min(v.co.x for v in verts), min(v.co.y for v in verts), min(v.co.z for v in verts)))
    hi = Vector((max(v.co.x for v in verts), max(v.co.y for v in verts), max(v.co.z for v in verts)))
    return lo, hi


def capsule_profile(radius, height, rings=4, z0=0.0):
    """(radius, z) points of an upright capsule from z0 to z0 + height, for MeshBuilder.lathe."""
    radius = min(radius, height / 2)
    pts = [(0.0, z0)]
    for i in range(1, rings + 1):
        a = -math.pi / 2 + (math.pi / 2) * i / rings
        pts.append((radius * math.cos(a), z0 + radius + radius * math.sin(a)))
    top = z0 + height - radius
    if top - (z0 + radius) > 1e-6:
        for i in range(0, rings):
            a = (math.pi / 2) * i / rings
            pts.append((radius * math.cos(a), top + radius * math.sin(a)))
    else:
        for i in range(1, rings):
            a = (math.pi / 2) * i / rings
            pts.append((radius * math.cos(a), top + radius * math.sin(a)))
    pts.append((0.0, z0 + height))
    return pts
