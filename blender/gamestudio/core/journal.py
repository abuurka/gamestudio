# SPDX-License-Identifier: GPL-3.0-or-later
"""The export journal: what changed in a scene since its last export (docs/SPEC.md §4).

export.export_scene() asks plan() first and does only what the changes need:

* reuse: nothing that reaches the export changed and the files on disk are the ones the last
  export wrote. The last result comes back with the same rev; nothing is exported or written.
* layout: only transforms, components (their enabled state included), the manifest-only scene
  settings (gravity, background, sky, fog, active camera, view transform and exposure), the game
  shader settings of materials or asset files changed. Only the manifest is written again
  (export.refreshed_manifest): the GLBs stay byte-identical and every entity away from its GLB node
  gets "transform", local to its parent and Y-up like an entity.transform item (§4.3).
* full: everything else, and whatever the journal cannot be sure of. The plan says why.

How it knows:

* After every export a listener takes a snapshot (_capture) of the view layer's objects, the
  prefabs' members and the contents of instanced collections: name, ID, data, parent, mode,
  visibility, material slots, modifiers, instancing, animation, components and the transform as
  exported. Also the scene's frame rate, units and world; the settings fingerprint (export
  settings, component registry, format, add-on and Blender versions); images and libraries (file
  stamps); the bake cache and the scene's lightmaps; the files the export wrote (stat, else their
  hash); a digest of the keys of every action those objects use (_action_digest: actions on muted
  NLA tracks become clips, but no depsgraph evaluates them, so editing their keys sends no update);
  and, after a full export, a digest of every evaluated mesh (once per mesh: objects without
  modifiers share theirs), of every armature's pose (_digest) and of every material those objects
  draw with, without its game shader settings (_material_digest). plan() takes the same picture
  and compares.
* depsgraph_update_post. An update of any datablock other than objects, scenes and collections
  (meshes, materials, node trees, images, actions, worlds, lights, ...) means full, except a
  material update: a change of its game shader settings sends one too (with its node tree's and,
  for shading, the meshes' that draw with it), so it is checked against the material's digest at
  plan() and, when only those settings changed, the manifest takes it. A geometry or
  shading update of an object is checked against its digest at plan(): Blender sends them for
  edits that change nothing the GLB holds (a component field written through the UI, the scene's
  gravity), and an object whose evaluated geometry follows another (a boolean cutter or an
  array's offset object that moved) shows its change that way. Objects without a digest (curves,
  geometry-nodes or particle instancers, edit mode) mean full. An update of an object the
  snapshot does not hold (a dependency) means full too, and so does moving a prefab member or an
  instanced object. plan() evaluates the view layer first, so pending updates arrive before it
  decides. Objects no depsgraph evaluates (prefab members off the scene, objects disabled in
  viewports) are compared more deeply instead: mesh data, materials (_material_digest), local matrix.
* A component edit through the UI also tags the object's data. note_component_edit()
  (live.mark_components_dirty calls it) makes an update of that data in the next second count as
  the object's geometry update, checked against its digest.
* undo and redo mean full; load_post forgets every snapshot (the next export is full).

Updates while an export runs (export.is_exporting()) are ignored, and so are the export's own
writes (IDs, scene["gs_rev"], the manifest). Snapshots are kept only for exports to the project's
export folder (the ones export_scene() writes).
"""

import copy
import hashlib
import json
import math
import os
import pathlib
import time

import bpy
from bpy.app.handlers import persistent

from . import bake, components, convert, export, ids, lightmap, project, registry
from .log import get_logger

log = get_logger(__name__)

# Datablocks whose updates never reach an export (painting tools, the UI, scripts).
_IGNORED_TYPES = tuple(t for t in (getattr(bpy.types, n, None) for n in (
    "Brush", "Palette", "PaintCurve", "WorkSpace", "Screen", "WindowManager", "Text")) if t is not None)
# Objects without geometry of their own in the GLB (their data, a camera's or a light's, updates on its own).
_NO_GEOMETRY = ("EMPTY", "CAMERA", "LIGHT", "LIGHT_PROBE", "SPEAKER")
# How long after a component edit an update of the object's data counts as the edit's (note_component_edit).
COMPONENT_TAG_SECONDS = 1.0
# mesh.attributes data type -> (foreach_get property, values per item, buffer type).
_ATTRIBUTE_VALUES = {
    "FLOAT": ("value", 1, "f4"), "INT": ("value", 1, "i4"), "INT8": ("value", 1, "i4"),
    "BOOLEAN": ("value", 1, "?"), "FLOAT2": ("vector", 2, "f4"), "FLOAT_VECTOR": ("vector", 3, "f4"),
    "FLOAT_COLOR": ("color", 4, "f4"), "BYTE_COLOR": ("color", 4, "f4"), "INT32_2D": ("value", 2, "i4"),
    "INT16_2D": ("value", 2, "i4"), "QUATERNION": ("value", 4, "f4"), "FLOAT4X4": ("value", 16, "f4"),
}
# Manifest sections a layout export refreshes; any other difference is reported as "manifest".
_REFRESHED = ("rev", "exportedAt", "settings", "entities", "prefabs", "materials", "assets")
# Material settings that only change Blender's UI (the preview, texture paint), not what the exporter reads.
_MATERIAL_UI = ("preview_render_type", "use_preview_world", "paint_active_slot")

