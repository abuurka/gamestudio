# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake procedural materials into image textures for export (docs/SPEC.md §4.6).

A glTF material is a Principled BSDF whose inputs are constants or image
textures. Node setups the exporter cannot translate (noise and Voronoi
textures, color ramps, mix and math nodes, bump, other shaders) would export
as flat colours, so their inputs are baked with Cycles:

- Principled inputs (Base Color, Metallic, Roughness, Emission) through an
  Emission shader (EMIT bake), the normal as a tangent-space normal map;
- any other surface approximately: diffuse colour, roughness and normal passes.

A bake covers one object: its slots that need baking share one image per
channel. The images use the object's evaluated UV map when that map is usable
(inside 0-1, no overlaps). Otherwise a Smart UV Project of the evaluated mesh
(the "proxy") is baked selected-to-active from the object itself, so
Generated and Object coordinates shade exactly as in Blender, and the proxy
replaces the object's mesh during the export.

Bakes are cached in <project>/.gamestudio/bake/<key>/ by a hash of the
materials, the evaluated geometry and the settings. The user's materials,
meshes and objects are never changed for good: export_swap() puts temporary
copies in place for the exporter and restores everything afterwards.

Finding the cached bakes is cheap: the hashes read the geometry from the
depsgraph the export evaluates anyway (objects it does not draw from a scene
of their own), once per evaluated mesh that objects share, and the Cycles
bake scene is made only when something needs baking (_Evaluation).
"""

import contextlib
import datetime
import hashlib
import json
import math
import os
import pathlib
import shutil
import time

import bpy

from .log import get_logger

log = get_logger(__name__)

BAKE_VERSION = 1
BAKE_UV = "GS_BakeUV"
BAKE_SCENE = "gs_bake"
CHANNELS = ("base_color", "metallic", "roughness", "normal", "emission")
COLOR_CHANNELS = ("base_color", "emission")
SCALAR_CHANNELS = ("metallic", "roughness")
# channel -> (Principled input, what the exporter accepts there; see _classify)
PRINCIPLED = {
    "base_color": ("Base Color", "color"),
    "metallic": ("Metallic", "scalar"),
    "roughness": ("Roughness", "scalar"),
    "normal": ("Normal", "normal"),
    "emission": ("Emission Color", "color"),
}
# glTF carries these through extensions; procedural values there are noted, not baked.
UNBAKED_INPUTS = ("Alpha", "Transmission Weight", "Coat Weight", "Sheen Weight", "Specular IOR Level")
# Nodes whose output depends on where the object is or where it is seen from.
WORLD_NODES = {"NEW_GEOMETRY", "OBJECT_INFO", "LIGHT_PATH", "AMBIENT_OCCLUSION", "BEVEL", "WIREFRAME", "CAMERA",
               "VECTOR_TRANSFORM", "LAYER_WEIGHT", "FRESNEL", "PARTICLE_INFO", "POINT_INFO"}
VIEW_COORDS = ("Window", "Camera", "Reflection")
NORMAL_SOURCES = {"BUMP", "NORMAL_MAP", "DISPLACEMENT", "VECTOR_DISPLACEMENT"}

DEFAULT_SETTINGS = {"bake": "auto", "bakeSize": 1024, "bakeDensity": 128, "bakeSamples": 4, "bakeBudgetMB": 64}
BAKE_MODES = ("auto", "cached", "off")
MIN_SIZE = 64
PACKING = 0.6           # share of a Smart UV Project layout covered by islands
MARGIN = 8              # px of edge padding around UV islands
MAX_AGE_DAYS = 30       # unused cache entries older than this are deleted


class BakeError(RuntimeError):
    pass


# --- Cycles ---------------------------------------------------------------------------


def cycles_available() -> bool:
    """True if this Blender build has the Cycles engine registered."""
    return any(getattr(cls, "bl_idname", "") == "CYCLES" for cls in bpy.types.RenderEngine.__subclasses__())


def require_cycles() -> None:
    if not cycles_available():
        raise BakeError("Baking procedural materials needs the Cycles render engine, which this Blender build "
                        "does not have (or the Cycles add-on is disabled in Preferences > Add-ons)")


# --- What the glTF exporter can take ---------------------------------------------------


def _link(socket):
    """(node, output socket) feeding an input socket through reroutes, or None."""
    for _ in range(64):
        if not socket.is_linked:
            return None
        link = socket.links[0]
        if link.is_muted or not link.is_valid:
            return None
        node = link.from_node
        if node.type != "REROUTE":
            return node, link.from_socket
        socket = node.inputs[0]
    return None


def _uv_source_ok(found) -> bool:
    if found is None:
        return True
    node, out = found
    if node.mute:
        return False
    return node.type == "UVMAP" or (node.type == "TEX_COORD" and out.name == "UV" and node.object is None)


def _image_ok(node) -> bool:
    """An Image Texture the exporter writes as is: a file or generated image on a UV map."""
    image = node.image
    if node.mute or image is None or image.source not in ("FILE", "GENERATED") or node.projection != "FLAT":
        return False
    found = _link(node.inputs["Vector"])
    if found is not None and found[0].type == "MAPPING":
        mapping = found[0]
        if mapping.mute or mapping.vector_type not in ("POINT", "TEXTURE"):
            return False
        if any(s.is_linked for s in mapping.inputs if s.name != "Vector"):
            return False
        found = _link(mapping.inputs["Vector"])
    return _uv_source_ok(found)


def _image_output(found, output="Color") -> bool:
    return found is not None and found[0].type == "TEX_IMAGE" and found[1].name == output and _image_ok(found[0])


def _classify(socket, kind: str) -> str:
    """"constant", "image" (exported as is) or "procedural" (needs baking)."""
    found = _link(socket)
    if found is None:
        return "constant"
    node, out = found
    if kind == "color":
        if _image_output(found) or (node.type == "VERTEX_COLOR" and not node.mute):
            return "image"
    elif kind == "scalar":
        if _image_output(found) or _image_output(found, "Alpha"):
            return "image"
        if node.type in ("SEPARATE_COLOR", "SEPRGB") and not node.mute and getattr(node, "mode", "RGB") == "RGB":
            if _image_output(_link(node.inputs[0])):
                return "image"
    elif kind == "normal":
        if (node.type == "NORMAL_MAP" and not node.mute and node.space == "TANGENT"
                and not node.inputs["Strength"].is_linked and _image_output(_link(node.inputs["Color"]))):
            return "image"
    elif kind == "alpha":
        if _image_output(found, "Alpha"):
            return "image"
    return "procedural"


def output_node(tree):
    """The material output the renderers use (active first)."""
    outputs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.target in ("ALL", "EEVEE", "CYCLES")]
    active = [n for n in outputs if n.is_active_output]
    return (active or outputs or [None])[0]


def _walk_nodes(tree, seen=None):
    seen = seen if seen is not None else set()
    if tree is None or tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        yield node
        if node.type == "GROUP":
            yield from _walk_nodes(node.node_tree, seen)


def _world_dependent(tree) -> bool:
    for node in _walk_nodes(tree):
        if node.type in WORLD_NODES:
            return True
        if node.type == "TEX_COORD" and (node.object is not None
                                         or any(s.is_linked for s in node.outputs if s.name in VIEW_COORDS)):
            return True
    return False


def _is_transparent_mix(node) -> bool:
    if node.type != "MIX_SHADER":
        return False
    shaders = [_link(s) for s in node.inputs[1:3]]
    return any(f is not None and f[0].type == "BSDF_TRANSPARENT" for f in shaders)


def analyze_material(mat) -> dict:
    """How the glTF exporter sees a material.

    status: "safe" (exports as it renders), "bake" (procedural inputs to bake).
    surface: "principled", "generic" (any other shader, baked approximately) or
    the native case ("emission", "background", "transparent", None).
    channels: what to bake, in CHANNELS order. notes: what baking cannot fix.
    """
    info = {"status": "safe", "surface": None, "node": None, "channels": [], "notes": [], "world": False,
            "strength_linked": False}
    tree = getattr(mat, "node_tree", None) if mat is not None else None
    if tree is None or not getattr(mat, "use_nodes", True):
        return info
    output = output_node(tree)
    found = _link(output.inputs["Surface"]) if output is not None else None
    if found is None:
        return info
    node = found[0]
    info["node"] = node.name
    channels = []
    if node.type == "BSDF_PRINCIPLED" and not node.mute:
        info["surface"] = "principled"
        for channel, (name, kind) in PRINCIPLED.items():
            socket = node.inputs.get(name)
            if socket is not None and _classify(socket, kind) == "procedural":
                channels.append(channel)
        strength = node.inputs.get("Emission Strength")
        if strength is not None and _link(strength) is not None:
            info["strength_linked"] = True
            if "emission" not in channels:
                channels.append("emission")
        if "normal" not in channels and _link(output.inputs["Displacement"]) is not None:
            channels.append("normal")
        for name in UNBAKED_INPUTS:
            socket = node.inputs.get(name)
            kind = "alpha" if name == "Alpha" else "scalar"
            if socket is not None and _classify(socket, kind) == "procedural":
                info["notes"].append(f"procedural {name} is not baked")
    elif node.type in ("EMISSION", "BACKGROUND") or _is_transparent_mix(node):
        info["surface"] = "transparent" if node.type == "MIX_SHADER" else node.type.lower()
    else:
        info["surface"] = "generic"
        types = {n.type for n in _walk_nodes(tree)}
        channels = ["base_color", "roughness"]
        if types & NORMAL_SOURCES or _link(output.inputs["Displacement"]) is not None:
            channels.append("normal")
        if types & {"EMISSION"} or any(n.type == "BSDF_PRINCIPLED" and _link(n.inputs["Emission Color"]) is not None
                                       for n in _walk_nodes(tree)):
            channels.append("emission")
        info["notes"].append(f"'{node.name}' is not a Principled BSDF: baked as colour, roughness and normal only")
    info["channels"] = [c for c in CHANNELS if c in channels]
    if info["channels"]:
        info["status"] = "bake"
        info["world"] = _world_dependent(tree)
    return info


def object_status(obj) -> list:
    """[(slot index, material, analysis)] for obj's materials that need baking (cheap; for the UI)."""
    return [(index, mat, info) for index, (mat, info) in _bake_slots(obj, {}).items()]


