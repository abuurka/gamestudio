# SPDX-License-Identifier: GPL-3.0-or-later
"""The assistant's typed tools.

The same registry serves the in-Blender agent loop and live-link tool peers
(bridge.py); the agent bridge (gamestudio/bridge) reuses some handlers, such as
get_object. Tools come in three tiers:

* read: inspect the scene, project files, docs or the running game. These never
  change anything.
* exec: run something that changes no project data, such as a type check.
* propose: stage a Proposal (see proposals.py). The developer has to apply it.

No tool can apply a proposal, run arbitrary Python, or reach the network.
Handlers run on Blender's main thread unless ``main_thread=False``.
"""

import fnmatch
import json
import math
import os
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

import bpy

from ..core import components, ids, registry, toolchain, trust
from ..core import link as link_mod
from . import audit, proposals
from .proposals import STORE, Proposal, ProposalError
from .validate import validate

ToolError = ProposalError

READ, EXEC, PROPOSE = "read", "exec", "propose"
ROLES = ("general", "scene", "script", "debug", "mentor")
WRITE_ROLES = ("general", "scene", "script", "debug")
MAX_RESULT_CHARS = 30_000


@dataclass
class ToolContext:
    source: str  # "assistant" or "mcp"
    project_root: pathlib.Path | None
    respond: Callable | None = None  # used by deferred tools


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    tier: str
    handler: Callable
    roles: tuple = ROLES
    main_thread: bool = True
    deferred: bool = False

    def api_definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            # Inputs stream as generated; validate() checks them before running.
            "eager_input_streaming": True,
        }


TOOLS: dict[str, Tool] = {}


def tool(name, description, properties=None, required=(), *, tier=READ, roles=ROLES,
         main_thread=True, deferred=False):
    schema = {"type": "object", "properties": properties or {}, "required": list(required),
              "additionalProperties": False}

    def register(fn):
        TOOLS[name] = Tool(name, description, schema, tier, fn, tuple(roles), main_thread, deferred)
        return fn
    return register


def tools_for(role: str) -> list[Tool]:
    """The tools a role sees.

    The list depends only on the role, so switching between Ask and Propose
    mid-conversation keeps the prompt cache warm. The mentor role has no
    propose tools at all.
    """
    return [t for t in TOOLS.values() if role in t.roles]


def allowed_names(role: str, mode: str) -> set[str]:
    """The tools that may actually run. Ask mode blocks every propose tool."""
    return {t.name for t in tools_for(role) if not (t.tier == PROPOSE and mode == "ask")}


# ------------------------------------------------------------------- dispatch


