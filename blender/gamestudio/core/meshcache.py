# SPDX-License-Identifier: GPL-3.0-or-later
"""Meshes the glTF exporter extracted in earlier exports, reused while nothing they depend on changed
(docs/SPEC.md §4.6).

Most of the exporter's own time goes to primitive_extract.extract_primitives(): it splits a mesh's
corners into glTF vertices (positions, normals, UV maps, colours, joints and weights, morph
targets) and sorts the triangles by material. The exporter keeps the result for one export only,
so a full export re-extracted every mesh of the level, even after an edit to a single vertex.

While export._gltf() runs the exporter (armed()), extract_primitives is wrapped. Each call is keyed
by a hash of everything its result depends on, and a key seen before returns a copy of the earlier
result instead of extracting again:

- the mesh as the exporter passes it (the object's own data, or the exporter's temporary copy with
  the modifiers applied): element counts, face offsets, every attribute except selection and
  hiding state (positions, topology, UV maps, colours, material indices, sharp edges and faces,
  custom normals, custom attributes), the corner normals, the active UV and colour layers and
  the shape keys;
- the exporter's material info (the UV maps and colour attributes a material reads) of every
  material the faces use. The result holds slot indices, not materials, so objects that give a
  mesh other materials with the same info share it;
- for a skinned mesh: the skin's joint names, the object's vertex group names and every vertex's
  weights, and the object's and the armature's world matrices;
- the exporter settings extraction reads, the Blender and exporter versions and CACHE_VERSION.

The arrays are read with foreach_get and hashed in place (BLAKE2b). Vertex weights are the one
exception: Blender has no bulk accessor for them, so they come per vertex from a BMesh copy, which
hands them out as tuples (several times faster than MeshVertex.groups; still a fraction of what the
exporter spends on a skinned mesh).

Extraction calls skins.gather_skin() and materials.get_base_material(); the key calls them first,
with the same arguments and in the same order. Both are cached for the export, so a hit has the
side effects extraction would have had. The other side effect, the flag asking for a neutral bone
on the armature, is stored with the entry and set again on a hit. Calls that cannot be keyed with
certainty run the exporter's own code: UDIM materials, exporter extensions hooking attribute
gathering, attribute types with no known accessor, invalid meshes.

Results stay in memory (MAX_BYTES, the least recently used go first) and are handed out as copies,
since the exporter rewrites parts of them. armed() wraps extract_primitives only when the exporter's
extraction code is the one this module was checked against (KNOWN_SOURCES) and has the expected
signature; otherwise the exporter runs as it is, and the reason is logged once. GS_MESHCACHE=0 in
the environment turns the cache off (for comparisons; set_enabled() does it at run time).
"""

import collections
import contextlib
import enum
import hashlib
import importlib
import inspect
import marshal
import os
import pathlib
import sys
from operator import itemgetter, methodcaller

import bmesh
import bpy
import numpy as np

from .log import get_logger

log = get_logger(__name__)

CACHE_VERSION = 1
MAX_BYTES = 512 * 1024 * 1024
EXTRACTOR = "io_scene_gltf2.blender.exp.primitive_extract"
CALLER = "io_scene_gltf2.blender.exp.primitives"  # calls EXTRACTOR.extract_primitives through the module
SIGNATURE = ("materials", "blender_mesh", "uuid_for_skined_data", "blender_vertex_groups", "modifiers",
             "export_settings")
# _fingerprint() of the extraction code checked against this module: what it reads from Blender is
# what _key() hashes. Another exporter version runs uncached until it is checked and added here.
KNOWN_SOURCES = {
    "1c0a91b5e4da5cd0ebe53d1de8311805bf53ec56": "Blender 5.2.2 (glTF exporter 5.2.40)",
}
# The export settings extraction reads.
SETTINGS = ("gltf_normals", "gltf_tangents", "gltf_texcoords", "gltf_morph", "gltf_morph_normal",
            "gltf_morph_tangent", "gltf_materials", "gltf_skins", "gltf_attributes", "gltf_loose_edges",
            "gltf_loose_points", "gltf_shared_accessors", "gltf_vertex_color", "gltf_vertex_color_name",
            "gltf_active_vertex_color_when_no_material", "gltf_all_vertex_colors", "gltf_yup")