def _bake_slots(obj, analyses) -> dict:
    """{slot index: (material, analysis)} for obj's materials that need baking. analyses: material
    pointer -> analyze_material(), shared by the objects of one export."""
    slots = {}
    for index, slot in enumerate(getattr(obj, "material_slots", ())):
        mat = slot.material
        if mat is None:
            continue
        key = mat.as_pointer()
        if key not in analyses:
            analyses[key] = analyze_material(mat)
        if analyses[key]["status"] == "bake":
            slots[index] = (mat, analyses[key])
    return slots


# --- Digests ---------------------------------------------------------------------------


_SKIP_PROPS = {"rna_type", "name", "internal_links", "inputs", "outputs", "type"}
# Node layout and decoration (only on the nodes themselves: a color ramp element's color matters).
_LAYOUT_PROPS = {"label", "location", "location_absolute", "width", "height", "dimensions", "select", "hide",
                 "use_custom_color", "color", "color_tag", "parent", "warning_propagation"}
_COLLECTIONS = {"elements", "curves", "points"}


def _value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bpy.types.ID):
        return f"{type(value).__name__}:{value.name_full}"
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    try:
        return [_value(v) for v in value]
    except TypeError:
        return repr(value)


def _struct_digest(struct, memo, depth=0) -> list:
    out = []
    for prop in struct.bl_rna.properties:
        ident = prop.identifier
        if ident in _SKIP_PROPS or ident.startswith(("bl_", "show_")) or (depth == 0 and ident in _LAYOUT_PROPS):
            continue
        try:
            value = getattr(struct, ident)
        except Exception:
            continue
        if prop.type == "POINTER":
            if value is None or isinstance(value, bpy.types.ID):
                out.append((ident, _id_digest(value, memo)))
            elif depth < 3:
                out.append((ident, _struct_digest(value, memo, depth + 1)))
        elif prop.type == "COLLECTION":
            if depth < 3 and ident in _COLLECTIONS:
                out.append((ident, [_struct_digest(item, memo, depth + 1) for item in value]))
        elif not prop.is_readonly:
            out.append((ident, _value(value)))
    return out


def _file_stamp(path: str):
    try:
        st = os.stat(bpy.path.abspath(path))
    except (OSError, ValueError):
        return None
    return st.st_size, st.st_mtime_ns


def _id_digest(data, memo):
    if data is None:
        return None
    if isinstance(data, bpy.types.ShaderNodeTree):
        return _tree_digest(data, memo)
    if isinstance(data, bpy.types.Image):
        packed = data.packed_file
        return ("image", data.name_full, data.source, data.filepath, _file_stamp(data.filepath),
                packed.size if packed is not None else None, tuple(data.size), data.colorspace_settings.name,
                data.alpha_mode, data.is_dirty and time.time())  # unsaved paint: never cached
    if isinstance(data, bpy.types.Object):
        return "object", data.name_full, _rounded(data.matrix_world)
    return type(data).__name__, data.name_full


def _rounded(matrix) -> list:
    return [round(v, 5) for row in matrix for v in row]


def _tree_digest(tree, memo) -> str:
    key = tree.as_pointer()
    if key in memo:
        return memo[key]
    memo[key] = "cycle"
    nodes = []
    for node in sorted(tree.nodes, key=lambda n: n.name):
        if node.type == "FRAME":
            continue
        sockets = [(s.identifier, _value(s.default_value)) for s in node.inputs
                   if hasattr(s, "default_value") and not s.is_linked]
        nodes.append((node.name, node.bl_idname, _struct_digest(node, memo), sockets))
    links = sorted((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier,
                    link.is_muted) for link in tree.links)
    digest = hashlib.sha1(repr((nodes, links)).encode("utf-8")).hexdigest()
    memo[key] = digest
    return digest


def material_digest(mat, memo=None) -> str:
    """A hash of everything in a material's node tree that changes how it renders."""
    if mat is None or mat.node_tree is None:
        return "none"
    return _tree_digest(mat.node_tree, memo if memo is not None else {})


# --- Geometry -----------------------------------------------------------------------------


