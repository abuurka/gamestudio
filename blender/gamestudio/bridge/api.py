# SPDX-License-Identifier: GPL-3.0-or-later
"""The bridge's method table and the rules every call follows.

* Methods run on the main thread (the server only calls dispatch() from pump()).
* A method marked `mutating` changes the scene. It runs in Object Mode (the bridge leaves
  Edit Mode first, which commits the edit) and, if it succeeds, becomes exactly one undo step
  named "Agent: <method>". run_python manages its own undo steps (see pyexec.py).
* Locked objects (obj.gs.ai_lock) are refused by the structured tools. activity.py checks every
  change afterwards as well (run_python included, see pyexec.py) and takes back a call that changed
  a locked object.
* Every call goes through `call_hook` when it is set: activity.py logs it, and refuses changes while
  the person has paused the agent in Blender.
"""

import difflib
from dataclasses import dataclass
from typing import Callable

import bpy

from .server import INVALID_PARAMS, METHOD_NOT_FOUND, BridgeError


@dataclass
class Method:
    name: str
    fn: Callable
    mutating: bool = False
    undo: str = "after"     # "after": one undo step on success; "custom": the method handles undo
    object_mode: bool = False


METHODS: dict[str, Method] = {}

# In --background bpy.data.is_dirty is unreliable (edits do not set it), so agent edits are tracked
# here; saving or loading a file clears the flag (see bridge/__init__.py).
agent_dirty = False


def is_dirty() -> bool:
    if bpy.app.background:
        return agent_dirty
    return bool(bpy.data.is_dirty or agent_dirty)


def method(name: str, *, mutating: bool = False, undo: str = "after", object_mode: bool | None = None):
    def register(fn):
        needs_object_mode = mutating and undo == "after" if object_mode is None else object_mode
        METHODS[name] = Method(name, fn, mutating, undo, needs_object_mode)
        return fn
    return register


# hook(name, params, conn, method or None, run) -> result, where run() executes the call. Set by activity.py.
call_hook: Callable | None = None

# In --background a file load leaves Blender's undo stack empty: the first undo_push only records a base
# that cannot be undone to, so the first agent change after a load could not be undone. The bridge records
# that base itself before the first change (set again by the bridge's load_post handler).
undo_base_needed = True


def ensure_undo_base() -> None:
    """Record the state before the first change after a file load as an undo step (background only;
    with a UI, Blender keeps an "Original" step itself)."""
    global undo_base_needed
    if bpy.app.background and undo_base_needed and undo_push("Agent: (file loaded)"):
        undo_base_needed = False


calls = 0  # agent calls running now: no dialog waits for the person then (environment.can_ask_trust)


def dispatch(name: str, params: dict, conn=None):
    global calls
    m = METHODS.get(name)
    if m is not None and m.mutating:
        ensure_undo_base()  # before the hook, which stamps the scene for "Undo to Here"
    calls += 1
    try:
        if call_hook is None:
            return _dispatch(name, params, conn)
        return call_hook(name, params, conn, METHODS.get(name), lambda: _dispatch(name, params, conn))
    finally:
        calls -= 1


def _dispatch(name: str, params: dict, conn=None):
    global agent_dirty
    m = METHODS.get(name)
    if m is None:
        close = difflib.get_close_matches(name, list(METHODS), n=3)
        raise BridgeError(f"Unknown bridge method '{name}'.",
                          hint=f"Did you mean: {', '.join(close)}?" if close else "Call 'methods' for the list.",
                          code=METHOD_NOT_FOUND)
    notes = []
    if m.object_mode:
        left = ensure_object_mode()
        if left:
            notes.append(f"Left {left} mode first (Object Mode is needed for agent edits).")
    try:
        result = m.fn(params)
    except BridgeError:
        raise
    except ValueError as exc:  # the core APIs (components, kits) raise ValueError with a user-facing message
        raise BridgeError(str(exc)) from None
    if m.mutating:
        agent_dirty = True
    if m.mutating and m.undo == "after":
        undo_push(f"Agent: {name}")
    if notes and isinstance(result, dict):
        result.setdefault("notes", []).extend(notes)
    return result


# --- Shared helpers -----------------------------------------------------------------


