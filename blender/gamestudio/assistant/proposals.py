# SPDX-License-Identifier: GPL-3.0-or-later
"""Staged AI changes ("proposals") and the human-approved apply step.

The assistant never edits the scene or project files directly. Write tools
create a Proposal here. The developer reviews it in the Assistant panel, and
nothing changes until they click Apply:

* A scene proposal is a list of typed operations. Applying it runs inside one
  operator with the UNDO flag, so one proposal is one Ctrl+Z step.
* A file proposal is a set of exact text replacements, or a new file. Applying
  it backs up the original under .gamestudio/ai/backups/ and can be reverted.

Each proposal records a fingerprint of the state it was based on. If that
state changes before Apply, the proposal is marked stale and needs explicit
confirmation (research/07 section 4).
"""

import difflib
import hashlib
import math
import pathlib
import re
import shutil
import time
from dataclasses import dataclass, field

import bmesh
import bpy
from mathutils import Euler

from ..core import components, ids, registry


class ProposalError(Exception):
    """A model-facing error: the message is returned to the model as a tool error."""


# --------------------------------------------------------------------------- data


@dataclass
class Proposal:
    id: str
    kind: str  # "scene" | "file"
    title: str
    rationale: str
    source: str  # "assistant" | "mcp"
    risk: str = "low"  # low | medium | high
    risk_reasons: list = field(default_factory=list)
    status: str = "pending"  # pending | applied | rejected | failed | reverted
    created: float = field(default_factory=time.time)
    message: str = ""
    # Scene proposals
    operations: list = field(default_factory=list)
    fingerprints: dict = field(default_factory=dict)
    # File proposals
    path: str = ""  # project-relative, forward slashes
    file_mode: str = ""  # "edit" | "create"
    edits: list = field(default_factory=list)
    new_content: str = ""
    base_hash: str = ""
    applied_hash: str = ""
    eol: str = "\n"
    diff: str = ""
    backup_path: str = ""
    preview_text: str = ""  # name of a Text datablock showing the preview, if any

    def summary_lines(self) -> list[str]:
        if self.kind == "file":
            verb = "Create" if self.file_mode == "create" else "Edit"
            return [f"{verb} {self.path}"]
        return [describe_op(op) for op in self.operations]

    def to_model_dict(self) -> dict:
        """What the model sees about this proposal."""
        return {
            "proposal_id": self.id,
            "status": self.status,
            "risk": self.risk,
            "title": self.title,
            "changes": self.summary_lines(),
            "note": "Staged for the developer's review. Nothing has changed yet; "
                    "the developer applies or rejects it in the Assistant panel.",
        }


class ProposalStore:
    def __init__(self):
        self._items: dict[str, Proposal] = {}
        self._counter = 0
        self._updates: list[dict] = []  # decisions not yet reported to the model

    def new_id(self) -> str:
        self._counter += 1
        return f"p{self._counter}"

    def add(self, proposal: Proposal) -> Proposal:
        self._items[proposal.id] = proposal
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        return self._items.get(proposal_id)

    def all(self) -> list[Proposal]:
        return list(self._items.values())

    def pending(self) -> list[Proposal]:
        return [p for p in self._items.values() if p.status == "pending"]

    def record_decision(self, proposal: Proposal, decision: str, note: str = "") -> None:
        self._updates.append({"proposal_id": proposal.id, "title": proposal.title,
                              "decision": decision, "note": note})

    def take_updates(self) -> list[dict]:
        updates, self._updates = self._updates, []
        return updates

    def clear(self) -> None:
        for p in self._items.values():
            remove_preview(p)
        self._items.clear()
        self._updates.clear()


STORE = ProposalStore()


# ------------------------------------------------------------------ object lookup


def resolve_object(ref, *, allow_missing=False):
    """Find an object by exact name or gs_id. Raises ProposalError with suggestions."""
    if not isinstance(ref, str) or not ref:
        raise ProposalError(f"Expected an object name, got {ref!r}")
    obj = bpy.data.objects.get(ref)
    if obj is None and ref.startswith("gs_"):
        obj = ids.find_by_id(ref)
    if obj is None and not allow_missing:
        names = [o.name for o in bpy.data.objects]
        close = difflib.get_close_matches(ref, names, n=3)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ProposalError(f"No object named '{ref}'.{hint}")
    return obj