class _Mesh:
    """An evaluated mesh as numpy arrays (read once, then freed), with what its UV checks found. Objects
    whose evaluated mesh is the same one (linked duplicates without modifiers) share it: _shared_key."""

    def __init__(self, obj, depsgraph):
        import numpy as np

        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            self.loops = len(mesh.loops)
            co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", co)
            self.co = co.reshape(-1, 3)
            loop_verts = np.empty(self.loops, dtype=np.int32)
            mesh.loops.foreach_get("vertex_index", loop_verts)
            starts = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("loop_start", starts)
            mats = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", mats)
            tris = mesh.loop_triangles
            tri_loops = np.empty(len(tris) * 3, dtype=np.int32)
            tris.foreach_get("loops", tri_loops)
            tri_polys = np.empty(len(tris), dtype=np.int32)
            tris.foreach_get("polygon_index", tri_polys)
            self.tri_loops = tri_loops.reshape(-1, 3)
            self.tri_mats = mats[tri_polys] if len(tri_polys) else np.empty(0, dtype=np.int32)
            self.tri_verts = loop_verts[self.tri_loops] if len(tri_polys) else np.empty((0, 3), dtype=np.int32)
            render = [layer.name for layer in mesh.uv_layers if layer.active_render]
            active = mesh.uv_layers.active
            self.uv_name = (render or [active.name if active is not None else None])[0]
            self.uv = None
            if self.uv_name is not None:
                uv = np.empty(self.loops * 2, dtype=np.float32)
                mesh.attributes[self.uv_name].data.foreach_get("vector", uv)
                self.uv = uv.reshape(-1, 2)
            digest = hashlib.sha1()
            for part in (self.co, loop_verts, starts, mats):
                digest.update(part.tobytes())
            self.digest = digest.hexdigest()
        finally:
            evaluated.to_mesh_clear()
        self._masks, self._uv_checks = {}, {}

    def mask(self, slots):
        import numpy as np

        key = tuple(sorted(slots))
        if key not in self._masks:
            self._masks[key] = np.isin(self.tri_mats, np.array(key, dtype=np.int32))
        return self._masks[key]

    def uv_check(self, mask) -> tuple:
        """(problem, collapsed): the UV checks of the masked faces that do not depend on the object's size.
        problem is why the UV map cannot hold a bake ("" if it can); collapsed marks the faces whose UVs
        have no area (None when an earlier check failed: uv_problem tests them before the repeat check)."""
        import numpy as np

        key = np.packbits(mask).tobytes()
        if key not in self._uv_checks:
            self._uv_checks[key] = self._check_uvs(mask)
        return self._uv_checks[key]

    def _check_uvs(self, mask) -> tuple:
        import numpy as np

        if self.uv is None:
            return "it has no UV map", None
        tri = self.uv[self.tri_loops[mask]]
        if not len(tri):
            return "it has no faces", None
        if tri.min() < -1e-3 or tri.max() > 1.001:
            return "its UV map reaches outside the 0-1 square", None
        areas = _tri_areas(tri)
        if areas.sum() > 1.02:
            return "its UV map has overlapping faces", None
        collapsed = areas < 1e-10
        # Arrays and mirrors copy UVs exactly: repeated triangles overlap.
        keys = np.round(tri * 4096).astype(np.int32)
        order = np.lexsort((keys[:, :, 1], keys[:, :, 0]), axis=1)
        keys = np.take_along_axis(keys, order[:, :, None], axis=1).reshape(len(keys), 6)
        unique = len(np.unique(keys, axis=0))
        if unique < 0.99 * len(keys):
            return "its UV map has overlapping faces (repeated by a modifier?)", collapsed
        return "", collapsed


def _shared_key(obj, depsgraph):
    """What objects with the same evaluated mesh have in common (None if obj's own): the mesh, when
    nothing but the mesh makes the evaluated one (no modifiers or shape keys, Object Mode)."""
    if obj.modifiers or obj.data.shape_keys is not None or obj.mode != "OBJECT":
        return None
    return depsgraph.as_pointer(), obj.data.as_pointer()


class _Geometry:
    """The evaluated mesh of an object (a _Mesh, possibly shared with other objects) and its size in the
    world."""

    def __init__(self, obj, depsgraph, mesh=None):
        import numpy as np

        self.mesh = mesh if mesh is not None else _Mesh(obj, depsgraph)
        for name in ("loops", "co", "tri_loops", "tri_mats", "tri_verts", "uv_name", "uv", "digest"):
            setattr(self, name, getattr(self.mesh, name))
        world = np.asarray(obj.matrix_world, dtype=np.float64)
        points = self.co @ world[:3, :3].T + world[:3, 3]
        self.world_areas = _tri_areas(points[self.tri_verts]) if len(self.tri_verts) else np.empty(0)
        bounds = points.max(axis=0) - points.min(axis=0) if len(points) else np.zeros(3)
        self.diagonal = float(np.linalg.norm(bounds))

    def mask(self, slots):
        return self.mesh.mask(slots)

    def uv_problem(self, mask) -> str:
        """Why the evaluated UV map cannot hold a bake of the masked faces ("" if it can)."""
        problem, collapsed = self.mesh.uv_check(mask)
        if collapsed is not None:
            world = self.world_areas[mask]
            if world[collapsed].sum() > 0.02 * max(world.sum(), 1e-12):
                return "parts of its UV map are collapsed"
        return problem

    def uv_area(self, mask) -> float:
        return float(_tri_areas(self.uv[self.tri_loops[mask]]).sum()) if self.uv is not None else 0.0


def _tri_areas(tri):
    import numpy as np

    a = tri[:, 1] - tri[:, 0]
    b = tri[:, 2] - tri[:, 0]
    if tri.shape[-1] == 2:
        return 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    return 0.5 * np.linalg.norm(np.cross(a, b), axis=1)


def _orco_digest(obj) -> str:
    """Generated coordinates follow the original mesh: its vertices and texture space."""
    import numpy as np

    mesh = obj.data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    space = (tuple(mesh.texspace_location), tuple(mesh.texspace_size), mesh.use_auto_texspace)
    return hashlib.sha1(co.tobytes() + repr(space).encode()).hexdigest()


# --- Planning ----------------------------------------------------------------------------


class Unit:
    """One object's bake: the slots that need it, where and how big."""

    def __init__(self, obj):
        self.obj = obj
        self.name = obj.name
        self.slots = {}           # slot index -> (material, analysis)
        self.channels = []        # union over the slots, CHANNELS order
        self.strategy = None      # "uv": the evaluated UV map, "proxy": a Smart UV Project
        self.uv_name = None
        self.size = 0
        self.texels = 0.0
        self.key = None
        self.problem = ""         # why it cannot be baked
        self.uv_reason = ""       # why the evaluated UV map was not used
        self.meta = None          # cache entry once baked or found
        self.cached = False
        self.seconds = 0.0
        self.extrusion = 0.0
        self.loops = 0

    def describe(self) -> dict:
        return {"object": self.name, "materials": sorted({m.name for m, _ in self.slots.values()}),
                "channels": list(self.channels), "size": self.size, "uv": "existing" if self.strategy == "uv" else
                "generated", "cached": self.cached, "seconds": round(self.seconds, 2)}


