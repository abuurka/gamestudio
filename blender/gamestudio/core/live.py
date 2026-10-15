# SPDX-License-Identifier: GPL-3.0-or-later
"""Live editing: stream edits to the running game (docs/SPEC.md §5.4, §5.5).

Works only while the link is connected, a game peer is present and the
scene's "Live Link" toggle is on, in level files: a UI file's scenes are
screens, which gameui_live.py sends instead. Sources of change:

* depsgraph_update_post (persistent): iterates only depsgraph.updates.
  Transform updates become `entity.transform` items (local, Y-up), sent at
  most 30 times a second and only for IDs whose local TRS changed, so moving
  a parent sends only the parent. Geometry/shading changes and new objects
  schedule a debounced re-export followed by `scene.load` (when the
  "Live geometry reload" preference is on). The re-export does only what
  changed since the last export (journal.py): a trigger that changed nothing
  the export holds reuses it and sends nothing, one that only moved objects or
  edited components rewrites just the manifest. Removed objects are sent as
  `entity.remove`. Animation changes re-export too: an updated Action (keys
  edited), or an object whose active action or NLA tracks/strips changed
  (compared with a per-object signature, since those only tag the object).
* Prefabs (§4.4): their templates are exported whatever the scene shows, but
  objects of a prefab collection that is not in the scene send no depsgraph
  updates, and toggling gs_prefab sends none at all. A cheap signature of every
  prefab (marker, members, transforms, data, counts, components, materials,
  animation) is compared once a second, and one that includes vertex positions
  on save; a change re-exports. Vertex-only edits of an off-scene prefab mesh
  therefore reach the game on the next save or export.
* Component field and enable-toggle update callbacks, plus the components
  API, call mark_components_dirty(). After a 100 ms debounce the object's
  exported components are hashed and sent as `entity.components` if changed.
  In --background, writes to component fields never show up in
  depsgraph.updates, so the update callbacks are the primary signal. In the
  UI it is the opposite problem: a write to a property that has an update
  callback tags the object AND its data (mesh) with transform, geometry and
  shading updates. Those flags are ignored for a short time after a
  component edit so they do not trigger a re-export. Every component edit is
  also reported to the export journal (journal.note_component_edit), which
  checks those updates itself, live link or not.
* Game shaders of materials (shaders.py): the update callbacks of
  Material.gs.shader and the Material.gss_<Name> fields, and the shaders API,
  call mark_material_dirty(). After a 30 ms debounce (60 ms at most while a
  slider is dragged) the material is compared with what the game has: changed
  values are sent as `material.values` (every field, as exported: the game
  applies them in place, without a reload); a change of the shader itself, of
  the material's name or of an asset field (its file may not be in the
  export yet) re-exports and reloads instead. A write to those properties
  tags the material and its node tree as a property write does (transform and
  geometry), and the meshes that draw with it for shading; those updates are
  ignored for a short time after the edit. A material update with shading
  alone (a node or setting edit) still re-exports. The export journal sees
  the same updates and knows the manifest is stale: the next export is a
  layout one (game shader settings are manifest-only).
* undo/redo: everything is re-compared against what was last sent.

pump() does the sending; a timer calls it in the UI and tests call it directly.
"""

import json
import time

import bpy
from bpy.app.handlers import persistent

from . import components, convert, export, gameui, gameui_live, ids, journal, link, registry, shaders
from .log import get_logger

log = get_logger(__name__)

TRANSFORM_INTERVAL = 1.0 / 30.0
PREFAB_CHECK_INTERVAL = 1.0
COMPONENT_DEBOUNCE = 0.1
COMPONENT_MAX_WAIT = 0.3
MATERIAL_DEBOUNCE = 0.03
MATERIAL_MAX_WAIT = 0.06
GEOMETRY_DEBOUNCE = 1.0
GEOMETRY_MAX_WAIT = 4.0
SUPPRESS_SECONDS = 0.5
TIMER_INTERVAL = 0.02
IDLE_INTERVAL = 0.2