def check_writable(obj) -> None:
    if obj.library is not None:
        raise ProposalError(f"'{obj.name}' is linked from a library and cannot be edited here.")
    override = obj.override_library
    if override is not None and override.is_system_override:
        raise ProposalError(f"'{obj.name}' is a non-editable library override.")
    if obj.gs.ai_lock:
        raise ProposalError(f"'{obj.name}' is locked against AI changes by the developer.")


# ------------------------------------------------------------ scene operations

PRIMITIVES = ("cube", "sphere", "icosphere", "cylinder", "cone", "plane", "empty")

_VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
_COLOR = {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
          "minItems": 3, "maxItems": 4}

# Input schema for one operation. A flat schema, because the op-specific checks
# happen in validate_scene_ops with clearer error messages.
OPERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": [
            "create_object", "set_transform", "rename", "add_component", "remove_component",
            "set_component_field", "set_parent", "move_to_collection", "set_color",
            "duplicate_object", "delete_object",
        ]},
        "object": {"type": "string", "description": "Target object name (or gs_id)."},
        "name": {"type": "string", "description": "create_object/duplicate_object: name for the new object."},
        "new_name": {"type": "string", "description": "rename: the new name."},
        "primitive": {"type": "string", "enum": list(PRIMITIVES)},
        "size": {"type": "number", "minimum": 0.001, "maximum": 10000},
        "location": {**_VEC3, "description": "Blender coordinates (Z-up, metres)."},
        "rotation_deg": {**_VEC3, "description": "XYZ Euler rotation in degrees."},
        "scale": _VEC3,
        "parent": {"type": ["string", "null"], "description": "set_parent/create_object: parent name, or null."},
        "collection": {"type": "string"},
        "component": {"type": "string"},
        "values": {"type": "object", "description": "add_component: initial field values."},
        "field": {"type": "string"},
        "value": {"description": "set_component_field: the new value."},
        "color": _COLOR,
    },
    "required": ["op"],
    "additionalProperties": False,
}

_REQUIRED = {
    "create_object": ("name", "primitive"),
    "set_transform": ("object",),
    "rename": ("object", "new_name"),
    "add_component": ("object", "component"),
    "remove_component": ("object", "component"),
    "set_component_field": ("object", "component", "field", "value"),
    "set_parent": ("object", "parent"),
    "move_to_collection": ("object", "collection"),
    "set_color": ("object", "color"),
    "duplicate_object": ("object", "name"),
    "delete_object": ("object",),
}


def describe_op(op: dict) -> str:
    kind = op["op"]
    target = op.get("object", "")
    if kind == "create_object":
        where = f" at {_fmt_vec(op['location'])}" if "location" in op else ""
        return f"Create {op['primitive']} '{op['name']}'{where}"
    if kind == "set_transform":
        parts = [f"{k}={_fmt_vec(op[k])}" for k in ("location", "rotation_deg", "scale") if k in op]
        return f"Set transform of '{target}': {', '.join(parts)}"
    if kind == "rename":
        return f"Rename '{target}' to '{op['new_name']}'"
    if kind == "add_component":
        vals = f" {op['values']}" if op.get("values") else ""
        return f"Add {op['component']} to '{target}'{vals}"
    if kind == "remove_component":
        return f"Remove {op['component']} from '{target}'"
    if kind == "set_component_field":
        return f"Set {op['component']}.{op['field']} = {op['value']!r} on '{target}'"
    if kind == "set_parent":
        return f"Parent '{target}' to '{op['parent']}'" if op["parent"] else f"Clear parent of '{target}'"
    if kind == "move_to_collection":
        return f"Move '{target}' to collection '{op['collection']}'"
    if kind == "set_color":
        return f"Set color of '{target}' to {_fmt_vec(op['color'])}"
    if kind == "duplicate_object":
        return f"Duplicate '{target}' as '{op['name']}'"
    if kind == "delete_object":
        return f"DELETE '{target}'"
    return kind


def _fmt_vec(v) -> str:
    return "(" + ", ".join(f"{x:g}" for x in v) + ")"


def _fingerprint(obj, aspect: str, component: str | None = None) -> str:
    if aspect == "transform":
        return ",".join(f"{v:.4f}" for row in obj.matrix_basis for v in row)
    if aspect == "component":
        if component not in components.list_components(obj):
            return "absent"
        if registry.get_schema(component) is None:
            return "missing-definition"
        return repr(sorted(components.get_values(obj, component).items()))
    if aspect == "parent":
        return obj.parent.name if obj.parent else ""
    return obj.name


_ASPECT = {
    "set_transform": "transform",
    "add_component": "component",
    "remove_component": "component",
    "set_component_field": "component",
    "set_parent": "parent",
    "delete_object": "transform",
    "duplicate_object": "transform",
}