def _settings(settings) -> dict:
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_SETTINGS})
    if merged["bake"] not in BAKE_MODES:
        merged["bake"] = "auto"
    return merged


def _instancers(depsgraph) -> set:
    """Original objects that emit instances (geometry nodes, particles, vertex/face instancing)."""
    found = set()
    for inst in depsgraph.object_instances:
        if inst.is_instance and inst.parent is not None:
            parent = inst.parent.original
            if parent.instance_type != "COLLECTION":
                found.add(parent.name_full)
    return found


def _proxy_problem(obj, instancers) -> str:
    if obj.data.shape_keys is not None:
        return "it has shape keys"
    if any(m.type == "ARMATURE" for m in obj.modifiers):
        return "it is deformed by an armature"
    if obj.name_full in instancers:
        return "its modifiers create instances"
    return ""


def plan(objects, depsgraph, settings, camera=None, analyses=None) -> list:
    """Units for the objects whose materials need baking. Geometry is read from depsgraph: a Depsgraph
    that evaluates every object, or a function object -> Depsgraph (_Evaluation.depsgraph)."""
    settings = _settings(settings)
    memo, analyses = {}, analyses if analyses is not None else {}
    meshes, orcos = {}, {}   # shared within this plan: _shared_key -> _Mesh, mesh pointer -> _orco_digest
    instancers = {}          # depsgraph pointer -> _instancers()
    units = []
    for obj in objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        slots = _bake_slots(obj, analyses)
        if not slots:
            continue
        unit = Unit(obj)
        unit.slots = slots
        unit.channels = [c for c in CHANNELS if any(c in info["channels"] for _m, info in slots.values())]
        units.append(unit)
        if obj.library is not None or obj.override_library is not None:
            unit.problem = "it is linked from a library (bake it in its own file)"
            continue
        graph = depsgraph(obj) if callable(depsgraph) else depsgraph
        shared = _shared_key(obj, graph)
        mesh = meshes.get(shared) if shared is not None else None
        geometry = _Geometry(obj, graph, mesh)
        if shared is not None:
            meshes[shared] = geometry.mesh
        mask = geometry.mask(slots)
        if not mask.any():
            unit.problem = "none of its faces use the material"
            continue
        unit.loops = geometry.loops
        unit.extrusion = max(geometry.diagonal * 5e-4, 1e-4)
        unit.uv_reason = geometry.uv_problem(mask)
        world = float(geometry.world_areas[mask].sum())
        if not unit.uv_reason:
            unit.strategy, unit.uv_name = "uv", geometry.uv_name
            share = max(geometry.uv_area(mask), 0.05)
        else:
            if graph.as_pointer() not in instancers:
                instancers[graph.as_pointer()] = _instancers(graph)
            problem = (_proxy_problem(obj, instancers[graph.as_pointer()])
                       or ("leave Edit Mode first" if obj.mode != "OBJECT" else ""))
            if problem:
                unit.problem = f"{unit.uv_reason} and it cannot be unwrapped at export ({problem}); add a UV map"
                continue
            unit.strategy, unit.uv_name = "proxy", BAKE_UV
            share = PACKING
        unit.texels = world * settings["bakeDensity"] ** 2 / share
        side = math.sqrt(max(unit.texels, 1.0))
        unit.size = int(min(max(2 ** math.ceil(math.log2(side)), MIN_SIZE), settings["bakeSize"]))
        unit.key = _unit_key(unit, geometry, memo, settings, camera, orcos)
    _apply_budget([u for u in units if not u.problem], settings)
    for unit in units:
        if unit.key is not None and not unit.problem:
            unit.key = hashlib.sha1(f"{unit.key}:{unit.size}".encode()).hexdigest()[:24]
    return units


def _unit_key(unit, geometry, memo, settings, camera, orcos=None) -> str:
    digest = hashlib.sha1()
    parts = [BAKE_VERSION, unit.strategy, unit.uv_name, settings["bakeSamples"], geometry.digest, MARGIN]
    for index in sorted(unit.slots):
        mat, info = unit.slots[index]
        parts.append((index, material_digest(mat, memo), info["channels"]))
        if info["world"]:
            parts.append(("world", _rounded(unit.obj.matrix_world),
                          _rounded(camera.matrix_world) if camera is not None else None))
    if unit.strategy == "uv":
        digest.update(geometry.uv.tobytes())
    orcos = orcos if orcos is not None else {}
    data = unit.obj.data.as_pointer()
    if data not in orcos:
        orcos[data] = _orco_digest(unit.obj)
    parts.append(orcos[data])
    digest.update(repr(parts).encode("utf-8"))
    return digest.hexdigest()