class _State:
    def __init__(self):
        self.active = False
        self.scene_uid = None
        self.scene_name = None      # the live scene's export name: edits name it, other levels ignore them
        self.known = {}             # object session_uid -> gs_id (or None)
        self.parent_of = {}         # object session_uid -> parent session_uid (or None)
        self.sent_trs = {}          # gs_id -> rounded (p, r, s) last sent or seeded
        self.comp_hash = {}         # gs_id -> JSON of the last sent components
        self.pending_trs = {}       # gs_id -> entity.transform item
        self.last_trs_send = 0.0
        self.dirty_comps = set()    # gs_ids
        self.comp_first = 0.0
        self.comp_last = 0.0
        self.suppress_geom = {}     # session_uid (object or its data) -> monotonic expiry
        self.mat_sent = {}          # material session_uid -> _material_state() last sent or seeded
        self.dirty_mats = set()     # material session_uids
        self.mat_first = 0.0
        self.mat_last = 0.0
        self.suppress_mat = {}      # session_uid (material or its node tree) -> monotonic expiry
        self.check_membership = False
        self.rebuild_due = None
        self.rebuild_first = None
        self.resync = False
        self.anim_sig = {}          # object session_uid -> animation signature
        self.prefab_sig = None      # cheap signature of every prefab (checked each second)
        self.prefab_geo = None      # the same with vertex positions (checked on save)
        self.prefab_checked = 0.0
        self.stats = {"transform_batches": 0, "component_messages": 0, "material_messages": 0, "rebuilds": 0,
                      "reused": 0, "removes": 0}


_s = _State()


# --- Status --------------------------------------------------------------------


def _scene_live(scene) -> bool:
    props = getattr(scene, "gs", None)
    return props is not None and props.live_enabled


def _link_ready() -> bool:
    client = link.get_link()
    return client is not None and client.connected and bool(client.game_peers())


def is_active() -> bool:
    return _s.active


def stats() -> dict:
    return dict(_s.stats)


# --- Seeding -------------------------------------------------------------------


def _trs_key(item: dict) -> tuple:
    return tuple(item["p"]), tuple(item["r"]), tuple(item["s"])


def _components_json(obj) -> str:
    return json.dumps(components.export_components(obj), sort_keys=True)


def _material_state(mat) -> tuple:
    """(state, values): what the game has of mat's game shader once it got it, and the values as
    exported (asset fields as project-relative paths). state is (exported name, shader, values JSON,
    asset values JSON), or None when mat has no game shader."""
    shader = mat.gs.shader
    if not shader:
        return None, {}
    values = shaders.get_values(mat, for_export=True)
    entry = registry.shader_schema_ref(shader)
    assets = {f["name"]: values.get(f["name"]) for f in entry["fields"] if f["type"] == "asset"} if entry else {}
    return (mat.name, shader, json.dumps(values, sort_keys=True), json.dumps(assets, sort_keys=True)), values


def _anim_signature(obj):
    """What decides an object's clips: its active action and its NLA tracks and strips."""
    anim = obj.animation_data
    if anim is None:
        return None
    tracks = tuple((t.name, t.mute, tuple((s.action.name if s.action else None, s.mute) for s in t.strips))
                   for t in anim.nla_tracks)
    return anim.action.name if anim.action else None, tracks


def _prefab_signature(with_geometry: bool = False):
    """A comparable summary of every prefab collection (see the module docstring)."""
    out = []
    for coll in export.prefab_collections():
        members = []
        for obj in coll.all_objects:
            data = obj.data
            entry = (obj.name, obj.parent.name if obj.parent else None,
                     tuple(round(v, 5) for row in obj.matrix_basis for v in row),
                     data.name if data is not None else None,
                     len(getattr(data, "vertices", ())), len(getattr(data, "polygons", ())),
                     tuple(m.name if m else None for m in getattr(data, "materials", ())),
                     _components_json(obj), _anim_signature(obj))
            if with_geometry and obj.type == "MESH" and data is not None:
                entry += (_vertex_digest(data),)
            members.append(entry)
        out.append((coll.name, tuple(c.name for c in coll.children), tuple(members)))
    return tuple(out)