def validate_scene_ops(ops: list) -> tuple[list[dict], dict]:
    """Check every operation against the current scene and registry.

    Returns the normalized operations and the staleness fingerprints. Raises
    ProposalError naming the first problem, so the model can fix it and retry.
    """
    if not ops:
        raise ProposalError("A scene proposal needs at least one operation.")
    created: set[str] = set()
    deleted: set[str] = set()
    fingerprints: dict[str, str] = {}
    normalized = []

    def target(ref, op_index):
        if ref in created:
            return None  # created earlier in this proposal
        if ref in deleted:
            raise ProposalError(f"Operation {op_index}: '{ref}' is deleted earlier in this proposal.")
        obj = resolve_object(ref)
        check_writable(obj)
        return obj

    for i, raw in enumerate(ops):
        op = dict(raw)
        kind = op.get("op")
        missing = [k for k in _REQUIRED.get(kind, ()) if k not in op]
        if missing:
            raise ProposalError(f"Operation {i} ({kind}): missing {', '.join(missing)}.")

        obj = target(op["object"], i) if "object" in op and kind != "create_object" else None

        if kind == "create_object":
            if op["name"] in created or bpy.data.objects.get(op["name"]):
                raise ProposalError(f"Operation {i}: an object named '{op['name']}' already exists.")
            if op.get("parent"):
                target(op["parent"], i)
            created.add(op["name"])
        elif kind == "duplicate_object":
            if op["name"] in created or bpy.data.objects.get(op["name"]):
                raise ProposalError(f"Operation {i}: an object named '{op['name']}' already exists.")
            created.add(op["name"])
        elif kind == "rename":
            if not op["new_name"].strip():
                raise ProposalError(f"Operation {i}: new_name is empty.")
        elif kind in ("add_component", "remove_component", "set_component_field"):
            schema = registry.get_schema(op["component"])
            if schema is None and kind != "remove_component":
                known = ", ".join(s["name"] for s in registry.schemas())
                raise ProposalError(f"Operation {i}: unknown component '{op['component']}'. Known: {known}.")
            if kind == "add_component":
                for fname, fval in (op.get("values") or {}).items():
                    op["values"][fname] = _coerce(schema, fname, fval, i)
            elif kind == "set_component_field":
                op["value"] = _coerce(schema, op["field"], op["value"], i)
                if obj is not None and op["component"] not in components.list_components(obj):
                    if not _added_earlier(normalized, op["object"], op["component"]):
                        raise ProposalError(
                            f"Operation {i}: '{op['object']}' has no {op['component']} component. "
                            "Add it first with add_component.")
        elif kind == "set_parent":
            if op["parent"]:
                target(op["parent"], i)
        elif kind == "delete_object":
            deleted.add(op["object"])

        if obj is not None and kind in _ASPECT:
            aspect = _ASPECT[kind]
            key = f"{obj.name}:{aspect}:{op.get('component', '')}"
            fingerprints.setdefault(key, _fingerprint(obj, aspect, op.get("component")))
        elif obj is not None:
            fingerprints.setdefault(f"{obj.name}:name:", obj.name)
        normalized.append(op)
    return normalized, fingerprints


def _added_earlier(ops, obj_name, component) -> bool:
    return any(o["op"] == "add_component" and o.get("object") == obj_name
               and o.get("component") == component for o in ops)


def _coerce(schema, field_name, value, op_index):
    fields = {f["name"]: f for f in schema["fields"]}
    if field_name not in fields:
        raise ProposalError(
            f"Operation {op_index}: {schema['name']} has no field '{field_name}'. "
            f"Fields: {', '.join(fields)}.")
    try:
        coerced = components.coerce_value(fields[field_name], value)
    except ValueError as exc:
        raise ProposalError(f"Operation {op_index}: {exc}") from None
    # Entity fields come back as Object references; keep the plain name in the proposal.
    if isinstance(coerced, bpy.types.Object):
        return coerced.name
    if hasattr(coerced, "__len__") and not isinstance(coerced, str):
        return list(coerced)
    return coerced