def _channel_size(size: int, channel: str) -> int:
    return max(size // 2, MIN_SIZE) if channel in SCALAR_CHANNELS else size


def unit_bytes(unit) -> int:
    """GPU memory of a unit's textures: RGBA8 plus mipmaps."""
    return int(sum(_channel_size(unit.size, c) ** 2 * 4 * 4 / 3 for c in unit.channels))


def _apply_budget(units, settings) -> None:
    """Halve the largest unit (by size, then bytes, then order) until the textures fit the budget."""
    import heapq

    budget = settings["bakeBudgetMB"] * 1024 * 1024
    if budget <= 0:
        return
    sizes = [unit_bytes(u) for u in units]
    total = sum(sizes)
    heap = [(-u.size, -sizes[i], i) for i, u in enumerate(units)]
    heapq.heapify(heap)
    while total > budget:
        largest = units[heap[0][2]] if heap else None
        if largest is None or largest.size <= MIN_SIZE:
            log.warning("Baked textures need %.0f MB, over export.bakeBudgetMB (%s)", total / 2 ** 20,
                        settings["bakeBudgetMB"])
            return
        index = heapq.heappop(heap)[2]
        largest.size //= 2
        total += unit_bytes(largest) - sizes[index]
        sizes[index] = unit_bytes(largest)
        heapq.heappush(heap, (-largest.size, -sizes[index], index))


# --- Cache -------------------------------------------------------------------------------


def _entry_dir(cache_dir, key) -> pathlib.Path:
    return pathlib.Path(cache_dir) / key


def _read_entry(cache_dir, unit):
    folder = _entry_dir(cache_dir, unit.key)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = [folder / c["file"] for c in meta.get("channels", {}).values()]
    if meta.get("version") != BAKE_VERSION or {*meta.get("channels", {}), *meta.get("flat", [])} != set(unit.channels):
        return None
    if meta.get("strategy") == "proxy":
        files.append(folder / "uv.npy")
    if not all(f.is_file() for f in files):
        return None
    try:
        os.utime(folder / "meta.json")  # last used, for pruning
    except OSError:
        pass
    meta["dir"] = str(folder)
    return meta


def prune(cache_dir, max_age_days: float = MAX_AGE_DAYS) -> int:
    """Delete cache entries not used for max_age_days. Returns how many were deleted."""
    root = pathlib.Path(cache_dir)
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for folder in root.iterdir():
        meta = folder / "meta.json"
        try:
            old = folder.is_dir() and meta.is_file() and meta.stat().st_mtime < cutoff
        except OSError:
            continue
        if old:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


# --- Baking ------------------------------------------------------------------------------


class _EvalScene:
    """A throw-away scene that links objects so that a depsgraph evaluates them the way the bake scene
    does (same frame and engine, nothing hidden in viewports), shown in no window."""

    hide = ("hide_viewport",)   # what keeps a linked object out of the depsgraph

    def __init__(self, frame: int):
        self.scene = bpy.data.scenes.new(BAKE_SCENE)
        if cycles_available():
            self.scene.render.engine = "CYCLES"
        self.scene.frame_current = frame
        self.view_layer = self.scene.view_layers[0]
        self.hidden = []
        self.linked = []

    def link(self, obj) -> None:
        if obj.name_full not in {o.name_full for o in self.scene.collection.objects}:
            self.scene.collection.objects.link(obj)
            self.linked.append(obj)
        for attr in self.hide:
            if getattr(obj, attr):
                self.hidden.append((obj, attr))
                setattr(obj, attr, False)

    def depsgraph(self):
        self.view_layer.update()
        with bpy.context.temp_override(scene=self.scene, view_layer=self.view_layer):
            return bpy.context.evaluated_depsgraph_get()

    def close(self) -> None:
        for obj, attr in reversed(self.hidden):
            setattr(obj, attr, True)
        self.hidden.clear()
        for obj in self.linked:
            try:
                self.scene.collection.objects.unlink(obj)
            except (RuntimeError, ReferenceError):
                pass
        bpy.data.scenes.remove(self.scene)


class _BakeScene(_EvalScene):
    """A throw-away Cycles scene that links the objects to bake (the window shows it meanwhile)."""

    hide = ("hide_viewport", "hide_render", "hide_select")

    def __init__(self, window, samples: int, frame: int):
        require_cycles()
        self.window = window
        self.saved_scene = window.scene
        super().__init__(frame)
        cycles = self.scene.cycles
        cycles.device = "CPU"
        cycles.samples = max(1, int(samples))
        for name, value in (("use_denoising", False), ("use_adaptive_sampling", False)):
            if hasattr(cycles, name):
                setattr(cycles, name, value)
        self.temp_objects = []
        self.dummy = None
        window.scene = self.scene  # the bake runs in the scene the window shows (planning needs none)

    def depsgraph(self):
        self.view_layer.update()
        with bpy.context.temp_override(window=self.window, scene=self.scene, view_layer=self.view_layer):
            return bpy.context.evaluated_depsgraph_get()

    def override(self, active, selected=(), edit=False) -> dict:
        kwargs = {"window": self.window, "scene": self.scene, "view_layer": self.view_layer,
                  "active_object": active, "object": active}
        if edit:
            kwargs["edit_object"] = active
        else:
            kwargs["selected_objects"] = list(selected)
            kwargs["selected_editable_objects"] = list(selected)
        return kwargs

    def select(self, active, selected) -> None:
        for obj in self.view_layer.objects:
            if obj.select_get(view_layer=self.view_layer):
                obj.select_set(False, view_layer=self.view_layer)
        for obj in selected:
            obj.select_set(True, view_layer=self.view_layer)
        self.view_layer.objects.active = active

    def dummy_material(self):
        """A material whose active image is a 1x1 throw-away: faces that are not baked write there."""
        if self.dummy is None:
            image = bpy.data.images.new("gs_bake_dummy", 1, 1)
            mat = bpy.data.materials.new("gs_bake_dummy")
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            mat.node_tree.nodes.active = node
            self.dummy = (mat, image)
        return self.dummy[0]

    def close(self) -> None:
        try:
            self.window.scene = self.saved_scene
        except (TypeError, ReferenceError):
            pass
        for obj, attr in reversed(self.hidden):
            setattr(obj, attr, True)
        self.hidden.clear()
        for obj in self.temp_objects:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        if self.dummy is not None:
            bpy.data.materials.remove(self.dummy[0])
            bpy.data.images.remove(self.dummy[1])
        super().close()


class _Evaluation:
    """Where planning reads evaluated geometry, and the bake scene when something needs baking.

    The objects the export's view layer draws are read from its depsgraph, which the export evaluates
    anyway: no second evaluation and no window switch. The others (hidden, disabled in viewports or not in
    the view layer, such as prefab members) are evaluated in an _EvalScene. The Cycles _BakeScene, with
    every object linked, is made by bake_scene() for the first unit that misses the cache.
    """

    def __init__(self, scene, window, objects, samples: int):
        from . import export

        self.scene, self.window, self.objects, self.samples = scene, window, objects, samples
        self.view_layer = export._export_view_layer(scene)
        self.drawn = {o.as_pointer() for o in objects if o.visible_get(view_layer=self.view_layer)}
        self.extra = None
        self.baking = None
        self.graphs = {}
        others = [o for o in objects if o.as_pointer() not in self.drawn]
        if others:
            self.extra = _EvalScene(scene.frame_current)
            try:
                for obj in others:
                    self.extra.link(obj)
            except BaseException:
                self.close()
                raise

    def depsgraph(self, obj):
        drawn = obj.as_pointer() in self.drawn
        if drawn not in self.graphs:
            if drawn:
                with bpy.context.temp_override(window=self.window, scene=self.scene, view_layer=self.view_layer):
                    self.graphs[drawn] = bpy.context.evaluated_depsgraph_get()
            else:
                self.graphs[drawn] = self.extra.depsgraph()
        return self.graphs[drawn]

    def bake_scene(self):
        if self.baking is None:
            self.baking = _BakeScene(self.window, self.samples, self.scene.frame_current)
            for obj in self.objects:
                self.baking.link(obj)
        return self.baking

    def finish(self) -> None:
        """Close the bake scene, if any (the depsgraphs are evaluated again on next use)."""
        self.graphs.clear()
        if self.baking is not None:
            self.baking, baking = None, self.baking
            baking.close()

    def close(self) -> None:
        try:
            self.finish()
        finally:
            if self.extra is not None:
                self.extra, extra = None, self.extra
                extra.close()


def _find_output(node, identifier):
    return next(s for s in node.outputs if s.identifier == identifier)


def _routed_copy(mat, info, channel):
    """A copy of mat whose surface is an Emission shader showing one Principled input (EMIT bake)."""
    copy = mat.copy()
    tree = copy.node_tree
    bsdf = tree.nodes[info["node"]]
    emission = tree.nodes.new("ShaderNodeEmission")
    if channel == "emission":
        color, strength = bsdf.inputs["Emission Color"], bsdf.inputs["Emission Strength"]
        for source, target in ((color, emission.inputs["Color"]), (strength, emission.inputs["Strength"])):
            found = _link(source)
            if found is not None:
                tree.links.new(_find_output(found[0], found[1].identifier), target)
            else:
                target.default_value = source.default_value
        if not info["strength_linked"]:
            emission.inputs["Strength"].default_value = 1.0  # the constant strength stays on the material
    else:
        found = _link(bsdf.inputs[PRINCIPLED[channel][0]])
        tree.links.new(_find_output(found[0], found[1].identifier), emission.inputs["Color"])
        emission.inputs["Strength"].default_value = 1.0
    output = output_node(tree)
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return copy


def _with_target(mat, image):
    """mat (a temporary copy) with an active Image Texture node: where Cycles writes the bake."""
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    mat.node_tree.nodes.active = node
    return mat


def _bake_pass(info, channel) -> tuple:
    """(Cycles bake type, pass filter, routed through an Emission shader)."""
    if channel == "normal":
        return "NORMAL", None, False
    if info["surface"] == "principled":
        return "EMIT", None, True
    if channel == "base_color":
        return "DIFFUSE", {"COLOR"}, False
    if channel == "roughness":
        return "ROUGHNESS", None, False
    return "EMIT", None, False


def _slot_state(obj, index):
    slot = obj.material_slots[index]
    link = slot.link
    if link == "DATA":
        slot.link = "OBJECT"
        own = slot.material
        slot.link = "DATA"
    else:
        own = slot.material
    return link, own


def _set_slot(obj, index, mat) -> None:
    slot = obj.material_slots[index]
    slot.link = "OBJECT"
    slot.material = mat


def _restore_slot(obj, index, state) -> None:
    slot = obj.material_slots[index]
    slot.link = "OBJECT"
    slot.material = state[1]
    slot.link = state[0]


def _smart_project(bs, obj) -> None:
    layer = obj.data.uv_layers.get(BAKE_UV) or obj.data.uv_layers.new(name=BAKE_UV)
    obj.data.uv_layers.active = layer
    bs.select(obj, [obj])
    with bpy.context.temp_override(**bs.override(obj, [obj])):
        bpy.ops.object.mode_set(mode="EDIT")
    try:
        with bpy.context.temp_override(**bs.override(obj, edit=True)):
            bpy.ops.mesh.reveal(select=False)
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02, area_weight=0.0,
                                     correct_aspect=True, scale_to_bounds=False)
    finally:
        with bpy.context.temp_override(**bs.override(obj, [obj])):
            bpy.ops.object.mode_set(mode="OBJECT")