def _vertex_digest(mesh) -> str:
    import hashlib

    import numpy as np

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return hashlib.sha1(co.tobytes()).hexdigest()


def _seed(scene) -> None:
    """Record the current scene as what the game has."""
    _s.known.clear()
    _s.anim_sig.clear()
    _s.parent_of.clear()
    _s.sent_trs.clear()
    _s.comp_hash.clear()
    _s.pending_trs.clear()
    _s.dirty_comps.clear()
    _s.suppress_geom.clear()
    _s.check_membership = False
    for obj in scene.objects:
        gs_id = ids.get_id(obj)
        _s.known[obj.session_uid] = gs_id
        _s.parent_of[obj.session_uid] = obj.parent.session_uid if obj.parent else None
        _s.anim_sig[obj.session_uid] = _anim_signature(obj)
        if gs_id is None:
            continue
        _s.sent_trs[gs_id] = _trs_key(convert.transform_item(obj, gs_id))
        _s.comp_hash[gs_id] = _components_json(obj)
    _s.mat_sent = {m.session_uid: _material_state(m)[0] for m in bpy.data.materials if m.gs.shader}
    _s.dirty_mats.clear()
    _s.suppress_mat.clear()
    _s.prefab_sig = _prefab_signature()
    _s.prefab_geo = _prefab_signature(with_geometry=True)
    _s.prefab_checked = time.monotonic()


def _activate(scene) -> None:
    _s.active = True
    _s.scene_uid = scene.session_uid
    _s.scene_name = export.export_name(scene)
    _s.rebuild_due = _s.rebuild_first = None
    _s.resync = False
    _seed(scene)
    log.info("Live editing active for scene '%s'", scene.name)


def _deactivate() -> None:
    _s.active = False
    _s.scene_uid = None
    _s.pending_trs.clear()
    _s.dirty_comps.clear()
    _s.dirty_mats.clear()
    _s.rebuild_due = _s.rebuild_first = None


def reseed(scene=None) -> None:
    """Treat the current state as synced (call after sending a fresh scene.load)."""
    if _s.active:
        _seed(scene or bpy.context.scene)


def _on_export(scene, _result) -> None:
    if _s.active and scene.session_uid == _s.scene_uid:
        _seed(scene)


# --- Change collection ---------------------------------------------------------


def mark_components_dirty(obj) -> None:
    """Note that obj's components changed (called by update callbacks and the API)."""
    journal.note_component_edit(obj)
    gameui_live.mark_object_dirty(obj)  # UI elements (a UI file's live link)
    if not _s.active or obj is None:
        return
    try:
        gs_id = ids.get_id(obj)
        uid = obj.session_uid
        data_uid = obj.data.session_uid if obj.data is not None else None
    except (ReferenceError, AttributeError):
        return
    now = time.monotonic()
    # The write tags the object and its data for a geometry update; ignore that.
    _s.suppress_geom[uid] = now + SUPPRESS_SECONDS
    if data_uid is not None:
        _s.suppress_geom[data_uid] = now + SUPPRESS_SECONDS
    if gs_id is None:
        return
    if not _s.dirty_comps:
        _s.comp_first = now
    _s.dirty_comps.add(gs_id)
    _s.comp_last = now


def mark_material_dirty(mat) -> None:
    """Note that mat's game shader or one of its values changed (called by the update callbacks of
    Material.gs.shader and the Material.gss_<Name> fields, and by the shaders API)."""
    if not _s.active or mat is None or export.is_exporting():
        return
    try:
        uid = mat.session_uid
        tree = mat.node_tree
        tree_uid = tree.session_uid if tree is not None else None
    except (ReferenceError, AttributeError):
        return
    now = time.monotonic()
    # The write tags the material and its node tree (and, for shading, its meshes); ignore that.
    _s.suppress_mat[uid] = now + SUPPRESS_SECONDS
    if tree_uid is not None:
        _s.suppress_mat[tree_uid] = now + SUPPRESS_SECONDS
    if not _s.dirty_mats:
        _s.mat_first = now
    _s.dirty_mats.add(uid)
    _s.mat_last = now