def stale_reasons(proposal: Proposal) -> list[str]:
    """Return what changed since the proposal was made (empty when still current)."""
    if proposal.kind == "file":
        if _project_root is None:  # e.g. a scene outside the project was opened since
            return ["no GameStudio project is open"]
        current = _read_text(_abs_path(proposal.path)) if proposal.file_mode == "edit" else None
        if proposal.file_mode == "create":
            return [f"{proposal.path} now exists"] if _abs_path(proposal.path).exists() else []
        if current is None:
            return [f"{proposal.path} was deleted"]
        return [] if _hash(current[0]) == proposal.base_hash else [f"{proposal.path} changed on disk"]
    reasons = []
    for key, value in proposal.fingerprints.items():
        name, aspect, comp = key.split(":", 2)
        obj = bpy.data.objects.get(name)
        if obj is None:
            reasons.append(f"'{name}' no longer exists")
        elif _fingerprint(obj, aspect, comp or None) != value:
            reasons.append(f"'{name}' {aspect} changed")
    return reasons


def apply_scene(proposal: Proposal, context) -> list[str]:
    """Run every operation through bpy.data. Called inside the Apply operator."""
    # Re-validate against the current state so a failure happens before any change.
    validate_scene_ops(proposal.operations)
    names: dict[str, str] = {}  # requested name -> actual Blender name
    touched: list[str] = []

    def get(ref):
        obj = bpy.data.objects.get(names.get(ref, ref))
        return obj if obj is not None else resolve_object(ref)

    for op in proposal.operations:
        kind = op["op"]
        if kind == "create_object":
            obj = _create_primitive(op, context)
            names[op["name"]] = obj.name
            if op.get("parent"):
                _set_parent(obj, get(op["parent"]))
        elif kind == "duplicate_object":
            src = get(op["object"])
            obj = src.copy()  # shares mesh data, like Alt+D
            obj.name = op["name"]
            if "gs_id" in obj:
                del obj["gs_id"]
            for coll in src.users_collection:
                coll.objects.link(obj)
            ids.ensure_id(obj)
            if "location" in op:
                obj.location = op["location"]
            names[op["name"]] = obj.name
        else:
            obj = get(op["object"])
            if kind == "set_transform":
                _set_transform(obj, op)
            elif kind == "rename":
                obj.name = op["new_name"]
                names[op["object"]] = obj.name
            elif kind == "add_component":
                values = dict(op.get("values") or {})
                components.add_component(obj, op["component"], values or None)
            elif kind == "remove_component":
                components.remove_component(obj, op["component"])
            elif kind == "set_component_field":
                components.set_field(obj, op["component"], op["field"], op["value"])
            elif kind == "set_parent":
                _set_parent(obj, get(op["parent"]) if op["parent"] else None)
            elif kind == "move_to_collection":
                _move_to_collection(obj, op["collection"], context)
            elif kind == "set_color":
                _set_color(obj, op["color"])
            elif kind == "delete_object":
                name = obj.name
                bpy.data.objects.remove(obj, do_unlink=True)
                touched.append(name)
                continue
        obj.gs.ai_touched = proposal.id
        if obj.name not in touched:
            touched.append(obj.name)
    return touched


def _set_transform(obj, op):
    if "location" in op:
        obj.location = op["location"]
    if "rotation_deg" in op:
        euler = Euler([math.radians(a) for a in op["rotation_deg"]], "XYZ")
        if obj.rotation_mode == "QUATERNION":
            obj.rotation_quaternion = euler.to_quaternion()
        elif obj.rotation_mode == "AXIS_ANGLE":
            axis, angle = euler.to_quaternion().to_axis_angle()
            obj.rotation_axis_angle = (angle, *axis)
        else:
            obj.rotation_euler = euler.to_matrix().to_euler(obj.rotation_mode)
    if "scale" in op:
        obj.scale = op["scale"]


def _set_parent(obj, parent):
    world = obj.matrix_world.copy()
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.matrix_world = world


def _target_collection(name, context):
    if not name:
        return context.view_layer.active_layer_collection.collection
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
    return coll


def _move_to_collection(obj, name, context):
    coll = _target_collection(name, context)
    if coll not in obj.users_collection:
        coll.objects.link(obj)
    for other in list(obj.users_collection):
        if other != coll:
            other.objects.unlink(obj)