def _uv_array(mesh, name):
    import numpy as np

    uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    mesh.attributes[name].data.foreach_get("vector", uv)
    return uv


def make_proxy(obj, depsgraph, uv=None):
    """A mesh of obj's evaluated geometry (modifiers applied), with the bake UV map (from uv if given)."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
    if uv is not None:
        if len(uv) != len(mesh.loops) * 2:
            bpy.data.meshes.remove(mesh)
            return None
        layer = mesh.uv_layers.get(BAKE_UV) or mesh.uv_layers.new(name=BAKE_UV)
        mesh.attributes[layer.name].data.foreach_set("vector", uv)
    return mesh


def _bake_unit(bs, unit, cache_dir, depsgraph) -> dict:
    """Bake one unit into a new cache entry; returns its meta."""
    import numpy as np

    started = time.perf_counter()
    obj = unit.obj
    folder = _entry_dir(cache_dir, unit.key)
    tmp_folder = folder.with_name(folder.name + ".tmp")
    shutil.rmtree(tmp_folder, ignore_errors=True)
    tmp_folder.mkdir(parents=True)
    images, copies, slot_states = {}, [], {}
    low = None
    try:
        if unit.strategy == "proxy":
            mesh = make_proxy(obj, depsgraph)
            low = bpy.data.objects.new(f"gs_bake_{obj.name}", mesh)
            bs.scene.collection.objects.link(low)
            bs.temp_objects.append(low)
            low.matrix_world = obj.matrix_world
            _smart_project(bs, low)
        count = max(len(obj.material_slots), 1)
        for channel in unit.channels:
            size = _channel_size(unit.size, channel)
            image = bpy.data.images.new(f"gs_bake_{channel}", size, size, alpha=False, float_buffer=False,
                                        is_data=channel not in COLOR_CHANNELS)
            if channel == "normal":
                image.generated_color = (0.5, 0.5, 1.0, 1.0)
            images[channel] = image
            # One bake per pass kind: Principled slots route through Emission, others use Cycles passes.
            groups = {}
            for index, (mat, info) in unit.slots.items():
                if channel in info["channels"]:
                    groups.setdefault(_bake_pass(info, channel), []).append(index)
            for (kind, pass_filter, routed), indices in groups.items():
                high, targets = {}, {}
                for index in range(len(obj.material_slots)):
                    if index in indices:
                        mat, info = unit.slots[index]
                        copy = _routed_copy(mat, info, channel) if routed else mat.copy()
                        copies.append(copy)
                        high[index] = copy
                    elif unit.strategy == "uv":
                        high[index] = bs.dummy_material()
                if unit.strategy == "uv":
                    for index in indices:
                        _with_target(high[index], image)
                else:
                    for index in range(count):
                        if index in indices:
                            target = _with_target(bpy.data.materials.new("gs_bake_target"), image)
                            copies.append(target)
                        else:
                            target = bs.dummy_material()
                        targets[index] = target
                    set_materials(low.data, [targets[i] for i in range(count)])
                for index, mat in high.items():
                    if index not in slot_states:
                        slot_states[index] = _slot_state(obj, index)
                    _set_slot(obj, index, mat)
                if unit.strategy == "uv":
                    _run_bake(bs, obj, [obj], kind, pass_filter, unit.uv_name)
                else:
                    _run_bake(bs, low, [obj, low], kind, pass_filter, BAKE_UV, extrusion=unit.extrusion)
                for index, state in slot_states.items():
                    _restore_slot(obj, index, state)
                slot_states.clear()
        meta = {"version": BAKE_VERSION, "key": unit.key, "object": obj.name, "strategy": unit.strategy,
                "uv": unit.uv_name, "loops": unit.loops, "channels": {}, "flat": [],
                "slots": {str(i): info["channels"] for i, (_m, info) in unit.slots.items()},
                "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in obj.name)[:40] or "object"
        for channel, image in images.items():
            if channel == "normal" and _is_flat_normal(image):
                meta["flat"].append(channel)  # a bump too small to show: no normal map
                continue
            path = tmp_folder / f"{stem}_{channel}.png"
            image.filepath_raw = str(path)
            image.file_format = "PNG"
            image.save()
            meta["channels"][channel] = {"file": path.name, "size": image.size[0]}
        if low is not None:
            np.save(tmp_folder / "uv.npy", _uv_array(low.data, BAKE_UV))
        unit.seconds = time.perf_counter() - started
        meta["seconds"] = round(unit.seconds, 3)
        (tmp_folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(tmp_folder, folder)
        meta["dir"] = str(folder)
        return meta
    finally:
        for index, state in slot_states.items():
            _restore_slot(obj, index, state)
        for image in images.values():
            bpy.data.images.remove(image)
        for copy in copies:
            bpy.data.materials.remove(copy)
        shutil.rmtree(tmp_folder, ignore_errors=True)


def set_materials(mesh, materials) -> None:
    """Set a mesh's material list in place (materials.clear() would reset every face to slot 0)."""
    while len(mesh.materials) > len(materials):
        mesh.materials.pop()
    for index, mat in enumerate(materials):
        if index < len(mesh.materials):
            mesh.materials[index] = mat
        else:
            mesh.materials.append(mat)


def _is_flat_normal(image) -> bool:
    import numpy as np

    pixels = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return bool(np.abs(pixels.reshape(-1, 4)[:, :3] - (0.5, 0.5, 1.0)).max() < 2.5 / 255)


