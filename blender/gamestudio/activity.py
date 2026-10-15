# SPDX-License-Identifier: GPL-3.0-or-later
"""What the agent does in Blender, and the person's controls over it.

The agent (Claude Code, through the MCP server and the agent bridge) acts directly. This module
keeps the person in charge; activity_ui.py draws it.

* The activity log. Every bridge call passes through observe() (bridge.api.call_hook): its method,
  a short summary of its arguments, when it started, how long it took, ok or the error, and the
  objects it created, changed or deleted. The newest MAX_ENTRIES stay in memory, and every call is
  appended to <project>/.gamestudio/agent-activity.jsonl (cut back to FILE_KEEP_LINES once it passes
  FILE_MAX_LINES lines).
* Pause. While paused, calls that change the scene or the open file fail with PAUSED_BY_USER
  (code "paused_by_user" in the MCP server); calls that only read still work.
* AI locks (obj.gs.ai_lock, "Protect" in the Agent panel). The structured tools refuse locked
  objects themselves. observe() also compares the locked objects before and after every change and
  takes back a call that changed one (run_python does the same itself, in bridge/pyexec.py).
* Undo to here. Each agent change is one undo step, "Agent: <tool>". Python cannot read Blender's
  undo history: ed.undo_history(item=N) jumps by index into a list Python cannot see, and an index
  past the end does nothing yet reports FINISHED. So each change also stamps the scene with its
  entry id (scene["gs_agent_step"], which undo restores with the scene), and undo_to() undoes one
  step at a time until the stamp is older than the chosen entry.

The bridge's `status` result gets `agent: {paused, locked_objects, log}` from here.
"""

import collections
import hashlib
import json
import os
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import IntProperty

from .bridge import api
from .bridge.server import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND, PAUSED_BY_USER, BridgeError
from .core import project
from .core.log import get_logger

log = get_logger(__name__)

MAX_ENTRIES = 200
FILE_NAME = "agent-activity.jsonl"
FILE_MAX_LINES = 2000
FILE_KEEP_LINES = 1000
ARGS_MAX = 160
OBJECTS_MAX = 100          # names kept per list (created, changed, deleted) in an entry
STATE_LIMIT = 1000         # above this many objects, run_python's changed objects are not worked out (~40 ms)
STAMP_PROP = "gs_agent_step"

PAUSED_HINT = "the user paused the agent in Blender; ask them or wait"
LOCK_HINT = ("Leave locked objects alone: the developer protected them from AI changes. Ask them to unlock "
             "an object (Agent panel in Blender) if it really has to change.")

# Changes to the scene or the open file that are not `mutating` (one undo step) but are refused while
# paused all the same. checkpoint, export_scene and devserver change neither, so they keep working.
ALSO_CHANGES = frozenset({"open_scene", "save", "restore_checkpoint", "undo", "redo", "play_in_blender",
                          "asset_reload", "ui_open"})
# Long calls: the status bar is redrawn before they start, so the person sees what Blender is busy with.
SLOW = frozenset({"run_python", "render_views", "render_camera_view", "export_scene", "play_in_blender",
                  "import_asset", "create_from_kit", "regenerate_kit", "validate_assets", "open_scene",
                  "restore_checkpoint", "checkpoint", "save", "asset_add", "asset_place", "compare_images",
                  "bake_lighting", "ui_open"})
_CODE_NAMES = {PAUSED_BY_USER: "paused_by_user", INVALID_PARAMS: "invalid_params",
               METHOD_NOT_FOUND: "unknown_method", INTERNAL_ERROR: "bridge_bug"}


class _State:
    def __init__(self):
        self.entries = collections.deque(maxlen=MAX_ENTRIES)
        self.seq = 0               # the last entry id; ids double as undo stamps
        self.current = None        # the entry of the call running now
        self.paused = False
        self.selected = 0          # the entry the panel shows in detail
        self.epoch = 0             # bumped on every file load: the undo steps of older entries are gone
        self.session = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        self.file_lines = {}       # log file -> its line count
        self.version = 0           # bumped on every change the UI shows


_state = _State()


# --- Reading the state -------------------------------------------------------------------------


def entries() -> list[dict]:
    """The recorded calls, newest first (the running one included)."""
    return list(reversed(_state.entries))