def _create_primitive(op, context):
    kind = op["primitive"]
    size = float(op.get("size", 1.0))
    if kind == "empty":
        obj = bpy.data.objects.new(op["name"], None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = size / 2
    else:
        mesh = bpy.data.meshes.new(op["name"])
        bm = bmesh.new()
        try:
            r = size / 2
            if kind == "cube":
                bmesh.ops.create_cube(bm, size=size)
            elif kind == "sphere":
                bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=r)
            elif kind == "icosphere":
                bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r)
            elif kind == "cylinder":
                bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=r, radius2=r, depth=size)
            elif kind == "cone":
                bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=r, radius2=0, depth=size)
            elif kind == "plane":
                bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=r)
            if kind in ("sphere", "icosphere", "cylinder", "cone"):
                for face in bm.faces:
                    face.smooth = True
            bm.to_mesh(mesh)
        finally:
            bm.free()
        obj = bpy.data.objects.new(op["name"], mesh)
    _target_collection(op.get("collection", ""), context).objects.link(obj)
    _set_transform(obj, op)
    if "color" in op:
        _set_color(obj, op["color"])
    ids.ensure_id(obj)
    return obj


def _set_color(obj, color):
    rgba = list(color) + [1.0] * (4 - len(color))
    if obj.type != "MESH":
        obj.color = rgba
        return
    mat = bpy.data.materials.new(f"AI_{obj.name}")
    mat.diffuse_color = rgba
    if mat.node_tree is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = rgba
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


# --------------------------------------------------------------- file operations

ALLOWED_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".css", ".html", ".glsl", ".txt"}
BLOCKED_DIRS = {"node_modules", ".gamestudio", ".git", "dist", ".vite"}
SUSPICIOUS = [
    (re.compile(r"\bchild_process\b|\brequire\s*\(\s*['\"](?:fs|net|http|https|child_process)"), "uses Node system modules"),
    (re.compile(r"\beval\s*\(|\bnew\s+Function\s*\("), "evaluates code from strings"),
    (re.compile(r"\bfetch\s*\(\s*['\"`]https?://(?!localhost|127\.0\.0\.1)"), "contacts an external server"),
    (re.compile(r"\bnew\s+WebSocket\s*\(\s*['\"`]wss?://(?!localhost|127\.0\.0\.1)"), "opens an external socket"),
    (re.compile(r"\bdocument\.cookie\b|\blocalStorage\.clear\b"), "touches browser storage"),
]

_project_root: pathlib.Path | None = None


def set_project_root(root: pathlib.Path | None) -> None:
    global _project_root
    _project_root = root


def require_root() -> pathlib.Path:
    if _project_root is None:
        raise ProposalError("This .blend is not inside a GameStudio project (no gamestudio.json found).")
    return _project_root


def safe_relpath(path: str) -> str:
    """Normalize a model-supplied path and confine it to the project root."""
    root = require_root().resolve()
    if not isinstance(path, str) or not path.strip():
        raise ProposalError("Path is empty.")
    candidate = (root / path.replace("\\", "/")).resolve()
    if not candidate.is_relative_to(root):
        raise ProposalError(f"'{path}' is outside the project folder.")
    rel = candidate.relative_to(root)
    if rel.parts and rel.parts[0] in BLOCKED_DIRS:
        raise ProposalError(f"'{path}' is inside {rel.parts[0]}/, which the assistant may not edit.")
    return rel.as_posix()


def _abs_path(rel: str) -> pathlib.Path:
    return require_root() / rel


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: pathlib.Path) -> tuple[str, str] | None:
    """Read a text file as LF-normalized text. Returns (text, original_eol) or None."""
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def _risk_for_code(text: str, reasons: list[str]) -> str:
    level = "medium"
    for pattern, why in SUSPICIOUS:
        if pattern.search(text):
            reasons.append(f"code {why}")
            level = "high"
    return level