_entries: dict = {}     # manifest path (normcase) -> _Entry
_addon_version = None


class _Entry:
    """What the journal knows about one scene's export files."""

    def __init__(self, key: str, scene_uid: int):
        self.key = key
        self.scene_uid = scene_uid
        self.full = ""           # why the next export must be full ("" while it need not be)
        self.state = None        # _capture() after the last export
        self.glb_trs = {}        # object session_uid -> its transform at the last full export (its GLB node)
        self.manifest = None     # the manifest the last export wrote
        self.result = None       # the last full export's result
        self.rev = 0             # the last export's rev
        self.files = {}          # path -> (size, mtime_ns, hash or None) of the files the exports wrote
        self.depsgraph = 0       # the view layer's depsgraph at the last export
        self.digests = {}        # object session_uid -> _digest() at the last full export
        self.suspects = set()    # objects with a geometry or shading update, checked against their digest
        self.materials = {}      # material session_uid -> _material_digest() at the last full export
        self.material_suspects = set()  # materials with an update, checked against their digest
        self.edited = {}         # data session_uid -> (object session_uid, until): a component edit's tag
        self.pending = None      # the state plan() captured for a reuse or layout export

    def mark_full(self, reason: str) -> None:
        if not self.full:
            self.full = reason
            log.debug("Next export of %s is full: %s", self.key, reason)


class Plan:
    """What export_scene() does next. tier: "reuse", "layout" or "full"; reason: why (full) or what
    changed (layout). For reuse and layout: result (a copy of the last full export's), rev (the
    manifest's on disk) and manifest (what a layout export writes; reuse finds it equal)."""

    def __init__(self, tier: str, reason: str = "", entry=None):
        self.tier = tier
        self.reason = reason
        self.result = copy.deepcopy(entry.result) if entry is not None else None
        self.rev = entry.rev if entry is not None else 0
        self.manifest = None


# --- Deciding -------------------------------------------------------------------


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def plan(scene, out_dir, settings: dict) -> Plan:
    """The tier of the next export of scene to out_dir (see the module docstring). settings: the
    export settings it would use (export.export_settings with the bake override)."""
    register()
    name = export.export_name(scene)
    entry = _entries.get(_key(pathlib.Path(out_dir) / f"{name}.scene.json"))
    if entry is None or entry.state is None:
        return Plan("full", "first export of this scene since Blender started or the file was opened")
    view_layer = export._export_view_layer(scene)
    view_layer.update()  # updates still pending arrive now, in _on_depsgraph_update
    if entry.full:
        # A change of the scene's structure re-evaluates every object: say what it was, when it shows.
        try:
            reason = _compare(entry.state, _capture(scene, view_layer, settings, out_dir, name, actions=False)[0])
        except Exception:
            reason = ""
        return Plan("full", reason or entry.full)
    if entry.scene_uid != scene.session_uid:
        return Plan("full", "another scene was exported to the same files")
    if int(scene.get(export.REV_PROP, 0) or 0) < entry.rev:
        return Plan("full", "the scene's export revision went back (undo or an older copy of the file)")
    if _depsgraph_pointer(view_layer) != entry.depsgraph:
        return Plan("full", "the scene's dependency graph was rebuilt")
    state, refs = _capture(scene, view_layer, settings, out_dir, name)
    reason = _compare(entry.state, state) or _files_changed(entry)
    if reason:
        return Plan("full", reason)
    depsgraph = view_layer.depsgraph
    memo = {}
    for uid in sorted(entry.suspects):
        obj = refs.get(uid)
        if obj is None or uid not in entry.digests or _digest(obj, depsgraph, memo) != entry.digests[uid]:
            return Plan("full", f"the geometry of '{obj.name if obj is not None else '?'}' changed")
    if entry.material_suspects:
        materials, memo = {m.session_uid: m for m in bpy.data.materials}, {}
        for uid in sorted(entry.material_suspects):
            mat = materials.get(uid)
            if mat is None or _material_digest(mat, memo) != entry.materials.get(uid):
                return Plan("full", f"material '{mat.name if mat is not None else '?'}' changed")

    entity_ids = {e["id"] for e in entry.manifest.get("entities", [])}
    lightmapped = set(((entry.manifest.get("lightmaps") or {}).get("objects") or {}))
    transforms = {}
    for uid, trs in state["trs"].items():
        if trs == entry.glb_trs.get(uid):
            continue
        reason = _cannot_move(refs[uid], uid, state, entity_ids, lightmapped)
        if reason:
            return Plan("full", reason)
        gs_id = state["objects"][uid]["ID"]
        transforms[gs_id] = {"id": gs_id, "p": list(trs[0]), "r": list(trs[1]), "s": list(trs[2])}
    objects = {state["objects"][uid]["ID"]: obj for uid, obj in refs.items() if state["objects"][uid]["ID"]}
    try:
        manifest = export.refreshed_manifest(scene, out_dir, entry.manifest, objects, transforms)
    except KeyError as exc:
        return Plan("full", f"an exported object is gone ({exc})")
    changes = _manifest_changes(entry.manifest, manifest)
    entry.pending = state
    decision = Plan("layout" if changes else "reuse", "; ".join(changes), entry)
    decision.manifest = manifest
    return decision