def _run_bake(bs, active, selected, kind, pass_filter, uv_layer, extrusion=0.0) -> None:
    kwargs = {"type": kind, "margin": MARGIN, "margin_type": "EXTEND", "use_clear": False, "uv_layer": uv_layer,
              "target": "IMAGE_TEXTURES", "save_mode": "INTERNAL", "use_selected_to_active": extrusion > 0}
    if extrusion > 0:
        kwargs.update(cage_extrusion=extrusion, max_ray_distance=extrusion * 4)
    if kind == "NORMAL":
        kwargs["normal_space"] = "TANGENT"
    if pass_filter:
        kwargs["pass_filter"] = pass_filter
    bs.select(active, selected)
    with bpy.context.temp_override(**bs.override(active, selected)):
        result = bpy.ops.object.bake(**kwargs)
    if "FINISHED" not in result:
        raise BakeError(f"the Cycles {kind} bake returned {sorted(result)}")


def collect_objects(objects, depsgraph, extra=()) -> list:
    """What an export writes meshes for: objects (the view layer's), extra ones (prefab members), and
    the objects instanced by collection instances, geometry nodes and particles."""
    found = {}
    for obj in [*objects, *extra]:
        found.setdefault(obj.name_full, obj)
    valid = {obj.as_pointer() for obj in bpy.data.objects}
    for inst in depsgraph.object_instances:
        if not inst.is_instance:
            continue
        source = inst.object.original
        parent = inst.parent.original if inst.parent is not None else None
        if source != parent and isinstance(source, bpy.types.Object) and source.as_pointer() in valid:
            found.setdefault(source.name_full, source)
    return list(found.values())


def run(units, bs, cache_dir, allow_bake=True, force=False) -> None:
    """Find each unit's cache entry, baking the missing ones when allowed. bs: the _BakeScene, or a
    function that makes it (called for the first unit that needs baking)."""
    depsgraph = None
    entries = {}   # key -> cache entry: units with the same key (linked duplicates) share one
    for unit in units:
        if unit.problem or unit.key is None:
            continue
        if force:
            meta = None
        else:
            if unit.key not in entries:
                entries[unit.key] = _read_entry(cache_dir, unit)
            meta = entries[unit.key]
        if meta is not None:
            unit.meta, unit.cached = meta, True
            continue
        if not allow_bake:
            unit.problem = "it has not been baked yet (export.bake is \"cached\": use Bake Materials)"
            continue
        if depsgraph is None:
            bs = bs() if callable(bs) else bs
            depsgraph = bs.depsgraph()
        try:
            unit.meta = entries[unit.key] = _bake_unit(bs, unit, cache_dir, depsgraph)
        except (RuntimeError, OSError) as exc:
            unit.problem = f"the bake failed: {exc}"
            log.exception("Baking '%s' failed", unit.name)
    if depsgraph is not None:
        prune(cache_dir)  # new bakes were made: drop entries nothing used for a month


def _report_problems(units) -> list:
    problems = []
    for unit in units:
        if unit.problem:
            names = ", ".join(f"'{n}'" for n in sorted({m.name for m, _ in unit.slots.values()}))
            problems.append(f"Material {names} on '{unit.name}' is procedural and was not baked: {unit.problem}")
        for _mat, info in unit.slots.values():
            for note in info["notes"]:
                text = f"Material '{_mat.name}': {note}"
                if text not in problems:
                    problems.append(text)
    for problem in problems:
        log.warning(problem)
    return problems


def bake_objects(objects, scene=None, cache_dir=None, settings=None, force=False, window=None) -> dict:
    """Bake (or find in the cache) the procedural materials of objects, now.

    Returns {"units": [...], "baked": n, "cached": n, "problems": [...], "seconds": s}.
    Raises BakeError when something needs baking and Cycles is missing.
    """
    from .util import any_window

    settings = _settings(settings)
    scene = scene or bpy.context.scene
    window = window or any_window()
    if window is None:
        raise BakeError("Baking needs a Blender window")
    if cache_dir is None:
        raise BakeError("No bake cache folder (save the .blend inside a GameStudio project)")
    started = time.perf_counter()
    analyses = {}
    candidates = [o for o in objects if _bake_slots(o, analyses)]
    if not candidates:
        return {"units": [], "baked": 0, "cached": 0, "problems": [], "seconds": 0.0}
    require_cycles()
    evaluation = _Evaluation(scene, window, candidates, settings["bakeSamples"])
    try:
        units = plan(candidates, evaluation.depsgraph, settings, scene.camera, analyses)
        run(units, evaluation.bake_scene, cache_dir, allow_bake=True, force=force)
    finally:
        evaluation.close()
    return {"units": [u.describe() for u in units if u.meta is not None],
            "baked": sum(1 for u in units if u.meta is not None and not u.cached),
            "cached": sum(1 for u in units if u.cached), "problems": _report_problems(units),
            "seconds": round(time.perf_counter() - started, 3)}


# --- Export swap ------------------------------------------------------------------------


def _image(meta, channel):
    entry = meta["channels"][channel]
    image = bpy.data.images.load(str(pathlib.Path(meta["dir"]) / entry["file"]), check_existing=False)
    image.name = f"{meta['object']}_{channel}"
    image.colorspace_settings.name = "sRGB" if channel in COLOR_CHANNELS else "Non-Color"
    return image


def _baked_material(mat, info, channels, images, uv_name, flat=()):
    """An export copy of mat with the baked channels as image textures on uv_name (flat ones unlinked)."""
    copy = mat.copy()
    tree = copy.node_tree
    links = tree.links
    output = output_node(tree)
    if info["surface"] == "principled":
        bsdf = tree.nodes[info["node"]]
    else:
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Metallic"].default_value = 0.0
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    for channel in flat:
        target = bsdf.inputs[PRINCIPLED[channel][0]]
        for link in [*target.links, *(output.inputs["Displacement"].links if channel == "normal" else ())]:
            links.remove(link)
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_name
    for channel in channels:
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = images[channel]
        links.new(uv.outputs["UV"], tex.inputs["Vector"])
        target = bsdf.inputs[PRINCIPLED[channel][0]]
        for link in list(target.links):
            links.remove(link)
        if channel == "normal":
            normal_map = tree.nodes.new("ShaderNodeNormalMap")
            normal_map.space = "TANGENT"
            normal_map.uv_map = uv_name
            links.new(tex.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], target)
            for link in list(output.inputs["Displacement"].links):
                links.remove(link)
        else:
            links.new(tex.outputs["Color"], target)
        if channel == "emission" and (info["strength_linked"] or info["surface"] != "principled"):
            strength = bsdf.inputs["Emission Strength"]
            for link in list(strength.links):
                links.remove(link)
            strength.default_value = 1.0
    return copy