def _queue_transform(obj, gs_id: str) -> None:
    item = convert.transform_item(obj, gs_id)
    if _s.sent_trs.get(gs_id) == _trs_key(item):
        _s.pending_trs.pop(gs_id, None)
    else:
        _s.pending_trs[gs_id] = item


def schedule_rebuild(reason: str) -> None:
    """Re-export and reload the running game soon (debounced), e.g. after a prefab was marked."""
    if _s.active:
        _schedule_rebuild(reason)


def _schedule_rebuild(reason: str) -> None:
    now = time.monotonic()
    if _s.rebuild_first is None:
        _s.rebuild_first = now
        log.debug("Live re-export scheduled: %s", reason)
    _s.rebuild_due = now + GEOMETRY_DEBOUNCE


def _suppressed(obj, now: float) -> bool:
    """True right after a component edit on obj or on another user of its data."""
    if _s.suppress_geom.get(obj.session_uid, 0.0) >= now:
        return True
    data = obj.data
    return data is not None and _s.suppress_geom.get(data.session_uid, 0.0) >= now


def _shader_edit_noise(data, update, now: float) -> bool:
    """True for an update that a game shader edit (mark_material_dirty) made Blender send: the
    material and its node tree tagged as by a property write (transform or geometry), and the meshes
    drawing with it, tagged for shading alone. A node or material setting edit tags the material for
    shading alone: that is never noise."""
    if isinstance(data, (bpy.types.Material, bpy.types.NodeTree)):
        return ((update.is_updated_transform or update.is_updated_geometry)
                and _s.suppress_mat.get(data.session_uid, 0.0) >= now)
    if isinstance(data, bpy.types.Mesh) and update.is_updated_shading and not (
            update.is_updated_geometry or update.is_updated_transform):
        return any(m is not None and _s.suppress_mat.get(m.session_uid, 0.0) >= now for m in data.materials)
    return False


def _in_object_mode() -> bool:
    return getattr(bpy.context, "mode", "OBJECT") == "OBJECT"


def _collect(scene, depsgraph) -> None:
    now = time.monotonic()
    object_mode = _in_object_mode()
    for update in depsgraph.updates:
        data = update.id
        if isinstance(data, bpy.types.Object):
            obj = data.original
            uid = obj.session_uid
            if uid not in _s.known:
                _schedule_rebuild(f"new object '{obj.name}'")
                continue
            parent_uid = obj.parent.session_uid if obj.parent else None
            if parent_uid != _s.parent_of.get(uid):
                _schedule_rebuild(f"'{obj.name}' was re-parented")
                continue
            gs_id = _s.known[uid]
            if gs_id is not None and update.is_updated_transform:
                _queue_transform(obj, gs_id)
            anim = _anim_signature(obj)
            if anim != _s.anim_sig.get(uid):
                _s.anim_sig[uid] = anim
                _schedule_rebuild(f"animation of '{obj.name}'")
            if update.is_updated_geometry and obj.mode == "OBJECT" and object_mode and not _suppressed(obj, now):
                _schedule_rebuild(f"geometry of '{obj.name}'")
        elif isinstance(data, bpy.types.Action):
            _schedule_rebuild(f"action '{data.name}'")
        elif isinstance(data, (bpy.types.Scene, bpy.types.Collection)):
            if update.is_updated_transform or update.is_updated_geometry:
                _s.check_membership = True
        elif update.is_updated_geometry or update.is_updated_shading:
            original = getattr(data, "original", data)
            if (object_mode and not getattr(original, "is_editmode", False)
                    and _s.suppress_geom.get(original.session_uid, 0.0) < now
                    and not (_s.suppress_mat and _shader_edit_noise(original, update, now))):
                _schedule_rebuild(f"{type(data).__name__} '{data.name}'")


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if not _s.active or export.is_exporting() or scene.session_uid != _s.scene_uid:
        return
    try:
        _collect(scene, depsgraph)
    except Exception:
        log.exception("Live update collection failed")