def _cannot_move(obj, uid: int, state: dict, entity_ids: set, lightmapped: set) -> str:
    """Why an object away from its GLB node needs a full export (its node cannot just take a
    "transform"), or ""."""
    gs_id = state["objects"][uid]["ID"]
    if not all(math.isfinite(v) for part in state["trs"][uid] for v in part):
        return f"'{obj.name}' has a degenerate transform"
    if gs_id not in entity_ids:
        return f"'{obj.name}' moved and is not an entity of the export"
    if uid in state["members"]:
        return f"'{obj.name}' moved and is part of a prefab"
    if uid in state["instanced"]:
        return f"'{obj.name}' moved and an instancer draws it too"
    if gs_id in lightmapped:
        return f"'{obj.name}' moved and has baked lighting"
    parent = obj.parent
    if obj.parent_type != "OBJECT" or (parent is not None and parent.instance_type == "COLLECTION"):
        return f"'{obj.name}' moved and is parented to a bone, vertex or collection instance"
    if any(m.type == "ARMATURE" for m in obj.modifiers):
        return f"'{obj.name}' moved and is skinned"
    return ""


def _plain(value):
    return json.loads(json.dumps(value))


def _names(names: list) -> str:
    return ", ".join(names[:5]) + (f" (+{len(names) - 5})" if len(names) > 5 else "")


def _manifest_changes(old: dict, new: dict) -> list:
    """What differs between two manifests apart from rev and exportedAt ([] when nothing does)."""
    old, new = _plain(old), _plain(new)
    changes = []
    if old.get("settings") != new.get("settings"):
        changes.append("scene settings")
    before = {e["id"]: e for e in old.get("entities", [])}
    moved = [e["name"] for e in new["entities"] if e.get("transform") != before.get(e["id"], {}).get("transform")]
    edited = [e["name"] for e in new["entities"] if e.get("components") != before.get(e["id"], {}).get("components")]
    if moved:
        changes.append("moved: " + _names(moved))
    if edited:
        changes.append("components: " + _names(edited))
    if old.get("prefabs") != new.get("prefabs"):
        changes.append("prefab components")
    before_shaders, after_shaders = old.get("materials") or {}, new.get("materials") or {}
    shaded = [n for n in dict.fromkeys([*before_shaders, *after_shaders])
              if before_shaders.get(n) != after_shaders.get(n)]
    if shaded:
        changes.append("game shaders: " + _names(shaded))
    if old.get("assets") != new.get("assets"):
        changes.append("asset files")
    if not changes and old.get("entities") != new.get("entities"):
        changes.append("entities")
    if any(old.get(k) != new.get(k) for k in set(old) | set(new) if k not in _REFRESHED):
        changes.append("manifest")
    return changes


def _compare(old: dict, new: dict) -> str:
    """Why the scene needs a full export since the snapshot old, or "" (moves and component values,
    which a layout export takes, aside)."""
    if old["fingerprint"] != new["fingerprint"]:
        return "the export settings, the component registry or the add-on changed"
    if old["layer"] != new["layer"] or old["scene_objects"] != new["scene_objects"]:
        added = [new["objects"][u]["name"] for u in new["layer"] - old["layer"] if u in new["objects"]]
        removed = [old["objects"][u]["name"] for u in old["layer"] - new["layer"] if u in old["objects"]]
        if added:
            return "objects were added: " + _names(added)
        if removed:
            return "objects were removed: " + _names(removed)
        return "objects were added to or removed from the scene"
    if old["prefabs"] != new["prefabs"] or old["members"] != new["members"]:
        return "prefabs changed"
    for uid, state in new["objects"].items():
        before = old["objects"].get(uid)
        if before is None:
            return f"'{state['name']}' is new"
        for key in dict.fromkeys([*before, *state]):
            if before.get(key) != state.get(key):
                return f"'{before['name']}': {key} changed"
    if None not in (old["actions"], new["actions"]) and old["actions"] != new["actions"]:
        changed = [name for uid, (name, digest) in new["actions"].items()
                   if old["actions"].get(uid, (name, None))[1] != digest]
        return f"the keys of action '{changed[0] if changed else '?'}' changed"
    if old["scene"] != new["scene"]:
        return "the scene's frame, frame rate, units, world or view layer changed"
    if old["instanced"] != new["instanced"]:
        return "the contents of an instanced collection changed"
    if old["images"] != new["images"]:
        return "an image or its file changed"
    if old["libraries"] != new["libraries"]:
        return "a linked library changed"
    if old["folders"] != new["folders"]:
        return "baked textures or baked lighting changed"
    return ""


def _files_changed(entry) -> str:
    """Why the files the last export wrote are not those any more, or ""."""
    for path, (size, mtime, digest) in list(entry.files.items()):
        name = os.path.basename(path)
        try:
            st = os.stat(path)
        except OSError:
            return f"{name} is missing"
        if (st.st_size, st.st_mtime_ns) == (size, mtime):
            continue
        if digest is None or _hash_file(path) != digest:
            return f"{name} was changed outside this export"
        entry.files[path] = (st.st_size, st.st_mtime_ns, digest)  # touched, same bytes
    return ""


def _hash_file(path) -> str | None:
    try:
        return export.file_hash(pathlib.Path(path).read_bytes())
    except OSError:
        return None


# --- What a snapshot holds ------------------------------------------------------------