def build_file_edit(path: str, edits: list[dict]) -> Proposal:
    rel = safe_relpath(path)
    current = _read_text(_abs_path(rel))
    if current is None:
        raise ProposalError(f"{rel} does not exist. Use propose_new_file to create it.")
    text, eol = current
    new_text = text
    for i, edit in enumerate(edits):
        old, new = edit.get("old_text", ""), edit.get("new_text", "")
        if not old:
            raise ProposalError(f"Edit {i}: old_text is empty.")
        count = new_text.count(old)
        if count == 0:
            raise ProposalError(
                f"Edit {i}: old_text was not found in {rel}. Read the file again and copy the text exactly.")
        if count > 1:
            raise ProposalError(f"Edit {i}: old_text matches {count} places in {rel}; include more context.")
        new_text = new_text.replace(old, new, 1)
    if new_text == text:
        raise ProposalError("The edits do not change the file.")
    diff = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    reasons = ["writes to a project file"]
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    risk = _risk_for_code(added, reasons)
    changed = sum(1 for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---"))
    if changed > 300:
        reasons.append(f"large change ({changed} lines)")
        risk = "high"
    return Proposal(id="", kind="file", title="", rationale="", source="", path=rel, file_mode="edit",
                    edits=list(edits), new_content=new_text, base_hash=_hash(text), eol=eol,
                    diff=diff, risk=risk, risk_reasons=reasons)


def build_new_file(path: str, content: str) -> Proposal:
    rel = safe_relpath(path)
    if pathlib.PurePosixPath(rel).suffix not in ALLOWED_SUFFIXES:
        raise ProposalError(f"New files must be one of: {', '.join(sorted(ALLOWED_SUFFIXES))}.")
    if _abs_path(rel).exists():
        raise ProposalError(f"{rel} already exists. Use propose_file_edit instead.")
    text = content.replace("\r\n", "\n")
    diff = "".join(difflib.unified_diff([], text.splitlines(keepends=True), fromfile="/dev/null", tofile=f"b/{rel}"))
    reasons = ["creates a project file"]
    risk = _risk_for_code(text, reasons)
    return Proposal(id="", kind="file", title="", rationale="", source="", path=rel, file_mode="create",
                    new_content=text, diff=diff, risk=risk, risk_reasons=reasons)


def apply_file(proposal: Proposal, *, force=False) -> str:
    path = _abs_path(proposal.path)
    content = proposal.new_content
    if proposal.file_mode == "edit":
        current = _read_text(path)
        if current is None:
            raise ProposalError(f"{proposal.path} no longer exists.")
        if _hash(current[0]) != proposal.base_hash:
            if not force:
                raise ProposalError(f"{proposal.path} changed since the proposal was made.")
            # Re-apply the same edits on top of the newer file; fails if they no longer fit.
            content = build_file_edit(proposal.path, proposal.edits).new_content
        backup = require_root() / ".gamestudio" / "ai" / "backups" / proposal.id / proposal.path
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        proposal.backup_path = str(backup)
    elif path.exists():
        raise ProposalError(f"{proposal.path} already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content.replace("\n", proposal.eol))
    proposal.applied_hash = _hash(content)
    return proposal.path


def revert_file(proposal: Proposal) -> str:
    path = _abs_path(proposal.path)
    current = _read_text(path)
    if current is not None and _hash(current[0]) != proposal.applied_hash:
        raise ProposalError(f"{proposal.path} was edited after the AI change; revert it manually "
                            f"(backup: {proposal.backup_path or 'none, it was a new file'}).")
    if proposal.file_mode == "create":
        if path.exists():
            path.unlink()
        return f"Deleted {proposal.path}"
    shutil.copy2(proposal.backup_path, path)
    return f"Restored {proposal.path} from backup"


# ------------------------------------------------------------------- previews


def preview_text(proposal: Proposal) -> str:
    lines = [f"# AI proposal {proposal.id}: {proposal.title}",
             f"# Risk: {proposal.risk} ({'; '.join(proposal.risk_reasons) or 'no flags'})",
             f"# Status: {proposal.status}", "#", "# Why:"]
    lines += [f"#   {line}" for line in proposal.rationale.splitlines() or [""]]
    lines.append("")
    if proposal.kind == "file":
        lines.append(proposal.diff)
    else:
        lines += [f"{i + 1}. {describe_op(op)}" for i, op in enumerate(proposal.operations)]
    return "\n".join(lines)


def show_preview(proposal: Proposal):
    """Write the preview into a Text datablock and return it."""
    name = f"AI {proposal.id} preview"
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(preview_text(proposal))
    proposal.preview_text = text.name
    return text


def remove_preview(proposal: Proposal) -> None:
    if proposal.preview_text:
        text = bpy.data.texts.get(proposal.preview_text)
        if text is not None:
            bpy.data.texts.remove(text)
        proposal.preview_text = ""


# ---------------------------------------------------------------- risk scoring


def score_scene(ops: list[dict]) -> tuple[str, list[str]]:
    """Risk comes from what the operations do, never from the model's own claims."""
    reasons = []
    level = "low"
    deletes = sum(1 for op in ops if op["op"] == "delete_object")
    renames = sum(1 for op in ops if op["op"] == "rename")
    if deletes:
        reasons.append(f"deletes {deletes} object(s)")
        level = "high"
    if renames:
        reasons.append(f"renames {renames} object(s)")
        level = "medium" if level == "low" else level
    if len(ops) > 25:
        reasons.append(f"{len(ops)} operations")
        level = "high" if len(ops) > 60 else ("medium" if level == "low" else level)
    return level, reasons
