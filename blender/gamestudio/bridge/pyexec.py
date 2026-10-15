# SPDX-License-Identifier: GPL-3.0-or-later
"""run_python: the escape hatch. Runs agent-written code in Blender's Python, on the main thread.

The code sees `bpy`, `C` (bpy.context), `D` (bpy.data), `mathutils`, `Vector`, `Matrix`,
`Euler`, `math` and `gs` (the gamestudio package: gs.core.components, gs.kits, ...). Its value
is the variable `result` if the code sets one, else the value of a final expression.

Undo: the state after the call is pushed as one undo step named "Agent: run_python", so one
Ctrl+Z undoes the whole call. If the code raises (or runs past its timeout), the scene is rolled
back to the previous undo step, which is the state before the call as long as every earlier change
was an undo step (every agent tool is; so are Blender's own operators), unless rollback is off.

AI locks: the objects the developer locked (obj.gs.ai_lock) are compared before and after the code
runs (activity.lock_snapshot). If one changed, the whole call is undone and fails, rollback or not.

Risks: this is arbitrary code in the user's Blender, with the user's permissions. It can
delete data, write or delete files, open network connections, or hang Blender (the timeout only
interrupts Python code, not a long-running C call such as a huge modifier). Prefer the
structured tools; keep code short and scene-scoped.
"""

import ast
import contextlib
import io
import json
import math
import sys
import time
import traceback

import bpy

from ..activity import LOCK_HINT, lock_snapshot, locked_changes, relock
from .api import BridgeError, method, param, undo_push
from .server import INVALID_PARAMS

MAX_OUTPUT = 20_000
MAX_RESULT_CHARS = 40_000
DEFAULT_TIMEOUT = 60.0


class ScriptTimeout(Exception):
    pass


