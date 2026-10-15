# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene export: <scene>.glb + <scene>.scene.json (docs/SPEC.md §4).

The GLB comes from Blender's stock glTF exporter. Afterwards its JSON chunk is
rewritten to put {"gs_id": ...} into the extras of every node that stands for
a scene object. The manifest carries the component data, the animation clips
in the GLB, the prefab templates and the asset files components refer to.

Prefabs (§4.4) are collections with the custom property gs_prefab. They are
exported to <scene>.prefabs.glb through a temporary scene that links them, so
the user's scene is never modified. Collection instances of a prefab keep only
their own node in <scene>.glb; the runtime instantiates the template under it.

Objects come from the view layer (what the exporter writes). Linked data may
share names with local data: objects are keyed by name_full, a local object
whose name a linked one also has is renamed for the exporter run so its node
can be told apart, and a linked prefab whose name a local one has exports as
"Name [library.blend]". A missing library file is an export error.

Materials with a game shader (Material.gs.shader, shaders.py) that the GLBs
draw with are listed in the manifest's "materials" by their name in the GLB,
with every field's value. Asset files named by `asset` fields of components
and game shaders (sounds, textures, ...) are copied into export/files/ (§4.5)
so the export folder is self-contained.

Levels (§4.7): a project has one .blend per level in scenes/. A level file with a
single scene exports under the file's name (scenes/level2.blend -> level2), so
ctx.loadScene('level2') finds it whatever its scene is called. export_all()
exports every level: the open file here, the others in a background Blender.
UI files (ui/*.blend, docs/GAME-UI.md) are never levels: export_scene() refuses
them, and export_all() exports them too (gameui_export.py).
The world's gradient sky (the sky kit) and mist become settings.sky and
settings.fog; the scene's view transform and exposure (Color Management)
become settings.toneMapping and settings.exposure.

Fidelity (§4.6): procedural materials are baked to textures (bake.py) and
swapped in for the exporter run only; geometry-nodes and particle instances
are exported; repeated meshes become GPU instances, big textures are
downscaled and hidden boolean cutters lose their mesh (fidelity.py).
Collision-only objects (a Collider, drawn as Wire or Bounds) keep a bare mesh
for the collider and are marked drawn: false. What the scene does not use
(objects of excluded collections, instance sources, detached prefab
contents) is pruned from the GLB. The result reports the export size, its
largest items and every warning.

Export never changes the user's data beyond assigning missing IDs (and
repairing duplicated ones) and bumping scene["gs_rev"]. Selection, active
object, object mode and the window's scene are restored afterwards.
"""

import contextlib
import datetime
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import struct
import subprocess
import time
import types

import bpy

from . import bake, components, compression, convert, fidelity, ids, project, registry
from .log import get_logger
from .util import any_window

log = get_logger(__name__)

GLTF_SETTINGS = {
    "export_format": "GLB",
    "export_extras": False,
    "export_yup": True,
    "export_apply": True,
    "export_cameras": True,
    "export_lights": True,
    "use_visible": False,
    "export_animations": True,
    # Not in §4.1 but required: without it the exporter writes every scene in the file.
    "use_active_scene": True,
    "use_selection": False,
    # Animation (§4.1): the active action and the action of every NLA track with a single
    # unmuted strip become clips named after the action, starting at 0 s. Actions shared by
    # several objects become one clip. Only actions on the object count: with a lone armature
    # the exporter would otherwise add every bone action in the file (other characters' too).
    "export_animation_mode": "ACTIONS",
    "export_anim_slide_to_zero": True,
    "export_frame_range": False,
    "export_force_sampling": True,
    "export_anim_single_armature": False,
    "export_skins": True,
    "export_morph": True,
    "export_morph_animation": True,
}

# gamestudio.json "export" section (§4.6): every key is optional.
EXPORT_DEFAULTS = {
    "bake": "auto",            # procedural materials: "auto" bakes at export, "cached" uses earlier bakes, "off"
    "bakeSize": 1024,          # largest baked texture, px
    "bakeDensity": 128,        # baked texels per metre of surface
    "bakeSamples": 4,
    "bakeBudgetMB": 64,        # GPU memory for all baked textures of a scene
    "maxTextureSize": 2048,    # larger images are downscaled (0: never)
    "webp": False,             # colour maps as WebP (EXT_texture_webp)
    "webpQuality": 90,
    "instancing": 4,           # this many repeats of a mesh under one parent become GPU instances (0: off)
    "warnSizeMB": 32,          # warn when a scene's export is bigger
}
INSTANCE_WARN = 5000           # instances from one object before the export result warns about the cost

FORMAT_VERSION = 2
DEFAULT_BACKGROUND = [0.05, 0.05, 0.08]
REV_PROP = "gs_rev"
PREFAB_PROP = "gs_prefab"
ASSET_DIR = "files"
BAKE_DIR = "bake"
PREFAB_SCENE = "gs_prefab_export"
SCENES_DIR = "scenes"
SKY_COLORS_PROP = "gs_sky_colors"  # world[...]: {horizon, zenith, ground} as sRGB hex (the sky kit)
FOG_COLOR_PROP = "gs_fog_color"    # world[...]: optional fog colour as sRGB hex (default: the horizon)
# scene.view_settings.view_transform -> settings.toneMapping (§4.2); any other transform is "none", with a warning.
TONE_MAPPINGS = {"Khronos PBR Neutral": "neutral", "AgX": "agx", "Standard": "none", "Filmic": "aces",
                 "Filmic Log": "aces", "ACES 1.3": "aces", "ACES 2.0": "aces"}
EXPORT_LINE = "GS_EXPORT "

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

_exporting = False
_last_export: dict = {}
_listeners: list = []


class ExportError(RuntimeError):
    pass


def add_listener(fn) -> None:
    """Call fn(scene, result) after every successful export."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def is_exporting() -> bool:
    """True while an export runs (the live link ignores the depsgraph noise)."""
    return _exporting


def last_export() -> dict:
    """Info about the last successful export in this session (runtime only)."""
    return dict(_last_export)


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "scene"


def _user_scenes() -> list:
    """The file's scenes, without the temporary ones GameStudio makes while it works."""
    return [s for s in bpy.data.scenes if not s.name.startswith("gs_")]


def level_name(scene) -> str | None:
    """The file's name when scene is the only scene of a level file (<project>/scenes/<name>.blend)."""
    if not bpy.data.filepath:
        return None
    blend = pathlib.Path(bpy.data.filepath)
    if blend.parent.name != SCENES_DIR:
        return None
    root = project.project_root()
    if root is None:
        return None
    if os.path.normcase(os.path.abspath(blend.parent)) != os.path.normcase(os.path.abspath(root / SCENES_DIR)):
        return None
    scenes = _user_scenes()
    return blend.stem if len(scenes) == 1 and scenes[0] == scene else None


def export_name(scene) -> str:
    """File-system safe name used for <name>.glb / <name>.scene.json: the level file's name for the only
    scene of <project>/scenes/<name>.blend, else the scene's name."""
    return _sanitize(level_name(scene) or scene.name)


def manifest_url(name: str) -> str:
    return f"/__gs/export/{name}.scene.json"


def scene_load_message(result: dict) -> dict:
    """The `scene.load` message for an export result."""
    return {"t": "scene.load", "scene": result["scene"], "url": manifest_url(result["scene"]), "rev": result["rev"]}


def export_settings(root=None) -> dict:
    """EXPORT_DEFAULTS overridden by the "export" section of the project's gamestudio.json, plus the
    texture setting of its "compression" section (compression.py)."""
    settings = dict(EXPORT_DEFAULTS)
    section = project.read_config(root).get("export") if root is not None else None
    if not isinstance(section, dict):
        return compression.apply_to_export(settings, compression.settings(root))
    for key, default in EXPORT_DEFAULTS.items():
        value = section.get(key)
        if value is None:
            continue
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, (int, float)):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
        else:
            ok = value in bake.BAKE_MODES
        if ok:
            settings[key] = value
        else:
            log.warning("gamestudio.json: export.%s = %r is not valid; using %r", key, value, default)
    return compression.apply_to_export(settings, compression.settings(root))


class _WarningLog(logging.Handler):
    """Collects the warnings export, bake and fidelity log during one export (for the result)."""

    LOGGERS = ("gamestudio.export", "gamestudio.bake", "gamestudio.fidelity")

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        message = record.getMessage()
        if message not in self.messages:
            self.messages.append(message)

    def __enter__(self):
        for name in self.LOGGERS:
            logging.getLogger(name).addHandler(self)
        return self

    def __exit__(self, *_exc):
        for name in self.LOGGERS:
            logging.getLogger(name).removeHandler(self)


# --- Prefabs -----------------------------------------------------------------------


def is_prefab(collection) -> bool:
    """True if the collection is marked as a prefab (custom property gs_prefab)."""
    return collection is not None and bool(collection.get(PREFAB_PROP))


def set_prefab(collection, value: bool = True) -> None:
    """Mark or unmark a collection as a prefab."""
    if value:
        collection[PREFAB_PROP] = True
    elif PREFAB_PROP in collection:
        del collection[PREFAB_PROP]


def prefab_collections() -> list:
    """Every collection marked as a prefab: local ones first, then linked ones by library."""
    found = [c for c in bpy.data.collections if is_prefab(c)]
    return sorted(found, key=lambda c: (c.library is not None, ids.library_key(c.library) if c.library else "",
                                        c.name))


def prefab_names(prefabs, warn: bool = False) -> dict:
    """collection.as_pointer() -> template name. A name used by several collections (a local and a
    linked "Crate", or two libraries) stays with the first in prefab_collections() order; the others
    use their name_full ("Crate [props.blend]")."""
    names, taken = {}, {}
    for coll in prefabs:
        if coll.name in taken:
            names[coll.as_pointer()] = coll.name_full
            if warn:
                log.warning("Prefab name '%s' is used by '%s' and '%s': the second exports as '%s' "
                            "(ctx.spawn('%s') spawns the first; instances use their own collection)",
                            coll.name, taken[coll.name].name_full, coll.name_full, coll.name_full, coll.name)
        else:
            taken[coll.name] = coll
            names[coll.as_pointer()] = coll.name
    return names


def missing_libraries(objects, prefabs) -> dict:
    """{library path: [what needs it]} for linked data whose library file is missing."""
    found = {}

    def check(data, what):
        library = data.library
        if library is None:
            return
        path = bpy.path.abspath(library.filepath, library=library.parent)
        if data.is_missing or not os.path.isfile(path):
            found.setdefault(library.filepath, []).append(what)

    for obj in objects:
        check(obj, f"object '{obj.name}'")
        if obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
            check(obj.instance_collection, f"collection '{obj.instance_collection.name}' (instanced by '{obj.name}')")
    for coll in prefabs:
        check(coll, f"prefab '{coll.name}'")
    return found


def instanced_prefab(obj):
    """The prefab collection that obj instances (a collection-instance empty), or None."""
    coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
    return coll if is_prefab(coll) else None


# --- GLB reading and writing ---------------------------------------------------


def read_glb(path) -> tuple:
    """Return (gltf_json, chunks) where chunks lists (type, bytes) after the JSON chunk."""
    data = pathlib.Path(path).read_bytes()
    if len(data) < 20:
        raise ExportError(f"{path} is too short to be a GLB file")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise ExportError(f"{path} is not a glTF 2.0 binary file")
    offset = 12
    gltf = None
    chunks = []
    while offset + 8 <= min(length, len(data)):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8: offset + 8 + chunk_len]
        if chunk_type == CHUNK_JSON and gltf is None:
            gltf = json.loads(body.decode("utf-8"))
        else:
            chunks.append((chunk_type, body))
        offset += 8 + chunk_len
    if gltf is None:
        raise ExportError(f"{path} has no JSON chunk")
    return gltf, chunks


def build_glb(gltf: dict, chunks: list) -> bytes:
    """Serialize a GLB: JSON chunk padded with spaces, other chunks copied as-is."""
    body = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body += b" " * (-len(body) % 4)
    parts = [struct.pack("<II", len(body), CHUNK_JSON), body]
    for chunk_type, data in chunks:
        parts.append(struct.pack("<II", len(data), chunk_type))
        parts.append(data)
    payload = b"".join(parts)
    return struct.pack("<4sII", GLB_MAGIC, 2, 12 + len(payload)) + payload


def _replace(src: str, dst: str) -> None:
    # The dev server or a game may be reading the old file; on Windows that
    # blocks the rename for a while, so retry for about two seconds.
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05)


REPLACE_ATTEMPTS = 40  # x 50 ms


def _write_atomic(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    _replace(str(tmp), str(path))


# --- Node matching ---------------------------------------------------------------


def _trs_distance(node: dict, obj) -> float:
    """How far a node's transform is from obj's (to tell same-named nodes apart)."""
    item = convert.transform_item(obj, "")
    t = node.get("translation", [0, 0, 0])
    r = node.get("rotation", [0, 0, 0, 1])
    sc = node.get("scale", [1, 1, 1])
    rotation = min(sum((a - b) ** 2 for a, b in zip(r, item["r"])), sum((a + b) ** 2 for a, b in zip(r, item["r"])))
    return sum((a - b) ** 2 for a, b in zip(t, item["p"])) + sum((a - b) ** 2 for a, b in zip(sc, item["s"])) + rotation


def inject_ids(gltf: dict, objects: dict, node_names=None) -> dict:
    """Set node.extras = {"gs_id": id} on nodes that stand for scene objects.

    objects maps a key (obj.name_full) -> (Object, gs_id); node_names maps a key
    to the node name the exporter wrote when it differs from obj.name. Nodes are
    matched by name and must sit under the node of the object's parent, so
    instance contents and geometry-nodes instances never match. Same-named
    candidates are told apart by transform. Children of collection-instance
    nodes are only entered when they are real (parented) children.
    Returns {key: node index}.
    """
    nodes = gltf.get("nodes", [])
    node_names = node_names or {}
    by_name: dict = {}
    for key, (obj, _gs_id) in objects.items():
        by_name.setdefault(node_names.get(key, obj.name), []).append(key)
    matched: dict = {}

    def candidates(node, parent_obj) -> list:
        return [k for k in by_name.get(node.get("name"), ()) if k not in matched and objects[k][0].parent == parent_obj]

    def visit(index: int, parent_obj) -> None:
        node = nodes[index]
        keys = candidates(node, parent_obj)
        if len(keys) > 1:
            keys.sort(key=lambda k: _trs_distance(node, objects[k][0]))
        obj = None
        if keys:
            obj, gs_id = objects[keys[0]]
            extras = node.get("extras") if isinstance(node.get("extras"), dict) else {}
            extras["gs_id"] = gs_id
            node["extras"] = extras
            matched[keys[0]] = index
        owner = obj if obj is not None else parent_obj
        is_instancer = obj is not None and obj.instance_type == "COLLECTION" and obj.instance_collection
        for child in node.get("children", []):
            if is_instancer and not candidates(nodes[child], obj):
                continue  # instance contents, not entities
            visit(child, owner)

    scene_index = gltf.get("scene", 0)
    scenes = gltf.get("scenes", [])
    roots = scenes[scene_index].get("nodes", []) if scenes else []
    for root in roots:
        visit(root, None)
    return matched


def strip_instance_contents(gltf: dict, matched: dict, instancers) -> int:
    """Drop the instance contents the exporter wrote under the given instancers (keys of matched).

    The runtime builds those instances from the prefab templates instead. Real
    (parented) children stay. Animation channels that targeted the dropped
    nodes are removed too. Returns the number of nodes detached.
    """
    nodes = gltf.get("nodes", [])
    entity_nodes = set(matched.values())
    dropped = []
    for name in instancers:
        index = matched.get(name)
        if index is None:
            continue
        node = nodes[index]
        children = node.get("children", [])
        keep = [c for c in children if c in entity_nodes]
        dropped += [c for c in children if c not in entity_nodes]
        if keep:
            node["children"] = keep
        else:
            node.pop("children", None)
    if not dropped:
        return 0
    gone = set()
    stack = list(dropped)
    while stack:
        index = stack.pop()
        if index not in gone:
            gone.add(index)
            stack.extend(nodes[index].get("children", []))
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
    if animations:
        gltf["animations"] = animations
    else:
        gltf.pop("animations", None)
    return len(dropped)


def check_nla(objects) -> list:
    """Warnings about NLA setups the exporter skips: tracks with several unmuted strips."""
    problems = []
    for obj in objects:
        anim = obj.animation_data
        for track in (anim.nla_tracks if anim is not None else []):
            strips = [s for s in track.strips if s.action is not None and not s.mute]
            if len(strips) > 1:
                problems.append(f"NLA track '{track.name}' on '{obj.name}' has {len(strips)} strips; only tracks with "
                                f"one strip become clips (put each action on its own track)")
    for problem in problems:
        log.warning(problem)
    return problems


def object_clips(obj) -> list:
    """Names of the clips the export writes for obj and the objects under it (what its Animator can
    play): active actions, single-strip NLA tracks and shape-key actions, prefab templates included."""
    found = []

    def add(anim):
        if anim is None:
            return
        actions = [anim.action] if anim.action is not None else []
        for track in anim.nla_tracks:
            strips = [s for s in track.strips if s.action is not None and not s.mute]
            if len(strips) == 1:
                actions.append(strips[0].action)
        for action in actions:
            if action.name not in found:
                found.append(action.name)

    if obj is None:
        return found
    members = [obj, *obj.children_recursive]
    prefab = instanced_prefab(obj)
    if prefab is not None:
        members += list(prefab.all_objects)
    for member in members:
        add(member.animation_data)
        keys = getattr(member.data, "shape_keys", None) if member.type == "MESH" else None
        if keys is not None:
            add(keys.animation_data)
    return found


def gather_animations(gltf: dict, node_ids: dict) -> list:
    """The clips in a GLB: [{name, duration, entities}].

    node_ids maps node index -> gs_id. A channel that targets a bone or another
    non-entity node counts for the nearest entity above it.
    """
    nodes = gltf.get("nodes", [])
    accessors = gltf.get("accessors", [])
    parent = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            parent[child] = index

    def owner(index):
        seen = 0
        while index is not None and seen < len(nodes):
            if index in node_ids:
                return node_ids[index]
            index = parent.get(index)
            seen += 1
        return None

    clips = []
    for i, anim in enumerate(gltf.get("animations", [])):
        entities = []
        for channel in anim.get("channels", []):
            gs_id = owner(channel.get("target", {}).get("node"))
            if gs_id is not None and gs_id not in entities:
                entities.append(gs_id)
        duration = 0.0
        for sampler in anim.get("samplers", []):
            index = sampler.get("input")
            accessor = accessors[index] if isinstance(index, int) and 0 <= index < len(accessors) else {}
            if accessor.get("max"):
                duration = max(duration, float(accessor["max"][0]))
        clips.append({"name": anim.get("name") or f"animation_{i}", "duration": round(duration, 6), "entities": entities})
    return clips


# --- Manifest --------------------------------------------------------------------


def _background(scene) -> list:
    world = scene.world
    if world is None:
        return list(DEFAULT_BACKGROUND)
    tree = world.node_tree
    if tree is not None:
        for node in tree.nodes:
            if node.type == "BACKGROUND":
                color = node.inputs.get("Color")
                if color is not None and not color.is_linked:
                    return [round(float(c), 6) for c in color.default_value[:3]]
                break
    return [round(float(c), 6) for c in world.color[:3]]


def _srgb_hex_to_linear(value) -> list | None:
    """'#RRGGBB' (sRGB) -> linear [r, g, b], or None."""
    text = str(value or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        return None
    out = []
    for i in (0, 2, 4):
        c = int(text[i:i + 2], 16) / 255.0
        out.append(round(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4, 6))
    return out


def _sky(scene) -> dict | None:
    """The sky kit's gradient: world["gs_sky_colors"] as linear colours, or None."""
    world = scene.world
    colors = world.get(SKY_COLORS_PROP) if world is not None else None
    if not colors:
        return None
    try:
        horizon, zenith = _srgb_hex_to_linear(colors.get("horizon")), _srgb_hex_to_linear(colors.get("zenith"))
        ground = _srgb_hex_to_linear(colors.get("ground")) or horizon
    except AttributeError:
        return None
    if horizon is None or zenith is None:
        return None
    return {"horizon": horizon, "zenith": zenith, "ground": ground}


def _fog(scene, background, sky) -> dict | None:
    """Linear fog from the world's mist settings when mist is on (mist_settings.use_mist, or the Mist
    pass of the exported view layer): from `start`, full after `depth` more metres. The colour is
    world["gs_fog_color"], else the sky's horizon, else the background."""
    world = scene.world
    mist = world.mist_settings if world is not None else None
    if mist is None:
        return None
    if not (mist.use_mist or _export_view_layer(scene).use_pass_mist):
        return None
    near = max(0.0, float(mist.start))
    far = near + max(float(mist.depth), 0.001)
    color = _srgb_hex_to_linear(world.get(FOG_COLOR_PROP)) or (sky["horizon"] if sky else background)
    return {"color": list(color), "near": round(near, 6), "far": round(far, 6)}


def _gravity(scene) -> list:
    if not scene.use_gravity:
        return [0.0, 0.0, 0.0]
    return [round(v, 6) + 0.0 for v in convert.vector_to_runtime(scene.gravity)]


def _tone_mapping(scene) -> tuple:
    """(settings.toneMapping, settings.exposure): the game tone-maps like the scene's Color Management
    (view transform and exposure, in stops). Transforms three.js lacks map to the nearest one, and looks
    are not carried; both with a warning."""
    view = scene.view_settings
    transform = view.view_transform
    mapping = TONE_MAPPINGS.get(transform, "none")
    if transform in ("Filmic", "Filmic Log"):
        log.warning("View transform '%s': Filmic has no three.js equivalent; the game uses ACES", transform)
    elif transform == "ACES 2.0":
        log.warning("View transform '%s' has no three.js equivalent; the game uses ACES 1.x (ACES Filmic)",
                    transform)
    elif transform not in TONE_MAPPINGS:
        log.warning("View transform '%s' has no three.js equivalent; the game uses no tone mapping (as Standard)",
                    transform)
    if view.look not in ("None", ""):
        log.warning("Look '%s' (Color Management) is not carried to the game: only the view transform and "
                    "exposure are", view.look)
    return mapping, round(float(view.exposure), 6) + 0.0


def clips_by_entity(clips: list) -> dict:
    """gs_id -> names of the clips that animate it (from gather_animations)."""
    found = {}
    for clip in clips:
        for gs_id in clip["entities"]:
            names = found.setdefault(gs_id, [])
            if clip["name"] not in names:
                names.append(clip["name"])
    return found


def _entity_entry(obj, exported_set, template_names, clip_names=None, not_drawn=()) -> dict:
    """template_names: collection.as_pointer() -> template name, for the prefabs that have one.
    not_drawn: objects whose mesh the game does not draw (collision-only objects)."""
    parent = obj.parent if obj.parent in exported_set else None
    gs_id = ids.get_id(obj) or ids.ensure_id(obj)
    entry = {
        "id": gs_id,
        "name": obj.name,
        "type": obj.type,
        "parent": ids.ensure_id(parent) if parent is not None else None,
        "components": components.export_components(obj),
    }
    prefab = instanced_prefab(obj)
    if prefab is not None and prefab.as_pointer() in template_names:
        entry["prefab"] = template_names[prefab.as_pointer()]
    if clip_names and clip_names.get(gs_id):
        entry["animations"] = list(clip_names[gs_id])
    if obj in not_drawn:
        entry["drawn"] = False
    return entry


def build_manifest(scene, name: str, rev: int, exported: list, animations=None, prefabs=None, assets=None,
                   template_names=None, not_drawn=(), materials=None) -> dict:
    """exported: objects in node order that received a gs_id in the GLB. template_names:
    collection.as_pointer() -> name of the exported templates (default: by collection name).
    not_drawn: objects the game does not draw (entity "drawn": false). materials: the "materials"
    section (shader_section), left out when empty."""
    exported_set = set(exported)
    prefabs = prefabs or []
    if template_names is None:
        wanted = {p["name"] for p in prefabs}
        template_names = {c.as_pointer(): c.name for c in prefab_collections() if c.name in wanted}
    clip_names = clips_by_entity(animations or [])
    camera = scene.camera if scene.camera in exported_set else None
    background = _background(scene)
    sky = _sky(scene)
    settings = {
        "gravity": _gravity(scene),
        "background": background,
        "activeCamera": ids.ensure_id(camera) if camera is not None else None,
    }
    if sky is not None:
        settings["sky"] = sky
    fog = _fog(scene, background, sky)
    if fog is not None:
        settings["fog"] = fog
    settings["toneMapping"], settings["exposure"] = _tone_mapping(scene)
    manifest = {
        "format": "gamestudio.scene",
        "formatVersion": FORMAT_VERSION,
        "scene": name,
        "rev": rev,
        "exportedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "blenderVersion": bpy.app.version_string.split(" ")[0],
        "glb": f"{name}.glb",
        "settings": settings,
        "entities": [_entity_entry(obj, exported_set, template_names, clip_names, not_drawn) for obj in exported],
        "animations": animations or [],
        "prefabs": prefabs,
    }
    if materials:
        manifest["materials"] = materials
    manifest["assets"] = assets or {}
    return manifest


# --- Asset files (§4.5) -------------------------------------------------------------


def _asset_values(entities: list, extra=()) -> list:
    """Values of `asset` fields in manifest entities, then the extra ones (game shaders'), in first-seen order."""
    found = []
    for entity in entities:
        for comp, values in entity["components"].items():
            entry = registry.schema_ref(comp)
            for f in (entry or {}).get("fields", []):
                value = values.get(f["name"]) if f["type"] == "asset" else None
                if isinstance(value, str) and value and value not in found:
                    found.append(value)
    return found + [v for v in dict.fromkeys(extra) if v not in found]


def _same_file(src: os.stat_result, dst: pathlib.Path) -> bool:
    try:
        st = dst.stat()
    except OSError:
        return False
    return st.st_size == src.st_size and st.st_mtime_ns == src.st_mtime_ns


def copy_assets(out_dir, values: list, root=None) -> dict:
    """Copy the files named by asset fields into <out_dir>/files/.

    Paths are project-relative (or absolute). Files inside the project keep
    their relative path; others go to files/external/. Unchanged files are not
    copied again. Returns {asset value: URL relative to the manifest, with a
    ?v= version}. Missing files are logged and left out.
    """
    out = pathlib.Path(out_dir)
    root = pathlib.Path(root) if root is not None else project.project_root()
    mapping = {}
    for value in values:
        if value.startswith(("http:", "https:", "data:", "blob:", "/")):
            continue  # already a URL
        path = pathlib.Path(value)
        if not path.is_absolute():
            if root is None:
                log.warning("Asset '%s' is relative but the scene is not in a project; not copied", value)
                continue
            path = root / path
        path = pathlib.Path(os.path.normpath(path))
        try:
            st = path.stat()
        except OSError:
            log.warning("Asset file not found: %s (referenced as '%s')", path, value)
            continue
        try:
            rel = path.relative_to(pathlib.Path(os.path.normpath(root))) if root is not None else None
        except ValueError:
            rel = None
        if rel is None:
            digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
            rel = pathlib.Path("external") / f"{digest}-{path.name}"
        dest = out / ASSET_DIR / rel
        if not _same_file(st, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp")
            shutil.copy2(path, tmp)
            _replace(str(tmp), str(dest))
        _copy_font_licence(path, dest)
        version = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]
        mapping[value] = f"{ASSET_DIR}/{rel.as_posix()}?v={version}"
    return mapping


def _copy_font_licence(font: pathlib.Path, dest: pathlib.Path) -> None:
    """A font asset gets its licence next to its copy (<font name>-OFL.txt), as the game UI's fonts do:
    the OFL wants it with every copy, and the dev server lists it in a build's THIRD-PARTY-NOTICES.md."""
    from .gameui_export import FONT_SUFFIXES, font_licence, font_licence_name

    if font.suffix.lower() not in FONT_SUFFIXES:
        return
    licence = font_licence(font)
    if licence is None:
        return
    target = dest.with_name(font_licence_name(dest.name, licence))
    if not _same_file(licence.stat(), target):
        tmp = target.with_name(target.name + ".tmp")
        shutil.copy2(licence, tmp)
        _replace(str(tmp), str(target))


# --- Game shaders (§4.2 "materials") ---------------------------------------------------------


def glb_material_names(gltf: dict) -> list:
    """Names of the materials the meshes in a GLB's scene draw with, in first-use order."""
    nodes, meshes, materials = gltf.get("nodes", []), gltf.get("meshes", []), gltf.get("materials", [])
    names = []
    for index, _drawn in fidelity.scene_nodes(gltf):
        mesh = nodes[index].get("mesh")
        if not isinstance(mesh, int) or not 0 <= mesh < len(meshes):
            continue
        for prim in meshes[mesh].get("primitives", []):
            variants = ((prim.get("extensions") or {}).get("KHR_materials_variants") or {}).get("mappings", [])
            for ref in [prim.get("material"), *(v.get("material") for v in variants)]:
                name = materials[ref].get("name") if isinstance(ref, int) and 0 <= ref < len(materials) else None
                if name and name not in names:
                    names.append(name)
    return names


def read_glb_json(path) -> dict:
    """The JSON chunk of a GLB, without reading its BIN chunk."""
    with open(path, "rb") as f:
        head = f.read(20)
        if len(head) < 20 or head[:4] != GLB_MAGIC:
            raise ExportError(f"{path} is not a glTF 2.0 binary file")
        length, kind = struct.unpack_from("<II", head, 12)
        if kind != CHUNK_JSON:
            raise ExportError(f"{path} has no JSON chunk")
        return json.loads(f.read(length).decode("utf-8"))


_glb_names: dict = {}  # GLB path -> ((size, mtime_ns), glb_material_names): for layout exports


def _remember_material_names(path: pathlib.Path, names: list) -> None:
    st = path.stat()
    _glb_names[str(path)] = ((st.st_size, st.st_mtime_ns), list(names))


def exported_material_names(out_dir, manifest: dict) -> list:
    """The material names in the GLBs a manifest names (the scene GLB, then the prefab GLB)."""
    names = []
    for glb in dict.fromkeys([manifest.get("glb"), *(t.get("glb") for t in manifest.get("prefabs") or [])]):
        if not glb:
            continue
        path = pathlib.Path(out_dir) / glb
        st = path.stat()
        known = _glb_names.get(str(path))
        if known is None or known[0] != (st.st_size, st.st_mtime_ns):
            _remember_material_names(path, glb_material_names(read_glb_json(path)))
            known = _glb_names[str(path)]
        names += [n for n in known[1] if n not in names]
    return names


def _materials_by_name(objects) -> dict:
    """Material name -> {pointer: material} for the materials the objects draw with: their slots, their
    data's and those of the objects in the collections they instance."""
    found, seen = {}, set()
    stack = list(objects)
    while stack:
        obj = stack.pop()
        data = obj.data
        for mat in [*(s.material for s in obj.material_slots), *(getattr(data, "materials", None) or ())]:
            if mat is not None:
                found.setdefault(mat.name, {})[mat.as_pointer()] = mat
        coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
        if coll is not None and coll.as_pointer() not in seen:
            seen.add(coll.as_pointer())
            stack.extend(coll.all_objects)
    return found


def _material_order(mat) -> tuple:
    """Local materials first, then linked ones by library."""
    return mat.library is not None, ids.library_key(mat.library) if mat.library is not None else "", mat.name_full


def _shader_settings(mat) -> tuple:
    """(shader, values) of a material: its game shader (None) and every field's value, defaults filled."""
    from . import shaders

    shader = shaders.get_shader(mat)
    return (shader, shaders.get_values(mat, for_export=True)) if shader else (None, {})


def shader_section(names, objects) -> tuple:
    """(the manifest "materials" section, the asset values in it) for the material names the GLBs hold.

    Each name maps back to the material of that name the objects draw with (else to any material of that
    name: geometry nodes may set one); export copies keep the original names. {name: {"shader",
    "values"}} lists those with a game shader. A name that several materials share (a local and a linked
    one) takes the settings of the local one, else of the first library's, with a warning when the
    others' differ."""
    used = _materials_by_name(objects)
    loaded = registry.source_info()["origin"] != "none"
    section, assets = {}, []
    for name in names:
        found = list((used.get(name) or {}).values()) or [m for m in bpy.data.materials if m.name == name]
        if not found:
            continue
        found.sort(key=_material_order)
        settings = [_shader_settings(mat) for mat in found]
        if any(s != settings[0] for s in settings[1:]):
            log.warning("Materials %s are all exported as '%s' but their game shader settings differ: the game "
                        "applies those of '%s' to all of them (rename one)",
                        ", ".join(f"'{m.name_full}'" for m in found), name, found[0].name_full)
        shader, values = settings[0]
        if not shader:
            continue
        schema = registry.shader_schema_ref(shader)
        if schema is None and loaded:
            log.warning('Material "%s" uses game shader "%s", which no src/shaders file defines', name, shader)
        section[name] = {"shader": shader, "values": values}
        for f in (schema or {}).get("fields", []):
            value = values.get(f["name"]) if f["type"] == "asset" else None
            if isinstance(value, str) and value and value not in assets:
                assets.append(value)
    return section, assets


# --- Running the glTF exporter ---------------------------------------------------


def _mode_for_restore(mode: str) -> str:
    return "EDIT" if mode.startswith("EDIT") else mode


def _export_view_layer(scene):
    """The view layer an export of scene uses: the active one for the active scene, else the first."""
    return bpy.context.view_layer if bpy.context.scene == scene else scene.view_layers[0]


@contextlib.contextmanager
def _preserved_state(scene):
    """Yields (window, view_layer) for exporting `scene` and restores what the exporter may touch."""
    window = any_window()
    if window is None:
        raise ExportError("The glTF exporter needs a Blender window")
    view_layer = _export_view_layer(scene)

    saved_scene = window.scene
    saved_view_layer = window.view_layer
    active = view_layer.objects.active
    selected = [o for o in view_layer.objects if o.select_get(view_layer=view_layer)]
    mode = active.mode if active is not None else "OBJECT"

    if active is not None and mode == "EDIT":
        # Flush pending edit-mode changes so the export sees them.
        for obj in scene.objects:
            if obj.mode == "EDIT":
                obj.update_from_editmode()
    try:
        yield window, view_layer
    finally:
        if window.scene != saved_scene:
            window.scene = saved_scene
        if window.view_layer != saved_view_layer:
            try:
                window.view_layer = saved_view_layer
            except (TypeError, ReferenceError):
                pass
        _restore_state(scene, view_layer, window, active, selected, mode)


def _gltf(window, scene, view_layer, filepath: str, extra=None) -> None:
    from . import meshcache

    # Meshes that did not change since an earlier export are not extracted again (meshcache.py).
    with meshcache.armed(), bpy.context.temp_override(window=window, scene=scene, view_layer=view_layer):
        result = bpy.ops.export_scene.gltf(filepath=filepath, **GLTF_SETTINGS, **(extra or {}))
    if "FINISHED" not in result:
        raise ExportError(f"The glTF exporter returned {result}")


def _depsgraph(window, scene, view_layer):
    with bpy.context.temp_override(window=window, scene=scene, view_layer=view_layer):
        return bpy.context.evaluated_depsgraph_get()


def instance_counts(depsgraph) -> tuple:
    """({instancer: count}, {source objects}) for geometry-nodes, particle and vertex/face instances.

    Collection instances are left out: the exporter writes their contents in any case.
    """
    counts, sources = {}, set()
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        parent = inst.parent.original
        if parent.instance_type == "COLLECTION":
            continue
        counts[parent.name] = counts.get(parent.name, 0) + 1
        source = inst.object.original
        if source != parent:
            sources.add(source.name_full)
    return counts, sources


def _may_instance(obj) -> bool:
    return (obj.instance_type in ("VERTS", "FACES") or len(obj.particle_systems) > 0
            or any(m.type == "NODES" and m.show_viewport for m in obj.modifiers))


def gltf_overrides(instancing: bool) -> dict:
    """Exporter settings on top of GLTF_SETTINGS: instances are exported only when there are some
    (export_gn_mesh walks every instance for every object, so it is not free)."""
    return {"export_gn_mesh": True} if instancing else {}


def _run_gltf_export(scene, filepath: str) -> None:
    with _preserved_state(scene) as (window, view_layer):
        counts, _sources = instance_counts(_depsgraph(window, scene, view_layer))
        _gltf(window, scene, view_layer, filepath, gltf_overrides(bool(counts)))


def _top_level(prefabs: list) -> list:
    """Prefabs that are not inside another prefab's collection tree."""
    nested = set()
    for coll in prefabs:
        nested.update(c.as_pointer() for c in coll.children_recursive)
    return [c for c in prefabs if c.as_pointer() not in nested]


def _export_prefab_glb(window, scene, prefabs: list, filepath: str, extra=None) -> None:
    """Export the prefab collections, linked into a temporary scene, to filepath."""
    tmp = bpy.data.scenes.new(PREFAB_SCENE)
    linked = []
    try:
        tmp.render.fps = scene.render.fps
        tmp.render.fps_base = scene.render.fps_base
        tmp.frame_current = scene.frame_current  # the exporter restores this frame afterwards
        for coll in _top_level(prefabs):
            tmp.collection.children.link(coll)
            linked.append(coll)
        # A new scene has no depsgraph until a window shows it; exporting it before that crashes Blender.
        window.scene = tmp
        view_layer = tmp.view_layers[0]
        view_layer.update()
        _gltf(window, tmp, view_layer, filepath, extra)
    finally:
        window.scene = scene
        for coll in linked:
            try:
                tmp.collection.children.unlink(coll)
            except RuntimeError:
                pass
        bpy.data.scenes.remove(tmp)


def _restore_state(scene, view_layer, window, active, selected, mode) -> None:
    try:
        if view_layer.objects.active != active:
            view_layer.objects.active = active
        wanted = set(selected)
        for obj in view_layer.objects:
            want = obj in wanted
            if obj.select_get(view_layer=view_layer) != want:
                obj.select_set(want, view_layer=view_layer)
        if active is not None and mode != "OBJECT" and active.mode != mode:
            with bpy.context.temp_override(window=window, scene=scene, view_layer=view_layer, active_object=active,
                                           object=active):
                bpy.ops.object.mode_set(mode=_mode_for_restore(mode))
    except (RuntimeError, ReferenceError) as exc:
        log.warning("Could not fully restore the selection/mode after export: %s", exc)


def _build_templates(gltf: dict, prefabs: list, glb_name: str, names: dict, node_names=None, not_drawn=()) -> list:
    """Tag template nodes in the prefab GLB and describe each prefab for the manifest.

    names: collection.as_pointer() -> template name (prefab_names). not_drawn: template objects the
    game does not draw (collision-only)."""
    objects = {}
    for coll in prefabs:
        for obj in coll.all_objects:
            objects.setdefault(obj.name_full, (obj, ids.ensure_id(obj)))
    matched = inject_ids(gltf, objects, node_names)
    strip_instance_contents(gltf, matched, [k for k in matched if instanced_prefab(objects[k][0]) is not None])
    clips = gather_animations(gltf, {index: objects[key][1] for key, index in matched.items()})
    clip_names = clips_by_entity(clips)
    templates = []
    for coll in prefabs:
        name = names[coll.as_pointer()]
        members = sorted((o for o in coll.all_objects if o.name_full in matched), key=lambda o: matched[o.name_full])
        missing = sorted(o.name for o in coll.all_objects if o.name_full not in matched)
        if missing:
            log.warning("Prefab '%s': not exported (hidden from the viewport, or parented outside the "
                        "collection?): %s", name, ", ".join(missing))
        if not members:
            log.warning("Prefab '%s' has no exportable objects; skipped", name)
            continue
        member_set = set(members)
        entities = [_entity_entry(obj, member_set, names, clip_names, not_drawn) for obj in members]
        own = {e["id"] for e in entities}
        templates.append({
            "name": name,
            "glb": glb_name,
            "offset": [round(v, 6) + 0.0 for v in convert.vector_to_runtime(coll.instance_offset)],
            "entities": entities,
            "animations": [dict(c, entities=[i for i in c["entities"] if i in own])
                           for c in clips if any(i in own for i in c["entities"])],
        })
    return templates


@contextlib.contextmanager
def _distinct_node_names(objs):
    """While the exporter runs, give local objects that share their name with linked ones a temporary
    unique name, so their nodes can be told apart. Yields {name_full: node name}; restores the names."""
    groups = {}
    for obj in objs:
        groups.setdefault(obj.name, {})[obj.as_pointer()] = obj
    renamed, names = [], {}
    try:
        for name, members in groups.items():
            if len(members) < 2:
                continue
            members = list(members.values())
            local = [o for o in members if o.library is None and o.override_library is None]
            log.warning("%d exported objects are named '%s' (%s): export keeps them apart, but rename them so "
                        "findByName in the game finds the one you mean", len(members), name,
                        ", ".join(o.name_full for o in members))
            for obj in local:
                key = obj.name_full
                obj.name = f"{name}~gs"
                renamed.append((obj, name))
                names[key] = obj.name
        yield names
    finally:
        for obj, name in reversed(renamed):
            obj.name = name


def _restore_node_names(gltf: dict, names: dict) -> None:
    """Give nodes written under a temporary name (_distinct_node_names) their object's name back
    (renamed objects are local, so their key, name_full, is their name)."""
    original = {temp: key for key, temp in names.items()}
    for node in gltf.get("nodes", []):
        if node.get("name") in original:
            node["name"] = original[node["name"]]


# --- Fidelity (§4.6) ---------------------------------------------------------------------

DRAWN_TYPES = ("MESH", "CURVE", "SURFACE", "FONT", "META")
COLLIDER = "Collider"


def display_only(scene, view_layer, sources=()) -> list:
    """Mesh objects Blender does not draw as solid surfaces but the game would: boolean cutters that
    are hidden, not rendered or drawn as wire/bounds; hidden objects that geometry nodes or particles
    instance (sources: names); other objects both drawn as wire/bounds and not rendered. Export leaves
    their nodes without a mesh (or warns, when they have components)."""
    layer_objects = list(view_layer.objects)
    cutters = set()
    for obj in layer_objects:
        for mod in obj.modifiers:
            if mod.type != "BOOLEAN" or not mod.show_viewport:
                continue
            if mod.operand_type == "OBJECT" and mod.object is not None:
                cutters.add(mod.object.name_full)
            elif mod.operand_type == "COLLECTION" and mod.collection is not None:
                cutters.update(o.name_full for o in mod.collection.all_objects)
    found = []
    for obj in layer_objects:
        if obj.type != "MESH":
            continue
        wire = obj.display_type in ("WIRE", "BOUNDS")
        if obj.name_full in cutters:
            if wire or obj.hide_render or not obj.visible_get(view_layer=view_layer):
                found.append(obj)
        elif (wire and obj.hide_render) or (obj.name_full in sources and not obj.visible_get(view_layer=view_layer)):
            found.append(obj)
    return found


def collision_only(objs) -> list:
    """Objects whose mesh only shapes their collider (§4.6): a Collider, and drawn as Wire or Bounds
    (Object Properties > Viewport Display > Display As). Export keeps their mesh and the manifest
    marks them drawn: false, so the game builds the collider but does not draw them."""
    if registry.schema_ref(COLLIDER) is None:
        return []
    return [o for o in objs if o.type in DRAWN_TYPES and o.display_type in ("WIRE", "BOUNDS")
            and components.is_enabled(o, COLLIDER)]


def _drop_meshes(gltf: dict, matched: dict, objs) -> tuple:
    """Leave the nodes of objs without a mesh. Objects with components keep it, for what the
    components do with it (a Collider is sized from it), but are not drawn. Returns (names of the
    objects whose mesh went, objects kept but not drawn)."""
    dropped, kept = [], []
    nodes = gltf.get("nodes", [])
    for obj in objs:
        index = matched.get(obj.name_full)
        if index is None or "mesh" not in nodes[index]:
            continue
        if components.list_components(obj):
            kept.append(obj)
            continue
        for key in ("mesh", "skin", "weights"):
            nodes[index].pop(key, None)
        dropped.append(obj.name)
    if dropped:
        log.info("Not drawn in the game (hidden cutters, instance sources, wire-only): %s", ", ".join(dropped))
    if kept:
        log.info("Kept for their components but not drawn: %s", ", ".join(o.name for o in kept))
    return dropped, kept


def _drop_excluded(gltf: dict, matched: dict, scene, layer_objects) -> list:
    """The exporter also writes the objects of collections excluded from the view layer (unevaluated:
    linked ones at the origin). Take those root nodes out of the scene; returns their names."""
    keys = {o.name_full for o in layer_objects}
    excluded = {o.name for o in scene.objects if o.name_full not in keys}
    scenes = gltf.get("scenes", [])
    if not excluded or not scenes:
        return []
    nodes, kept_nodes = gltf.get("nodes", []), set(matched.values())
    root = scenes[gltf.get("scene", 0)]
    dropped = [i for i in root.get("nodes", []) if i not in kept_nodes and nodes[i].get("name") in excluded]
    root["nodes"] = [i for i in root.get("nodes", []) if i not in dropped]
    if dropped:
        log.info("Not exported (in collections excluded from the view layer): %s",
                 ", ".join(nodes[i].get("name", "?") for i in dropped))
    return [nodes[i].get("name") for i in dropped]


def _node_ids(gltf: dict) -> dict:
    """node index -> gs_id, from the extras inject_ids wrote."""
    return {i: n["extras"]["gs_id"] for i, n in enumerate(gltf.get("nodes", []))
            if isinstance(n.get("extras"), dict) and n["extras"].get("gs_id")}


def _slim_glb(glb, hidden_ids, label: str) -> dict:
    """Collision-only meshes down to positions, then everything the scene does not use deleted
    (fidelity.collision_meshes, fidelity.prune). Returns what prune removed."""
    slimmed = fidelity.collision_meshes(glb, hidden_ids)
    pruned = fidelity.prune(glb)
    if slimmed:
        log.info("%s: collision-only meshes reduced to their triangles: %s", label, ", ".join(slimmed))
    if pruned:
        log.info("%s: removed what the scene does not use: %s", label,
                 ", ".join(f"{v} {k}" for k, v in pruned.items() if k != "bytes")
                 + (f" ({fidelity.format_bytes(pruned['bytes'])})" if pruned.get("bytes") else ""))
    return pruned


def _finish_glb(glb, settings, cache, label: str, report: dict, hidden_ids=()) -> list:
    """Textures and the size report of a post-processed GLB; returns its chunks."""
    report["textures"] += fidelity.process_textures(glb, settings, cache / "textures", label)
    chunks = glb.finish()
    report["sizes"].append(fidelity.size_report(glb, label, hidden_ids))
    return chunks


def _manifest_rev(path: pathlib.Path) -> int:
    """The rev of an existing manifest on disk, or 0."""
    try:
        rev = json.loads(path.read_text(encoding="utf-8")).get("rev", 0)
    except (OSError, ValueError, AttributeError):
        return 0
    return rev if isinstance(rev, int) and rev > 0 else 0


def _asset_bytes(out: pathlib.Path, assets: dict) -> int:
    total = 0
    for url in assets.values():
        try:
            total += (out / url.split("?", 1)[0]).stat().st_size
        except OSError:
            pass
    return total


def _size_summary(report: dict, settings: dict, glb_bytes: int, prefab_bytes: int, asset_bytes: int) -> dict:
    size = {"bytes": glb_bytes + prefab_bytes + asset_bytes, "glb": glb_bytes, "prefabs": prefab_bytes,
            "files": asset_bytes, **fidelity.merge_reports(report["sizes"])}
    limit = settings["warnSizeMB"] * 1024 * 1024
    if limit and size["bytes"] > limit:
        top = ", ".join(f"{i['name']} ({fidelity.format_bytes(i['bytes'])})" for i in size["top"][:3])
        log.warning("The export is %s, over export.warnSizeMB (%s MB); largest: %s",
                    fidelity.format_bytes(size["bytes"]), settings["warnSizeMB"], top)
    return size


# --- Layout and reuse (journal.py) ---------------------------------------------------

# Warnings that building the manifest logs (copy_assets, the tone mapping, shader_section): a layout or reuse
# result reports them from its own run, not the last full export's.
MANIFEST_WARNINGS = ("Asset file not found", "Asset '", "View transform '", "Look '", "Materials '", 'Material "')


def file_hash(data: bytes) -> str:
    """The manifest's "files" value for a file's bytes: sha1, 16 hex digits."""
    return hashlib.sha1(data).hexdigest()[:16]


_hasher = None


def _hash_later(data: bytes):
    """file_hash(data) on a worker thread (sha1 lets go of the GIL); returns a Future."""
    global _hasher
    if _hasher is None:
        import concurrent.futures

        _hasher = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="gs-export-hash")
    return _hasher.submit(file_hash, data)


def _with_bake(settings: dict, bake) -> dict:
    """Export settings with the bake argument of export_scene() applied (as export_scene_to applies it)."""
    if bake is not None:
        settings["bake"] = bake if isinstance(bake, str) else ("auto" if bake else "off")
    return settings


def refreshed_manifest(scene, out_dir, manifest: dict, objects: dict, transforms: dict) -> dict:
    """The manifest a layout export writes: `manifest` (the last export's) with the scene settings, the
    components of every entity and template entity, the game shaders of the materials in its GLBs and the
    asset files (copied again when they changed) as a full export would write them now. objects: gs_id ->
    object for every entity and template entity
    (a missing one raises KeyError). transforms: gs_id -> entity.transform item for the entities that are
    away from their GLB node; they get "transform" ({p, r, s}, local to the parent, Y-up). Everything a
    GLB decides (animations, lightmaps, files, ...) stays as it was."""
    import copy

    fresh = copy.deepcopy(manifest)
    templates = fresh.get("prefabs") or []
    for template in templates:
        for entity in template["entities"]:
            entity["components"] = components.export_components(objects[entity["id"]])
    wanted = {t["name"] for t in templates}
    template_names = {ptr: n for ptr, n in prefab_names(prefab_collections()).items() if n in wanted}
    exported = [objects[e["id"]] for e in manifest["entities"]]
    not_drawn = {objects[e["id"]] for e in manifest["entities"] if e.get("drawn") is False}
    built = build_manifest(scene, manifest["scene"], manifest["rev"], exported, manifest.get("animations"),
                           templates, template_names=template_names, not_drawn=not_drawn)
    for entity in built["entities"]:
        item = transforms.get(entity["id"])
        if item is not None:
            entity["transform"] = {"p": list(item["p"]), "r": list(item["r"]), "s": list(item["s"])}
    fresh.update(exportedAt=built["exportedAt"], settings=built["settings"], entities=built["entities"])
    template_objects = [objects[e["id"]] for t in templates for e in t["entities"]]
    materials, shader_assets = shader_section(exported_material_names(out_dir, manifest),
                                              [*exported, *template_objects])
    ordered = {}
    for key, value in fresh.items():
        if key == "assets" and materials:
            ordered["materials"] = materials  # before "assets", where a full export puts it
        if key != "materials":
            ordered[key] = value
    fresh = ordered
    asset_entities = built["entities"] + [e for t in templates for e in t["entities"]]
    fresh["assets"] = copy_assets(out_dir, _asset_values(asset_entities, shader_assets))
    return fresh


def _measure_compressed(size: dict, out: pathlib.Path) -> None:
    """size["compressed"] and per_file[...]["compressed"]: what `npm run build` ships (compression.measure)."""
    size["compressed"] = compression.measure(project.project_root() or out.parent.parent,
                                             [out / i["file"] for i in size.get("per_file", [])])
    for item in size["compressed"].get("files", []):
        next((i for i in size.get("per_file", []) if i["file"] == item["file"]), {})["compressed"] = item["after"]


def _export_from_journal(scene, out, decision, plan_warnings: list, compress: bool, started: float) -> dict:
    """The reuse and layout tiers: the last full export's result for GLBs that are still current, and for
    layout a new manifest (and rev) beside them. plan_warnings: what journal.plan() logged (building the
    manifest: asset files, tone mapping, game shaders)."""
    global _exporting
    out = pathlib.Path(out)
    result = decision.result
    manifest = decision.manifest
    manifest_path = pathlib.Path(result["manifest"])
    rev = decision.rev
    if decision.tier == "layout":
        rev = max(int(scene.get(REV_PROP, 0)), _manifest_rev(manifest_path)) + 1
        manifest["rev"] = rev
        _exporting = True  # the journal ignores what these writes cause
        try:
            _write_atomic(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
            scene[REV_PROP] = rev
        finally:
            _exporting = False
    kept = [w for w in result.get("warnings", []) if not w.startswith(MANIFEST_WARNINGS)]
    result.update(rev=rev, tier=decision.tier, warnings=list(dict.fromkeys([*kept, *plan_warnings])),
                  assets=len(manifest["assets"]), materials=len(manifest.get("materials") or {}))
    result.pop("reason", None)
    size = result.get("size")
    if isinstance(size, dict):
        size["files"] = _asset_bytes(out, manifest["assets"])
        size["bytes"] = size.get("glb", 0) + size.get("prefabs", 0) + size["files"]
        size.pop("compressed", None)
        for item in size.get("per_file", []):
            item.pop("compressed", None)
        if compress:
            _measure_compressed(size, out)
    seconds = time.perf_counter() - started
    _last_export.clear()
    _last_export.update(result, seconds=round(seconds, 3), time=time.time())
    log.info("Export of '%s' %s: rev %d in %.3f s (%s)", result["scene"],
             "reused the last one" if decision.tier == "reuse" else "wrote the manifest only", rev, seconds,
             decision.reason or "nothing changed")
    for listener in list(_listeners):
        try:
            listener(scene, result)
        except Exception:
            log.exception("Export listener failed")
    return result


# --- Public API --------------------------------------------------------------------


def export_scene(scene=None, bake=None, compress=False, force=False) -> dict:
    """Export a scene to <project>/.gamestudio/export/ (§4), doing only what changed since its last export.

    journal.py picks the tier: "reuse" (nothing that reaches the export changed and the files are the
    ones it wrote: the last result, same rev, nothing written), "layout" (only transforms, components,
    manifest-only scene settings, game shader settings of materials or asset files changed: a new manifest
    and rev beside the untouched GLBs, entities away from their GLB node get "transform") or "full"
    (export_scene_to). force=True always exports in full.

    Returns {"glb", "manifest", "rev", "scene", "entities", "repaired",
    "prefabs", "animations", "assets", "materials", "size", "bake", "textures", "instancing",
    "instances", "hidden_meshes", "not_drawn", "pruned", "warnings", "tier"} (§4.6), and "reason" (why)
    for a full export ("materials": how many materials have a game shader). Reuse and layout return the
    last full export's result with rev, assets, materials, size and warnings refreshed. bake overrides
    export.bake ("auto", "cached", "off"). compress: also measure what
    `npm run build` ships (size["compressed"], compression.measure; the export itself stays uncompressed).
    Raises ExportError if the .blend is not inside a GameStudio project.
    """
    root = project.project_root()
    if root is None:
        raise ExportError("Save the .blend inside a GameStudio project (folder with gamestudio.json)")
    from . import gameui, journal

    if gameui.is_ui_file():
        raise ExportError(f"{gameui.source_path(root)} is a UI file: its scenes are game UI screens, not levels. "
                          "They export when it is saved, on Play and with Export All (gameui_export.py)")
    scene = scene or bpy.context.scene
    out = project.export_dir(root)
    started = time.perf_counter()
    decision = journal.Plan("full", "forced")
    if not force:
        with _WarningLog() as warnings:
            try:
                decision = journal.plan(scene, out, _with_bake(export_settings(root), bake))
            except Exception as exc:  # the journal must never cost an export
                log.exception("The export journal failed")
                decision = journal.Plan("full", f"the export journal failed ({exc})")
        if decision.tier != "full":
            try:
                return _export_from_journal(scene, out, decision, warnings.messages, compress, started)
            except (ExportError, OSError):
                raise
            except Exception as exc:
                log.exception("Exporting only what changed failed")
                decision = journal.Plan("full", f"exporting only what changed failed ({exc})")
    log.info("Full export of '%s': %s", export_name(scene), decision.reason)
    result = export_scene_to(out, scene, bake=bake, compress=compress)
    result.update(tier="full", reason=decision.reason)
    _last_export.update(tier="full", reason=decision.reason)
    return result


def bake_cache_dir(out_dir) -> pathlib.Path:
    """<project>/.gamestudio/bake for <project>/.gamestudio/export (beside out_dir in general)."""
    return pathlib.Path(out_dir).parent / BAKE_DIR


def export_scene_to(out_dir, scene=None, bake=None, compress=False) -> dict:
    """Export to any directory (tests, tools). See export_scene()."""
    global _exporting
    from . import bake as baking
    from . import lightmap as lightmapping

    scene = scene or bpy.context.scene
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = export_name(scene)
    glb_path = out / f"{name}.glb"
    prefab_path = out / f"{name}.prefabs.glb"
    manifest_path = out / f"{name}.scene.json"
    raw_path = out / f"{name}.raw.glb"
    prefab_raw_path = out / f"{name}.prefabs.raw.glb"
    cache = bake_cache_dir(out)
    settings = export_settings(project.project_root())
    if bake is not None:
        settings["bake"] = bake if isinstance(bake, str) else ("auto" if bake else "off")
    started = time.perf_counter()
    report = {"textures": [], "sizes": []}
    bake_report = {}
    lightmap_report = {}

    _exporting = True
    try:
        with _WarningLog() as warnings:
            prefabs = prefab_collections()
            prefab_objects = {o for coll in prefabs for o in coll.all_objects}
            if prefab_objects:
                # Template ids must be unique too: check them together with the scene's objects.
                everything = list(dict.fromkeys([*scene.objects, *prefab_objects]))
                repaired = ids.repair_duplicate_ids(types.SimpleNamespace(objects=everything))
            else:
                repaired = ids.repair_duplicate_ids(scene)
            # What the exporter writes: the view layer's objects (not those of excluded collections).
            view_layer = _export_view_layer(scene)
            view_layer.update()  # objects linked since the last update are not in view_layer.objects yet
            layer_objects = list(view_layer.objects)
            missing = missing_libraries(layer_objects, prefabs)
            if missing:
                raise ExportError("Linked library file missing: " + "; ".join(
                    f"{path} (needed by {', '.join(what[:3])}{' ...' if len(what) > 3 else ''})"
                    for path, what in missing.items()) + ". Restore the file or remove what links to it")
            objects = {obj.name_full: (obj, ids.ensure_id(obj)) for obj in layer_objects}
            names = prefab_names(prefabs, warn=True)
            check_nla(dict.fromkeys([*scene.objects, *prefab_objects]))

            prefab_gltf = None
            try:
                with _preserved_state(scene) as (window, view_layer):
                    depsgraph = _depsgraph(window, scene, view_layer)
                    counts, sources = instance_counts(depsgraph)
                    for instancer, count in sorted(counts.items()):
                        if count >= INSTANCE_WARN:
                            log.warning("'%s' makes %s instances: the exporter walks every one (slow exports); the "
                                        "game draws repeated meshes as GPU instances", instancer, f"{count:,}")
                    cutters = display_only(scene, view_layer, sources)
                    hidden = [o.name_full for o in layer_objects if o.type in DRAWN_TYPES and not o.hide_viewport
                              and not o.visible_get(view_layer=view_layer) and o not in cutters]
                    bake_objects = baking.collect_objects(layer_objects, depsgraph, prefab_objects)
                    # Collision-only objects are not drawn: no point baking their materials (unless
                    # geometry nodes draw copies of them).
                    collision = set(collision_only([*layer_objects, *prefab_objects]))
                    bake_objects = [o for o in bake_objects if o not in collision or o.name_full in sources]
                    extra = gltf_overrides(bool(counts))
                    prefab_extra = gltf_overrides(bool(counts) or any(_may_instance(o) for o in prefab_objects))
                    # Armature clips: sampled from their F-curves (clips.py), not by the exporter.
                    from . import clips
                    clip_plan = clips.plan(scene, layer_objects, prefabs, prefab_objects, clips.cache_dir(out))
                    with baking.export_swap(scene, window, bake_objects, settings, cache, bake_report,
                                            instanced=sources), \
                            lightmapping.export_swap(scene, view_layer, name, out.parent, lightmap_report):
                        with _distinct_node_names([*layer_objects, *prefab_objects]) as node_names:
                            with clips.armed(clip_plan.scene):
                                _gltf(window, scene, view_layer, str(raw_path), extra)
                            if prefabs:
                                with clips.armed(clip_plan.prefabs):
                                    _export_prefab_glb(window, scene, prefabs, str(prefab_raw_path), prefab_extra)
                    view_layer.update()  # the restore's depsgraph updates arrive now, while _exporting is set
                gltf, chunks = read_glb(raw_path)
                raw_bytes = {glb_path.name: raw_path.stat().st_size}  # what the glTF exporter wrote
                if prefabs:
                    prefab_gltf, prefab_chunks = read_glb(prefab_raw_path)
                    raw_bytes[prefab_path.name] = prefab_raw_path.stat().st_size
            finally:
                for path in (raw_path, prefab_raw_path):
                    if path.exists():
                        path.unlink()

            matched = inject_ids(gltf, objects, node_names)
            _restore_node_names(gltf, node_names)
            _drop_excluded(gltf, matched, scene, layer_objects)
            exported = [objects[k][0] for k in sorted(matched, key=lambda k: matched[k])]
            skipped = [k for k in objects if k not in matched]
            if skipped:
                log.info("Not exported as nodes (not reachable in the glTF tree): %s", ", ".join(sorted(skipped)))
            shown_hidden = [k for k in hidden if k in matched]
            if shown_hidden:
                log.warning("%d object(s) hidden in the viewport are exported and shown in the game: %s%s",
                            len(shown_hidden), ", ".join(shown_hidden[:5]), " ..." if len(shown_hidden) > 5 else "")

            templates = []
            prefab_glb = None
            renames = (bake_report.get("material_names") or {}, bake_report.get("mesh_names") or {})
            # The lightmap swap ran inside the bake swap: its copies are named back to the bake's copies first,
            # then those to the originals.
            fidelity.rename(gltf, {}, lightmap_report.get("mesh_names") or {})
            fidelity.rename(gltf, *renames)
            prefab_hidden = set()
            if prefab_gltf is not None:
                fidelity.rename(prefab_gltf, *renames)
                prefab_glb = fidelity.Glb(prefab_gltf, prefab_chunks)
                # The sampled clips go in before _build_templates gathers the templates' clips.
                prefab_keys = {o.name_full: (o, ids.ensure_id(o)) for coll in prefabs for o in coll.all_objects}
                clips.splice(prefab_glb, clip_plan.prefabs, clip_plan,
                             inject_ids(prefab_gltf, prefab_keys, node_names), prefab_keys)
                prefab_not_drawn = set(collision_only(prefab_objects))
                templates = _build_templates(prefab_gltf, prefabs, prefab_path.name, names, node_names,
                                             prefab_not_drawn)
                _restore_node_names(prefab_gltf, node_names)
                prefab_hidden = {ids.get_id(o) for o in prefab_not_drawn}
                _slim_glb(prefab_glb, prefab_hidden, prefab_path.name)
            exported_templates = {t["name"] for t in templates}
            template_names = {ptr: n for ptr, n in names.items() if n in exported_templates}
            instancers = [o.name_full for o in exported
                          if (p := instanced_prefab(o)) is not None and p.as_pointer() in template_names]
            strip_instance_contents(gltf, matched, instancers)
            dropped, kept = _drop_meshes(gltf, matched, cutters)
            # Collision-only objects and display-only objects kept for their components: not drawn.
            not_drawn = list(dict.fromkeys([*collision_only(exported), *kept]))
            hidden_ids = {ids.get_id(o) for o in not_drawn}
            glb = fidelity.Glb(gltf, chunks)
            clips.splice(glb, clip_plan.scene, clip_plan, matched, objects)  # before prune compacts the BIN
            pruned = _slim_glb(glb, hidden_ids, glb_path.name)
            instanced = fidelity.instance_meshes(glb, int(settings["instancing"]))
            if prefab_glb is not None:
                instanced += fidelity.instance_meshes(prefab_glb, int(settings["instancing"]))
            animations = gather_animations(gltf, _node_ids(gltf))
            # The game shaders of the materials the GLBs draw with.
            material_names = {glb_path: glb_material_names(gltf)}
            if templates:
                material_names[prefab_path] = glb_material_names(prefab_gltf)
            template_objects = {gs_id: obj for obj, gs_id in prefab_keys.values()} if templates else {}
            materials, shader_assets = shader_section(
                list(dict.fromkeys(n for names_in_glb in material_names.values() for n in names_in_glb)),
                [*exported, *(template_objects[e["id"]] for t in templates for e in t["entities"])])

            # After an undo or a checkpoint restore scene["gs_rev"] can be behind the last export: never repeat a rev.
            rev = max(int(scene.get(REV_PROP, 0)), _manifest_rev(manifest_path)) + 1
            manifest = build_manifest(scene, name, rev, exported, animations, templates,
                                      template_names=template_names, not_drawn=set(not_drawn), materials=materials)
            asset_entities = manifest["entities"] + [e for t in templates for e in t["entities"]]
            manifest["assets"] = copy_assets(out, _asset_values(asset_entities, shader_assets))
            lightmaps = lightmapping.manifest_section(lightmap_report, out, {o.name_full: ids.get_id(o) for o in exported})
            if lightmaps is not None:
                manifest["lightmaps"] = lightmaps  # baked lighting (lightmap.py)

            chunks = _finish_glb(glb, settings, cache, glb_path.name, report, hidden_ids)
            prefab_bytes = 0
            glb_data = build_glb(gltf, chunks)
            # A hash of every GLB the manifest names, from the bytes in memory: the runtime keys its caches on
            # it (layout exports keep it). Hashed beside the prefab GLB's finishing and the writes.
            hashes = {glb_path.name: _hash_later(glb_data)}
            if templates:
                prefab_chunks = _finish_glb(prefab_glb, settings, cache, prefab_path.name, report, prefab_hidden)
                prefab_data = build_glb(prefab_gltf, prefab_chunks)
                prefab_bytes = len(prefab_data)
                hashes[prefab_path.name] = _hash_later(prefab_data)
                _write_atomic(prefab_path, prefab_data)
            elif prefab_path.exists():
                prefab_path.unlink()
            _write_atomic(glb_path, glb_data)  # the GLBs first, then the manifest that names them
            for path, names_in_glb in material_names.items():
                _remember_material_names(path, names_in_glb)  # what a layout export reads (shader_section)
            manifest["files"] = {name: future.result() for name, future in hashes.items()}
            _write_atomic(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
            scene[REV_PROP] = rev
            size = _size_summary(report, settings, len(glb_data), prefab_bytes, _asset_bytes(out, manifest["assets"]))
            size["per_file"] = [{"file": f, "exported": raw_bytes.get(f), "bytes": n}
                                for f, n in ((glb_path.name, len(glb_data)), (prefab_path.name, prefab_bytes)) if n]
    finally:
        _exporting = False

    seconds = time.perf_counter() - started
    if compress:  # the sizes the build ships (geometry compression runs in npm run build)
        size["compressed"] = compression.measure(project.project_root() or out.parent.parent,
                                                 [out / i["file"] for i in size["per_file"]])
        for item in size["compressed"].get("files", []):
            next((i for i in size["per_file"] if i["file"] == item["file"]), {})["compressed"] = item["after"]
    result = {
        "glb": str(glb_path),
        "manifest": str(manifest_path),
        "rev": rev,
        "scene": name,
        "entities": len(exported),
        "repaired": repaired,
        "prefabs": [t["name"] for t in templates],
        "animations": len(animations) + sum(len(t["animations"]) for t in templates),
        "clips": clip_plan.summary(),
        "assets": len(manifest["assets"]),
        "materials": len(materials),
        "size": size,
        "bake": {k: bake_report.get(k) for k in ("mode", "baked", "cached", "units", "fixed", "seconds")},
        "lightmaps": lightmapping.summary(lightmap_report),
        "textures": report["textures"],
        "instancing": instanced,
        "instances": counts,
        "hidden_meshes": dropped,
        "not_drawn": [o.name for o in not_drawn],
        "pruned": pruned,
        "warnings": list(warnings.messages),
    }
    _last_export.clear()
    _last_export.update(result, seconds=round(seconds, 3), time=time.time())
    log.info("Exported '%s' rev %d (%d entities, %d prefabs, %d clips, %s) in %.2f s",
             name, rev, len(exported), len(templates), result["animations"], fidelity.format_bytes(size["bytes"]),
             seconds)
    for listener in list(_listeners):
        try:
            listener(scene, result)
        except Exception:
            log.exception("Export listener failed")
    return result


# --- Every level (§4.7) ---------------------------------------------------------------


def level_files(root=None) -> list:
    """The project's level files: <project>/scenes/*.blend, sorted."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    folder = root / SCENES_DIR if root is not None else None
    return sorted(folder.glob("*.blend")) if folder is not None and folder.is_dir() else []


def _summary(result: dict, file=None) -> dict:
    return {"file": file, "scene": result["scene"], "rev": result["rev"], "entities": result["entities"],
            "manifest": result["manifest"], "warnings": result.get("warnings", [])}


def _background_script(bake) -> str:
    package = pathlib.Path(__file__).resolve().parents[1]
    return "\n".join([
        "import json, sys, importlib",
        f"sys.path.insert(0, {str(package.parent)!r})",
        f"gs = importlib.import_module({package.name!r})",
        "gs.register()",
        f"core = importlib.import_module({package.name!r} + '.core')",
        "export, project, registry = core.export, core.project, core.registry",
        "registry.reload_registry(project.project_root())",
        "try:",
        f"    out = export.export_scene(bake={bake!r})",
        "    out = {k: out[k] for k in ('scene', 'rev', 'entities', 'manifest', 'warnings')}",
        "except Exception as exc:",
        "    out = {'error': f'{type(exc).__name__}: {exc}'}",
        f"print({EXPORT_LINE!r} + json.dumps(out), flush=True)",
    ])


def export_file(path, bake=None, blender=None, timeout=600.0) -> dict:
    """Export the level file at path in a background Blender (this session's file stays open and
    unchanged; the other file's export revision is not saved into it). Returns the export summary."""
    exe = blender or bpy.app.binary_path
    cmd = [exe, "--background", "--factory-startup", str(path), "--python-exit-code", "1",
           "--python-expr", _background_script(bake)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=dict(os.environ))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExportError(f"Could not export {path} in the background: {exc}") from exc
    lines = [line for line in proc.stdout.splitlines() if line.startswith(EXPORT_LINE)]
    if not lines:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
        raise ExportError(f"Exporting {path} in the background failed (exit {proc.returncode}): {tail}")
    out = json.loads(lines[-1][len(EXPORT_LINE):])
    if "error" in out:
        raise ExportError(f"{pathlib.Path(path).name}: {out['error']}")
    return out


def export_all(bake=None, blender=None) -> dict:
    """Export every level of the project (§4.7): each scene of the open file, then every other
    scenes/*.blend in a background Blender, then every UI file (docs/GAME-UI.md §4; an open UI file's
    scenes are screens, never levels). Returns {"scenes": [summary], "ui": [gameui_export.export_ui_file()
    results], "errors": [{file, error}]}."""
    from . import gameui, gameui_export

    root = project.project_root()
    if root is None:
        raise ExportError("Save the .blend inside a GameStudio project (folder with gamestudio.json)")
    current = pathlib.Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    ui_open = gameui.is_ui_file()

    def rel(path):
        if path is None:
            return None
        try:
            return pathlib.Path(path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return str(path)

    scenes, errors = [], []
    window = any_window()
    active = bpy.context.scene
    for scene in [] if ui_open else _user_scenes():
        try:
            if window is not None and window.scene != scene:
                window.scene = scene
            scenes.append(_summary(export_scene(scene, bake=bake), rel(current)))
        except ExportError as exc:
            errors.append({"file": rel(current), "scene": scene.name, "error": str(exc)})
        finally:
            if window is not None and window.scene != active:
                window.scene = active
    for path in level_files(root):
        if current is not None and path.resolve() == current:
            continue
        try:
            scenes.append({**export_file(path, bake=bake, blender=blender), "file": rel(path)})
        except ExportError as exc:
            errors.append({"file": rel(path), "error": str(exc)})
    for error in errors:
        log.warning("Export of %s failed: %s", error["file"], error["error"])
    ui = gameui_export.export_all_ui(root, blender=blender)  # logs its own failures
    return {"scenes": scenes, "ui": ui["ui"], "errors": errors + ui["errors"]}