def _capture(scene, view_layer, settings: dict, out_dir, name: str, actions: bool = True) -> tuple:
    """(state, refs): the picture of scene two exports compare (see the module docstring), and
    session_uid -> object for its objects (the view layer's and the prefabs' members). actions=False
    leaves out the action digests (the dearest part, for an animated level)."""
    prefabs = export.prefab_collections()
    members = list(dict.fromkeys(o for coll in prefabs for o in coll.all_objects))
    layer = list(view_layer.objects)
    layer_uids = {o.session_uid for o in layer}
    depsgraph = view_layer.depsgraph
    collision = {o.session_uid for o in export.collision_only([*layer, *members])}
    # What the GLB also holds as instance copies: the contents of instanced collections (nested ones
    # too) and the children of vertex and face instancers.
    contents, seen = [], set()
    stack = [o for o in layer if o.instance_type in ("COLLECTION", "VERTS", "FACES")]
    while stack:
        obj = stack.pop()
        if obj.instance_type in ("VERTS", "FACES"):
            found = list(obj.children)
        elif obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
            if obj.instance_collection.session_uid in seen:
                continue
            seen.add(obj.instance_collection.session_uid)
            found = list(obj.instance_collection.all_objects)
        else:
            continue
        contents += found
        stack += [o for o in found if o.instance_type == "COLLECTION"]
    instanced = {o.session_uid for o in contents}
    memo = {}
    objects, trs, refs = {}, {}, {}
    for obj in dict.fromkeys([*layer, *members, *contents]):
        uid = obj.session_uid
        refs[uid] = obj
        objects[uid] = _object_state(obj, view_layer, depsgraph, uid in layer_uids, uid in collision, memo)
        if uid in layer_uids:
            trs[uid] = _trs(obj)
    animated = any(s["animation"] != (None, None) for s in objects.values())
    out = pathlib.Path(out_dir)
    state = {
        # Every action of these objects (active, NLA, shape keys, data): those on muted NLA tracks
        # become clips but no depsgraph evaluates them, so their edits send no update.
        "actions": {uid: (action.name_full, _action_digest(action))
                    for uid, action in _actions(refs.values()).items()} if actions else None,
        "fingerprint": _fingerprint(settings),
        "scene": _scene_signature(scene, view_layer, animated),
        "layer": frozenset(layer_uids),
        "scene_objects": frozenset(o.session_uid for o in scene.objects),
        "prefabs": _prefab_signature(prefabs),
        "members": frozenset(o.session_uid for o in members),
        "instanced": frozenset(instanced),
        "objects": objects,
        "trs": trs,
        "images": _image_signature(),
        "libraries": _library_signature(),
        # The bake cache (what bake: "cached" and "auto" use) and the scene's stored lightmaps.
        "folders": (_folder_signature(export.bake_cache_dir(out)), _folder_signature(out.parent / lightmap.DIR / name)),
    }
    return state, refs


def _trs(obj) -> tuple:
    item = convert.transform_item(obj, "")
    return tuple(item["p"]), tuple(item["r"]), tuple(item["s"])


def _evaluated(obj, depsgraph) -> bool:
    """True if the depsgraph evaluates obj (so its edits show up as updates). Not for prefab members
    off the scene or objects disabled in viewports."""
    try:
        return bool(obj.evaluated_get(depsgraph).is_evaluated)
    except (RuntimeError, ReferenceError, AttributeError):
        return False


def _matrix(matrix) -> tuple:
    return tuple(v for row in matrix for v in row)


def _object_state(obj, view_layer, depsgraph, in_layer: bool, collider: bool, memo: dict) -> dict:
    """What decides an object's part of the GLB, by name (the names appear in plan() reasons)."""
    data = obj.data
    parent = obj.parent
    coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
    evaluated = _evaluated(obj, depsgraph)
    shown = None
    if in_layer:
        try:
            shown = (obj.hide_get(view_layer=view_layer), obj.visible_get(view_layer=view_layer))
        except RuntimeError:
            pass
    # Whether components are attached decides whether export.display_only objects keep their mesh.
    may_drop = obj.type == "MESH" and (obj.display_type in ("WIRE", "BOUNDS") or obj.hide_render
                                       or (shown is not None and not shown[1]))
    state = {
        "name": obj.name_full,
        "ID": ids.get_id(obj),
        "data": (obj.type, data.session_uid, data.name_full) if data is not None else (obj.type,),
        "parent": (parent.session_uid if parent is not None else None, obj.parent_type, obj.parent_bone),
        "mode": obj.mode,
        "visibility": (obj.hide_viewport, obj.hide_render, obj.display_type, shown, evaluated),
        "material slots": tuple((s.link, s.material.session_uid if s.material else None) for s in obj.material_slots),
        "modifiers": tuple((m.name, m.type, m.show_viewport, m.show_render) for m in obj.modifiers),
        "instancing": (obj.instance_type, coll.session_uid if coll is not None else None,
                       tuple(coll.instance_offset) if coll is not None else None, len(obj.particle_systems)),
        "animation": _anim_signature(obj),
        "collider or components attached": (collider, bool(components.list_components(obj)) if may_drop else None),
        "library": (obj.library.session_uid if obj.library is not None else None, obj.override_library is not None),
    }
    if not in_layer or not evaluated:
        # A prefab template or instance copy (no "transform" can move it), or an object no depsgraph
        # reports on: its local matrix decides its node.
        state["local matrix"] = (_matrix(obj.matrix_basis), _matrix(obj.matrix_parent_inverse))
    if not evaluated:  # no depsgraph reports its edits: compare what the exporter reads
        state["geometry"] = _data_digest(obj)
        state["materials"] = tuple(_material_digest(s.material, memo) if s.material is not None else None
                                   for s in obj.material_slots)
    return state


