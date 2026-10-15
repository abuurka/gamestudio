# Modelling with the kit toolkit (in `run_python`)

`run_python` code sees `bpy`, `C`, `D`, `Vector`, `Matrix`, `math` and `gs`, the GameStudio package. The kits are built with `gs.kits.geometry.MeshBuilder`, and so are your custom models. Everything is in metres, Z-up, with the front facing −Y. Build around the origin, with the floor at z = 0.

## MeshBuilder

```python
m = gs.kits.geometry.MeshBuilder()
s = m.slot("wood")                     # a material slot from a palette preset; m.slot("plastic", color="#3F7FD9")
m.box(lo, hi, s, matrix=None)          # an axis-aligned box from corner lo=(x,y,z) to corner hi
m.prism(profile, axis, a0, a1, s)      # extrude a 2D polygon (concave is fine) along "X", "Y" or "Z" from a0 to a1
                                       #   profile coordinates: (y,z) for X, (x,z) for Y, (x,y) for Z
m.lathe(profile, segments, s, smooth=True, angle=0.0, matrix=None,
        band_slots=None, band_smooth=None, cap_slots=(None, None))
                                       # revolve (radius, z) points, bottom to top, around Z.
                                       # r=0 gives a pole; open ends are capped
m.icosphere(subdivisions, s, smooth=False, deform=None, matrix=None)
                                       # unit sphere; deform(Vector) -> Vector reshapes it
m.add(verts, faces, s, smooth=False)   # raw shell: positions plus index tuples
m.smooth_angle = math.radians(40)      # smooth faces get hard edges above this angle
obj = m.to_object("Name", location=(x, y, z), collection="Props")
```

Helpers in `gs.kits.geometry`:
- `capsule_profile(radius, height, rings=4, z0=0)` gives a lathe profile;
- `fbm(point, octaves, persistence)` is noise from −1 to 1;
- `seed_offset(random.Random(seed))` gives a different patch of noise for each seed.

`to_object` makes the normals point outward, marks hard edges, adds box-projected UVs (1 unit per metre) and assigns the shared palette materials. Every shell should be closed. Overlapping shells are fine: they are one mesh and one entity.

Give a surface its own slot when it will move or react in the game (leaves apart from the trunk, the water in a fountain): a game shader attaches to one material (`set_material_shader`).

To move a part, pass `matrix=Matrix.Translation((x, y, z))`. Rotate it with `Matrix.Rotation(math.radians(a), 4, "X")`, composing as `T @ R`.

## Example: street lamp (about 170 tris)

```python
m = gs.kits.geometry.MeshBuilder()
iron, glow = m.slot("iron"), m.slot("emissive", color="#FFE2A0")
m.lathe([(0.12, 0.0), (0.09, 0.15), (0.05, 0.2), (0.04, 2.6)], 8, iron)                    # base and pole
m.box((-0.18, -0.18, 2.6), (0.18, 0.18, 2.66), iron)                                        # lamp floor
m.icosphere(2, glow, deform=lambda v: Vector((v.x * 0.13, v.y * 0.13, v.z * 0.16 + 2.82)))  # bulb
m.lathe([(0.22, 2.95), (0.0, 3.15)], 8, iron, smooth=False, angle=math.pi / 8)             # cap
result = m.to_object("StreetLamp", location=(4, 2, 0), collection="Props").name
```

## Example: treasure chest (about 120 tris)

```python
m = gs.kits.geometry.MeshBuilder()
wood, iron = m.slot("wood"), m.slot("iron")
w, d, h = 0.9, 0.55, 0.45
m.box((-w / 2, -d / 2, 0), (w / 2, d / 2, h), wood)                                         # body
arch = [(d / 2 * math.cos(a), h + d / 2 * math.sin(a)) for a in [math.pi * i / 6 for i in range(7)]]
m.prism(arch, "X", -w / 2, w / 2, wood)                                                     # rounded lid (y, z) profile
for x in (-0.3, 0.3):                                                                       # iron bands, slightly proud
    band = [(y * 1.04, (z - h) * 1.04 + h) for y, z in arch]
    m.prism(band, "X", x - 0.03, x + 0.03, iron)
    m.box((x - 0.03, -d / 2 - 0.01, 0), (x + 0.03, d / 2 + 0.01, h), iron)
m.box((-0.06, -d / 2 - 0.03, h - 0.12), (0.06, -d / 2, h + 0.02), iron)                    # lock, on the front (-Y)
result = m.to_object("TreasureChest", location=(0, 3, 0), collection="Props").name
```

Floating islands and round arenas don't need a custom model: use the `terrain` kit with `shape: "island"` or `"round"`. For a distant decorative islet, flip a `rock` kit (`set_transform` with `rotation_deg: [180, 0, 0]`) and put a small tree or bush on its flat face.

## A hill or a ramp from the terrain kit

The `terrain` kit has no slope option. Make a `square` terrain with its final params (low `height`, such as 0.5, for gentle bumps), then add a height function to its **top** vertices. The top is at z ≥ 0; the skirt and the bottom are below 0, and the bottom is a fan around one centre vertex, so lifting everything pokes that fan up through the grass.

```python
def ss(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def lift(x, y):  # 0 m up to y = -20, rising to 10 m at y = 15 (at most about 23°), full width for |x| < 10
    return 10 * ss((y + 20) / 35) * ss(1 - (abs(x) - 10) / 16)

hill = D.objects["Hill"]  # terrain kit, shape "square", size 80, at the origin
for v in hill.data.vertices:
    if v.co.z >= 0:       # the top only
        v.co.z += lift(v.co.x, v.co.y)
hill.data.update()
```

- Snap props again afterwards (`set_transform {"items": [...], "snap_to_ground": true}`), and give the hill `Collider {shape: "mesh"}`.
- `regenerate_kit` rebuilds the flat terrain and discards the lift; run the code again after it. The cliff material is not recomputed, so keep slopes under the kit's `cliff_angle` or accept grass on them.
- To tuck the tile's edges under a surrounding `round` terrain, subtract up to 1 m within 3 m of the border.

## Measuring

- Queries (sizes, counts, vertex positions) go in `run_python` with `read_only: true`: no undo step, and a change by mistake is refused and undone.
- `obj.dimensions` and bounding boxes are stale right after you change an existing object's mesh (`gs.kits.regenerate`, bmesh edits). Call `C.view_layer.update()` before reading them, or read them in the next call with `get_object`.
- To try a change and measure it without keeping it, end the code with `raise RuntimeError(str(result))`: the error carries your result, and the scene rolls back.

## Editing an existing mesh with bmesh

```python
import bmesh
obj = D.objects["Crate"]
bm = bmesh.new(); bm.from_mesh(obj.data)
bmesh.ops.bevel(bm, geom=list(bm.edges), offset=0.02, segments=1, affect="EDGES")
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
bm.to_mesh(obj.data); bm.free(); obj.data.update()
```

Kit objects can be rebuilt with `gs.kits.regenerate(obj, {...})`, which discards manual edits. Edit them by hand only once their params are final.

## Applying transforms without bpy.ops

```python
obj = D.objects["Imported"]
obj.data.transform(obj.matrix_basis)       # bake location, rotation and scale into the mesh
obj.matrix_basis.identity()
# Then put the origin at the bottom centre and move the object back:
lo = min(v.co.z for v in obj.data.vertices)
cx = sum(v.co.x for v in obj.data.vertices) / len(obj.data.vertices)
cy = sum(v.co.y for v in obj.data.vertices) / len(obj.data.vertices)
obj.data.transform(Matrix.Translation((-cx, -cy, -lo)))
obj.location = (cx, cy, lo)
```