def entry(entry_id: int) -> dict | None:
    return next((e for e in _state.entries if e["id"] == entry_id), None)


def current() -> dict | None:
    return _state.current


def is_paused() -> bool:
    return _state.paused


def set_paused(paused: bool) -> None:
    _state.paused = bool(paused)
    _touch()
    log.info("Agent %s by the user", "paused" if paused else "resumed")


def version() -> int:
    return _state.version


def selected() -> int:
    return _state.selected


def select(entry_id: int) -> None:
    _state.selected = entry_id
    _touch()


def clear() -> None:
    """Forget the in-memory log (the file is kept). For tests."""
    _state.entries.clear()
    _state.current = None
    _state.selected = 0
    _touch()


def _touch() -> None:
    _state.version += 1


def log_path():
    root = project.project_root()
    return project.gs_dir(root) / FILE_NAME if root is not None else None


def status_info() -> dict:
    """What the bridge's status reports about the person's controls."""
    path = log_path()
    return {"paused": _state.paused, "locked_objects": lock_count(), "log": str(path) if path else None}


def has_undo_step(e: dict) -> bool:
    """Whether "Undo to here" can still reach this entry's undo step."""
    return bool(e.get("step")) and e.get("epoch") == _state.epoch


def is_undone(e: dict, stamp: int | None = None) -> bool:
    """Whether an entry's change is undone in the current state (its step is in the redo history)."""
    return has_undo_step(e) and e["step"] > (current_stamp() if stamp is None else stamp)


# --- Locks ---------------------------------------------------------------------------------------


def is_locked(obj) -> bool:
    gs = getattr(obj, "gs", None)
    return bool(gs is not None and gs.ai_lock)


def locked_objects() -> list:
    return [o for o in bpy.data.objects if is_locked(o)]


def lock_count() -> int:
    return sum(1 for o in bpy.data.objects if is_locked(o))


def set_locked(objects, locked: bool) -> int:
    """Lock or unlock objects, as one undo step (so undoing an agent change never takes a lock with it)."""
    changed = 0
    for obj in objects:
        if obj.library is None and is_locked(obj) != locked:
            obj.gs.ai_lock = locked
            changed += 1
    if changed:
        api.undo_push("Protect from Agent" if locked else "Unprotect from Agent")
    _touch()
    return changed


# Properties that change without anyone editing the object (evaluation results, UI state).
_VOLATILE = frozenset({
    "rna_type", "name_full", "is_evaluated", "original", "users", "session_uid", "is_runtime_data", "tag",
    "is_missing", "is_library_indirect", "library_weak_reference", "id_type", "is_embedded_data", "preview",
    "override_library", "asset_data", "library", "use_extra_user", "is_editmode", "is_active", "active",
    "show_expanded", "is_override_data", "execution_time", "error_location", "error_rotation", "is_valid",
    "persistent_uid", "use_fake_user", "is_linked_packed", "is_override_library",
})


def _plain(value, depth: int = 0):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return value.name
    if depth < 3:
        try:
            return tuple(_plain(v, depth + 1) for v in value)
        except TypeError:
            pass
    return repr(value)[:200]


def _rna_digest(struct) -> str:
    """A hash of a struct's own simple properties (not its collections)."""
    h = hashlib.sha1()
    for prop in struct.bl_rna.properties:
        key = prop.identifier
        if key in _VOLATILE or prop.type == "COLLECTION":
            continue
        try:
            value = getattr(struct, key)
        except Exception:
            continue
        if prop.type == "POINTER":
            value = getattr(value, "name", None) if isinstance(value, bpy.types.ID) else None
        h.update(f"{key}={_plain(value)!r};".encode())
    return h.hexdigest()[:16]


def _data_digest(data) -> tuple:
    if isinstance(data, bpy.types.Mesh):
        import numpy as np

        n = len(data.vertices)
        digest = ""
        if n <= 200_000:
            co = np.empty(n * 3, dtype=np.float32)
            data.vertices.foreach_get("co", co)
            digest = hashlib.sha1(co.tobytes()).hexdigest()[:16]
        return (n, len(data.edges), len(data.polygons), digest, tuple(uv.name for uv in data.uv_layers))
    return (_rna_digest(data),)