def format_result(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n... [truncated {len(text) - MAX_RESULT_CHARS} characters]"
    return text


def invoke(name: str, args, ctx: ToolContext, done: Callable[[bool, str], None],
           allowed: set[str] | None = None) -> None:
    """Validate and run a tool, then report through ``done(ok, content)``.

    ``done`` runs exactly once: immediately for normal tools, later for deferred
    ones. Call this on the main thread unless the tool sets main_thread=False.
    """
    t = TOOLS.get(name)
    if t is None or (allowed is not None and name not in allowed):
        why = "Ask mode is read-only; the developer must switch to Propose mode for changes." \
            if t is not None and t.tier == PROPOSE else f"Tool '{name}' is not available."
        done(False, format_result({"error": why}))
        return
    proposals.set_project_root(ctx.project_root)
    problems = validate(args, t.input_schema)
    if problems:
        done(False, format_result({"error": "Invalid tool input", "problems": problems[:10]}))
        return
    if t.deferred:
        replied = []

        def respond(result):
            if replied:
                return
            replied.append(True)
            if isinstance(result, Exception):
                done(False, format_result({"error": str(result)}))
            else:
                done(True, format_result(result))
        ctx.respond = respond
    try:
        result = t.handler(args, ctx)
    except ProposalError as exc:
        done(False, format_result({"error": str(exc)}))
        return
    except Exception as exc:  # a bug in a tool must not crash the conversation
        done(False, format_result({"error": f"{type(exc).__name__}: {exc}"}))
        return
    if not t.deferred:
        done(True, format_result(result))


# -------------------------------------------------------------------- helpers


def _round(values, digits=3):
    return [round(float(v), digits) for v in values]


def _rotation_deg(obj):
    euler = obj.matrix_basis.to_euler("XYZ")
    return [round(math.degrees(a), 2) for a in euler]


def _project_name(root: pathlib.Path | None) -> str | None:
    if root is None:
        return None
    try:
        with open(root / "gamestudio.json", encoding="utf-8") as f:
            return json.load(f).get("name") or root.name
    except (OSError, ValueError):
        return root.name


def _require_root(ctx: ToolContext) -> pathlib.Path:
    if ctx.project_root is None:
        raise ToolError("This .blend is not inside a GameStudio project (no gamestudio.json found).")
    return ctx.project_root


def _iter_project_files(root: pathlib.Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in proposals.BLOCKED_DIRS and not d.startswith("."))
        for fname in sorted(filenames):
            path = pathlib.Path(dirpath) / fname
            yield path, path.relative_to(root).as_posix()


def _link():
    try:
        return link_mod.get_link()
    except Exception:
        return None


# --------------------------------------------------------------- read tools


@tool("get_scene_overview",
      "Summarize the open Blender scene: collections, objects (type, gs_id, parent, world location, "
      "attached components), the selection, and whether the game is connected. Start here.",
      {"max_objects": {"type": "integer", "minimum": 1, "maximum": 1000,
                       "description": "Cap on listed objects (default 200)."}})
def get_scene_overview(args, ctx):
    scene = bpy.context.scene
    limit = args.get("max_objects", 200)
    objects = list(scene.objects)

    def tree(coll):
        return {"collection": coll.name,
                "objects": [o.name for o in coll.objects][:limit],
                "children": [tree(c) for c in coll.children]}

    listed = []
    for obj in objects[:limit]:
        entry = {"name": obj.name, "type": obj.type, "id": ids.get_id(obj),
                 "parent": obj.parent.name if obj.parent else None,
                 "world_location": _round(obj.matrix_world.translation),
                 "components": components.list_components(obj)}
        if obj.gs.ai_lock:
            entry["ai_locked"] = True
        listed.append(entry)
    link = _link()
    active = bpy.context.view_layer.objects.active
    return {
        "scene": scene.name,
        "project": _project_name(ctx.project_root),
        "coordinates": "Blender world space: Z-up, metres. Component vec3 fields are runtime space (Y-up).",
        "object_count": len(objects),
        "truncated": len(objects) > limit,
        "collections": tree(scene.collection),
        "objects": listed,
        "selection": [o.name for o in bpy.context.selected_objects],
        "active": active.name if active else None,
        "component_types": [s["name"] for s in registry.schemas()],
        "game_connected": bool(link and link.connected and _game_peers(link)),
    }


def _game_peers(link) -> int:
    peers = getattr(link, "peers", None) or []
    return sum(1 for p in peers if (p.get("role") if isinstance(p, dict) else None) == "game")


@tool("get_selection", "List the selected objects and the active object.")
def get_selection(args, ctx):
    active = bpy.context.view_layer.objects.active
    return {"selected": [o.name for o in bpy.context.selected_objects],
            "active": active.name if active else None}


@tool("get_object",
      "Full details of one object: transform (local, Blender space), dimensions, parent/children, "
      "collections, components with all field values, mesh stats, materials and modifiers.",
      {"name": {"type": "string", "description": "Object name or gs_id."}}, ["name"])
def get_object(args, ctx):
    obj = proposals.resolve_object(args["name"])
    info = {
        "name": obj.name, "type": obj.type, "id": ids.get_id(obj),
        "parent": obj.parent.name if obj.parent else None,
        "children": [c.name for c in obj.children],
        "collections": [c.name for c in obj.users_collection],
        "location": _round(obj.location), "rotation_deg": _rotation_deg(obj), "scale": _round(obj.scale),
        "world_location": _round(obj.matrix_world.translation),
        "dimensions": _round(obj.dimensions),
        "components": {},
        "ai_locked": bool(obj.gs.ai_lock),
        "linked_from_library": obj.library is not None,
    }
    refs = {ref.name: ref for ref in obj.gs.components}
    for name in components.list_components(obj):
        entry = {"values": components.get_values(obj, name) if registry.get_schema(name) else None}
        if name in refs and not refs[name].enabled:
            entry["enabled"] = False
        if registry.get_schema(name) is None:
            entry["missing_definition"] = True
        info["components"][name] = entry
    if obj.type == "MESH" and obj.data is not None:
        info["mesh"] = {"vertices": len(obj.data.vertices), "faces": len(obj.data.polygons)}
        info["materials"] = [m.name for m in obj.data.materials if m]
    if obj.modifiers:
        info["modifiers"] = [f"{m.name} ({m.type})" for m in obj.modifiers]
    custom = {k: str(obj[k])[:200] for k in obj.keys() if not k.startswith("gs_")}
    if custom:
        info["custom_properties"] = custom
    return info


@tool("find_objects",
      "Search scene objects by name substring, type, attached component or collection.",
      {"name_contains": {"type": "string"},
       "type": {"type": "string", "description": "Blender type, e.g. MESH, EMPTY, CAMERA, LIGHT."},
       "component": {"type": "string"},
       "collection": {"type": "string"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 500}})
def find_objects(args, ctx):
    needle = args.get("name_contains", "").lower()
    found = []
    for obj in bpy.context.scene.objects:
        if needle and needle not in obj.name.lower():
            continue
        if "type" in args and obj.type != args["type"].upper():
            continue
        if "component" in args and args["component"] not in components.list_components(obj):
            continue
        if "collection" in args and args["collection"] not in {c.name for c in obj.users_collection}:
            continue
        found.append({"name": obj.name, "type": obj.type,
                      "components": components.list_components(obj),
                      "world_location": _round(obj.matrix_world.translation)})
        if len(found) >= args.get("limit", 50):
            break
    return {"count": len(found), "objects": found}


@tool("list_component_types",
      "List every component type the game defines, with fields, types, defaults and ranges. "
      "Only these components exist; to create a new one, propose a file in src/components/.")
def list_component_types(args, ctx):
    return {"components": registry.schemas()}


@tool("list_project_files",
      "List files in the game project (skips node_modules, .gamestudio, dist and hidden folders).",
      {"glob": {"type": "string", "description": "fnmatch pattern on the relative path, e.g. 'src/**/*.ts'."}})
def list_project_files(args, ctx):
    root = _require_root(ctx)
    pattern = args.get("glob", "*")
    files = [rel for _, rel in _iter_project_files(root)
             if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**/", ""))]
    return {"count": len(files), "files": files[:400], "truncated": len(files) > 400}


@tool("read_project_file",
      "Read a text file from the game project, with line numbers. Use line ranges for long files.",
      {"path": {"type": "string", "description": "Path relative to the project root."},
       "start_line": {"type": "integer", "minimum": 1},
       "end_line": {"type": "integer", "minimum": 1}}, ["path"])
def read_project_file(args, ctx):
    _require_root(ctx)
    rel = proposals.safe_relpath(args["path"])
    path = ctx.project_root / rel
    if not path.is_file():
        raise ToolError(f"{rel} does not exist.")
    if path.stat().st_size > 1_000_000:
        raise ToolError(f"{rel} is larger than 1 MB.")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        raise ToolError(f"{rel} is not a UTF-8 text file.") from None
    start = args.get("start_line", 1)
    end = min(args.get("end_line", start + 499), start + 499, len(lines))
    numbered = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    return {"path": rel, "total_lines": len(lines), "start_line": start, "end_line": end,
            "content": numbered}


@tool("search_project",
      "Regex search across project text files. Returns file:line matches.",
      {"pattern": {"type": "string"},
       "glob": {"type": "string", "description": "fnmatch filter, default 'src/*'."},
       "max_results": {"type": "integer", "minimum": 1, "maximum": 300}}, ["pattern"])
def search_project(args, ctx):
    root = _require_root(ctx)
    try:
        regex = re.compile(args["pattern"])
    except re.error as exc:
        raise ToolError(f"Invalid regex: {exc}") from None
    pattern = args.get("glob", "src/*")
    limit = args.get("max_results", 100)
    matches = []
    for path, rel in _iter_project_files(root):
        if not fnmatch.fnmatch(rel, pattern) or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append(f"{rel}:{number}: {line.strip()[:200]}")
                if len(matches) >= limit:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def engine_docs_path(root: pathlib.Path | None) -> pathlib.Path | None:
    candidates = []
    if root is not None:
        candidates.append(root / "node_modules" / "@gamestudio" / "engine" / "API.md")
    # Development checkout: walk up from this file to the repository's runtime/ folder.
    for parent in pathlib.Path(__file__).resolve().parents:
        candidates.append(parent / "runtime" / "packages" / "engine" / "API.md")
    return next((p for p in candidates if p.is_file()), None)


def _sections(markdown: str) -> list[tuple[str, str]]:
    parts, heading, buf = [], "Introduction", []
    for line in markdown.splitlines():
        if line.startswith("#"):
            if buf:
                parts.append((heading, "\n".join(buf)))
            heading, buf = line.lstrip("#").strip(), [line]
        else:
            buf.append(line)
    if buf:
        parts.append((heading, "\n".join(buf)))
    return parts


@tool("get_engine_docs",
      "Read the GameStudio engine API reference (API.md). Pass a section name to get just that part. "
      "Only use APIs documented here.",
      {"section": {"type": "string", "description": "Heading text to search for, e.g. 'defineSystem'."}})
def get_engine_docs(args, ctx):
    path = engine_docs_path(ctx.project_root)
    if path is None:
        raise ToolError("Engine docs not found: the project's packages are not set up yet (GameStudio sets them "
                        "up when the project opens or plays).")
    text = path.read_text(encoding="utf-8")
    query = args.get("section", "").lower()
    if not query:
        if len(text) <= MAX_RESULT_CHARS:
            return text
        return {"sections": [h for h, _ in _sections(text)],
                "note": "Docs are long; call again with a section name."}
    hits = [body for heading, body in _sections(text) if query in heading.lower()]
    if not hits:
        return {"error": f"No section matching '{query}'.", "sections": [h for h, _ in _sections(text)]}
    return "\n\n".join(hits)


@tool("get_runtime_logs",
      "Recent console output and errors from the running game (newest last).",
      {"level": {"type": "string", "enum": ["all", "warn", "error"]},
       "limit": {"type": "integer", "minimum": 1, "maximum": 500}})
def get_runtime_logs(args, ctx):
    link = _link()
    if link is None:
        raise ToolError("The live link is not running. Press Play in the Game panel.")
    level = args.get("level", "all")
    wanted = {"all": None, "warn": {"warn", "error"}, "error": {"error"}}[level]
    entries = []
    for msg in list(link.logs):
        lvl = "error" if msg.get("t") == "error" else msg.get("level", "info")
        if wanted is None or lvl in wanted:
            entry = {"level": lvl, "msg": msg.get("msg", "")}
            for key in ("file", "line", "col", "stack", "source"):
                if msg.get(key):
                    entry[key] = msg[key]
            entries.append(entry)
    limit = args.get("limit", 50)
    return {"count": len(entries), "logs": entries[-limit:]}


@tool("get_runtime_state",
      "Ask the running game for live entity state (world position and component values). "
      "Requires the game to be running and connected.",
      {"objects": {"type": "array", "items": {"type": "string"}, "maxItems": 50,
                   "description": "Object names or gs_ids. Omit to filter by component or list all."},
       "component": {"type": "string"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
      deferred=True)
def get_runtime_state(args, ctx):
    link = _link()
    if link is None or not link.connected:
        raise ToolError("The game is not connected. Press Play in the Game panel first.")
    flt = {"limit": args.get("limit", 20)}
    if args.get("objects"):
        flt["ids"] = [ids.ensure_id(proposals.resolve_object(ref)) for ref in args["objects"]]
    if args.get("component"):
        flt["component"] = args["component"]

    def on_response(msg):
        if msg is None:
            ctx.respond(ToolError("The game did not answer within 5 seconds."))
        else:
            ctx.respond({"entities": msg.get("entities", [])})
    link.request({"t": "state.request", "filter": flt}, on_response, timeout=5.0)


# ---------------------------------------------------------------- exec tools


@tool("run_typecheck",
      "Type-check the game's TypeScript (npm run typecheck). Use it after proposing code to catch "
      "mistakes; note that staged proposals are not on disk until the developer applies them.",
      tier=EXEC, roles=WRITE_ROLES, main_thread=False)
def run_typecheck(args, ctx):
    root = _require_root(ctx)
    if not trust.is_trusted(root):  # `npm run typecheck` is the project's script
        raise ToolError(trust.ASK_USER)
    npm = toolchain.which("npm")
    if npm is None:
        raise ToolError("npm is not on PATH.")
    try:
        proc = subprocess.run([npm, "run", "typecheck", "--silent"], cwd=root, capture_output=True,
                              text=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise ToolError("Type check timed out after 180 s.") from None
    output = (proc.stdout + proc.stderr).strip().splitlines()
    return {"ok": proc.returncode == 0, "output": output[:80], "truncated": len(output) > 80}


# ------------------------------------------------------------- propose tools

_PROPOSAL_FIELDS = {
    "title": {"type": "string", "maxLength": 120, "description": "Short imperative summary."},
    "rationale": {"type": "string", "maxLength": 2000,
                  "description": "Why this change answers the developer's request. Shown to them."},
}


def _stage(proposal: Proposal, args, ctx) -> dict:
    proposal.id = STORE.new_id()
    proposal.title = args["title"].strip() or "Untitled change"
    proposal.rationale = args["rationale"].strip()
    proposal.source = ctx.source
    STORE.add(proposal)
    audit.log(ctx.project_root, "proposal_created", id=proposal.id, kind=proposal.kind,
              source=ctx.source, title=proposal.title, risk=proposal.risk,
              changes=proposal.summary_lines(), diff=proposal.diff or None)
    _notify_ui()
    return proposal.to_model_dict()


def _notify_ui():
    wm = bpy.context.window_manager
    for window in getattr(wm, "windows", []):
        for area in window.screen.areas:
            area.tag_redraw()


@tool("propose_scene_changes",
      "Stage a set of scene edits for the developer to review. Nothing changes until they click Apply; "
      "the whole set then becomes one undo step. Keep each proposal to one logical change. "
      "Operations: create_object(name, primitive, size?, location?, rotation_deg?, scale?, collection?, "
      "parent?, color?), set_transform(object, location?, rotation_deg?, scale?), rename(object, new_name), "
      "add_component(object, component, values?), remove_component(object, component), "
      "set_component_field(object, component, field, value), set_parent(object, parent|null), "
      "move_to_collection(object, collection), set_color(object, color), "
      "duplicate_object(object, name, location?), delete_object(object). "
      "Transforms are Blender space (Z-up, metres, degrees).",
      {**_PROPOSAL_FIELDS,
       "operations": {"type": "array", "items": proposals.OPERATION_SCHEMA, "minItems": 1, "maxItems": 100}},
      ["title", "rationale", "operations"], tier=PROPOSE, roles=("general", "scene"))
def propose_scene_changes(args, ctx):
    ops, fingerprints = proposals.validate_scene_ops(args["operations"])
    risk, reasons = proposals.score_scene(ops)
    proposal = Proposal(id="", kind="scene", title="", rationale="", source="",
                        operations=ops, fingerprints=fingerprints, risk=risk, risk_reasons=reasons)
    return _stage(proposal, args, ctx)


@tool("propose_file_edit",
      "Stage exact text replacements in an existing project file for review. Each old_text must match "
      "exactly once (copy it from read_project_file without the line-number prefix). The developer sees "
      "a diff and decides whether to apply.",
      {**_PROPOSAL_FIELDS,
       "path": {"type": "string"},
       "edits": {"type": "array", "minItems": 1, "maxItems": 30,
                 "items": {"type": "object",
                           "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}},
                           "required": ["old_text", "new_text"], "additionalProperties": False}}},
      ["title", "rationale", "path", "edits"], tier=PROPOSE, roles=("general", "script", "debug"))
def propose_file_edit(args, ctx):
    proposals.set_project_root(ctx.project_root)
    return _stage(proposals.build_file_edit(args["path"], args["edits"]), args, ctx)


@tool("propose_new_file",
      "Stage a new project file (for example a component in src/components/ or a system in src/systems/) "
      "for review.",
      {**_PROPOSAL_FIELDS,
       "path": {"type": "string"},
       "content": {"type": "string", "maxLength": 200_000}},
      ["title", "rationale", "path", "content"], tier=PROPOSE, roles=("general", "script", "debug"))
def propose_new_file(args, ctx):
    proposals.set_project_root(ctx.project_root)
    return _stage(proposals.build_new_file(args["path"], args["content"]), args, ctx)