def _anim_signature(obj) -> tuple:
    """What decides an object's clips: the active action (and slot) and the NLA tracks and strips of
    the object and of its shape keys."""
    out = []
    keys = getattr(obj.data, "shape_keys", None) if obj.type == "MESH" else None
    for owner in (obj, keys):
        anim = owner.animation_data if owner is not None else None
        if anim is None:
            out.append(None)
            continue
        slot = getattr(anim, "action_slot", None)
        out.append((anim.action.name_full if anim.action else None, getattr(slot, "identifier", None),
                    tuple((t.name, t.mute, t.is_solo, tuple((s.action.name_full if s.action else None, s.mute,
                                                             s.frame_start, s.frame_end) for s in t.strips))
                          for t in anim.nla_tracks)))
    return tuple(out)


def _actions(objects) -> dict:
    """session_uid -> action for every action the objects use: active and NLA (muted or not), on the
    object, its data and its shape keys. More than the exporter writes, never less."""
    found = {}
    for obj in objects:
        data = obj.data
        for owner in (obj, data, getattr(data, "shape_keys", None) if obj.type == "MESH" else None):
            anim = getattr(owner, "animation_data", None)
            if anim is None:
                continue
            if anim.action is not None:
                found[anim.action.session_uid] = anim.action
            for track in anim.nla_tracks:
                for strip in track.strips:
                    if strip.action is not None:
                        found[strip.action.session_uid] = strip.action
    return found


def _channelbags(action) -> list:
    """The F-curve containers of an action: the channelbags of its keyframe strips (layered actions)."""
    bags = [bag for layer in getattr(action, "layers", ()) for strip in layer.strips
            for bag in getattr(strip, "channelbags", ())]
    return bags or ([action] if hasattr(action, "fcurves") else [])


_bezier = None


def _action_digest(action) -> str:
    """A hash of an action's keys: every F-curve's keyframes (positions, interpolation, and handles or
    easing where they shape the curve), modifiers, mute and extrapolation, its groups' mute, the frame
    range and the slots. One foreach_get per array and curve, hashed in place (no copies)."""
    import numpy as np

    global _bezier
    if _bezier is None:
        _bezier = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["BEZIER"].value
    h = hashlib.sha1(repr((action.use_frame_range, tuple(action.frame_range),
                           tuple(s.identifier for s in getattr(action, "slots", ())))).encode())
    f4, i4 = np.float32, np.int32
    try:
        for bag in _channelbags(action):
            fcurves = bag.fcurves
            meta = [repr(tuple((g.name, g.mute) for g in bag.groups))]
            for name, dtype in (("array_index", i4), ("mute", np.bool_), ("extrapolation", i4)):
                values = np.empty(len(fcurves), dtype=dtype)
                fcurves.foreach_get(name, values)
                h.update(values)
            for fc in fcurves:
                points = fc.keyframe_points
                count = len(points)
                modifiers = fc.modifiers
                meta.append(f"{fc.data_path}|{count}")
                if len(modifiers):
                    meta += [_struct_digest(m) for m in modifiers]
                if not count:
                    continue
                co = np.empty(2 * count, dtype=f4)
                interp = np.empty(count, dtype=i4)
                points.foreach_get("co", co)
                points.foreach_get("interpolation", interp)
                h.update(co)
                h.update(interp)
                if (interp == _bezier).any():  # the handles shape Bezier keys
                    for name in ("handle_left", "handle_right"):
                        values = np.empty(2 * count, dtype=f4)
                        points.foreach_get(name, values)
                        h.update(values)
                if (interp > _bezier).any():  # sine, back, elastic, ...: their easing settings
                    for name, dtype in (("easing", i4), ("back", f4), ("amplitude", f4), ("period", f4)):
                        values = np.empty(count, dtype=dtype)
                        points.foreach_get(name, values)
                        h.update(values)
            h.update("\n".join(meta).encode())
    except (RuntimeError, TypeError, ValueError, AttributeError):
        return _uncertain()
    return h.hexdigest()


def _scene_signature(scene, view_layer, animated: bool) -> tuple:
    """Scene settings the GLB depends on: frame rate (clip sampling), the frame (the pose the nodes
    and bones are written in, when anything is animated), units, world and the view layer."""
    units = scene.unit_settings
    frame = (scene.frame_current, scene.frame_subframe) if animated else None
    return (scene.render.fps, scene.render.fps_base, frame, units.system, units.scale_length,
            scene.world.session_uid if scene.world is not None else None, view_layer.name)


def _prefab_signature(prefabs) -> tuple:
    names = export.prefab_names(prefabs)
    return tuple((c.session_uid, c.name_full, names[c.as_pointer()], tuple(c.instance_offset),
                  tuple(ch.session_uid for ch in c.children), tuple(o.session_uid for o in c.all_objects))
                 for c in prefabs)