def to_jsonable(value, depth: int = 0):
    """A JSON-compatible view of a Python value (bpy structs become their names or reprs)."""
    if depth > 6:
        return repr(value)[:200]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in list(value.items())[:500]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v, depth + 1) for v in list(value)[:1000]]
    if isinstance(value, bpy.types.ID):
        return {"type": type(value).__name__, "name": value.name}
    if hasattr(value, "to_list"):        # mathutils types
        try:
            return to_jsonable(value.to_list(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, bpy.types.bpy_struct):
        try:
            return [to_jsonable(v, depth + 1) for v in list(value)[:1000]]
        except Exception:
            pass
    return repr(value)[:500]


def _compile(code: str):
    try:
        tree = ast.parse(code, filename="<agent>", mode="exec")
    except SyntaxError as exc:
        raise BridgeError(f"SyntaxError: {exc.msg} (line {exc.lineno}, column {exc.offset})",
                          hint="Fix the code and call run_python again.",
                          data={"line": exc.lineno, "text": (exc.text or "").rstrip()}) from None
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = ast.Expression(tree.body.pop().value)
    body = compile(tree, "<agent>", "exec")
    tail = compile(last_expr, "<agent>", "eval") if last_expr is not None else None
    return body, tail


def _namespace() -> dict:
    import mathutils

    from .. import __name__ as gs_package
    gs = sys.modules[gs_package]
    return {"__name__": "__agent__", "bpy": bpy, "C": bpy.context, "D": bpy.data, "mathutils": mathutils,
            "Vector": mathutils.Vector, "Matrix": mathutils.Matrix, "Euler": mathutils.Euler,
            "Quaternion": mathutils.Quaternion, "math": math, "gs": gs}


def _tracer(deadline: float):
    count = [0]

    def local(frame, event, arg):
        count[0] += 1
        if count[0] & 1023 == 0 and time.monotonic() > deadline:
            raise ScriptTimeout()
        return local

    def global_trace(frame, event, arg):
        return local
    return global_trace


def _clip_result(value):
    encoded = json.dumps(value, default=str)
    if len(encoded) > MAX_RESULT_CHARS:
        return encoded[:MAX_RESULT_CHARS] + f"... [truncated {len(encoded) - MAX_RESULT_CHARS} characters]"
    return value


def fingerprint() -> dict:
    """A cheap summary of the data a script could change (read_only checks)."""
    import hashlib

    import numpy as np

    out = {"counts": {name: len(getattr(bpy.data, name)) for name in
                      ("objects", "meshes", "materials", "collections", "actions", "images", "cameras", "lights",
                       "node_groups", "scenes", "worlds")}}
    objects = {}
    for obj in bpy.data.objects:
        objects[obj.name] = (obj.type, obj.data.name if obj.data else None, obj.parent.name if obj.parent else None,
                             tuple(round(v, 6) for row in obj.matrix_basis for v in row), obj.hide_viewport,
                             tuple(c.name for c in obj.users_collection), tuple(sorted(obj.keys())))
    out["objects"] = objects
    meshes = {}
    for mesh in bpy.data.meshes:
        n = len(mesh.vertices)
        digest = ""
        if n <= 200_000:
            co = np.empty(n * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", co)
            digest = hashlib.sha1(co.tobytes()).hexdigest()
        meshes[mesh.name] = (n, len(mesh.polygons), tuple(m.name if m else None for m in mesh.materials), digest)
    out["meshes"] = meshes
    try:
        from ..core import components

        out["components"] = {o.name: json.dumps(components.export_components(o), sort_keys=True, default=str)
                             for o in bpy.data.objects if len(o.gs.components)}
    except Exception:
        pass
    scene = bpy.context.scene
    out["scene"] = (scene.name, scene.frame_current, scene.camera.name if scene.camera else None)
    return out


def _diff(a: dict, b: dict) -> list:
    changes = []
    for name, count in a["counts"].items():
        if b["counts"].get(name) != count:
            changes.append(f"{name}: {count} -> {b['counts'].get(name)}")
    for key, label in (("objects", "object"), ("meshes", "mesh"), ("components", "components of")):
        old, new = a.get(key, {}), b.get(key, {})
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                changes.append(f"{label} '{name}' changed" if name in old and name in new
                               else f"{label} '{name}' {'added' if name in new else 'removed'}")
    if a["scene"] != b["scene"]:
        changes.append("scene settings changed")
    return changes


def _clip(text: str) -> str:
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + f"\n... [{len(text) - MAX_OUTPUT} more characters]"
    return text


@method("run_python", mutating=True, undo="custom")
def run_python(params):
    code = param(params, "code", str)
    if not code.strip():
        raise BridgeError("code is empty.", code=INVALID_PARAMS)
    timeout = float(params.get("timeout", DEFAULT_TIMEOUT))
    rollback = bool(params.get("rollback_on_error", True))
    read_only = bool(params.get("read_only", False))
    label = str(params.get("label") or "run_python")[:60]
    body, tail = _compile(code)
    before = fingerprint() if read_only else None
    locks = lock_snapshot()

    ns = _namespace()
    out, err = io.StringIO(), io.StringIO()
    value = None
    failure = None
    started = time.perf_counter()
    previous_trace = sys.gettrace()
    sys.settrace(_tracer(time.monotonic() + timeout))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(body, ns)
            if tail is not None:
                value = eval(tail, ns)
    except ScriptTimeout:
        failure = {"error": f"Timed out after {timeout:g} s", "traceback": None,
                   "split_hint": ("Split the work over several calls (e.g. one object, or a few hundred items, per "
                                  "call), use bpy.data or bmesh instead of bpy.ops inside loops, and avoid "
                                  "view_layer.update() per item. Raise timeout (max 600) only for work that is "
                                  "genuinely long.")}
    except BaseException as exc:  # SystemExit/KeyboardInterrupt from agent code must not end Blender
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        # Keep only the frames from the agent's code.
        frames = [line for line in tb if "<agent>" in line or not line.startswith("  File")]
        failure = {"error": f"{type(exc).__name__}: {exc}", "traceback": "".join(frames)[-6000:]}
    finally:
        sys.settrace(previous_trace)
    seconds = round(time.perf_counter() - started, 4)
    if "result" in ns and ns["result"] is not None:
        value = ns["result"]

    response = {"stdout": _clip(out.getvalue()), "stderr": _clip(err.getvalue()), "seconds": seconds}
    if failure is not None:
        response.update(ok=False, **failure)
        broke = [] if rollback else locked_changes(locks)  # a locked object is restored even without rollback
        pushed = undo_push(f"Agent: {label} (failed)")
        response["rolled_back"] = False
        if (rollback or broke) and pushed:
            try:
                response["rolled_back"] = "FINISHED" in bpy.ops.ed.undo()
            except RuntimeError:
                pass
            relock(locks)
        response["hint"] = ("The scene was restored to its state before the call. " if response["rolled_back"]
                            else "Changes made before the error were kept (one undo step). ") + \
            "Fix the code and retry."
        if broke:
            response["locked_changed"] = broke
        return response
    broke = locked_changes(locks)
    if broke:
        undo_push(f"Agent: {label} (changed locked objects, reverted)")
        try:
            reverted = "FINISHED" in bpy.ops.ed.undo()
        except RuntimeError:
            reverted = False
        relock(locks)
        response.update(ok=False, error="The code changed objects the developer locked from AI: " + "; ".join(broke[:8]),
                        locked_changed=broke, rolled_back=reverted,
                        hint=("The call was undone. " if reverted else "The change could not be undone. ") + LOCK_HINT)
        return response
    if read_only:
        changed = _diff(before, fingerprint())
        if changed:
            # Record the change only to take it back: undo returns to the state before the call.
            undo_push(f"Agent: {label} (read-only, reverted)")
            try:
                reverted = "FINISHED" in bpy.ops.ed.undo()
            except RuntimeError:
                reverted = False
            relock(locks)
            response.update(ok=False, error="read_only code changed data: " + "; ".join(changed[:8]),
                            rolled_back=reverted,
                            hint=("The change was undone. " if reverted else "The change could not be undone. ")
                            + "Drop read_only to edit the scene.")
            return response
        response.update(ok=True, result=_clip_result(to_jsonable(value)), undo=False)
        return response
    has_undo = undo_push(f"Agent: {label}")
    text = to_jsonable(value)
    encoded = json.dumps(text, default=str)
    if len(encoded) > MAX_RESULT_CHARS:
        text = encoded[:MAX_RESULT_CHARS] + f"... [truncated {len(encoded) - MAX_RESULT_CHARS} characters]"
    response.update(ok=True, result=text, undo=has_undo)
    return response