def object_state(obj) -> dict:
    """What a lock protects, part by part; two states are compared part by part."""
    from .core import components

    data = obj.data
    anim = obj.animation_data
    try:
        comps = json.dumps(components.export_components(obj), sort_keys=True, default=str) \
            if len(obj.gs.components) else ""
    except Exception:
        comps = "?"
    return {
        "name": obj.name,
        "transform": _plain(obj.matrix_basis),
        "parent": (obj.parent.name if obj.parent else None, obj.parent_type, obj.parent_bone,
                   _plain(obj.matrix_parent_inverse)),
        "visibility": (obj.hide_viewport, obj.hide_render, obj.hide_select, obj.display_type),
        "collections": tuple(sorted(c.name for c in obj.users_collection)),
        "data": (obj.type, data.name if data is not None else None),
        "geometry": _data_digest(data) if data is not None else None,
        "materials": tuple((s.link, s.material.name if s.material else None) for s in obj.material_slots),
        "modifiers": tuple(_rna_digest(m) for m in obj.modifiers),
        "constraints": tuple(_rna_digest(c) for c in obj.constraints),
        "components": comps,
        "properties": tuple(sorted((k, repr(_plain(obj[k]))[:200]) for k in obj.keys() if k != "gs")),
        "animation": (anim.action.name if anim and anim.action else None,
                      tuple((t.name, t.mute, tuple(s.action.name if s.action else None for s in t.strips))
                            for t in anim.nla_tracks) if anim else ()),
        "lock": is_locked(obj),
    }


def quick_states() -> dict:
    """A cheap view of every object (much less than object_state), to tell which ones run_python changed."""
    import numpy as np

    from .core import components

    meshes = {}
    for mesh in bpy.data.meshes:
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        meshes[mesh.session_uid] = (len(co), len(mesh.polygons), float(co.sum()), float(co @ co))
    out = {}
    for obj in bpy.data.objects:
        data = obj.data
        comps = ""
        if len(obj.gs.components):
            try:
                comps = json.dumps(components.export_components(obj), sort_keys=True, default=str)
            except Exception:
                comps = "?"
        out[obj.session_uid] = (
            obj.name, obj.matrix_basis.copy(), obj.parent.name if obj.parent else None,
            data.name if data is not None else None, meshes.get(data.session_uid) if data is not None else None,
            obj.hide_viewport, obj.hide_render, tuple(s.material.name if s.material else None for s in obj.material_slots),
            tuple((m.name, m.type, m.show_viewport) for m in obj.modifiers), len(obj.constraints), comps,
            obj.animation_data.action.name if obj.animation_data and obj.animation_data.action else None)
    return out


def lock_snapshot() -> dict:
    """The state of every locked object, by session_uid (stable across renames and undo)."""
    return {o.session_uid: object_state(o) for o in bpy.data.objects if is_locked(o)}


def locked_changes(before: dict) -> list[str]:
    """What changed on the objects of a lock_snapshot(): ["'Crate' (transform, materials)", ...]."""
    if not before:
        return []
    now = {o.session_uid: o for o in bpy.data.objects if o.session_uid in before}
    out = []
    for uid, old in before.items():
        obj = now.get(uid)
        if obj is None:
            out.append(f"'{old['name']}' (deleted)")
            continue
        new = object_state(obj)
        parts = [key for key in old if old[key] != new.get(key)]
        if parts:
            out.append(f"'{old['name']}' ({', '.join('unlocked' if p == 'lock' else p for p in parts)})")
    return out


def relock(before: dict) -> None:
    """Lock again what a lock_snapshot() had locked. Run after taking a change back: undo returns to
    the last undo step, which lacks a lock that was set without one (from a script, say)."""
    if before:
        for obj in bpy.data.objects:
            if obj.session_uid in before and obj.library is None and not is_locked(obj):
                obj.gs.ai_lock = True


def _check_targets(name: str, params: dict) -> None:
    """The asset library methods do not check locks themselves (asset_add with move replaces objects)."""
    keys = {"asset_replace": ("instance",), "asset_make_local": ("instance",)}.get(name, ())
    if name == "asset_add" and params.get("move"):
        keys = ("objects", "object")
    for key in keys:
        value = params.get(key)
        for ref in [value] if isinstance(value, str) else value if isinstance(value, list) else []:
            obj = bpy.data.objects.get(ref) if isinstance(ref, str) else None
            if obj is not None and is_locked(obj):
                raise BridgeError(f"'{obj.name}' is locked against AI changes by the developer (Lock from AI).",
                                  hint="Ask the developer to unlock it; do not work around the lock.")