# Exporter extension hooks called during extraction: they could change anything.
HOOKS = ("gather_attribute_keep", "gather_attributes_change")
# Attributes holding selection and hiding state, which the exporter never reads.
UI_STATE = (".select_", ".uv_select_", ".hide_", ".vs.", ".es.", ".pn.")
# mesh.attributes data type -> (foreach_get property, values per element, buffer type). STRING data
# is never read by extraction (glTF has no string attributes): only its name counts.
VALUES = {
    "FLOAT": ("value", 1, np.float32), "INT": ("value", 1, np.int32), "INT8": ("value", 1, np.int32),
    "BOOLEAN": ("value", 1, np.bool_), "FLOAT2": ("vector", 2, np.float32),
    "FLOAT_VECTOR": ("vector", 3, np.float32), "FLOAT4": ("vector", 4, np.float32),
    "FLOAT_COLOR": ("color", 4, np.float32), "BYTE_COLOR": ("color", 4, np.float32),
    "INT32_2D": ("value", 2, np.int32), "INT16_2D": ("value", 2, np.int32),
    "QUATERNION": ("value", 4, np.float32), "FLOAT4X4": ("value", 16, np.float32), "STRING": None,
}
NEUTRAL_BONE = "need_neutral_bone"  # set on the armature's vtree node by extraction (hasattr counts)
_MARK = "_gamestudio_meshcache"

_enabled = os.environ.get("GS_MESHCACHE", "1") != "0"
_entries = collections.OrderedDict()  # key -> _Entry, least recently used first
_bytes = 0                            # what _entries hold (_Entry.nbytes)
_stats = collections.Counter()        # hits, misses, uncached, stored, evicted
_logged = set()                       # reasons already logged this session
_verdict = None                       # (module, function, problem or None, fingerprint): the last check


class _Uncacheable(Exception):
    """A call whose result the cache cannot key (or keep) with certainty."""


class _Entry:
    __slots__ = ("result", "nbytes", "neutral_bone")

    def __init__(self, result, nbytes: int, neutral_bone: bool):
        self.result = result
        self.nbytes = nbytes
        self.neutral_bone = neutral_bone


def _hash(data: bytes = b""):
    return hashlib.blake2b(data, digest_size=20)


# --- Public -----------------------------------------------------------------------------------


def set_enabled(value: bool) -> None:
    """Turn the cache on or off for later exports (off: armed() leaves the exporter alone)."""
    global _enabled
    _enabled = bool(value)


def clear() -> None:
    """Forget every entry and reset the counters."""
    global _bytes
    _entries.clear()
    _bytes = 0
    _stats.clear()


def stats() -> dict:
    """Counters since the last clear(): hits, misses, uncached, stored, evicted; entries and bytes held."""
    out = {name: _stats[name] for name in ("hits", "misses", "uncached", "stored", "evicted")}
    out.update(entries=len(_entries), bytes=_bytes)
    return out


def supported() -> str | None:
    """None when armed() can wrap the exporter, else why not."""
    try:
        module = importlib.import_module(EXTRACTOR)
        caller = importlib.import_module(CALLER)
    except ImportError:
        return "the glTF exporter is not installed"
    return _problem(module, caller, getattr(module, "extract_primitives", None))


@contextlib.contextmanager
def armed():
    """Wrap the exporter's extract_primitives with the cache for the duration (one exporter run).
    The original is always put back, whatever happens inside."""
    module = _extractor() if _enabled else None
    if module is None:
        yield
        return
    original = module.extract_primitives
    module.extract_primitives = _wrap(module, original)
    try:
        yield
    finally:
        module.extract_primitives = original


# --- Wrapping -----------------------------------------------------------------------------------


def _off(reason: str) -> None:
    if reason not in _logged:
        _logged.add(reason)
        log.info("Mesh cache off: %s. The glTF exporter extracts every mesh itself.", reason)


def _extractor():
    """The exporter's primitive_extract module when armed() can wrap it, else None."""
    try:
        module = importlib.import_module(EXTRACTOR)
        caller = importlib.import_module(CALLER)
    except ImportError:
        _off("the glTF exporter is not installed")
        return None
    function = getattr(module, "extract_primitives", None)
    if getattr(function, _MARK, False):
        return None  # already wrapped: an export inside an export (the outer armed() unwraps)
    problem = _problem(module, caller, function)
    if problem:
        _off(problem)
        return None
    return module