@persistent
def _on_undo_redo(*_args):
    if _s.active:
        _s.resync = True


@persistent
def _on_load_post(*_args):
    _deactivate()


@persistent
def _on_save_post(*_args):
    if not _s.active or export.is_exporting():
        return
    try:
        geo = _prefab_signature(with_geometry=True)
    except Exception:
        log.exception("Prefab check failed")
        return
    if geo != _s.prefab_geo:
        _s.prefab_geo = geo
        _schedule_rebuild("prefab contents changed (checked on save)")


# --- Sending -------------------------------------------------------------------


def _flush_transforms(client, now: float) -> None:
    if not _s.pending_trs or now - _s.last_trs_send < TRANSFORM_INTERVAL:
        return
    items = list(_s.pending_trs.values())
    _s.pending_trs.clear()
    if client.send({"t": "entity.transform", "items": items, "scene": _s.scene_name}):
        for item in items:
            _s.sent_trs[item["id"]] = _trs_key(item)
        _s.last_trs_send = now
        _s.stats["transform_batches"] += 1


def _send_components(client, obj, gs_id: str) -> None:
    comps = components.export_components(obj)
    digest = json.dumps(comps, sort_keys=True)
    if _s.comp_hash.get(gs_id) == digest:
        return
    if client.send({"t": "entity.components", "id": gs_id, "components": comps, "scene": _s.scene_name}):
        _s.comp_hash[gs_id] = digest
        _s.stats["component_messages"] += 1


def _flush_components(client, now: float) -> None:
    if not _s.dirty_comps:
        return
    if now - _s.comp_last < COMPONENT_DEBOUNCE and now - _s.comp_first < COMPONENT_MAX_WAIT:
        return
    dirty = list(_s.dirty_comps)
    _s.dirty_comps.clear()
    known_ids = set(_s.sent_trs)
    for gs_id in dirty:
        obj = ids.find_by_id(gs_id)
        if obj is not None and gs_id in known_ids:
            _send_components(client, obj, gs_id)


def _send_material(client, mat) -> None:
    """Send mat's game shader values if they differ from what the game has. A change of the shader, of
    the material's name or of an asset field re-exports instead (the reload carries everything)."""
    uid = mat.session_uid
    state, values = _material_state(mat)
    sent = _s.mat_sent.get(uid)
    if state == sent:
        return
    if state is None or sent is None or state[:2] != sent[:2] or state[3] != sent[3]:
        what = "asset values" if state is not None and sent is not None and state[:2] == sent[:2] else "game shader"
        _schedule_rebuild(f"{what} of material '{mat.name}'")
        return
    msg = {"t": "material.values", "material": mat.name, "values": values, "scene": _s.scene_name}
    if client.send(msg):
        _s.mat_sent[uid] = state
        _s.stats["material_messages"] += 1


def _flush_materials(client, now: float) -> None:
    if not _s.dirty_mats:
        return
    if now - _s.mat_last < MATERIAL_DEBOUNCE and now - _s.mat_first < MATERIAL_MAX_WAIT:
        return
    dirty = set(_s.dirty_mats)
    _s.dirty_mats.clear()
    for mat in bpy.data.materials:
        if mat.session_uid in dirty:
            _send_material(client, mat)