class _Swap:
    """Temporary changes for one export, undone in reverse order."""

    def __init__(self):
        self.undo = []
        self.meshes, self.materials, self.images = [], [], []
        self.material_names, self.mesh_names = {}, {}

    def set_data(self, obj, mesh) -> None:
        old = obj.data
        obj.data = mesh
        self.undo.append(lambda: setattr(obj, "data", old))

    def modifiers_off(self, obj) -> None:
        states = [(m, m.show_viewport) for m in obj.modifiers]
        for modifier, _shown in states:
            modifier.show_viewport = False
        self.undo.append(lambda: [setattr(m, "show_viewport", shown) for m, shown in states])

    def set_slot(self, obj, index, mat) -> None:
        state = _slot_state(obj, index)
        _set_slot(obj, index, mat)
        self.undo.append(lambda: _restore_slot(obj, index, state))

    def restore(self) -> None:
        for fn in reversed(self.undo):
            try:
                fn()
            except (RuntimeError, ReferenceError, AttributeError, TypeError) as exc:
                log.warning("Could not undo an export change: %s", exc)
        self.undo.clear()
        remove_all([*self.meshes, *self.materials, *self.images])
        for items in (self.meshes, self.materials, self.images):
            items.clear()


def remove_all(ids) -> None:
    """Delete temporary datablocks in one pass (remove() walks the whole file for each one); ones
    already deleted are skipped."""
    alive = []
    for item in ids:
        try:
            alive.append((item.id_type, item))
        except ReferenceError:
            pass
    if not alive:
        return
    try:
        bpy.data.batch_remove([item for _kind, item in alive])
    except (RuntimeError, ReferenceError, TypeError) as exc:
        log.warning("Could not remove temporary datablocks at once (%s): removing them one by one", exc)
        collections = {"MESH": bpy.data.meshes, "MATERIAL": bpy.data.materials, "IMAGE": bpy.data.images}
        for kind, item in alive:
            try:
                collections[kind].remove(item)
            except (RuntimeError, ReferenceError, KeyError):
                pass


def _exporter_materials(obj, instancers_or_sources) -> list:
    """The materials the glTF exporter takes for obj's slots: the mesh's own ones when modifiers are
    applied or obj is instanced, else the slots' (object-linked ones included)."""
    if obj.modifiers or obj.name_full in instancers_or_sources:
        return list(obj.data.materials) + [None] * max(0, len(obj.material_slots) - len(obj.data.materials))
    return [slot.material for slot in obj.material_slots]


@contextlib.contextmanager
def export_swap(scene, window, objects, settings, cache_dir, report, instanced=()):
    """Bake or load what objects need, put the baked materials in place for the exporter, restore after.

    objects: what the export writes meshes for (collect_objects). instanced: names of objects the
    exporter takes as instances. report is filled with {"mode", "baked", "cached", "units", "problems",
    "fixed", "seconds", "material_names", "mesh_names"}.
    """
    settings = _settings(settings)
    mode = settings["bake"]
    started = time.perf_counter()
    report.update(mode=mode, baked=0, cached=0, units=[], problems=[], fixed=[], seconds=0.0,
                  material_names={}, mesh_names={})
    swap = _Swap()
    units = []
    analyses = {}
    candidates = [o for o in objects if o.type == "MESH" and _bake_slots(o, analyses)] if mode != "off" else []
    proxies = {}
    try:
        if candidates and not cycles_available():
            names = sorted({m.name for o in candidates for _i, m, _a in object_status(o)})
            log.warning("Cycles is not available in this Blender, so %d procedural material(s) export as flat "
                        "colours: %s", len(names), ", ".join(names))
            candidates = []
        if candidates:
            try:
                units = _prepare(scene, window, candidates, settings, cache_dir, mode, swap, proxies, analyses)
            except Exception as exc:  # a failed bake must not cost the export
                log.exception("Baking failed")
                log.warning("Baking failed (%s): procedural materials export as flat colours", exc)
                units = []
                for mesh in swap.meshes:
                    bpy.data.meshes.remove(mesh)
                swap.meshes.clear()
                proxies.clear()
        report["problems"] = _report_problems(units)
        _swap_in(swap, objects, units, proxies, set(instanced), report)
        report["units"] = [u.describe() for u in units if u.meta is not None]
        report["baked"] = sum(1 for u in units if u.meta is not None and not u.cached)
        report["cached"] = sum(1 for u in units if u.cached)
        report["seconds"] = round(time.perf_counter() - started, 3)
        report["material_names"] = dict(swap.material_names)
        report["mesh_names"] = dict(swap.mesh_names)
        yield report
    finally:
        swap.restore()


def _prepare(scene, window, candidates, settings, cache_dir, mode, swap, proxies, analyses=None) -> list:
    """Plan and bake (or load) the candidates' units; build the proxy meshes for the export. When every
    unit is cached no bake scene is made (_Evaluation)."""
    import numpy as np

    evaluation = _Evaluation(scene, window, candidates, settings["bakeSamples"])
    try:
        units = plan(candidates, evaluation.depsgraph, settings, scene.camera, analyses)
        run(units, evaluation.bake_scene, cache_dir, allow_bake=mode == "auto")
        evaluation.finish()
        for unit in units:
            if unit.meta is not None and unit.strategy == "proxy":
                mesh = make_proxy(unit.obj, evaluation.depsgraph(unit.obj),
                                  np.load(pathlib.Path(unit.meta["dir"]) / "uv.npy"))
                if mesh is None:
                    unit.problem, unit.meta = "its cached bake no longer matches the mesh", None
                else:
                    swap.meshes.append(mesh)
                    proxies[unit.obj.name_full] = mesh
    finally:
        evaluation.close()
    return units


def _swap_in(swap, objects, units, proxies, instanced, report) -> None:
    by_object = {u.obj.name_full: u for u in units if u.meta is not None}
    images = {}
    shared = {}
    for obj in objects:
        if obj.type != "MESH" or obj.data is None or obj.library is not None or obj.override_library is not None:
            continue
        unit = by_object.get(obj.name_full)
        desired = [slot.material for slot in obj.material_slots]
        if unit is not None:
            if unit.key not in images:
                images[unit.key] = {c: _image(unit.meta, c) for c in unit.meta["channels"]}
                swap.images.extend(images[unit.key].values())
            loaded = images[unit.key]
            flat = unit.meta.get("flat", [])
            for index, (mat, info) in unit.slots.items():
                baked = _baked_material(mat, info, [c for c in info["channels"] if c in loaded], loaded,
                                        unit.uv_name, [c for c in info["channels"] if c in flat])
                swap.material_names[baked.name] = mat.name
                swap.materials.append(baked)
                desired[index] = baked
        exporter = _exporter_materials(obj, instanced)
        if desired == exporter and unit is None:
            continue
        if obj.mode != "OBJECT":
            log.warning("'%s' is in %s mode: its baked materials are not exported (leave the mode and export "
                        "again)", obj.name, obj.mode.title().replace("_", " "))
            continue
        if unit is None:
            report["fixed"].append(obj.name)
        proxy = proxies.get(obj.name_full)
        if proxy is not None:
            mesh = proxy
            swap.mesh_names[mesh.name] = obj.data.name
            swap.modifiers_off(obj)
        else:
            key = (obj.data.name_full, tuple(m.name_full if m else None for m in desired))
            mesh = shared.get(key)
            if mesh is None:
                mesh = obj.data.copy()
                swap.meshes.append(mesh)
                swap.mesh_names[mesh.name] = obj.data.name
                shared[key] = mesh
        set_materials(mesh, desired)
        swap.set_data(obj, mesh)
        for index, slot in enumerate(obj.material_slots):
            if slot.link == "OBJECT" and slot.material is not desired[index]:
                swap.set_slot(obj, index, desired[index])
