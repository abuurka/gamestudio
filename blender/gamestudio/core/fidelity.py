# SPDX-License-Identifier: GPL-3.0-or-later
"""GLB post-processing for export fidelity and size (docs/SPEC.md §4.6).

export.py runs these on the JSON and BIN of each GLB the exporter wrote:

- instance_meshes: repeated meshes under one parent (geometry-nodes scatters,
  collection instance contents) become one node with EXT_mesh_gpu_instancing,
  which three.js draws as an InstancedMesh: one draw call instead of one per copy.
- process_textures: images larger than export.maxTextureSize are downscaled,
  colour maps optionally re-encoded as WebP (EXT_texture_webp). Results are
  cached by a hash of the image bytes, so re-exports skip the work.
- collision_meshes: the meshes of collision-only objects (drawn: false) keep only
  welded positions and triangles, which is all a collider needs.
- prune: deletes what the scene does not use (nodes outside the scene tree, such
  as the objects of excluded collections, and the meshes, materials, textures,
  accessors and buffer views only they used) and compacts the BIN chunk.
- size_report: bytes per texture, mesh and animation, the largest items, and what
  the scene draws (triangles and draw calls of the nodes reachable from it).

The BIN chunk is rebuilt only when something changed.
"""

import collections
import hashlib
import os
import pathlib
import struct
import tempfile

import bpy

from .log import get_logger

log = get_logger(__name__)

CHUNK_BIN = 0x004E4942
FLOAT = 5126
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
COMPONENTS = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
TEXTURE_VERSION = 1
INSTANCING = "EXT_mesh_gpu_instancing"
WEBP = "EXT_texture_webp"
COLOR_TEXTURES = {"baseColorTexture", "emissiveTexture", "sheenColorTexture", "specularColorTexture",
                  "diffuseTexture"}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
TOP_ITEMS = 5