def _stamp(path: str, library=None):
    try:
        st = os.stat(bpy.path.abspath(path, library=library))
    except (OSError, ValueError):
        return None
    return st.st_size, st.st_mtime_ns


def _image_signature() -> tuple:
    """Every image's source, file stamp, packed size and unsaved state (the exporter may copy the file)."""
    out = []
    for image in bpy.data.images:
        if image.source == "VIEWER":
            continue
        packed = image.packed_file
        out.append((image.session_uid, image.name_full, image.source, image.filepath,
                    _stamp(image.filepath, image.library) if image.source in ("FILE", "SEQUENCE", "TILED") else None,
                    packed.size if packed is not None else None, image.is_dirty, image.colorspace_settings.name,
                    image.alpha_mode, image.generated_type, tuple(image.generated_color),
                    image.generated_width, image.generated_height))
    return tuple(out)


def _library_signature() -> tuple:
    return tuple((lib.session_uid, lib.filepath, _stamp(lib.filepath, lib.parent)) for lib in bpy.data.libraries)


def _folder_signature(folder) -> tuple | None:
    """(name, size, mtime) of a folder's entries. A bake writes new files (which changes its folder's
    mtime); looking up a cached bake only touches meta.json, which does not."""
    try:
        with os.scandir(folder) as it:
            return tuple(sorted((e.name, e.stat().st_size, e.stat().st_mtime_ns) for e in it))
    except OSError:
        return None


def _version() -> str:
    global _addon_version
    if _addon_version is None:
        import tomllib

        manifest = pathlib.Path(__file__).resolve().parents[1] / "blender_manifest.toml"
        try:
            _addon_version = tomllib.loads(manifest.read_text(encoding="utf-8"))["version"]
        except (OSError, KeyError, ValueError):
            _addon_version = "?"
    return _addon_version