# --- Argument summaries ------------------------------------------------------------------------


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _num(value) -> str:
    return str(value) if isinstance(value, int) else f"{round(value, 3):g}"


def _value(value, depth: int = 0) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _num(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        lines = value.splitlines() or [""]
        return clip(lines[0], 40) + (" …" if len(lines) > 1 else "")
    if isinstance(value, list):
        if value and len(value) <= 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "[" + ", ".join(_num(v) for v in value) + "]"
        if value and len(value) <= 3 and all(isinstance(v, str) for v in value):
            return "[" + ", ".join(clip(v, 24) for v in value) + "]"
        if depth == 0 and len(value) == 1 and isinstance(value[0], dict):
            return "[" + _value(value[0], 1) + "]"
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        if depth > 1:
            return "{…}"
        inner = ", ".join(f"{k}={_value(v, depth + 1)}" for k, v in list(value.items())[:4])
        return "{" + inner + (", …" if len(value) > 4 else "") + "}"
    return clip(repr(value), 40)


def summarize_args(name: str, params) -> str:
    """A short, single-line summary of a call's arguments: no code blobs, no base64 images."""
    if not isinstance(params, dict) or not params:
        return ""
    if name == "run_python":
        lines = [line.strip() for line in str(params.get("code") or "").splitlines() if line.strip()]
        text = clip(lines[0], 80) if lines else ""
        if len(lines) > 1:
            text += f" (+{len(lines) - 1} lines)"
        if params.get("label"):
            text = f"{params['label']}: {text}"
        if params.get("read_only"):
            text += " [read-only]"
        return clip(text, ARGS_MAX)
    return clip(", ".join(f"{k}={_value(v)}" for k, v in params.items() if k != "token"), ARGS_MAX)


# --- The hook ----------------------------------------------------------------------------------


def changes_scene(name: str, method, params: dict) -> bool:
    """Whether a call changes the scene or the open file (refused while paused)."""
    if method is None or (name == "run_python" and params.get("read_only")):
        return False
    return method.mutating or name in ALSO_CHANGES


def observe(name, params, conn, method, run):
    """bridge.api.call_hook: record the call; refuse it while paused; take it back if it changed a
    locked object."""
    e = _begin(name, params, conn, method)
    try:
        result = _guarded(name, params, method, e, run)
    except BridgeError as exc:
        _end(e, error=exc.message,
             code="locked" if exc.data.get("locked_changed") else _CODE_NAMES.get(exc.code, "error"))
        raise
    except Exception as exc:
        _end(e, error=f"{type(exc).__name__}: {exc}", code="bridge_bug")
        raise
    _end(e, result=result)
    if name == "status" and isinstance(result, dict):
        result["agent"] = status_info()
    return result


def _guarded(name, params, method, e, run):
    changing = changes_scene(name, method, params)
    if changing and _state.paused:
        raise BridgeError(f"The user paused the agent in Blender, so {name} was not run. Calls that only "
                          "read the scene still work.", hint=PAUSED_HINT, code=PAUSED_BY_USER)
    if changing:
        _check_targets(name, params)
    mutating = changing and method.mutating
    guard = method is not None and ((method.mutating and method.undo == "after") or name in ("undo", "redo"))
    locks = lock_snapshot() if guard else {}
    before = {o.session_uid: o.name for o in bpy.data.objects} if mutating else None
    states = quick_states() if mutating and name == "run_python" and len(bpy.data.objects) <= STATE_LIMIT else None
    stamp = _stamp(e["id"]) if mutating else None
    if name in SLOW:
        _redraw_now()
    try:
        result = run()
    except Exception:
        if stamp is not None:
            _unstamp(stamp)
        if locks and locked_changes(locks):  # a partial change before the error
            api.undo_push(f"Agent: {name} (failed, locked objects restored)")
            _step(bpy.ops.ed.undo)
            relock(locks)
        raise
    broke = locked_changes(locks)
    if broke:
        e["locked"] = broke
        _take_back(name, result)
        relock(locks)
        restored = not locked_changes(locks)
        raise BridgeError(f"{name} would change objects the developer locked from AI: {'; '.join(broke[:8])}. "
                          + ("The call was undone." if restored else
                             "Undoing it failed: the developer can undo it in Blender (Ctrl+Z)."),
                          hint=LOCK_HINT, data={"locked_changed": broke})
    if before is not None:
        _record_objects(e, before, states, result)
    if stamp is not None and _made_step(name, method, params, result):
        e["step"] = e["id"]
    return result


def _begin(name, params, conn, method) -> dict:
    _state.seq = max(_state.seq, current_stamp()) + 1
    e = {"id": _state.seq, "method": name, "args": summarize_args(name, params), "client": _client_name(conn),
         "start": time.time(), "seconds": None, "ok": None, "error": None, "code": None,
         "changes": changes_scene(name, method, params if isinstance(params, dict) else {}),
         "created": [], "changed": [], "deleted": [], "step": None, "epoch": _state.epoch,
         "_t0": time.perf_counter()}
    _state.current = e
    _state.entries.append(e)
    _touch()
    return e


def _end(e: dict, result=None, error: str | None = None, code: str | None = None) -> None:
    e["seconds"] = round(time.perf_counter() - e.pop("_t0", time.perf_counter()), 3)
    if error is None and isinstance(result, dict) and result.get("ok") is False:
        error = str(result.get("error") or "failed")
        code = "locked" if result.get("locked_changed") else "script_error"
    e["ok"] = error is None
    if error is not None:
        e["error"] = clip(" ".join(error.split()), 500)
        e["code"] = code
    if _state.current is e:
        _state.current = None
    _touch()
    try:
        _persist(e)
    except Exception:
        log.exception("Agent activity: writing the log failed")


def _client_name(conn) -> str:
    if conn is None:
        return ""
    agent = getattr(conn, "agent", None)
    if agent:
        return agent.get("name") or ""
    return (getattr(conn, "client", "") or "").partition(" ")[0]


def _record_objects(e: dict, before: dict, states: dict | None, result) -> None:
    now = {o.session_uid: o for o in bpy.data.objects}
    created = [o.name for uid, o in now.items() if uid not in before]
    deleted = [n for uid, n in before.items() if uid not in now]
    if states is not None:
        after = quick_states()
        changed = [o.name for uid, o in now.items() if uid in states and after.get(uid) != states[uid]]
    else:
        names = set(created)
        changed = []
        for n in _reported(result):
            if n not in names and n in bpy.data.objects:
                names.add(n)
                changed.append(n)
    e["created"], e["changed"], e["deleted"] = created[:OBJECTS_MAX], changed[:OBJECTS_MAX], deleted[:OBJECTS_MAX]
    counts = {k: len(v) for k, v in (("created", created), ("changed", changed), ("deleted", deleted))
              if len(v) > OBJECTS_MAX}
    if counts:
        e["counts"] = counts


def _reported(result) -> list[str]:
    """The objects a structured method says it changed (set_transform, add_component, apply_material...)."""
    if not isinstance(result, dict):
        return []
    names = []
    for key in ("updated", "added"):
        for item in result.get(key) or []:
            if isinstance(item, dict):
                names.append(item.get("name") or item.get("object"))
    for key in ("objects", "removed_from", "local_objects", "roots"):
        value = result.get(key)
        if isinstance(value, list):
            names += value
    names += [result.get("object"), result.get("instance")]
    return [n for n in names if isinstance(n, str)]


def _made_step(name, method, params, result) -> bool:
    if method.undo == "after":
        return True
    if name == "run_python" and isinstance(result, dict):
        if result.get("ok"):
            return bool(result.get("undo"))
        return result.get("rolled_back") is False and not params.get("read_only")  # "(failed)" kept its changes
    return False


def _take_back(name: str, result) -> None:
    """Undo a call that changed a locked object: its own step, or the steps undo/redo moved."""
    if name == "undo":
        steps, fn = (result or {}).get("undone", 0), bpy.ops.ed.redo
    elif name == "redo":
        steps, fn = (result or {}).get("redone", 0), bpy.ops.ed.undo
    else:
        steps, fn = 1, bpy.ops.ed.undo  # the "Agent: <name>" step dispatch just recorded
    for _ in range(steps):
        if not _step(fn):
            break


def _step(fn) -> bool:
    try:
        return "FINISHED" in fn()
    except RuntimeError:  # nothing left to undo, or no context for it
        return False


# --- Undo to here --------------------------------------------------------------------------------


def current_stamp() -> int:
    """The newest agent change in the current state (0 before the first)."""
    stamps = (s.get(STAMP_PROP, 0) for s in bpy.data.scenes)
    return max((int(v) for v in stamps if isinstance(v, int)), default=0)


def _stamp(entry_id: int):
    scene = getattr(bpy.context, "scene", None)
    if scene is None or scene.library is not None:
        return None
    old = scene.get(STAMP_PROP)
    scene[STAMP_PROP] = entry_id  # an ID property write: no depsgraph update, unlike scene.gs_agent_step = n
    return scene, old


def _unstamp(stamp) -> None:
    scene, old = stamp
    try:
        if old is None:
            del scene[STAMP_PROP]
        else:
            scene[STAMP_PROP] = old
    except (ReferenceError, KeyError, TypeError):
        pass


def undo_to(entry_id: int, max_steps: int = 512) -> tuple[bool, str]:
    """Undo back to just before an agent change (later steps, the person's too, are undone with it).
    Returns (ok, message); redo brings everything back."""
    e = entry(entry_id)
    if e is None:
        return False, "That action is no longer in the activity log."
    if not e.get("step"):
        return False, f"{e['method']} made no undo step: there is nothing to undo."
    if e.get("epoch") != _state.epoch:
        return False, "That action was made in a file that has been closed or reloaded since."
    target = e["step"]
    if current_stamp() < target:
        return False, f"{e['method']} is already undone."
    steps = 0
    while current_stamp() >= target and steps < max_steps:
        if not _step(bpy.ops.ed.undo):
            break
        steps += 1
    if current_stamp() >= target:
        for _ in range(steps):  # the step before it fell off Blender's undo history: go back to where we were
            _step(bpy.ops.ed.redo)
        return False, (f"Blender no longer has the undo steps before {e['method']} (it keeps the last "
                       f"{bpy.context.preferences.edit.undo_steps}; Preferences > System > Undo Steps).")
    _touch()
    return True, (f"Undid {steps} step{'s' if steps != 1 else ''}: back to before {e['method']}. "
                  "Redo (Ctrl+Shift+Z) brings them back.")


# --- The log file ------------------------------------------------------------------------------


def _persist(e: dict) -> None:
    path = log_path()
    if path is None:
        return
    record = {k: v for k, v in e.items() if not k.startswith("_") and k != "epoch"}
    record["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(e["start"]))
    record["session"] = _state.session
    line = json.dumps(record, ensure_ascii=False, default=str)
    key = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        count = _state.file_lines.get(key)
        if count is None:
            count = _count_lines(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        count += 1
        if count > FILE_MAX_LINES:
            count = _trim(path, FILE_KEEP_LINES)
        _state.file_lines[key] = count
    except OSError as exc:
        log.warning("Agent activity: cannot write %s: %s", path, exc)


def _count_lines(path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def _trim(path, keep: int) -> int:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-keep:]
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, path)
    return len(lines)


# --- Redraws -----------------------------------------------------------------------------------


def _redraw_now() -> None:
    """Draw the windows before a long call, which freezes Blender, so they show what it is doing."""
    if bpy.app.background:
        return
    from . import activity_ui

    try:
        activity_ui.redraw(now=True)
    except Exception:
        log.debug("Agent activity: redraw before %s failed", _state.current, exc_info=True)


@persistent
def _on_load(*_args):
    _state.epoch += 1
    _state.current = None
    _touch()


def register():
    from . import activity_ui

    bpy.types.Scene.gs_agent_step = IntProperty(
        name="Agent Step", options={"HIDDEN"},
        description="The newest agent change in this state of the scene (for Undo to Here in the Agent panel)")
    api.call_hook = observe
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    activity_ui.register()


def unregister():
    from . import activity_ui

    activity_ui.unregister()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if api.call_hook is observe:
        api.call_hook = None
    del bpy.types.Scene.gs_agent_step