def undo_push(message: str) -> bool:
    """Record the current state as one undo step. False when undo is unavailable."""
    try:
        return "FINISHED" in bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        window = _any_window()
        if window is None:
            return False
        try:
            with bpy.context.temp_override(window=window):
                return "FINISHED" in bpy.ops.ed.undo_push(message=message)
        except RuntimeError:
            return False


def _any_window():
    from ..core.util import any_window

    return any_window()


def ensure_object_mode() -> str | None:
    """Switch to Object Mode if needed. Returns the mode that was left, or None."""
    mode = getattr(bpy.context, "mode", "OBJECT")
    if mode == "OBJECT":
        return None
    active = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    try:
        if active is not None:
            with bpy.context.temp_override(active_object=active, object=active):
                bpy.ops.object.mode_set(mode="OBJECT")
        else:
            bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError as exc:
        raise BridgeError(f"Could not leave {mode} mode: {exc}",
                          hint="Switch Blender to Object Mode, then retry.") from None
    return mode


def param(params: dict, key: str, kind=None, default=..., *, choices=None):
    """Read one parameter with a clear error when it is missing or has the wrong type."""
    if key not in params or params[key] is None:
        if default is ...:
            raise BridgeError(f"Missing parameter '{key}'.", code=INVALID_PARAMS)
        return default
    value = params[key]
    if kind is not None:
        ok = isinstance(value, kind) and not (kind in (int, float, (int, float)) and isinstance(value, bool))
        if not ok:
            names = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
            raise BridgeError(f"Parameter '{key}' must be {names}, got {type(value).__name__}.",
                              code=INVALID_PARAMS)
    if choices is not None and value not in choices:
        raise BridgeError(f"Parameter '{key}' must be one of {', '.join(map(str, choices))}; got {value!r}.",
                          code=INVALID_PARAMS)
    return value


def vec(params: dict, key: str, size: int = 3, default=None):
    value = params.get(key)
    if value is None:
        return default
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise BridgeError(f"Parameter '{key}' must be a list of {size} numbers, got {value!r}.",
                          code=INVALID_PARAMS)
    return [float(v) for v in value]


def names_param(params: dict, *keys: str) -> list[str]:
    """Object names from the first of `keys` present: a string or a list of strings."""
    for key in keys:
        value = params.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return [value]
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return list(value)
        raise BridgeError(f"Parameter '{key}' must be an object name or a list of names.", code=INVALID_PARAMS)
    raise BridgeError(f"Missing parameter '{keys[0]}' (an object name or a list of names).", code=INVALID_PARAMS)


def resolve(ref: str, *, writable: bool = False):
    """An object by exact name or gs_id, with suggestions when it does not exist."""
    from ..core import ids

    if not isinstance(ref, str) or not ref:
        raise BridgeError(f"Expected an object name, got {ref!r}.", code=INVALID_PARAMS)
    obj = bpy.data.objects.get(ref)
    if obj is None and ref.startswith("gs_"):
        obj = ids.find_by_id(ref)
    if obj is None:
        names = [o.name for o in bpy.data.objects]
        close = difflib.get_close_matches(ref, names, n=3)
        hint = f"Did you mean: {', '.join(close)}?" if close else "Call list_objects to see the names."
        raise BridgeError(f"No object named '{ref}'.", hint=hint)
    if writable:
        check_writable(obj)
    return obj


def check_writable(obj) -> None:
    if obj.library is not None:
        raise BridgeError(f"'{obj.name}' is linked from {obj.library.filepath} and is read-only.",
                          hint="Make it a library override first (run_python: obj.override_create()).")
    override = obj.override_library
    if override is not None and override.is_system_override:
        raise BridgeError(f"'{obj.name}' is a non-editable library override.",
                          hint="Make the override editable in Blender first.")
    if getattr(obj, "gs", None) is not None and obj.gs.ai_lock:
        raise BridgeError(f"'{obj.name}' is locked against AI changes by the developer (Lock from AI).",
                          hint="Ask the developer to unlock it; do not work around the lock.")


def rnd(values, digits: int = 3) -> list:
    return [round(float(v), digits) + 0.0 for v in values]