class Glb:
    """A GLB's JSON plus its BIN chunk, with buffer views that can be replaced or added."""

    def __init__(self, gltf: dict, chunks: list):
        self.gltf = gltf
        self.chunks = list(chunks)
        self.bin_at = next((i for i, (kind, _data) in enumerate(self.chunks) if kind == CHUNK_BIN), None)
        self.bin = self.chunks[self.bin_at][1] if self.bin_at is not None else b""
        self.replaced = {}

    def view(self, index: int) -> bytes:
        if index in self.replaced:
            return self.replaced[index]
        view = self.gltf["bufferViews"][index]
        start = view.get("byteOffset", 0)
        return self.bin[start:start + view["byteLength"]]

    def replace(self, index: int, data: bytes) -> None:
        self.replaced[index] = bytes(data)

    def add_accessor(self, values, kind: str, bounds: bool = False) -> int:
        """An accessor over a new buffer view: float32 VEC3/VEC4 rows, or uint16/uint32 indices
        (SCALAR). bounds adds min/max (required for POSITION). Returns its index."""
        import numpy as np

        views = self.gltf.setdefault("bufferViews", [])
        if not self.gltf.get("buffers"):
            self.gltf["buffers"] = [{"byteLength": 0}]
        if values.dtype.kind in "ui":
            component = UNSIGNED_INT if values.dtype.itemsize > 2 else UNSIGNED_SHORT
            data = values.astype("<u4" if component == UNSIGNED_INT else "<u2").tobytes()
        else:
            component, data = FLOAT, values.astype("<f4").tobytes()
        views.append({"buffer": 0, "byteOffset": 0, "byteLength": len(data)})
        self.replaced[len(views) - 1] = data
        accessors = self.gltf.setdefault("accessors", [])
        accessor = {"bufferView": len(views) - 1, "componentType": component, "count": len(values), "type": kind}
        if bounds and len(values):
            rows = np.asarray(values, dtype=np.float32).reshape(len(values), -1)
            accessor["min"] = [float(v) for v in rows.min(axis=0)]
            accessor["max"] = [float(v) for v in rows.max(axis=0)]
        accessors.append(accessor)
        return len(accessors) - 1

    def read_accessor(self, index: int):
        """An accessor's values as a numpy array (rows of its width), or None when it is sparse,
        normalized or has no buffer view."""
        import numpy as np

        accessor = self.gltf["accessors"][index]
        if "bufferView" not in accessor or accessor.get("sparse") or accessor.get("normalized"):
            return None
        dtype = np.dtype(COMPONENTS[accessor["componentType"]])
        width = WIDTHS[accessor["type"]]
        count = accessor["count"]
        data = self.view(accessor["bufferView"])
        stride = self.gltf["bufferViews"][accessor["bufferView"]].get("byteStride") or dtype.itemsize * width
        start = accessor.get("byteOffset", 0)
        if stride == dtype.itemsize * width:
            flat = np.frombuffer(data, dtype=dtype, count=count * width, offset=start)
            return flat.reshape(count, width) if width > 1 else flat.copy()
        rows = np.lib.stride_tricks.as_strided(np.frombuffer(data, dtype=np.uint8, offset=start),
                                               shape=(count, dtype.itemsize * width), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
        return out if width > 1 else out.reshape(count)

    def compact(self, keep_views: list) -> int:
        """Keep only the given buffer views (old indices, in order), packed into a new BIN chunk.
        Returns the bytes saved. The caller renumbers the references to the views."""
        views = self.gltf.get("bufferViews", [])
        before = len(self.bin)
        out = bytearray()
        kept = []
        for index in keep_views:
            view = dict(views[index])
            if view.get("buffer", 0) == 0:
                data = self.view(index)
                out += b"\0" * (-len(out) % 4)
                view["byteOffset"] = len(out)
                view["byteLength"] = len(data)
                out += data
            kept.append(view)
        out += b"\0" * (-len(out) % 4)
        self.gltf["bufferViews"] = kept
        if not kept:
            self.gltf.pop("bufferViews", None)
        if self.gltf.get("buffers"):
            self.gltf["buffers"][0]["byteLength"] = len(out)
        self.replaced = {}
        self.bin = bytes(out)
        if self.bin_at is not None:
            self.chunks[self.bin_at] = (CHUNK_BIN, self.bin)
        elif out:
            self.chunks.append((CHUNK_BIN, self.bin))
            self.bin_at = len(self.chunks) - 1
        return before - len(out)

    def finish(self) -> list:
        """The chunks, with a rebuilt BIN chunk if any view changed."""
        if not self.replaced:
            return self.chunks
        out = bytearray()
        for index, view in enumerate(self.gltf.get("bufferViews", [])):
            if view.get("buffer", 0) != 0:
                continue
            data = self.view(index)
            out += b"\0" * (-len(out) % 4)
            view["byteOffset"] = len(out)
            view["byteLength"] = len(data)
            out += data
        out += b"\0" * (-len(out) % 4)
        self.gltf["buffers"][0]["byteLength"] = len(out)
        chunks = list(self.chunks)
        if self.bin_at is None:
            chunks.append((CHUNK_BIN, bytes(out)))
        else:
            chunks[self.bin_at] = (CHUNK_BIN, bytes(out))
        self.chunks, self.bin, self.replaced = chunks, bytes(out), {}
        self.bin_at = next(i for i, (kind, _data) in enumerate(chunks) if kind == CHUNK_BIN)
        return chunks


def _use_extension(gltf: dict, name: str, required: bool = False) -> None:
    keys = ("extensionsUsed", "extensionsRequired") if required else ("extensionsUsed",)
    for key in keys:
        names = gltf.setdefault(key, [])
        if name not in names:
            names.append(name)


# --- GPU instancing ------------------------------------------------------------------------


def _mirrored(node) -> bool:
    scale = node.get("scale")
    return scale is not None and scale[0] * scale[1] * scale[2] <= 0


def instance_meshes(glb: Glb, min_count: int) -> list:
    """Replace groups of >= min_count leaf siblings that share a mesh with one instanced node.

    Only nodes that are not entities (no extras.gs_id), not animated, not skinned and not mirrored
    qualify. Returns [{"parent", "mesh", "count"}].
    """
    import numpy as np

    gltf = glb.gltf
    nodes = gltf.get("nodes", [])
    if min_count < 2 or not nodes:
        return []
    animated = {c.get("target", {}).get("node") for a in gltf.get("animations", []) for c in a.get("channels", [])}
    joints = {j for s in gltf.get("skins", []) for j in [*s.get("joints", []), s.get("skeleton")]}

    def candidate(index: int) -> bool:
        node = nodes[index]
        return ("mesh" in node and not node.get("children") and not node.get("extensions")
                and not any(k in node for k in ("skin", "camera", "matrix", "weights"))
                and not (node.get("extras") or {}).get("gs_id") and index not in animated
                and index not in joints and not _mirrored(node))

    meshes = gltf.get("meshes", [])
    removed, report = set(), []
    for parent in list(nodes):
        groups = {}
        for child in parent.get("children", []):
            if candidate(child):
                groups.setdefault(nodes[child]["mesh"], []).append(child)
        for mesh, members in groups.items():
            if len(members) < min_count:
                continue
            members_set = set(members)

            def column(key, default):
                return np.array([nodes[m].get(key, default) for m in members], dtype=np.float32)

            rotation, scale = column("rotation", [0, 0, 0, 1]), column("scale", [1, 1, 1])
            attributes = {"TRANSLATION": glb.add_accessor(column("translation", [0, 0, 0]), "VEC3")}
            if not np.allclose(rotation, [0, 0, 0, 1]):
                attributes["ROTATION"] = glb.add_accessor(rotation, "VEC4")
            if not np.allclose(scale, 1.0):
                attributes["SCALE"] = glb.add_accessor(scale, "VEC3")
            mesh_name = meshes[mesh].get("name", f"mesh {mesh}") if mesh < len(meshes) else f"mesh {mesh}"
            nodes.append({"name": f"{parent.get('name', 'node')}.{mesh_name}.instances", "mesh": mesh,
                          "extensions": {INSTANCING: {"attributes": attributes}}})
            parent["children"] = [c for c in parent["children"] if c not in members_set] + [len(nodes) - 1]
            removed |= members_set
            report.append({"parent": parent.get("name"), "mesh": mesh_name, "count": len(members)})
    if removed:
        compact_nodes(gltf, removed)
        _use_extension(gltf, INSTANCING)
    return report


def compact_nodes(gltf: dict, removed: set) -> dict:
    """Delete nodes (no longer referenced) and renumber every node reference. Returns old -> new."""
    remap, kept = {}, []
    for index, node in enumerate(gltf.get("nodes", [])):
        if index not in removed:
            remap[index] = len(kept)
            kept.append(node)
    gltf["nodes"] = kept
    for node in kept:
        if "children" in node:
            node["children"] = [remap[c] for c in node["children"] if c in remap]
    for scene in gltf.get("scenes", []):
        scene["nodes"] = [remap[i] for i in scene.get("nodes", []) if i in remap]
    for skin in gltf.get("skins", []):
        skin["joints"] = [remap[j] for j in skin.get("joints", [])]
        if "skeleton" in skin:
            skin["skeleton"] = remap[skin["skeleton"]]
    for anim in gltf.get("animations", []):
        for channel in anim.get("channels", []):
            target = channel.get("target", {})
            if "node" in target:
                target["node"] = remap[target["node"]]
    return remap


# --- What the scene uses ----------------------------------------------------------------------

# Extensions whose references prune() follows. Another extension could point at nodes, accessors or
# views it does not see, so a GLB that uses one is left as it is.
PRUNE_SAFE = {INSTANCING, WEBP, "KHR_lights_punctual", "KHR_texture_transform", "KHR_mesh_quantization",
              "KHR_materials_variants", "KHR_texture_basisu", "EXT_texture_avif", "KHR_draco_mesh_compression"}


def scene_nodes(gltf: dict, hidden_ids=(), scene=None) -> list:
    """[(node index, drawn)] for the nodes reachable from a scene's root nodes (the default scene),
    parents first. drawn is False from a node whose extras.gs_id is in hidden_ids (a collision-only
    entity) down to the next node of another entity."""
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes", [])
    if not scenes:
        return []
    index = gltf.get("scene", 0) if scene is None else scene
    hidden_ids = set(hidden_ids)
    out, seen = [], set()
    stack = [(i, True) for i in reversed(scenes[index].get("nodes", []))]
    while stack:
        i, drawn = stack.pop()
        if i in seen or not isinstance(i, int) or not 0 <= i < len(nodes):
            continue
        seen.add(i)
        extras = nodes[i].get("extras")
        gs_id = extras.get("gs_id") if isinstance(extras, dict) else None
        if gs_id:
            drawn = gs_id not in hidden_ids
        out.append((i, drawn))
        stack.extend((c, drawn) for c in reversed(nodes[i].get("children", [])))
    return out


def _texture_refs(value, found: list) -> None:
    """Collect the textureInfo dicts ({"index": n, ...}) under keys ending in "Texture"."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("Texture") and isinstance(item, dict) and isinstance(item.get("index"), int):
                found.append(item)
            _texture_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _texture_refs(item, found)


def prune(glb: Glb) -> dict:
    """Delete what the scenes do not use and compact the BIN chunk.

    Nodes outside every scene's tree go (the exporter writes the objects of excluded collections,
    and prefab instance contents are detached from their instancers), with the animation channels
    that target them; then the skins, meshes, materials, textures, images, samplers, accessors and
    buffer views that nothing kept refers to. Returns the counts removed by kind, plus "bytes" saved
    ({} when the GLB uses an extension prune does not know).
    """
    gltf = glb.gltf
    if not gltf.get("scenes"):
        return {}
    unknown = [n for n in gltf.get("extensionsUsed", []) if n not in PRUNE_SAFE and not n.startswith("KHR_materials_")]
    if unknown:
        log.debug("Not pruning: the GLB uses %s", ", ".join(unknown))
        return {}
    removed = {}
    nodes = gltf.get("nodes", [])
    skins = gltf.get("skins", [])
    keep = set()
    for scene in range(len(gltf["scenes"])):
        keep.update(i for i, _drawn in scene_nodes(gltf, scene=scene))
    while True:  # the joints of kept skins stay, with what is under them
        joints = {j for i in keep if "skin" in nodes[i]
                  for j in [*skins[nodes[i]["skin"]].get("joints", []), skins[nodes[i]["skin"]].get("skeleton")]
                  if j is not None} - keep
        if not joints:
            break
        keep.update(i for i, _drawn in scene_nodes({"nodes": nodes, "scenes": [{"nodes": sorted(joints)}]}))

    def reduce(key: str, refs: list) -> None:
        """Keep the items of gltf[key] that refs ([(holder, field)]) point at, and renumber refs."""
        items = gltf.get(key, [])
        used = sorted({holder[field] for holder, field in refs})
        if len(used) == len(items):
            return
        remap = {old: new for new, old in enumerate(used)}
        removed[key] = len(items) - len(used)
        for holder, field in refs:
            holder[field] = remap[holder[field]]
        if used:
            gltf[key] = [items[i] for i in used]
        else:
            gltf.pop(key, None)

    gone = set(range(len(nodes))) - keep
    if gone:
        animations = []
        for anim in gltf.get("animations", []):
            channels = [c for c in anim.get("channels", []) if c.get("target", {}).get("node") not in gone]
            if not channels:
                continue
            used = sorted({c["sampler"] for c in channels})
            remap = {old: new for new, old in enumerate(used)}
            anim["samplers"] = [anim["samplers"][i] for i in used]
            for channel in channels:
                channel["sampler"] = remap[channel["sampler"]]
            anim["channels"] = channels
            animations.append(anim)
        if len(animations) < len(gltf.get("animations", [])):
            removed["animations"] = len(gltf.get("animations", [])) - len(animations)
        if animations:
            gltf["animations"] = animations
        else:
            gltf.pop("animations", None)
        reduce("skins", [(nodes[i], "skin") for i in sorted(keep) if "skin" in nodes[i]])
        compact_nodes(gltf, gone)
        removed["nodes"] = len(gone)
    nodes = gltf.get("nodes", [])

    reduce("meshes", [(n, "mesh") for n in nodes if "mesh" in n])
    prims = [p for m in gltf.get("meshes", []) for p in m.get("primitives", [])]
    refs = [(p, "material") for p in prims if "material" in p]
    for prim in prims:
        variants = (prim.get("extensions") or {}).get("KHR_materials_variants") or {}
        refs += [(m, "material") for m in variants.get("mappings", []) if "material" in m]
    reduce("materials", refs)

    infos = []
    _texture_refs(gltf.get("materials", []), infos)
    reduce("textures", [(t, "index") for t in infos])
    textures = gltf.get("textures", [])
    refs = [(t, "source") for t in textures if "source" in t]
    refs += [(ext, "source") for t in textures for ext in (t.get("extensions") or {}).values()
             if isinstance(ext, dict) and "source" in ext]
    reduce("images", refs)
    reduce("samplers", [(t, "sampler") for t in textures if "sampler" in t])

    refs = []
    for prim in prims:
        refs += [(prim["attributes"], k) for k in prim.get("attributes", {})]
        if "indices" in prim:
            refs.append((prim, "indices"))
        for target in prim.get("targets", []):
            refs += [(target, k) for k in target]
    refs += [(skin, "inverseBindMatrices") for skin in gltf.get("skins", []) if "inverseBindMatrices" in skin]
    for anim in gltf.get("animations", []):
        for sampler in anim.get("samplers", []):
            refs += [(sampler, k) for k in ("input", "output") if k in sampler]
    for node in nodes:
        attributes = ((node.get("extensions") or {}).get(INSTANCING) or {}).get("attributes") or {}
        refs += [(attributes, k) for k in attributes]
    reduce("accessors", refs)

    refs = []
    for accessor in gltf.get("accessors", []):
        if "bufferView" in accessor:
            refs.append((accessor, "bufferView"))
        sparse = accessor.get("sparse")
        if sparse:
            refs += [(sparse["indices"], "bufferView"), (sparse["values"], "bufferView")]
    refs += [(image, "bufferView") for image in gltf.get("images", []) if "bufferView" in image]
    for prim in prims:
        refs += [(ext, "bufferView") for ext in (prim.get("extensions") or {}).values()
                 if isinstance(ext, dict) and "bufferView" in ext]
    used = sorted({holder[field] for holder, field in refs})
    if len(used) < len(gltf.get("bufferViews", [])):
        removed["bufferViews"] = len(gltf["bufferViews"]) - len(used)
        remap = {old: new for new, old in enumerate(used)}
        removed["bytes"] = glb.compact(used)
        for holder, field in refs:
            holder[field] = remap[holder[field]]
    return removed


def collision_meshes(glb: Glb, hidden_ids) -> list:
    """The meshes of collision-only entities (extras.gs_id in hidden_ids, and the nodes under them up
    to the next entity) become one primitive of welded positions and triangles, without material,
    normals or UVs: a collider needs nothing else, and nothing draws them. Meshes that a drawn node
    also uses, skinned or morphing meshes and non-triangle primitives are left alone. Returns the
    names of the meshes rewritten."""
    import numpy as np

    gltf = glb.gltf
    if not hidden_ids:
        return []
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    reachable = scene_nodes(gltf, hidden_ids)
    hidden = {i for i, drawn in reachable if not drawn}
    users = {}
    for index, node in enumerate(nodes):
        if "mesh" in node:
            users.setdefault(node["mesh"], []).append(index)
    rewritten = []
    for mesh_index, using in sorted(users.items()):
        mesh = meshes[mesh_index]
        prims = mesh.get("primitives", [])
        if (any(i not in hidden or "skin" in nodes[i] or "weights" in nodes[i] for i in using) or not prims
                or any(p.get("mode", 4) != 4 or p.get("targets") or p.get("extensions")
                       or "POSITION" not in p.get("attributes", {}) for p in prims)):
            continue
        positions, triangles, base = [], [], 0
        for prim in prims:
            pos = glb.read_accessor(prim["attributes"]["POSITION"])
            idx = glb.read_accessor(prim["indices"]) if "indices" in prim else None
            if pos is None or pos.dtype != np.float32 or pos.ndim != 2 or ("indices" in prim and idx is None):
                break
            idx = np.arange(len(pos), dtype=np.int64) if idx is None else idx.astype(np.int64)
            positions.append(pos)
            triangles.append(idx + base)
            base += len(pos)
        else:
            if not base:
                continue
            welded, inverse = np.unique(np.concatenate(positions), axis=0, return_inverse=True)
            indices = inverse.reshape(-1)[np.concatenate(triangles)]
            indices = indices.astype(np.uint16 if len(welded) < 65536 else np.uint32)
            mesh["primitives"] = [{"attributes": {"POSITION": glb.add_accessor(welded, "VEC3", bounds=True)},
                                   "indices": glb.add_accessor(indices, "SCALAR")}]
            rewritten.append(mesh.get("name") or f"mesh {mesh_index}")
    return rewritten


# --- Textures --------------------------------------------------------------------------


def image_size(data: bytes):
    """(width, height) of PNG, JPEG or WebP bytes, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF or marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
                i += 1 if marker == 0xFF else 2
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if kind == b"VP8L":
            bits = struct.unpack("<I", data[21:25])[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    return None


def _sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def texture_image(texture: dict):
    ext = texture.get("extensions") or {}
    for name in (WEBP, "EXT_texture_avif", "KHR_texture_basisu"):
        if name in ext:
            return ext[name].get("source")
    return texture.get("source")


def texture_usage(gltf: dict) -> dict:
    """image index -> {"color", "data"}: how materials sample it."""
    textures = gltf.get("textures", [])
    usage = {}

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("Texture") and isinstance(item, dict) and isinstance(item.get("index"), int):
                    if 0 <= item["index"] < len(textures):
                        source = texture_image(textures[item["index"]])
                        if source is not None:
                            usage.setdefault(source, set()).add("color" if key in COLOR_TEXTURES else "data")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(gltf.get("materials", []))
    return usage


def convert_image(data: bytes, mime: str, size: tuple, fmt: str, quality: int, colorspace: str,
                  cache_dir=None) -> bytes:
    """data re-encoded as fmt ("PNG", "JPEG", "WEBP") at size, through Blender's image IO (cached)."""
    key = hashlib.sha1(data + f"{size}:{fmt}:{quality}:{TEXTURE_VERSION}".encode()).hexdigest()[:24]
    out_ext = MIME_EXT[FORMATS[fmt]]
    cached = pathlib.Path(cache_dir) / f"{key}{out_ext}" if cache_dir is not None else None
    if cached is not None and cached.is_file():
        return cached.read_bytes()
    with tempfile.TemporaryDirectory(prefix="gs_texture_") as tmp:
        source = pathlib.Path(tmp) / f"source{MIME_EXT.get(mime, '.png')}"
        source.write_bytes(data)
        image = bpy.data.images.load(str(source), check_existing=False)
        try:
            image.colorspace_settings.name = colorspace
            if tuple(image.size) != tuple(size):
                image.scale(int(size[0]), int(size[1]))
            target = pathlib.Path(tmp) / f"out{out_ext}"
            image.file_format = fmt
            image.save(filepath=str(target), quality=int(quality))
            result = target.read_bytes()
        finally:
            bpy.data.images.remove(image)
    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            partial = cached.with_name(cached.name + ".tmp")
            partial.write_bytes(result)
            os.replace(partial, cached)
        except OSError as exc:
            log.debug("Could not cache %s: %s", cached, exc)
    return result


def _limited(size: tuple, limit: int) -> tuple:
    if limit <= 0 or max(size) <= limit:
        return tuple(size)
    factor = limit / max(size)
    return max(1, round(size[0] * factor)), max(1, round(size[1] * factor))


def process_textures(glb: Glb, settings: dict, cache_dir=None, file_label: str = "") -> list:
    """Downscale images over settings["maxTextureSize"]; colour maps to WebP when settings["webp"],
    data maps (normal, roughness, occlusion) to lossless WebP when settings["webpData"] (gamestudio.json
    "compression": {"textures": "webp"}, compression.py).

    Returns [{"name", "from", "to", "bytes_before", "bytes_after", "format"}].
    """
    gltf = glb.gltf
    limit = int(settings.get("maxTextureSize") or 0)
    webp = bool(settings.get("webp"))
    webp_data = bool(settings.get("webpData"))
    quality = int(settings.get("webpQuality") or 90)
    usage = texture_usage(gltf)
    converted, changed = set(), []
    for index, image in enumerate(gltf.get("images", [])):
        view = image.get("bufferView")
        if view is None:
            continue
        data = glb.view(view)
        mime = image.get("mimeType") or _sniff(data)
        size = image_size(data)
        if size is None or mime not in MIME_EXT:
            continue
        used = usage.get(index, set())
        lossy = webp and used == {"color"}
        lossless = webp_data and bool(used) and not lossy  # Blender writes WebP at quality 100 losslessly
        to_webp = (lossy or lossless) and mime != "image/webp"
        target = _limited(size, limit)
        if target == tuple(size) and not to_webp:
            continue
        fmt = "WEBP" if to_webp or mime == "image/webp" else ("JPEG" if mime == "image/jpeg" else "PNG")
        colorspace = "sRGB" if "color" in used else "Non-Color"
        try:
            out = convert_image(data, mime, target, fmt, 100 if lossless else quality, colorspace, cache_dir)
        except (RuntimeError, OSError) as exc:
            log.warning("Could not re-encode texture '%s': %s", image.get("name", index), exc)
            continue
        if to_webp and target == tuple(size) and len(out) >= len(data):
            continue  # WebP would not be smaller: keep the original
        name = image.get("name") or f"image {index}"
        if target != tuple(size):
            log.warning("Texture '%s' is %dx%d: exported at %dx%d (export.maxTextureSize is %d)%s",
                        name, size[0], size[1], target[0], target[1], limit, f" in {file_label}" if file_label else "")
        glb.replace(view, out)
        image["mimeType"] = FORMATS[fmt]
        if fmt == "WEBP" and mime != "image/webp":
            converted.add(index)
        changed.append({"name": name, "from": list(size), "to": list(target), "bytes_before": len(data),
                        "bytes_after": len(out), "format": FORMATS[fmt]})
    if converted:
        for texture in gltf.get("textures", []):
            if texture.get("source") in converted:
                texture.setdefault("extensions", {})[WEBP] = {"source": texture.pop("source")}
        _use_extension(gltf, WEBP, required=True)
    return changed


# --- Names ---------------------------------------------------------------------------------


def rename(gltf: dict, materials: dict, meshes: dict) -> None:
    """Give export-time copies their originals' names (e.g. a baked "Lava.001" back to "Lava")."""
    for key, names in (("materials", materials), ("meshes", meshes)):
        for item in gltf.get(key, []):
            if item.get("name") in names:
                item["name"] = names[item["name"]]


# --- Size report ---------------------------------------------------------------------------


def _accessor_views(accessors: list, index) -> set:
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        return set()
    accessor = accessors[index]
    views = {accessor["bufferView"]} if "bufferView" in accessor else set()
    sparse = accessor.get("sparse")
    if sparse:
        views |= {sparse["indices"]["bufferView"], sparse["values"]["bufferView"]}
    return views


def _draws(gltf: dict, prim: dict) -> int:
    """Draw calls three.js makes for a primitive: two for a transparent double-sided material (back
    faces, then front faces), else one."""
    materials = gltf.get("materials", [])
    index = prim.get("material")
    material = materials[index] if isinstance(index, int) and 0 <= index < len(materials) else {}
    return 2 if material.get("alphaMode") == "BLEND" and material.get("doubleSided") else 1


def size_report(glb: Glb, file_label: str, hidden_ids=()) -> dict:
    """What a GLB's bytes are spent on, and what its scene costs to draw: the triangles and draw
    calls of the nodes reachable from the default scene, one draw call per primitive (a GPU-instanced
    node too; two for a transparent double-sided material), leaving out collision-only entities
    (extras.gs_id in hidden_ids)."""
    gltf = glb.gltf
    views = gltf.get("bufferViews", [])
    accessors = gltf.get("accessors", [])
    meshes = gltf.get("meshes", [])
    nodes = gltf.get("nodes", [])
    owner = {}
    texture_memory = 0
    for index, image in enumerate(gltf.get("images", [])):
        if "bufferView" in image:
            size = image_size(glb.view(image["bufferView"])) or (0, 0)
            texture_memory += size[0] * size[1] * 4 * 4 // 3
            owner[image["bufferView"]] = ("texture", image.get("name") or f"image {index}",
                                          f"{size[0]}x{size[1]} {image.get('mimeType', '').split('/')[-1]}")
    uses = {}
    draw_calls = 0
    for index, drawn in scene_nodes(gltf, hidden_ids):
        node = nodes[index]
        if "mesh" not in node or not 0 <= node["mesh"] < len(meshes):
            continue
        instancing = (node.get("extensions") or {}).get(INSTANCING)
        count = 1
        if instancing:
            first = next(iter(instancing["attributes"].values()))
            count = accessors[first]["count"]
            for acc in instancing["attributes"].values():
                for view in _accessor_views(accessors, acc):
                    owner.setdefault(view, ("instancing", node.get("name", ""), ""))
        if drawn:
            uses[node["mesh"]] = uses.get(node["mesh"], 0) + count
            draw_calls += sum(_draws(gltf, prim) for prim in meshes[node["mesh"]].get("primitives", []))
    # Meshes are listed by name; several meshes of one name (realized geometry nodes output is all
    # called "Mesh") are told apart by the first node that uses them.
    users = {}
    for index, _drawn in scene_nodes(gltf):
        if "mesh" in nodes[index]:
            users.setdefault(nodes[index]["mesh"], nodes[index].get("name") or f"node {index}")
    names = [m.get("name") or f"mesh {i}" for i, m in enumerate(meshes)]
    repeated = {n for n, count in collections.Counter(names).items() if count > 1}
    triangles = 0
    for index, mesh in enumerate(meshes):
        tris = 0
        mesh_views = set()
        for prim in mesh.get("primitives", []):
            refs = [*prim.get("attributes", {}).values(), prim.get("indices")]
            refs += [a for target in prim.get("targets", []) for a in target.values()]
            for ref in refs:
                mesh_views |= _accessor_views(accessors, ref)
            if prim.get("mode", 4) == 4:
                ref = prim.get("indices", prim.get("attributes", {}).get("POSITION"))
                if isinstance(ref, int) and ref < len(accessors):
                    tris += accessors[ref]["count"] // 3
        triangles += tris * uses.get(index, 0)
        label = f"{tris:,} triangles" + (f" x {uses[index]}" if uses.get(index, 0) > 1 else "")
        if index in users and not uses.get(index):
            label += ", not drawn (collision only)"
        name = names[index]
        if name in repeated and index in users:
            name = f"{name} ({users[index]})"
        for view in mesh_views:
            owner.setdefault(view, ("mesh", name, label))
    for anim in gltf.get("animations", []):
        for sampler in anim.get("samplers", []):
            for ref in (sampler.get("input"), sampler.get("output")):
                for view in _accessor_views(accessors, ref):
                    owner.setdefault(view, ("animation", anim.get("name", ""), ""))
    totals = {"texture": 0, "mesh": 0, "animation": 0, "instancing": 0, "other": 0}
    items = {}
    for index, view in enumerate(views):
        kind, name, detail = owner.get(index, ("other", "", ""))
        totals[kind] += view.get("byteLength", 0)
        if kind in ("texture", "mesh"):
            item = items.setdefault((kind, name), {"kind": kind, "name": name, "bytes": 0, "detail": detail,
                                                   "file": file_label})
            item["bytes"] += view.get("byteLength", 0)
    return {"textures": totals["texture"], "geometry": totals["mesh"] + totals["instancing"],
            "animation": totals["animation"], "triangles": triangles, "draw_calls": draw_calls,
            "texture_memory": texture_memory,
            "items": sorted(items.values(), key=lambda i: -i["bytes"])[:TOP_ITEMS * 2]}


def merge_reports(reports: list) -> dict:
    """One scene report from the reports of its GLBs."""
    merged = {"textures": 0, "geometry": 0, "animation": 0, "triangles": 0, "draw_calls": 0, "texture_memory": 0}
    items = []
    for report in reports:
        for key in merged:
            merged[key] += report.get(key, 0)
        items += report.get("items", [])
    merged["top"] = sorted(items, key=lambda i: -i["bytes"])[:TOP_ITEMS]
    return merged


def format_bytes(count: float) -> str:
    for unit in ("B", "KB", "MB"):
        if count < 1024 or unit == "MB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} MB"