def _problem(module, caller, function) -> str | None:
    global _verdict
    if _verdict is not None and _verdict[0] is module and _verdict[1] is function:
        return _verdict[2]
    problem = fingerprint = None
    if (not inspect.isfunction(function) or function.__module__ != EXTRACTOR
            or function.__name__ != "extract_primitives"):
        problem = "the exporter's extract_primitives was replaced by another function"
    elif tuple(inspect.signature(function).parameters) != SIGNATURE:
        problem = f"the exporter's extract_primitives{inspect.signature(function)} has another signature"
    elif getattr(caller, "gltf2_blender_gather_primitives_extract", None) is not module:
        problem = "the exporter no longer calls extract_primitives through its module"
    else:
        try:
            fingerprint = _fingerprint(module)
        except (AttributeError, KeyError, OSError, TypeError) as exc:
            fingerprint, problem = None, f"the exporter's extraction code could not be read ({exc})"
        if fingerprint is not None and fingerprint not in KNOWN_SOURCES:
            problem = (f"the exporter's extraction code (fingerprint {fingerprint}) is not a version this "
                       "cache was checked against")
    _verdict = (module, function, problem, fingerprint)
    return problem


def _fingerprint(module) -> str:
    """A hash of the extraction code that reads Blender data: primitive_extract.py and the helpers it
    reads shape keys and attributes through."""
    h = _hash(pathlib.Path(module.__file__).read_bytes().replace(b"\r\n", b"\n"))
    data_path = sys.modules[module.get_sk_exported.__module__]
    for function in (module.get_sk_exported, data_path.skip_sk, data_path.is_first_index,
                     module.extract_attribute_data, module.gltf2_blender_conversion.get_attribute):
        h.update(inspect.getsource(function).encode("utf-8"))
    return h.hexdigest()


def _wrap(module, original):
    def extract_primitives(materials, blender_mesh, uuid_for_skined_data, blender_vertex_groups, modifiers,
                           export_settings):
        return _extract(module, original, (materials, blender_mesh, uuid_for_skined_data,
                                           blender_vertex_groups, modifiers, export_settings))

    setattr(extract_primitives, _MARK, True)
    extract_primitives.__wrapped__ = original
    return extract_primitives


def _extract(module, original, args):
    export_settings = args[-1]
    try:
        key, armature_uuid = _key(module, *args)
    except Exception as exc:  # _Uncacheable, or data the key could not read: the exporter's own code
        _stats["uncached"] += 1
        log.debug("Mesh cache: %s extracted uncached: %s", _name(args[1]), exc)
        return original(*args)
    entry = _entries.get(key)
    if entry is not None:
        _entries.move_to_end(key)
        _stats["hits"] += 1
        if entry.neutral_bone:
            setattr(export_settings["vtree"].nodes[armature_uuid], NEUTRAL_BONE, True)
        return _thaw(entry.result)
    _stats["misses"] += 1
    result, neutral_bone = _run(original, args, armature_uuid)
    try:
        size = [0]
        frozen = _freeze_result(result, size)
    except _Uncacheable as exc:
        _stats["uncached"] += 1
        log.debug("Mesh cache: %s not kept: %s", _name(args[1]), exc)
        return result
    _store(key, _Entry(frozen, size[0], neutral_bone))
    return result


def _run(original, args, armature_uuid):
    """Extract; for a skinned mesh, also report whether extraction asked for a neutral bone (the
    armature node's flag is taken off for the call and put back if extraction did not set it)."""
    if armature_uuid is None:
        return original(*args), False
    node = args[-1]["vtree"].nodes[armature_uuid]
    had = hasattr(node, NEUTRAL_BONE)
    saved = getattr(node, NEUTRAL_BONE, None)
    if had:
        delattr(node, NEUTRAL_BONE)
    try:
        result = original(*args)
        neutral_bone = hasattr(node, NEUTRAL_BONE)
    finally:
        if had and not hasattr(node, NEUTRAL_BONE):
            setattr(node, NEUTRAL_BONE, saved)
    return result, neutral_bone


def _name(mesh) -> str:
    try:
        return repr(mesh.name)
    except (AttributeError, ReferenceError):
        return "a mesh"


# --- The key ------------------------------------------------------------------------------------