def _check_membership(client, scene) -> None:
    _s.check_membership = False
    current = {obj.session_uid for obj in scene.objects}
    removed = [uid for uid in _s.known if uid not in current]
    removed_ids = []
    for uid in removed:
        gs_id = _s.known.pop(uid)
        _s.parent_of.pop(uid, None)
        if gs_id is not None:
            removed_ids.append(gs_id)
            _s.sent_trs.pop(gs_id, None)
            _s.comp_hash.pop(gs_id, None)
            _s.pending_trs.pop(gs_id, None)
    if removed_ids and client.send({"t": "entity.remove", "ids": removed_ids, "scene": _s.scene_name}):
        _s.stats["removes"] += 1
    if any(uid not in _s.known for uid in current):
        _schedule_rebuild("objects added to the scene")


def _resync(client, scene) -> None:
    """Compare everything with what was sent (after undo/redo)."""
    _s.resync = False
    _check_membership(client, scene)
    for obj in scene.objects:
        gs_id = _s.known.get(obj.session_uid)
        if gs_id is None:
            continue
        _queue_transform(obj, gs_id)
        _send_components(client, obj, gs_id)
    for mat in bpy.data.materials:
        if mat.gs.shader or mat.session_uid in _s.mat_sent:
            _send_material(client, mat)


def _rebuild(client, scene, now: float) -> None:
    from ..prefs import get_prefs

    if not _in_object_mode():
        _s.rebuild_due = now + GEOMETRY_DEBOUNCE  # wait until the user leaves edit/sculpt mode
        return
    _s.rebuild_due = _s.rebuild_first = None
    if not get_prefs().live_geometry:
        _seed(scene)
        return
    try:
        result = export.export_scene(scene)  # only what changed since the last export (journal.py)
    except Exception as exc:
        log.error("Live re-export failed: %s", exc)
        return
    if result.get("tier") == "reuse":
        _s.stats["reused"] += 1  # nothing the game has changed: no reload
    elif client.send(export.scene_load_message(result)):
        _s.stats["rebuilds"] += 1
    _seed(scene)


def pump() -> None:
    """Send pending live edits. Main thread only; tests call this directly."""
    scene = bpy.context.scene
    # A UI file's scenes are screens, not levels: gameui_live.py streams them instead.
    wanted = scene is not None and _scene_live(scene) and _link_ready() and not gameui.is_open_ui_file()
    if not wanted:
        if _s.active:
            _deactivate()
        return
    if not _s.active or _s.scene_uid != scene.session_uid:
        _activate(scene)
        return
    client = link.get_link()
    now = time.monotonic()
    _s.scene_name = export.export_name(scene)
    if len(_s.suppress_geom) > 256:
        _s.suppress_geom = {k: v for k, v in _s.suppress_geom.items() if v >= now}
    if _s.suppress_mat and not _s.dirty_mats and max(_s.suppress_mat.values()) < now:
        _s.suppress_mat.clear()
    if _s.resync:
        _resync(client, scene)
    elif _s.check_membership:
        _check_membership(client, scene)
    if now - _s.prefab_checked >= PREFAB_CHECK_INTERVAL and not export.is_exporting():
        _s.prefab_checked = now
        sig = _prefab_signature()
        if sig != _s.prefab_sig:
            _s.prefab_sig = sig
            _schedule_rebuild("prefabs changed")
    _flush_transforms(client, now)
    _flush_components(client, now)
    _flush_materials(client, now)
    if _s.rebuild_due is not None and (now >= _s.rebuild_due or now - _s.rebuild_first >= GEOMETRY_MAX_WAIT):
        _rebuild(client, scene, now)


def _timer():
    try:
        pump()
    except Exception:
        log.exception("Live pump failed")
    return TIMER_INTERVAL if _s.active else IDLE_INTERVAL


_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("undo_post", _on_undo_redo),
    ("redo_post", _on_undo_redo),
    ("load_post", _on_load_post),
    ("save_post", _on_save_post),
)


def register():
    journal.register()  # the export journal records from here on (it has no module of its own to register)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)
    export.add_listener(_on_export)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    export.remove_listener(_on_export)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _deactivate()
    journal.unregister()