def _fingerprint(settings: dict) -> str:
    """A hash of what every export depends on: the export settings, the component registry, the
    manifest format, the glTF exporter settings and the add-on and Blender versions."""
    text = json.dumps({"settings": settings, "registry": registry.schemas(), "format": export.FORMAT_VERSION,
                       "gltf": export.GLTF_SETTINGS, "addon": _version(), "blender": bpy.app.version_string},
                      sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# --- Digests of data no depsgraph reports on --------------------------------------------------


def _uncertain() -> str:
    """A digest that never equals another: what cannot be compared needs a full export."""
    return f"uncertain:{time.monotonic_ns()}"


def _geometry_digest(mesh) -> str:
    """A hash of a mesh's topology, every attribute (positions, UV maps, colours, materials,
    smoothing, ...) and its shape keys."""
    import numpy as np

    try:
        h = hashlib.sha1(f"{len(mesh.vertices)},{len(mesh.edges)},{len(mesh.loops)},{len(mesh.polygons)}".encode())
        for items, prop, width in ((mesh.loops, "vertex_index", 1), (mesh.polygons, "loop_start", 1),
                                   (mesh.edges, "vertices", 2)):
            buf = np.empty(len(items) * width, dtype=np.int32)
            items.foreach_get(prop, buf)
            h.update(buf.tobytes())
        # Internal attributes (".corner_vert", ".select_vert", ...) are the topology above, or UI state.
        for attr in sorted((a for a in mesh.attributes if not a.name.startswith(".")), key=lambda a: a.name):
            h.update(f"{attr.name}:{attr.domain}:{attr.data_type};".encode())
            key, width, dtype = _ATTRIBUTE_VALUES.get(attr.data_type, (None, 0, None))
            if key is None:
                return _uncertain()
            buf = np.empty(len(attr.data) * width, dtype=dtype)
            attr.data.foreach_get(key, buf)
            h.update(buf.tobytes())
        keys = mesh.shape_keys
        for block in (keys.key_blocks if keys is not None else ()):
            h.update(f"{block.name}:{block.value}:{block.mute}:{block.relative_key.name};".encode())
            buf = np.empty(len(block.data) * 3, dtype=np.float32)
            block.data.foreach_get("co", buf)
            h.update(buf.tobytes())
        return h.hexdigest()
    except (RuntimeError, TypeError, ValueError, AttributeError):
        return _uncertain()


def _struct_digest(struct, skip=()) -> str:
    """A hash of a struct's own writable simple properties (a camera's lens, a light's colour, ...),
    without those named in skip."""
    h = hashlib.sha1()
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if (prop.is_readonly or prop.type in ("POINTER", "COLLECTION") or name in ("tag", "active")
                or name.startswith("show_") or name in skip):  # UI state
            continue
        try:
            value = getattr(struct, prop.identifier)
        except Exception:
            continue
        if not isinstance(value, (str, bytes)):
            try:
                value = tuple(value)
            except TypeError:
                pass
        h.update(f"{prop.identifier}={value!r};".encode())
    return h.hexdigest()


def _data_digest(obj) -> str | None:
    """A hash of the data of an object no depsgraph evaluates (its mesh as written by the exporter)."""
    data = obj.data
    if data is None:
        return None
    if obj.type == "MESH":
        return _geometry_digest(data)
    if obj.type in ("CAMERA", "LIGHT", "LIGHT_PROBE", "SPEAKER"):
        return _struct_digest(data)
    if obj.type == "ARMATURE":  # the bones' rest pose and the pose the exporter writes
        bones = tuple((b.name, b.parent.name if b.parent else None, b.use_deform, _matrix(b.matrix_local))
                      for b in data.bones)
        pose = tuple((b.name, _matrix(b.matrix_basis)) for b in obj.pose.bones) if obj.pose is not None else ()
        return hashlib.sha1(repr((bones, pose)).encode("utf-8")).hexdigest()
    if obj.type in ("CURVE", "SURFACE", "FONT", "META"):
        try:
            return _geometry_digest(obj.to_mesh())
        except RuntimeError:
            return _uncertain()
        finally:
            obj.to_mesh_clear()
    return _uncertain()


def _material_digest(mat, memo: dict) -> str:
    """A hash of what the glTF exporter reads from a material: its name, its own settings (blend mode,
    backface culling, ...) and its node tree (bake.material_digest). Not its game shader settings
    (pointer properties: the manifest carries them) nor UI state. memo: node tree -> digest."""
    try:
        text = f"{mat.name_full}|{_struct_digest(mat, _MATERIAL_UI)}|{bake.material_digest(mat, memo)}"
    except (RuntimeError, ReferenceError, AttributeError, TypeError, ValueError):
        return _uncertain()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _material_digests(refs: dict, state: dict) -> dict:
    """material session_uid -> _material_digest() for the materials of the objects a depsgraph
    evaluates (only theirs send updates)."""
    found, memo = {}, {}
    for uid, obj in refs.items():
        if not state["objects"][uid]["visibility"][-1]:
            continue
        for mat in [*(s.material for s in obj.material_slots), *(getattr(obj.data, "materials", None) or ())]:
            if mat is not None and mat.session_uid not in found:
                found[mat.session_uid] = _material_digest(mat, memo)
    return found


def _digest(obj, depsgraph, memo: dict):
    """What a geometry or shading update of obj can change in the GLB: a mesh object's evaluated mesh
    (modifiers applied) and material slots, an armature's pose; nothing for empties, cameras and
    lights. None when that cannot be told cheaply (curves and text, geometry-nodes, particle and
    vertex instancers, edit mode): an update then means full. memo: evaluated mesh pointer -> digest,
    so the objects sharing a mesh without modifiers (one evaluated mesh) hash it once."""
    try:
        if obj.type in _NO_GEOMETRY:
            return obj.type
        if (obj.mode != "OBJECT" or len(obj.particle_systems) or obj.instance_type != "NONE"
                or any(m.type == "NODES" for m in obj.modifiers)):
            return None
        evaluated = obj.evaluated_get(depsgraph)
        if obj.type == "ARMATURE":
            return tuple(v for bone in evaluated.pose.bones for row in bone.matrix for v in row)
        if obj.type != "MESH":
            return None
        slots = tuple((s.link, s.material.session_uid if s.material else None) for s in evaluated.material_slots)
        mesh = evaluated.data
        key = mesh.as_pointer()
        if key not in memo:
            memo[key] = _geometry_digest(mesh)
        return memo[key], slots
    except (RuntimeError, ReferenceError, AttributeError):
        return None


def _depsgraph_pointer(view_layer) -> int:
    try:
        return view_layer.depsgraph.as_pointer()
    except (AttributeError, RuntimeError, ReferenceError):
        return 0


# --- Recording ------------------------------------------------------------------------


def note_component_edit(obj) -> None:
    """Note a component edit of obj: writing a component property through the UI also tags obj's
    data for an update, which then counts as obj's geometry update and is checked against its
    digest (live.mark_components_dirty calls this for every component edit)."""
    if not _entries or obj is None or export.is_exporting():
        return
    try:
        uid = obj.session_uid
        data = obj.data
    except (AttributeError, ReferenceError):
        return
    if data is None:
        return
    until = time.monotonic() + COMPONENT_TAG_SECONDS
    for entry in _entries.values():
        entry.edited[data.session_uid] = (uid, until)


def _record(scene, depsgraph) -> None:
    scene_uid = scene.session_uid
    now = time.monotonic()
    updates = list(depsgraph.updates)
    # The materials updated in this batch, and their node trees (a material update tags its tree too).
    materials = {u.id.original.session_uid: u.id.original for u in updates if isinstance(u.id, bpy.types.Material)}
    trees = {m.node_tree.session_uid: uid for uid, m in materials.items() if m.node_tree is not None}
    for update in updates:
        data = update.id
        if isinstance(data, bpy.types.Object):
            if not (update.is_updated_transform or update.is_updated_geometry or update.is_updated_shading):
                continue
            obj = data.original
            for entry in _entries.values():
                if entry.scene_uid == scene_uid and not entry.full:
                    _note_object(entry, obj, update)
        elif isinstance(data, (bpy.types.Scene, bpy.types.Collection, *_IGNORED_TYPES)):
            continue  # membership, settings and visibility are compared by plan()
        else:
            original = getattr(data, "original", data)
            for entry in _entries.values():
                owner, until = entry.edited.get(original.session_uid, (None, 0.0))
                if _note_material(entry, original, update, materials, trees):
                    continue
                if owner in entry.digests and until >= now:
                    entry.suspects.add(owner)  # the tag of a component edit, most likely
                else:
                    entry.mark_full(f"{type(original).__name__} '{original.name}' changed")


def _note_material(entry, data, update, materials: dict, trees: dict) -> bool:
    """True if an update of data is part of an update of materials the snapshot has a digest of: the
    material (noted as a suspect, checked at plan()), its node tree, or a mesh it tags for shading only
    (its objects' shading updates are checked against their digests)."""
    if isinstance(data, bpy.types.Material):
        if data.session_uid not in entry.materials:
            return False
        entry.material_suspects.add(data.session_uid)
        return True
    if isinstance(data, bpy.types.ShaderNodeTree):
        return trees.get(data.session_uid) in entry.materials
    if (isinstance(data, bpy.types.Mesh) and update.is_updated_shading
            and not (update.is_updated_geometry or update.is_updated_transform)):
        return any(m is not None and m.session_uid in materials and m.session_uid in entry.materials
                   for m in data.materials)
    return False


def _note_object(entry, obj, update) -> None:
    uid = obj.session_uid
    if uid not in entry.state["objects"]:
        entry.mark_full(f"'{obj.name}' changed (new, or a dependency)")
    elif update.is_updated_geometry or update.is_updated_shading:
        if uid in entry.digests:
            entry.suspects.add(uid)
        else:
            entry.mark_full(f"the geometry of '{obj.name}' changed")
    elif uid not in entry.state["layer"]:  # moved by a constraint or driver, say: no "transform" can follow it
        entry.mark_full(f"'{obj.name}' (a prefab template or instance copy) moved")


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if not _entries or export.is_exporting():
        return
    try:
        _record(scene, depsgraph)
    except Exception:
        log.exception("Export journal: recording an update failed")
        for entry in _entries.values():
            entry.mark_full("an update could not be recorded")


@persistent
def _on_undo_redo(*_args):
    for entry in _entries.values():
        entry.mark_full("undo or redo")


@persistent
def _on_load_post(*_args):
    _entries.clear()


# --- Snapshots ------------------------------------------------------------------------


def _on_export(scene, result) -> None:
    try:
        _snapshot(scene, result)
    except Exception:
        log.exception("Export journal: the snapshot failed (the next export is full)")
        _entries.pop(_key(result.get("manifest", "")), None)


def _snapshot(scene, result) -> None:
    manifest_path = pathlib.Path(result["manifest"])
    out = manifest_path.parent
    root = project.project_root()
    if root is None or _key(out) != _key(project.export_dir(root)):
        return  # an export elsewhere (tests, tools): export_scene() never reuses it
    key = _key(manifest_path)
    entry = _entries.get(key)
    view_layer = export._export_view_layer(scene)
    if result.get("tier") in ("reuse", "layout"):
        if entry is None or entry.pending is None:
            _entries.pop(key, None)  # cannot happen; if it does, the next export is full
            return
        state = entry.pending  # what plan() saw; nothing but the manifest was written since
    else:  # a full export (export_scene_to sets no tier)
        entry = _Entry(key, scene.session_uid)
        settings = export.export_settings(root)
        settings["bake"] = (result.get("bake") or {}).get("mode") or settings["bake"]
        state, refs = _capture(scene, view_layer, settings, out, result["scene"])
        entry.glb_trs = dict(state["trs"])
        entry.result = copy.deepcopy(result)
        # Only what a depsgraph evaluates sends updates to check.
        depsgraph, memo = view_layer.depsgraph, {}
        digests = ((uid, _digest(obj, depsgraph, memo)) for uid, obj in refs.items()
                   if state["objects"][uid]["visibility"][-1])
        entry.digests = {uid: digest for uid, digest in digests if digest is not None}
        entry.materials = _material_digests(refs, state)
    data = manifest_path.read_bytes()
    manifest = json.loads(data)
    entry.state = state
    entry.pending = None
    entry.manifest = manifest
    entry.rev = int(result["rev"])
    entry.depsgraph = _depsgraph_pointer(view_layer)
    entry.full = ""
    entry.suspects.clear()  # checked by plan(): the digests still hold
    entry.material_suspects.clear()
    entry.edited.clear()
    hashes = manifest.get("files") or {}
    files = {}
    for name in dict.fromkeys([manifest.get("glb"), *(t.get("glb") for t in manifest.get("prefabs", []))]):
        if name:
            st = (out / name).stat()
            files[str(out / name)] = (st.st_size, st.st_mtime_ns, hashes.get(name))
    for atlas in (manifest.get("lightmaps") or {}).get("atlases", []):
        path = out / atlas["file"].split("?", 1)[0]
        if path.is_file():
            st = path.stat()
            files[str(path)] = (st.st_size, st.st_mtime_ns, None)
    st = manifest_path.stat()
    files[str(manifest_path)] = (st.st_size, st.st_mtime_ns, export.file_hash(data))
    entry.files = files
    _entries[key] = entry


def reset() -> None:
    """Forget every snapshot: the next export of every scene is full."""
    _entries.clear()


# --- Registration -----------------------------------------------------------------------

_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("undo_post", _on_undo_redo),
    ("redo_post", _on_undo_redo),
    ("load_post", _on_load_post),
)


def register():
    """Idempotent: plan() calls it too, so the journal works wherever export_scene() runs."""
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)
    export.add_listener(_on_export)


def unregister():
    export.remove_listener(_on_export)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _entries.clear()