def _key(module, materials, mesh, uuid, vertex_groups, modifiers, export_settings):
    """(the cache key of an extract_primitives call, the skin's armature node uuid or None).
    Raises _Uncacheable when the call cannot be keyed."""
    if not isinstance(mesh, bpy.types.Mesh):
        raise _Uncacheable(f"{type(mesh).__name__} data")
    for extension in export_settings["gltf_user_extensions"]:
        if any(getattr(extension, hook, None) is not None for hook in HOOKS):
            raise _Uncacheable(f"an exporter extension ({type(extension).__module__}) hooks attribute gathering")
    exporter = getattr(sys.modules.get("io_scene_gltf2"), "bl_info", {}).get("version")
    h = _hash(repr((CACHE_VERSION, tuple(bpy.app.version), exporter, _verdict[3] if _verdict else None,
                    tuple(export_settings[name] for name in SETTINGS))).encode("utf-8"))

    counts = (len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons))
    h.update(repr((counts, mesh.is_evaluated, mesh.get("gltf2_mesh_applied") is not None)).encode("utf-8"))
    starts = np.empty(counts[3], dtype=np.int32)
    totals = np.empty(counts[3], dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    if counts[3] and int(totals.min()) < 3:
        raise _Uncacheable("a face with fewer than three corners")
    h.update(starts)
    h.update(totals)
    material_index = _attributes(h, mesh)
    if export_settings["gltf_normals"]:
        normals = np.empty(counts[2] * 3, dtype=np.float32)
        mesh.corner_normals.foreach_get("vector", normals)
        h.update(normals)
    colors = mesh.color_attributes
    h.update(repr((tuple((uv.name, uv.active, uv.active_render) for uv in mesh.uv_layers),
                   tuple(c.name for c in colors), colors.render_color_index,
                   colors.active_color_index)).encode("utf-8"))
    _shape_keys(h, mesh)

    # Extraction gathers the skin first, then the material of every primitive: the same here.
    armature_uuid = _skin(h, module, mesh, uuid, vertex_groups, modifiers, export_settings)
    if export_settings["gltf_materials"] == "NONE":
        used = (-1,)  # one primitive for every triangle
    else:
        faces = material_index if material_index is not None else np.zeros(counts[3], dtype=np.int32)
        used = np.unique(faces.astype(np.uint32))  # the triangles' material indices, as extraction reads them
    h.update(repr(len(materials)).encode("utf-8"))
    for index in used:
        base, info = module.get_base_material(index, materials, export_settings)
        if info.get("udim_info"):
            raise _Uncacheable("a UDIM material")
        h.update(repr((int(index), base is None, _canonical(info))).encode("utf-8"))
    return h.digest(), armature_uuid


def _attributes(h, mesh):
    """Hash every attribute but UI state, in Blender's order; return the face material indices."""
    material_index = None
    for attribute in mesh.attributes:
        name = attribute.name
        if name.startswith(UI_STATE):
            continue
        data_type = attribute.data_type
        if data_type not in VALUES:
            raise _Uncacheable(f"attribute {name!r} of type {data_type}")
        h.update(f"{name}\0{attribute.domain}\0{data_type}\0".encode("utf-8"))
        if VALUES[data_type] is None:
            continue
        prop, width, dtype = VALUES[data_type]
        values = np.empty(len(attribute.data) * width, dtype=dtype)
        attribute.data.foreach_get(prop, values)
        h.update(values)
        if name == "material_index" and attribute.domain == "FACE" and data_type == "INT":
            material_index = values
    return material_index


def _shape_keys(h, mesh) -> None:
    keys = mesh.shape_keys
    if keys is None:
        h.update(b"no shape keys")
        return
    blocks = keys.key_blocks
    h.update(repr((keys.use_relative, len(blocks))).encode("utf-8"))
    for block in blocks:
        h.update(repr((block.name, block.mute, block.relative_key.name)).encode("utf-8"))
        co = np.empty(len(block.points) * 3, dtype=np.float32)
        block.points.foreach_get("co", co)
        h.update(co)


def _skin(h, module, mesh, uuid, vertex_groups, modifiers, export_settings):
    """Hash what a skinned mesh's result depends on (extraction's PrimitiveCreator.prepare_data()
    decides on the skin the same way); return the armature node's uuid, or None for no skin."""
    if not export_settings["gltf_skins"] or modifiers is None:
        h.update(b"no skin")
        return None
    nodes = export_settings["vtree"].nodes
    armature = None
    by_type = {m.type: m for m in modifiers}
    if "ARMATURE" in by_type:
        armature = by_type["ARMATURE"].object
    obj = nodes[uuid].blender_object if uuid else None
    if armature and obj and obj.parent_type == "BONE" and obj.parent.name == armature.name:
        armature = None  # parented to one of its bones: no skin
    skin = None
    if armature:
        armature_uuid = nodes[uuid].armature
        skin = module.gltf2_blender_gather_skins.gather_skin(armature_uuid, export_settings)
    if not skin:
        h.update(b"no skin")
        return None
    if hasattr(type(nodes[armature_uuid]), NEUTRAL_BONE):
        raise _Uncacheable("the exporter's armature nodes define need_neutral_bone")
    h.update(repr((tuple(joint.name for joint in skin.joints), tuple(g.name for g in vertex_groups))).encode("utf-8"))
    h.update(np.array(obj.matrix_world, dtype=np.float64, order="C"))
    h.update(np.array(armature.matrix_world, dtype=np.float64, order="C"))
    _weights(h, mesh)
    return armature_uuid


def _weights(h, mesh) -> None:
    """Hash every vertex's (vertex group, weight) pairs in Blender's order."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        layer = bm.verts.layers.deform.active
        weights = [] if layer is None else list(map(methodcaller("items"), map(itemgetter(layer), bm.verts)))
    finally:
        bm.free()
    h.update(marshal.dumps(weights, 2))  # version 2: no back-references, so equal data, equal bytes


def _canonical(value):
    """value (the exporter's material info) as nested tuples of plain values, dict keys sorted."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return tuple(sorted((str(k), _canonical(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_canonical(v) for v in value)
    raise _Uncacheable(f"material info holds a {type(value).__name__}")


# --- Entries ------------------------------------------------------------------------------------


class _List:
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = items


class _Dict:
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = items


class _Numbers:
    """A list of Python ints or floats, kept as an array (tolist() gives the same values back)."""
    __slots__ = ("array",)

    def __init__(self, array):
        self.array = array


_SCALARS = (type(None), bool, int, float, str, bytes, np.generic, enum.Enum)


def _freeze_result(result, size):
    """A private copy of extract_primitives' (primitives, UDIM materials, shared attributes)."""
    if not isinstance(result, tuple) or len(result) != 3:
        raise _Uncacheable("an unexpected result")
    if any(material is not None for material in result[1]):
        raise _Uncacheable("UDIM tiles")
    return _freeze(result, size)


def _freeze(value, size):
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise _Uncacheable("an object array")
        size[0] += value.nbytes + 112
        return value.copy(order="K")
    if isinstance(value, dict):
        size[0] += 64 + 64 * len(value)
        return _Dict(tuple((k, _freeze(v, size)) for k, v in value.items()))
    if isinstance(value, list):
        kinds = set(map(type, value))
        if kinds == {int} or kinds == {float}:  # joints and weights
            try:
                array = np.array(value, dtype=np.int64 if kinds == {int} else np.float64)
            except OverflowError:
                pass
            else:
                size[0] += array.nbytes + 112
                return _Numbers(array)
        size[0] += 56 + 8 * len(value)
        return _List(tuple(_freeze(v, size) for v in value))
    if isinstance(value, tuple):
        size[0] += 40 + 8 * len(value)
        return tuple(_freeze(v, size) for v in value)
    if isinstance(value, _SCALARS):
        return value
    raise _Uncacheable(f"a {type(value).__name__} in the result")


def _thaw(value):
    if isinstance(value, np.ndarray):
        return value.copy(order="K")
    if isinstance(value, _Dict):
        return {k: _thaw(v) for k, v in value.items}
    if isinstance(value, _Numbers):
        return value.array.tolist()
    if isinstance(value, _List):
        return [_thaw(v) for v in value.items]
    if isinstance(value, tuple):
        return tuple(_thaw(v) for v in value)
    return value


def _store(key: bytes, entry: _Entry) -> None:
    global _bytes
    if entry.nbytes > MAX_BYTES:
        return
    old = _entries.pop(key, None)
    if old is not None:
        _bytes -= old.nbytes
    _entries[key] = entry
    _bytes += entry.nbytes
    _stats["stored"] += 1
    while _bytes > MAX_BYTES and len(_entries) > 1:
        _old, dropped = _entries.popitem(last=False)
        _bytes -= dropped.nbytes
        _stats["evicted"] += 1
