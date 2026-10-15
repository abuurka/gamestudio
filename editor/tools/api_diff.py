#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""api_diff: which Blender API changes touch what the GameStudio shell actually uses?

Part of the upgrade safety net (docs/fork/ARCHITECTURE.md section 7, step 4). Stdlib only.

  python editor/tools/api_diff.py dump --blender PATH --out api-5.2.2.json
      (a) run Blender headless (editor/tools/api_dump_blender.py) and write its API surface:
          bpy.types structs with RNA properties/functions, every operator with its properties
          and defaults, bpy.app/props/utils/..., Blender's own Python modules, the icon list
  python editor/tools/api_diff.py scan [--shell editor/shell] [--json OUT]
      (b) statically scan editor/shell/**/*.py for what it uses from Blender: bpy.types.X,
          bpy.ops.a.b (+ keywords), bpy.props, bpy.app..., attribute chains on RNA types,
          operator/menu ids in UI strings, icons, bl_* class attributes, overridden methods
  python editor/tools/api_diff.py diff OLD.json NEW.json [--json OUT] [--verbose]
      (c) report only the differences between two dumps that intersect the scan
  python editor/tools/api_diff.py compare --old PATH_TO_OLD_BLENDER --new PATH_TO_NEW_BLENDER [--keep DIR]
      dump both, then diff
  python editor/tools/api_diff.py source-dump --source BLENDER_SOURCE_DIR --out FILE
      no build needed: a partial dump read statically from a Blender source tree (RNA definitions in
      makesrna, C and Python operators, bl_ui/bl_operators classes, bpy.app, bpy.props, icons).
      Diff source dumps only against other source dumps (both miss the same things).

How usages are resolved: each usage is a chain such as `bpy.context.preferences.view.ui_scale` or
`layout.textbox(..., placeholder=...)`. The chain is walked through the OLD dump's RNA types
(Context.preferences -> Preferences, .view -> PreferencesView, ...). Every step that resolves in the
OLD dump becomes a fact ("Preferences.view exists", "UILayout.textbox has 'placeholder'") that is
then checked in the NEW dump. Steps that do not resolve in OLD (our own properties, plain Python
objects) are not compared. Receivers that are plain variables are typed by name (obj -> Object,
layout/row/col -> UILayout, scene -> Scene, ...) or, for `self`, by the class's Blender base.

Severities: BREAK (something we use is gone), CHANGE (it changed shape: type, required parameters,
operator defaults), INFO (guarded by hasattr/getattr, or new operator parameters). Exit code 1 if
any BREAK or CHANGE is reported.
"""

import argparse
import ast
import collections
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
SHELL = REPO / "editor" / "shell"
DUMPER = pathlib.Path(__file__).resolve().with_name("api_dump_blender.py")
FORMAT = "gamestudio.api_dump"

# Python modules shipped with Blender. Chains rooted at these are checked; anything else is ignored.
BLENDER_MODULES = ("bpy", "bmesh", "mathutils", "gpu", "gpu_extras", "blf", "bl_math", "idprop", "bl_ui",
                   "bl_operators", "bl_app_override", "bl_app_template_utils", "addon_utils", "_bpy_restrict_state",
                   "bpy_extras", "_bpy_types", "bpy_types", "bl_keymap_utils", "rna_prop_ui", "bl_ui_utils")

# Plain variable names typed by convention in the shell.
VAR_TYPES = {
    "context": "Context", "layout": "UILayout", "row": "UILayout", "col": "UILayout", "column": "UILayout",
    "box": "UILayout", "split": "UILayout", "sub": "UILayout", "left": "UILayout", "right": "UILayout",
    "obj": "Object", "ob": "Object", "scene": "Scene", "area": "Area", "region": "Region", "window": "Window",
    "win": "Window", "screen": "Screen", "wm": "WindowManager", "view_layer": "ViewLayer",
    "depsgraph": "Depsgraph", "update": "DepsgraphUpdate", "mesh": "Mesh", "me": "Mesh",
    "workspace": "WorkSpace", "ws": "WorkSpace", "prefs": "Preferences", "collection": "Collection",
    "coll": "Collection", "world": "World", "event": "Event", "space": "Space", "text_block": "Text",
}
# Dynamic bpy.context members (not RNA properties of Context) and their types.
CONTEXT_MEMBER_TYPES = {
    "active_object": ("type", "Object"), "object": ("type", "Object"), "edit_object": ("type", "Object"),
    "selected_objects": ("list", "Object"), "selected_editable_objects": ("list", "Object"),
    "visible_objects": ("list", "Object"), "active_operator": ("type", "Operator"),
}
UI_OPERATOR_CALLS = ("operator", "operator_menu_enum", "operator_menu_hold", "operator_enum")
UI_TYPE_CALLS = ("menu", "popover", "menu_contents", "panel_prop")
SEVERITIES = ("BREAK", "CHANGE", "INFO")


# =============================================================================================== (a)


def dump_binary(blender, out) -> pathlib.Path:
    """Run Blender headless with a throw-away profile and write its API dump to `out`."""
    out = pathlib.Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gs_api_dump_") as profile:
        env = dict(os.environ, BLENDER_USER_RESOURCES=profile)
        for var in ("BLENDER_SYSTEM_SCRIPTS", "BLENDER_SYSTEM_EXTENSIONS", "BLENDER_USER_SCRIPTS",
                    "BLENDER_USER_CONFIG", "BLENDER_USER_EXTENSIONS", "BLENDER_USER_DATAFILES"):
            env.pop(var, None)  # stock surface only: no GameStudio shell, no user add-ons
        cmd = [str(blender), "--background", "--factory-startup", "--app-template", "default",
               "--python-exit-code", "1", "--python", str(DUMPER), "--", str(out)]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=900)
    if result.returncode != 0 or "API_DUMP_OK" not in result.stdout:
        print("\n".join((result.stdout + result.stderr).splitlines()[-30:]))
        sys.exit(f"api_diff: dumping {blender} failed (exit code {result.returncode})")
    return out


def load_dump(path) -> dict:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        sys.exit(f"api_diff: {path} is not a {FORMAT} file")
    return data


# =============================================================================================== (b)


def _const(node):
    """A JSON-able constant for str/int/float/bool literals and sets/lists/tuples of them, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = [_const(e) for e in node.elts]
        if all(isinstance(v, str) for v in values):
            return sorted(values)
    return None


class _FileScanner:
    def __init__(self, scanner, path, tree):
        self.scanner = scanner
        self.path = path
        self.rel = path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.as_posix()
        self.tree = tree
        self.aliases = {}      # local name -> (root module, [prefix attrs])
        self.dicts = {}        # module-level NAME = {literal dict}
        self.parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

    def emit(self, usage, node):
        usage["file"] = self.rel
        usage["line"] = getattr(node, "lineno", 0)
        self.scanner.usages.append(usage)

    # --- imports and module constants ------------------------------------------------------

    def collect_aliases(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in BLENDER_MODULES:
                        continue
                    if alias.asname:
                        self.aliases[alias.asname] = (alias.name, [])
                    else:
                        self.aliases[top] = (top, [])
                    self.emit({"kind": "module", "name": alias.name}, node)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                parts = node.module.split(".")
                if parts[0] not in BLENDER_MODULES:
                    continue
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = (parts[0], parts[1:] + [alias.name])
                    self.emit({"kind": "chain", "root": {"module": parts[0]},
                               "steps": [["attr", p] for p in parts[1:] + [alias.name]]}, node)
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                if len(keys) == len(node.value.keys):
                    self.dicts[node.targets[0].id] = {k: _const(v) for k, v in zip(keys, node.value.values)}

    # --- chains -----------------------------------------------------------------------------

    def call_step(self, call):
        kwargs, values = [], {}
        for kw in call.keywords:
            if kw.arg is None:
                if isinstance(kw.value, ast.Name) and kw.value.id in self.dicts:
                    for key, value in self.dicts[kw.value.id].items():
                        kwargs.append(key)
                        if value is not None:
                            values[key] = value
                continue
            kwargs.append(kw.arg)
            value = _const(kw.value)
            if value is not None:
                values[kw.arg] = value
        args = [_const(a) for a in call.args]
        return ["call", sorted(set(kwargs)), values, args]

    def unwind(self, node):
        steps = []
        while True:
            if isinstance(node, ast.Attribute):
                steps.append(["attr", node.attr])
                node = node.value
            elif isinstance(node, ast.Call):
                steps.append(self.call_step(node))
                node = node.func
            elif isinstance(node, ast.Subscript):
                steps.append(["index"])
                node = node.value
            else:
                break
        steps.reverse()
        return node, steps

    def root_of(self, name_node, class_name):
        name = name_node.id
        if name in self.aliases:
            module, prefix = self.aliases[name]
            return {"module": module}, [["attr", p] for p in prefix]
        if name == "self" and class_name:
            return {"self": class_name}, []
        if name in VAR_TYPES:
            return {"type": VAR_TYPES[name]}, []
        return None, None

    def is_maximal(self, node):
        parent = self.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return False
        if isinstance(parent, ast.Call) and parent.func is node:
            return False
        if isinstance(parent, ast.Subscript) and parent.value is node:
            return False
        return True

    def chain_usage(self, node, class_name, extra_steps=(), guarded=False):
        root_node, steps = self.unwind(node)
        if not isinstance(root_node, ast.Name):
            return None
        root, prefix = self.root_of(root_node, class_name)
        if root is None:
            return None
        usage = {"kind": "chain", "root": root, "steps": prefix + steps + [list(s) for s in extra_steps]}
        if guarded:
            usage["guard_from"] = len(usage["steps"]) - 1   # facts from this step on are optional
        return usage

    # --- walk -------------------------------------------------------------------------------

    def run(self):
        self.collect_aliases()
        self.visit(self.tree, None, ())

    @staticmethod
    def hasattr_name(test):
        """"name" for an `if hasattr(x, "name"):` test, else None."""
        if isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "hasattr" \
                and len(test.args) == 2 and isinstance(test.args[1], ast.Constant) \
                and isinstance(test.args[1].value, str):
            return test.args[1].value
        return None

    def visit(self, node, class_name, guards):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self.class_def(child)
                self.visit(child, child.name, guards)
                continue
            self.inspect(child, class_name, guards)
            name = self.hasattr_name(child.test) if isinstance(child, ast.If) else None
            if name is None:
                self.visit(child, class_name, guards)
                continue
            # Uses of `.name` inside `if hasattr(x, "name"):` are optional: the code handles their absence.
            self.inspect(child.test, class_name, guards)
            self.visit(child.test, class_name, guards)
            for stmt in child.body:
                self.inspect(stmt, class_name, guards + (name,))
                self.visit(stmt, class_name, guards + (name,))
            for stmt in child.orelse:
                self.inspect(stmt, class_name, guards)
                self.visit(stmt, class_name, guards)

    def class_def(self, node):
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in self.scanner.own_classes and base.id not in self.aliases:
                bases.append({"own": base.id})
                continue
            usage = self.chain_usage(base, None)
            if usage is not None:
                bases.append({"root": usage["root"], "steps": usage["steps"]})
        methods, attrs = [], {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(item.name)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("bl_"):
                        attrs[target.id] = _const(item.value)
        self.scanner.classes[node.name] = {"bases": bases}
        if bases:
            self.emit({"kind": "class", "name": node.name, "bases": bases, "methods": methods, "attrs": attrs}, node)

    def inspect(self, node, class_name, guards=()):
        if isinstance(node, ast.Call):
            self.inspect_call(node, class_name)
        if isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)) and self.is_maximal(node):
            usage = self.chain_usage(node, class_name)
            if usage is not None and usage["steps"]:
                for index, step in enumerate(usage["steps"]):
                    if step[0] == "attr" and step[1] in guards:
                        usage["guard_from"] = index
                        break
                parent = self.parents.get(node)
                if isinstance(parent, ast.Assign) and node in parent.targets:
                    value = _const(parent.value)
                    if isinstance(value, str):
                        usage["assign"] = value
                    usage["assigned"] = True
                self.emit(usage, node)

    def dynamic_class(self, node):
        """type("Name", (bases...), {"bl_label": ..., "draw": fn}) creates a Blender class too."""
        if not (len(node.args) == 3 and isinstance(node.args[1], ast.Tuple) and isinstance(node.args[2], ast.Dict)):
            return
        bases = []
        for base in node.args[1].elts:
            if isinstance(base, ast.Name) and base.id in self.scanner.own_classes and base.id not in self.aliases:
                bases.append({"own": base.id})
                continue
            usage = self.chain_usage(base, None)
            if usage is not None:
                bases.append({"root": usage["root"], "steps": usage["steps"]})
        if not bases:
            return
        methods, attrs = [], {}
        for key, value in zip(node.args[2].keys, node.args[2].values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if key.value.startswith("bl_"):
                attrs[key.value] = _const(value)
            elif not key.value.startswith("__") and isinstance(value, (ast.Name, ast.Lambda, ast.Call)):
                methods.append(key.value)
        name = f"<type() at {self.rel}:{node.lineno}>"
        self.scanner.classes[name] = {"bases": bases}
        self.emit({"kind": "class", "name": name, "bases": bases, "methods": methods, "attrs": attrs}, node)

    def inspect_call(self, node, class_name):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "type":
            self.dynamic_class(node)
        # hasattr(x, "name") / getattr(x, "name", default): a guarded use of x.name
        if isinstance(func, ast.Name) and func.id in ("hasattr", "getattr") and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            guarded = func.id == "hasattr" or len(node.args) >= 3
            usage = self.chain_usage(node.args[0], class_name, [["attr", node.args[1].value]], guarded)
            if usage is not None:
                self.emit(usage, node)
        for kw in node.keywords:
            if kw.arg == "icon" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                self.emit({"kind": "icon", "name": kw.value.value}, node)
        if isinstance(func, ast.Attribute) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            text = node.args[0].value
            if func.attr in UI_OPERATOR_CALLS and re.fullmatch(r"[a-z_0-9]+\.[a-z_0-9]+", text) \
                    and text not in self.scanner.own_ops:
                self.emit({"kind": "op", "id": text}, node)
            elif func.attr in UI_TYPE_CALLS and re.fullmatch(r"[A-Za-z0-9_]+", text) \
                    and text not in self.scanner.own_types:
                self.emit({"kind": "type", "name": text}, node)


class ShellScanner:
    """Collect what the shell uses from Blender (b)."""

    def __init__(self, root=SHELL):
        self.root = pathlib.Path(root)
        self.usages = []
        self.classes = {}
        self.own_ops, self.own_types, self.own_classes = set(), set(), set()

    def files(self):
        return sorted(p for p in self.root.rglob("*.py")
                      if "__pycache__" not in p.parts and "_vendor" not in p.parts)

    def collect_own(self, tree):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            self.own_classes.add(node.name)
            if node.bases:
                self.own_types.add(node.name)
            for item in node.body:
                if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_idname"
                                                        for t in item.targets):
                    value = _const(item.value)
                    if isinstance(value, str):
                        (self.own_ops if "." in value else self.own_types).add(value)

    def scan(self) -> dict:
        trees = []
        for path in self.files():
            try:
                trees.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
            except SyntaxError as exc:
                print(f"api_diff: skipping {path}: {exc}", file=sys.stderr)
        for _path, tree in trees:
            self.collect_own(tree)
        for path, tree in trees:
            _FileScanner(self, path, tree).run()
        return {
            "root": self.root.as_posix(),
            "files": len(trees),
            "usages": self.usages,
            "classes": self.classes,
            "own_ops": sorted(self.own_ops),
            "own_types": sorted(self.own_types),
        }


# =============================================================================================== (c)


class Api:
    """Lookups on one dump."""

    def __init__(self, dump):
        self.dump = dump
        self.structs = dump.get("structs", {})
        self.ops = dump.get("operators", {})
        self.modules = dump.get("modules", {})
        self.py = dump.get("py_modules", {})
        self.icons = set(dump.get("icons", []))

    def find_member(self, type_name, attr):
        seen, current = set(), type_name
        while current and current not in seen:
            seen.add(current)
            struct = self.structs.get(current)
            if struct is None:
                break
            if attr in struct.get("properties", {}):
                return "prop", struct["properties"][attr]
            if attr in struct.get("functions", {}):
                return "func", struct["functions"][attr]
            if attr in struct.get("py_members", []) or attr in struct.get("py_attrs", {}):
                return "py", None
            current = struct.get("base")
        if attr in self.structs.get("bpy_struct", {}).get("py_members", []):
            return "py", None
        return None

    def module(self, path):
        if path in self.modules:
            return self.modules[path]
        return self.py.get(path)

    def enum_items(self, info):
        items = info.get("enum")
        if items == "@icons":
            return self.icons
        return set(items) if isinstance(items, list) else None


OPAQUE = ("opaque",)   # left the RNA world (a float, a mathutils value, a Python list, ...): stop, resolved
# bpy_prop_collection methods implemented in Python/C, not RNA functions.
COLLECTION_PY_METHODS = {"get", "keys", "values", "items", "find", "foreach_get", "foreach_set", "add", "clear",
                         "move", "data", "rna_type", "id_data", "path_from_id"}
# UILayout calls whose return value holds the properties of the operator named in the first argument.
OPERATOR_PROPS_CALLS = {"operator", "operator_menu_enum", "operator_menu_hold"}


def _state_after_member(type_name, attr, kind, info):
    if kind == "prop":
        if info.get("type") == "POINTER" and info.get("fixed_type"):
            return ("type", info["fixed_type"])
        if info.get("type") == "COLLECTION":
            return ("coll", info.get("srna"), info.get("fixed_type"))
        return OPAQUE
    if kind == "func":
        return ("func", type_name, attr)
    return OPAQUE


def _check_kwargs(api, facts, owner, params, kwargs, values, kind):
    """Facts for keyword arguments passed to an RNA function or operator."""
    for kw in kwargs:
        if kw not in params:
            continue
        facts.append((kind, *owner, kw))
        value = values.get(kw)
        items = api.enum_items(params[kw]) if params[kw].get("type") == "ENUM" else None
        if isinstance(value, str) and items is not None:
            facts.append((kind.replace("param", "enum") if kind == "op_param" else "param_enum", *owner, kw, value))


def walk(api, usage, classes):
    """Resolve one chain usage against `api`.

    Returns (facts, ok). ok is False when a step that should exist in the API (an RNA member, a
    module attribute, an operator) does not; such usages are ours or untyped and are not compared.
    """
    facts = []
    root = usage["root"]
    if "module" in root:
        state = ("module", root["module"])
        if root["module"] != "bpy":
            if api.module(root["module"]) is None:
                return facts, False
            facts.append(("module", root["module"]))
    elif "type" in root:
        if root["type"] not in api.structs:
            return facts, False
        state = ("type", root["type"])
    elif "self" in root:
        base = class_base_type(api, classes, root["self"])
        if base is None:
            return facts, False
        state = ("type", base)
    else:
        return facts, False

    last_member = None
    for step in usage["steps"]:
        kind = state[0]
        if kind == "opaque":
            break
        if step[0] == "attr":
            attr = step[1]
            last_member = None
            if kind == "module":
                path = state[1]
                if path == "bpy" and attr in ("types", "ops"):
                    state = ("bpy." + attr,)
                elif path == "bpy" and attr in ("context", "data"):
                    state = ("type", "Context" if attr == "context" else "BlendData")
                elif api.module(f"{path}.{attr}") is not None:
                    facts.append(("module", f"{path}.{attr}"))
                    state = ("module", f"{path}.{attr}")
                else:
                    members = api.module(path) or {}
                    if attr not in members:
                        return facts, False
                    facts.append(("module_member", path, attr))
                    entry_kind = members[attr].get("kind")
                    if entry_kind == "class" and attr in api.structs:
                        state = ("type", attr)
                    elif entry_kind == "class":
                        state = ("pyclass", path, attr)
                    elif entry_kind == "function":
                        state = ("modfunc", path, attr)
                    else:
                        state = OPAQUE
            elif kind == "bpy.types":
                if attr not in api.structs:
                    return facts, False
                facts.append(("type", attr))
                state = ("type", attr)
            elif kind == "bpy.ops":
                state = ("opmod", attr)
            elif kind == "opmod":
                op_id = f"{state[1]}.{attr}"
                if op_id not in api.ops:
                    return facts, False
                facts.append(("op", op_id))
                state = ("op", op_id)
            elif kind in ("type", "coll"):
                owner = state[1]
                found = api.find_member(owner, attr) if owner else None
                if found is None and kind == "coll":
                    if attr in COLLECTION_PY_METHODS or owner is None:
                        state = OPAQUE
                        continue
                    return facts, False
                if found is None and owner == "Context" and attr in api.modules.get("bpy.context", {}):
                    facts.append(("module_member", "bpy.context", attr))
                    mapped = CONTEXT_MEMBER_TYPES.get(attr)
                    state = (mapped[0], mapped[1]) if mapped else OPAQUE
                    continue
                if found is None:
                    return facts, False
                facts.append(("member", owner, attr))
                last_member = (owner, attr, found)
                state = _state_after_member(owner, attr, *found)
            elif kind == "opprops":
                props = api.ops[state[1]]["properties"]
                if attr not in props:
                    return facts, False
                facts.append(("op_param", state[1], attr))
                if usage.get("assign") is not None and api.enum_items(props[attr]) is not None:
                    facts.append(("op_enum", state[1], attr, usage["assign"]))
                state = OPAQUE
            elif kind == "pyclass":
                entry = api.module(state[1]).get(state[2], {})
                if attr not in entry.get("all_members", entry.get("members", [])):
                    return facts, False
                facts.append(("pyclass_member", state[1], state[2], attr))
                state = OPAQUE
            else:
                state = OPAQUE
        elif step[0] == "call":
            kwargs, values, args = step[1], step[2], step[3] if len(step) > 3 else []
            if kind == "func":
                type_name, func_name = state[1], state[2]
                info = api.find_member(type_name, func_name)[1]
                params = {p["id"]: p for p in info.get("params", [])}
                _check_kwargs(api, facts, (type_name, func_name), params, kwargs, values, "param")
                outputs = info.get("outputs") or []
                if type_name == "UILayout" and func_name in OPERATOR_PROPS_CALLS and args \
                        and isinstance(args[0], str):
                    state = ("opprops", args[0]) if args[0] in api.ops else OPAQUE  # ours: not compared
                elif outputs and outputs[0].get("type") == "POINTER" and outputs[0].get("fixed_type"):
                    state = ("type", outputs[0]["fixed_type"])
                else:
                    state = OPAQUE
            elif kind == "op":
                op_id = state[1]
                _check_kwargs(api, facts, (op_id,), api.ops[op_id]["properties"], kwargs, values, "op_param")
                facts.append(("op_called", op_id, tuple(kwargs)))
                state = OPAQUE
            elif kind == "modfunc":
                params = api.module(state[1])[state[2]].get("params")
                if params is not None:
                    names = {p.lstrip("*") for p in params}
                    for kw in kwargs:
                        if kw in names:
                            facts.append(("module_param", state[1], state[2], kw))
                state = OPAQUE
            else:
                state = OPAQUE
        elif step[0] == "index":
            if kind == "coll" and state[2]:
                state = ("type", state[2])
            elif kind == "list":
                state = ("type", state[1])
            else:
                state = OPAQUE
    if last_member is not None and usage.get("assign") is not None:
        type_name, attr, (member_kind, info) = last_member
        if member_kind == "prop" and info.get("type") == "ENUM" and api.enum_items(info) is not None:
            facts.append(("enum_value", type_name, attr, usage["assign"]))
    return facts, True


def class_base_type(api, classes, name, depth=0):
    """The Blender struct a shell class derives from (through other shell classes), or None."""
    entry = classes.get(name)
    if entry is None or depth > 8:
        return None
    for base in entry.get("bases", []):
        if "own" in base:
            found = class_base_type(api, classes, base["own"], depth + 1)
        else:
            facts, ok = walk(api, {"root": base["root"], "steps": base["steps"]}, classes)
            found = facts[-1][1] if ok and facts and facts[-1][0] == "type" else None
            if found is None and ok and facts and facts[-1][0] == "module_member" and facts[-1][2] in api.structs:
                found = facts[-1][2]
        if found:
            return found
    return None


def resolve(api, scan):
    """Map every usage to facts in `api`. Returns ({fact: [locations]}, {fact: guarded}, unresolved)."""
    locations = collections.defaultdict(list)
    guarded = {}
    unresolved = []

    def add(fact, usage, is_guarded=False):
        locations[fact].append(f"{usage['file']}:{usage['line']}")
        guarded[fact] = guarded.get(fact, True) and is_guarded

    precise_icons = set()   # locations where the icon= value was checked against its own parameter enum
    for usage in scan["usages"]:
        if usage["kind"] != "chain":
            continue
        facts, ok = walk(api, usage, scan["classes"])
        plain = len(facts)
        if usage.get("guard_from") is not None:
            prefix = {"root": usage["root"], "steps": usage["steps"][:usage["guard_from"]]}
            plain = len(walk(api, prefix, scan["classes"])[0])
        for index, fact in enumerate(facts):
            add(fact, usage, index >= plain)
            if fact[0] == "param" and fact[3] == "icon":
                precise_icons.add((usage["file"], usage["line"]))
        if not ok:
            unresolved.append(usage)

    for usage in scan["usages"]:
        kind = usage["kind"]
        if kind == "chain":
            continue
        if kind == "op":
            if usage["id"] in api.ops:
                add(("op", usage["id"]), usage)
            else:
                unresolved.append(usage)
        elif kind == "type":
            if usage["name"] in api.structs:
                add(("type", usage["name"]), usage)
            else:
                unresolved.append(usage)
        elif kind == "icon":
            # Any icon= keyword whose receiver could not be typed: check it against the global icon
            # list. Values outside that list belong to a function's own enum (invoke_confirm, ...).
            if usage["name"] in api.icons and (usage["file"], usage["line"]) not in precise_icons:
                add(("icon", usage["name"]), usage)
        elif kind == "module":
            if api.module(usage["name"]) is not None:
                add(("module", usage["name"]), usage)
        elif kind == "class":
            base = class_base_type(api, scan["classes"], usage["name"])
            if base is None:
                continue
            add(("type", base), usage)
            for method in usage["methods"]:
                if api.find_member(base, method) is not None:
                    add(("member", base, method), usage)
            for attr, value in usage["attrs"].items():
                found = api.find_member(base, attr)
                if found is None:
                    continue
                add(("member", base, attr), usage)
                member_kind, info = found
                if member_kind == "prop" and info.get("type") == "ENUM" and api.enum_items(info) is not None:
                    for item in (value if isinstance(value, list) else [value]):
                        if isinstance(item, str):
                            add(("enum_value", base, attr, item), usage)
    return locations, guarded, unresolved


def _describe(fact):
    kind = fact[0]
    if kind == "type":
        return f"bpy.types.{fact[1]}"
    if kind == "member":
        return f"{fact[1]}.{fact[2]}"
    if kind == "param":
        return f"{fact[1]}.{fact[2]}({fact[3]}=...)"
    if kind == "param_enum":
        return f"{fact[1]}.{fact[2]}({fact[3]}={fact[4]!r})"
    if kind == "enum_value":
        return f"{fact[1]}.{fact[2]} = {fact[3]!r}"
    if kind == "op":
        return f"bpy.ops.{fact[1]}"
    if kind == "op_param":
        return f"bpy.ops.{fact[1]}({fact[2]}=...)"
    if kind == "op_enum":
        return f"bpy.ops.{fact[1]}({fact[2]}={fact[3]!r})"
    if kind == "op_called":
        return f"bpy.ops.{fact[1]}(...) call"
    if kind == "module":
        return f"module {fact[1]}"
    if kind == "module_member":
        return f"{fact[1]}.{fact[2]}"
    if kind == "module_param":
        return f"{fact[1]}.{fact[2]}({fact[3]}=...)"
    if kind == "pyclass_member":
        return f"{fact[1]}.{fact[2]}.{fact[3]}"
    if kind == "icon":
        return f"icon {fact[1]!r}"
    return repr(fact)


def _prop_shape(info):
    return {k: info.get(k) for k in ("type", "fixed_type", "array_length", "readonly")}


def check(fact, old, new):
    """Compare one fact (true in `old`) with `new`. Returns [(severity, message)]."""
    kind = fact[0]
    if kind == "type":
        if fact[1] not in new.structs:
            return [("BREAK", "removed")]
        old_base, new_base = old.structs[fact[1]].get("base"), new.structs[fact[1]].get("base")
        if old_base != new_base:
            return [("CHANGE", f"base changed: {old_base} -> {new_base}")]
        return []
    if kind == "member":
        if fact[1] not in new.structs:
            return [("BREAK", f"type {fact[1]} removed")]
        before, after = old.find_member(fact[1], fact[2]), new.find_member(fact[1], fact[2])
        if after is None:
            return [("BREAK", "removed")]
        if before[0] != after[0]:
            return [("BREAK", f"was a {before[0]}, now a {after[0]}")]
        if before[0] == "prop":
            a, b = _prop_shape(before[1]), _prop_shape(after[1])
            if a != b:
                changes = ", ".join(f"{k}: {a[k]} -> {b[k]}" for k in a if a[k] != b[k])
                return [("CHANGE", changes)]
        if before[0] == "func":
            old_params = {p["id"]: p for p in before[1].get("params", [])}
            out = []
            for p in after[1].get("params", []):
                if p.get("required") and not old_params.get(p["id"], {}).get("required"):
                    out.append(("CHANGE", f"new required parameter '{p['id']}'"))
            old_out = [_prop_shape(o) for o in before[1].get("outputs", [])]
            new_out = [_prop_shape(o) for o in after[1].get("outputs", [])]
            if old_out != new_out:
                out.append(("CHANGE", f"return value changed: {old_out} -> {new_out}"))
            return out
        return []
    if kind in ("param", "param_enum"):
        found = new.find_member(fact[1], fact[2])
        if found is None or found[0] != "func":
            return [("BREAK", f"function {fact[1]}.{fact[2]} removed")]
        params = {p["id"]: p for p in found[1].get("params", [])}
        if fact[3] not in params:
            return [("BREAK", f"parameter '{fact[3]}' removed")]
        if kind == "param_enum":
            items = new.enum_items(params[fact[3]])
            if items is not None and fact[4] not in items:
                return [("BREAK", f"value {fact[4]!r} no longer valid")]
        return []
    if kind == "enum_value":
        found = new.find_member(fact[1], fact[2])
        if found is None or found[0] != "prop":
            return [("BREAK", "property removed")]
        items = new.enum_items(found[1])
        if items is not None and fact[3] not in items:
            return [("BREAK", f"value {fact[3]!r} no longer valid")]
        return []
    if kind == "op":
        return [] if fact[1] in new.ops else [("BREAK", "operator removed")]
    if kind in ("op_param", "op_enum"):
        if fact[1] not in new.ops:
            return [("BREAK", "operator removed")]
        props = new.ops[fact[1]]["properties"]
        if fact[2] not in props:
            return [("BREAK", f"parameter '{fact[2]}' removed")]
        if kind == "op_enum":
            items = new.enum_items(props[fact[2]])
            if items is not None and fact[3] not in items:
                return [("BREAK", f"value {fact[3]!r} no longer valid")]
        old_shape, new_shape = _prop_shape(old.ops[fact[1]]["properties"][fact[2]]), _prop_shape(props[fact[2]])
        if kind == "op_param" and old_shape != new_shape:
            return [("CHANGE", f"parameter '{fact[2]}' changed: {old_shape} -> {new_shape}")]
        return []
    if kind == "op_called":
        if fact[1] not in new.ops:
            return []  # reported by the ("op", id) fact
        passed = set(fact[2])
        before, after = old.ops[fact[1]]["properties"], new.ops[fact[1]]["properties"]
        out = []
        for name in sorted(set(before) | set(after)):
            if name in passed:
                continue
            if name not in after:
                out.append(("INFO", f"parameter '{name}' removed (we do not pass it)"))
            elif name not in before:
                default = after[name].get("default")
                out.append(("INFO", f"new parameter '{name}' (default {default!r}); check it does not change our output"))
            elif before[name].get("default") != after[name].get("default"):
                out.append(("CHANGE", f"default of '{name}' changed: {before[name].get('default')!r} -> "
                                      f"{after[name].get('default')!r} (we rely on the default)"))
        return out
    if kind == "module":
        return [] if new.module(fact[1]) is not None else [("BREAK", "module removed")]
    if kind == "module_member":
        members = new.module(fact[1])
        if members is None:
            return [("BREAK", f"module {fact[1]} removed")]
        return [] if fact[2] in members else [("BREAK", "removed")]
    if kind == "module_param":
        entry = (new.module(fact[1]) or {}).get(fact[2])
        if entry is None:
            return [("BREAK", "function removed")]
        params = entry.get("params")
        if params is None:
            return []
        names = {p.lstrip("*") for p in params}
        if fact[3] not in names and not any(p.startswith("**") for p in params):
            return [("BREAK", f"keyword '{fact[3]}' removed")]
        return []
    if kind == "pyclass_member":
        entry = (new.module(fact[1]) or {}).get(fact[2])
        if entry is None:
            return [("BREAK", f"class {fact[1]}.{fact[2]} removed")]
        return [] if fact[3] in entry.get("all_members", entry.get("members", [])) else [("BREAK", "removed")]
    if kind == "icon":
        return [] if fact[1] in new.icons else [("BREAK", "icon removed (drawing raises TypeError)")]
    return []


def raw_stats(old, new) -> dict:
    """Size of the whole API difference, before restricting it to what the shell uses."""
    both = set(old.structs) & set(new.structs)
    members_removed = members_added = 0
    for name in both:
        for section in ("properties", "functions"):
            a, b = set(old.structs[name].get(section, {})), set(new.structs[name].get(section, {}))
            members_removed += len(a - b)
            members_added += len(b - a)
    return {
        "types_removed": len(set(old.structs) - set(new.structs)),
        "types_added": len(set(new.structs) - set(old.structs)),
        "members_removed": members_removed,
        "members_added": members_added,
        "operators_removed": len(set(old.ops) - set(new.ops)),
        "operators_added": len(set(new.ops) - set(old.ops)),
        "icons_removed": len(old.icons - new.icons),
        "icons_added": len(new.icons - old.icons),
    }


def diff(old_dump, new_dump, scan) -> dict:
    old, new = Api(old_dump), Api(new_dump)
    locations, guarded, unresolved = resolve(old, scan)
    findings = []
    seen = set()
    for fact in sorted(locations, key=lambda f: (f[0], [str(x) for x in f[1:]])):
        for severity, message in check(fact, old, new):
            if severity == "BREAK" and guarded.get(fact):
                severity, message = "INFO", message + " (guarded by hasattr/getattr)"
            key = (severity, _describe(fact).replace("(...) call", ""), message)
            if key in seen:
                continue
            seen.add(key)
            findings.append({"severity": severity, "what": _describe(fact), "message": message,
                             "used_at": sorted(set(locations[fact]))})
    findings.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["what"]))
    return {
        "old": old_dump.get("blender"), "new": new_dump.get("blender"),
        "kinds": [old_dump.get("kind"), new_dump.get("kind")],
        "raw": raw_stats(old, new),
        "usages": len(scan["usages"]),
        "facts": len(locations),
        "unresolved": len(unresolved),
        "unresolved_usages": unresolved,
        "findings": findings,
    }


def print_report(report, verbose=False) -> None:
    def name(b):
        return (b or {}).get("version_string") or (b or {}).get("source") or "?"

    print(f"API diff {name(report['old'])} -> {name(report['new'])}  "
          f"(dumps: {report['kinds'][0]} -> {report['kinds'][1]})")
    print(f"  shell usages scanned: {report['usages']}, facts resolved in the old API: {report['facts']}, "
          f"not resolvable (ours or plain Python): {report['unresolved']}")
    raw = report["raw"]
    print(f"  whole API (unfiltered): types -{raw['types_removed']}/+{raw['types_added']}, "
          f"members -{raw['members_removed']}/+{raw['members_added']}, "
          f"operators -{raw['operators_removed']}/+{raw['operators_added']}, "
          f"icons -{raw['icons_removed']}/+{raw['icons_added']}")
    counts = collections.Counter(f["severity"] for f in report["findings"])
    print("  findings that touch the shell: " + ", ".join(f"{counts.get(s, 0)} {s}" for s in SEVERITIES))
    for finding in report["findings"]:
        where = ", ".join(finding["used_at"][:3]) + (" ..." if len(finding["used_at"]) > 3 else "")
        print(f"  {finding['severity']:6} {finding['what']}: {finding['message']}")
        print(f"         used at {where}")
    if verbose:
        print("\nUnresolved usages (not found in the old dump):")
        for usage in report["unresolved_usages"]:
            print(f"  {usage['file']}:{usage['line']} {json.dumps({k: v for k, v in usage.items() if k not in ('file', 'line')})}")


# =============================================================================================== source


def _block(text, start):
    """Index just past the brace block that opens at or after `start`."""
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


_C_FUNC = re.compile(r"^(?:static\s+)?(?:void|StructRNA\s*\*|PropertyRNA\s*\*|FunctionRNA\s*\*)\s*(\w+)\s*\(([^)]*)\)\s*\{",
                     re.M)
_PROP_DEFS = {
    "property": None, "boolean": "BOOLEAN", "boolean_array": "BOOLEAN", "boolean_vector": "BOOLEAN",
    "boolean_layer": "BOOLEAN", "boolean_layer_member": "BOOLEAN", "int": "INT", "int_vector": "INT",
    "int_array": "INT", "float": "FLOAT", "float_vector": "FLOAT", "float_vector_xyz": "FLOAT",
    "float_color": "FLOAT", "float_matrix": "FLOAT", "float_translation": "FLOAT", "float_rotation": "FLOAT",
    "float_array": "FLOAT", "float_percentage": "FLOAT", "float_factor": "FLOAT", "float_distance": "FLOAT",
    "string": "STRING", "string_file_path": "STRING", "string_dir_path": "STRING", "string_file_name": "STRING",
    "enum": "ENUM", "enum_flag": "ENUM", "pointer": "POINTER", "pointer_runtime": "POINTER",
    "collection": "COLLECTION", "collection_runtime": "COLLECTION",
}
_C_TOKENS = re.compile(
    r"(?P<struct>(?:StructRNA\s*\*\s*)?(?P<svar>\w+)\s*=\s*RNA_def_struct\(\s*\w+\s*,\s*\"(?P<sname>\w+)\"\s*,\s*"
    r"(?:\"(?P<sbase>\w+)\"|\w+))"
    r"|(?P<func>(?:(?:FunctionRNA\s*\*\s*)?(?P<fvar>\w+)\s*=\s*)?RNA_def_function\(\s*(?P<fowner>\w+)\s*,"
    r"\s*\"(?P<fname>\w+)\")"
    r"|(?P<stype>RNA_def_property_struct_type\(\s*(?P<stvar>\w+)\s*,\s*\"(?P<stname>\w+)\")"
    r"|(?P<srna>RNA_def_property_srna\(\s*(?P<srvar>\w+)\s*,\s*\"(?P<srname>\w+)\")"
    r"|(?P<eitems>RNA_def_property_enum_items\(\s*(?P<eivar>\w+)\s*,\s*(?P<einame>\w+)\s*\))"
    r"|(?P<req>RNA_def_parameter_flags\(\s*(?P<rqvar>\w+)\s*,[^;]*PARM_REQUIRED)"
    r"|(?P<ret>RNA_def_function_(?:return|output)\(\s*\w+\s*,\s*(?P<retvar>\w+)\s*\))"
    r"|(?P<noself>RNA_def_function_flag\(\s*(?P<nsvar>\w+)\s*,[^;]*FUNC_NO_SELF)"
    r"|(?P<opid>ot->idname\s*=\s*\"(?P<opname>\w+)\")"
    r"|(?P<prop>(?:(?:PropertyRNA\s*\*\s*)?(?P<pvar>\w+)\s*=\s*)?RNA_def_(?P<pdef>\w+?)\(\s*(?P<powner>[\w\->.]+)\s*,"
    r"\s*\"(?P<pname>\w+)\"(?P<prest>[^;]*);)"
    r"|(?P<sysidp>RNA_def_struct_system_idprops_func\(\s*(?P<sysvar>\w+))"
    r"|(?P<call>\b(?P<callee>(?:rna_def|RNA_api|rna_api|api)_\w+)\s*\((?P<cargs>[^;{}]*?)\)\s*;)"
)
_ENUM_ARRAY = re.compile(r"EnumPropertyItem\s+(\w+)\s*\[\s*\]\s*=\s*\{", re.M)
_ENUM_ITEM = re.compile(r"\{\s*[^{},]+,\s*\"(\w+)\"")


def _op_python_id(c_idname):
    match = re.fullmatch(r"([A-Z0-9_]+?)_OT_(\w+)", c_idname)
    return f"{match.group(1).lower()}.{match.group(2)}" if match else c_idname


class SourceDumper:
    """A partial API dump read statically from a Blender source tree (no build needed)."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.structs = collections.defaultdict(lambda: {"base": None, "properties": {}, "functions": {}})
        self.ops = collections.defaultdict(lambda: {"properties": {}})
        self.enums = {}
        self.pending_enums = []      # (target dict, enum array name)
        # (kind, helper fn, argument position) -> what callers pass there (struct names, property or
        # function infos); members a helper defines on its parameters are copied to those.
        self.helpers = collections.defaultdict(list)
        self.func_placeholders = {}  # (helper fn, position) -> {"params": [...]} defined on a FunctionRNA param

    def read(self, rel):
        path = self.root / rel
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    # --- RNA ---------------------------------------------------------------------------------

    def parse_enums(self, text):
        for match in _ENUM_ARRAY.finditer(text):
            end = _block(text, match.end() - 1)
            body = text[match.end():end]
            self.enums[match.group(1)] = sorted({m.group(1) for m in _ENUM_ITEM.finditer(body)})

    def parse_rna_file(self, text):
        # BlendData collections are defined from a table in rna_main.cc, not one call each.
        for match in re.finditer(r"\{\s*\"(\w+)\",\s*\"(\w+)\",\s*\"\w+\",\s*\"[^\"]*\",\s*\"[^\"]*\",\s*"
                                 r"(RNA_def_main_\w+)\s*\}", text):
            info = {"type": "COLLECTION", "fixed_type": match.group(2)}
            self.structs["BlendData"]["properties"][match.group(1)] = info
            self.helpers[("prop", match.group(3), 1)].append(info)
        for match in _C_FUNC.finditer(text):
            fn, params = match.group(1), match.group(2)
            end = _block(text, match.end() - 1)
            self.parse_c_function(fn, params, text[match.end():end])

    def parse_c_function(self, fn, params, body):
        svars, fvars, pvars = {}, {}, {}
        for pos, param in enumerate(p.strip() for p in params.split(",")):
            match = re.match(r"(StructRNA|PropertyRNA|FunctionRNA)\s*\*\s*(\w+)$", param)
            if match and match.group(1) == "StructRNA":
                svars[match.group(2)] = f"@{fn}#{pos}"
            elif match and match.group(1) == "FunctionRNA":
                fvars[match.group(2)] = self.func_placeholders.setdefault((fn, pos), {"params": []})
            elif match:
                pvars[match.group(2)] = ("@prop", fn, pos)
        op = None
        for m in _C_TOKENS.finditer(body):
            if m.group("struct"):
                name = m.group("sname")
                svars[m.group("svar")] = name
                struct = self.structs[name]
                if m.group("sbase"):
                    struct["base"] = m.group("sbase")
            elif m.group("func"):
                owner = svars.get(m.group("fowner"))
                if owner:
                    info = {"params": []}
                    self.structs[owner]["functions"][m.group("fname")] = info
                    # `func = cond ? RNA_def_function(...) : RNA_def_function(...)` has no direct
                    # assignment; the later parameter definitions then go through `func`.
                    fvars[m.group("fvar") or "func"] = info
            elif m.group("prop"):
                pdef = m.group("pdef")
                if pdef not in _PROP_DEFS:
                    continue
                ptype = _PROP_DEFS[pdef]
                rest = m.group("prest")
                if ptype is None:
                    t = re.match(r"\s*,\s*PROP_(\w+)", rest)
                    ptype = t.group(1) if t else "UNKNOWN"
                info = {"type": ptype}
                if ptype in ("POINTER", "COLLECTION"):
                    t = re.match(r"\s*,\s*\"(\w+)\"", rest)
                    if t:
                        info["fixed_type"] = t.group(1)
                if ptype == "ENUM" and pdef.startswith("enum"):
                    t = re.match(r"\s*,\s*(\w+)", rest)
                    if t:
                        self.pending_enums.append((info, t.group(1)))
                owner_var, name = m.group("powner"), m.group("pname")
                if owner_var in fvars:
                    info["id"] = name
                    fvars[owner_var]["params"].append(info)
                elif owner_var in svars:
                    self.structs[svars[owner_var]]["properties"][name] = info
                elif owner_var == "ot->srna" and op:
                    self.ops[op]["properties"][name] = info
                else:
                    continue
                if m.group("pvar"):
                    pvars[m.group("pvar")] = info
            elif m.group("stype"):
                if isinstance(pvars.get(m.group("stvar")), dict):
                    pvars[m.group("stvar")]["fixed_type"] = m.group("stname")
            elif m.group("srna"):
                target = pvars.get(m.group("srvar"))
                if isinstance(target, dict):
                    target["srna"] = m.group("srname")
                elif isinstance(target, tuple):
                    self.helpers[("srna", target[1], target[2])].append(m.group("srname"))
            elif m.group("eitems"):
                if isinstance(pvars.get(m.group("eivar")), dict):
                    self.pending_enums.append((pvars[m.group("eivar")], m.group("einame")))
            elif m.group("req"):
                target = self._param(fvars, m.group("rqvar"), pvars)
                if target is not None:
                    target["required"] = True
            elif m.group("ret"):
                self._mark_output(fvars, pvars, m.group("retvar"))
            elif m.group("noself"):
                if m.group("nsvar") in fvars:
                    fvars[m.group("nsvar")]["static"] = True
            elif m.group("opid"):
                op = _op_python_id(m.group("opname"))
                self.ops[op]
            elif m.group("sysidp"):
                # Generic accessor generated for structs with system (bpy.props) ID properties.
                if m.group("sysvar") in svars:
                    self.structs[svars[m.group("sysvar")]]["functions"].setdefault(
                        "bl_system_properties_get", {"params": [], "outputs": [{"id": "idprops", "type": "POINTER"}]})
            elif m.group("call"):
                args = [a.strip() for a in m.group("cargs").split(",")]
                for pos, arg in enumerate(args):
                    if arg in svars:
                        self.helpers[("struct", m.group("callee"), pos)].append(svars[arg])
                    elif arg in fvars:
                        self.helpers[("func", m.group("callee"), pos)].append(fvars[arg])
                    elif isinstance(pvars.get(arg), dict):
                        self.helpers[("prop", m.group("callee"), pos)].append(pvars[arg])

    @staticmethod
    def _param(fvars, var, pvars):
        target = pvars.get(var)
        return target if isinstance(target, dict) else None

    @staticmethod
    def _mark_output(fvars, pvars, var):
        target = pvars.get(var)
        if not isinstance(target, dict):
            return
        for info in fvars.values():
            for i, param in enumerate(info["params"]):
                if param is target:
                    info["params"].pop(i)
                    info.setdefault("outputs", []).append(param)
                    return

    def resolve_placeholders(self):
        """Copy members defined on helper-function parameters to the structs passed in."""
        for _ in range(6):
            changed = False
            for name in [n for n in list(self.structs) if n.startswith("@")]:
                fn, _, pos = name[1:].partition("#")
                for target in self.helpers.get(("struct", fn, int(pos)), []):
                    dest = self.structs[target]
                    src = self.structs[name]
                    for section in ("properties", "functions"):
                        for key, value in src[section].items():
                            if key not in dest[section]:
                                dest[section][key] = value
                                changed = True
            for (kind, fn, pos), names in list(self.helpers.items()):
                if kind != "srna":
                    continue
                for prop in self.helpers.get(("prop", fn, pos), []):
                    if "srna" not in prop and names:
                        prop["srna"] = names[0]
                        changed = True
            for (fn, pos), template in self.func_placeholders.items():
                for target in self.helpers.get(("func", fn, pos), []):
                    have = {p["id"] for p in target["params"]}
                    for param in template["params"]:
                        if param["id"] not in have:
                            target["params"].append(param)
                            changed = True
            if not changed:
                break
        for name in [n for n in self.structs if n.startswith("@")]:
            del self.structs[name]
        for info, enum_name in self.pending_enums:
            if enum_name == "rna_enum_icon_items":
                info["enum"] = "@icons"   # built from UI_icons.hh by the preprocessor
            elif enum_name in self.enums:
                info["enum"] = self.enums[enum_name]
            else:
                info["dynamic_enum"] = True

    def parse_rna(self):
        intern = self.root / "source" / "blender" / "makesrna" / "intern"
        files = sorted(intern.glob("rna_*.cc"))
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in files]
        for text in texts:
            self.parse_enums(text)
        for text in texts:
            self.parse_rna_file(text)

    def parse_c_operators(self):
        base = self.root / "source" / "blender"
        for path in sorted(base.rglob("*.cc")):
            if "makesrna" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ot->idname" not in text:
                continue
            self.parse_enums(text)
            for match in _C_FUNC.finditer(text):
                body_end = _block(text, match.end() - 1)
                body = text[match.end():body_end]
                if "ot->idname" in body:
                    self.parse_c_function(match.group(1), match.group(2), body)

    # --- Python ----------------------------------------------------------------------------------

    def parse_python(self):
        scripts = self.root / "scripts"
        roots = [("startup", ("bl_ui", "bl_operators")),
                 ("modules", ("bl_app_override", "bl_app_template_utils", "addon_utils", "_bpy_restrict_state",
                              "bpy_extras", "_bpy_types", "bpy_types", "bl_keymap_utils", "rna_prop_ui",
                              "bl_ui_utils")),
                 ("addons_core", ("io_scene_gltf2",))]
        self.py_modules = {}
        classes = {}
        for sub, prefixes in roots:
            base = scripts / sub
            for path in sorted(base.rglob("*.py")):
                rel = path.relative_to(base).with_suffix("")
                parts = list(rel.parts)
                if parts[-1] == "__init__":
                    parts.pop()
                if not parts or parts[0] not in prefixes:
                    continue
                if parts[0] == "io_scene_gltf2" and len(parts) > 1:
                    continue  # only the add-on's operator module
                module = ".".join(parts)
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                self.py_modules[module] = self.parse_py_module(module, tree, classes)
        self.finish_python(classes)
        # bpy.utils / bpy.path are Python too.
        for module, rel in (("bpy.utils", "modules/bpy/utils/__init__.py"), ("bpy.path", "modules/bpy/path.py")):
            path = scripts / rel
            if path.is_file():
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                members = {k: {"kind": v["kind"], **({"params": v["params"]} if "params" in v else {})}
                           for k, v in self.parse_py_module(module, tree, {}).items()}
                for node in tree.body:   # e.g. `from _bpy import register_class, ...`
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            members.setdefault(alias.asname or alias.name, {"kind": "function"})
                self.modules[module] = members

    @staticmethod
    def _base_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def parse_py_module(self, module, tree, classes):
        entries = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                members, attrs, annotations = [], {}, {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members.append(item.name)
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                members.append(target.id)
                                if target.id.startswith("bl_"):
                                    attrs[target.id] = _const(item.value)
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        members.append(item.target.id)
                        annotations[item.target.id] = self.py_prop(item.annotation)
                bases = [b for b in (self._base_name(b) for b in node.bases) if b]
                entry = {"kind": "class", "bases": bases, "members": sorted(set(members))}
                entries[node.name] = entry
                classes.setdefault(node.name, []).append((module, entry, attrs, annotations))
            elif isinstance(node, ast.FunctionDef):
                args = node.args
                params = [a.arg for a in args.posonlyargs + args.args]
                if args.vararg:
                    params.append("*" + args.vararg.arg)
                params += [a.arg for a in args.kwonlyargs]
                if args.kwarg:
                    params.append("**" + args.kwarg.arg)
                entries[node.name] = {"kind": "function", "params": params}
        return entries

    @staticmethod
    def py_prop(node):
        if not (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))):
            return None
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        kind = {"BoolProperty": "BOOLEAN", "BoolVectorProperty": "BOOLEAN", "IntProperty": "INT",
                "IntVectorProperty": "INT", "FloatProperty": "FLOAT", "FloatVectorProperty": "FLOAT",
                "StringProperty": "STRING", "EnumProperty": "ENUM", "PointerProperty": "POINTER",
                "CollectionProperty": "COLLECTION"}.get(name)
        if kind is None:
            return None
        info = {"type": kind}
        for kw in node.keywords:
            if kw.arg == "default":
                value = _const(kw.value)
                if value is not None:
                    info["default"] = value
            if kw.arg == "items" and kind == "ENUM":
                if isinstance(kw.value, (ast.List, ast.Tuple)):
                    ids = [e.elts[0].value for e in kw.value.elts if isinstance(e, ast.Tuple) and e.elts
                           and isinstance(e.elts[0], ast.Constant)]
                    info["enum"] = sorted(ids)
                else:
                    info["dynamic_enum"] = True
        return info

    def finish_python(self, classes):
        def all_members(name, module, depth=0):
            found = classes.get(name)
            if not found or depth > 10:
                return set(), {}, {}
            mod, entry, attrs, annotations = next(((m, e, a, n) for m, e, a, n in found if m == module), found[0])
            members, all_attrs, all_ann = set(entry["members"]), {}, {}
            for base in entry["bases"]:
                m, a, n = all_members(base, mod, depth + 1)
                members |= m
                all_attrs.update(a)
                all_ann.update(n)
            all_attrs.update(attrs)
            all_ann.update({k: v for k, v in annotations.items() if v})
            return members, all_attrs, all_ann

        # _bpy_types.py adds Python methods to RNA types (Object.children, Menu.append, ...).
        for module in ("_bpy_types", "bpy_types"):
            for name, entry in self.py_modules.get(module, {}).items():
                if entry["kind"] == "class" and name in self.structs:
                    members = {m for m in all_members(name, module)[0] if not m.startswith("__")}
                    self.structs[name]["py_members"] = sorted(set(self.structs[name].get("py_members", [])) | members)

        ui_bases = {"Menu", "Header", "Panel", "UIList", "Operator", "Macro", "PropertyGroup", "AddonPreferences",
                    "AssetShelf", "FileHandler", "Gizmo", "GizmoGroup", "WorkSpaceTool"}
        for module, entries in self.py_modules.items():
            for name, entry in entries.items():
                if entry["kind"] != "class":
                    continue
                members, attrs, annotations = all_members(name, module)
                entry["all_members"] = sorted(members)
                rna_base = self._rna_base(name, module, classes, ui_bases)
                if rna_base is None:
                    continue
                idname = attrs.get("bl_idname")
                if rna_base in ("Operator", "Macro") and isinstance(idname, str):
                    self.ops[idname] = {"properties": annotations}
                    continue
                struct_name = idname if isinstance(idname, str) and rna_base != "Operator" else name
                self.structs[struct_name] = {
                    "base": rna_base, "properties": {}, "functions": {},
                    "py_members": sorted(m for m in members if not m.startswith("__")),
                    "py_module": module, "py_bases": entry["bases"],
                    "py_attrs": {k: v for k, v in attrs.items() if v is not None},
                }

    def _rna_base(self, name, module, classes, ui_bases, depth=0):
        found = classes.get(name)
        if not found or depth > 10:
            return None
        _mod, entry, _attrs, _ann = next(((m, e, a, n) for m, e, a, n in found if m == module), found[0])
        for base in entry["bases"]:
            if base in ui_bases:
                return base
            hit = self._rna_base(base, module, classes, ui_bases, depth + 1)
            if hit:
                return hit
        return None

    # --- bpy.app, bpy.props, icons, context ------------------------------------------------------

    def parse_misc(self):
        self.icons = sorted(set(re.findall(r"^DEF_ICON(?:_[A-Z_]+)?\((\w+)\)", self.read(
            "source/blender/editors/include/UI_icons.hh"), re.M)) - {"BLANK"})

        def table(text, array):
            match = re.search(re.escape(array) + r"\s*\[\s*\]\s*=\s*\{", text)
            if not match:
                return []
            body = text[match.end():_block(text, match.end() - 1)]
            return re.findall(r"\{\s*\"(\w+)\"", body)

        app = self.read("source/blender/python/intern/bpy_app.cc")
        names = table(app, "app_info_fields") + table(app, "bpy_app_getsets")
        names += re.findall(r"PyModule_AddObject\w*\(\s*mod\s*,\s*\"(\w+)\"", app)
        self.modules["bpy.app"] = {n: {"kind": "app"} for n in names}
        handlers = self.read("source/blender/python/intern/bpy_app_handlers.cc")
        self.modules["bpy.app.handlers"] = {n: {"kind": "list"} for n in table(handlers, "app_cb_info_fields")}
        self.modules["bpy.app.handlers"]["persistent"] = {"kind": "function"}
        timers = self.read("source/blender/python/intern/bpy_app_timers.cc")
        self.modules["bpy.app.timers"] = {n: {"kind": "function"} for n in re.findall(r"\{\s*\"(\w+)\"\s*,", timers)}
        props = self.read("source/blender/python/intern/bpy_props.cc")
        self.modules["bpy.props"] = {}
        for match in re.finditer(r"\n(?:static )?PyObject \*BPy_(\w+Property)\s*\(", props):
            body = props[match.end():_block(props, match.end())]
            keywords = re.search(r"_keywords\[\]\s*=\s*\{([^}]*)\}", body)
            params = re.findall(r"\"(\w+)\"", keywords.group(1)) if keywords else None
            entry = {"kind": "function"}
            if params:
                entry["params"] = params
            self.modules["bpy.props"][match.group(1)] = entry
        msgbus = self.read("source/blender/python/intern/bpy_msgbus.cc")
        self.modules["bpy.msgbus"] = {n: {"kind": "function"} for n in re.findall(r"\{\s*\"(\w+)\"\s*,", msgbus)}
        context = self.read("source/blender/editors/screen/screen_context.cc")
        match = re.search(r"screen_context_dir\s*\[\s*\]\s*=\s*\{", context)
        names = re.findall(r"\"(\w+)\"", context[match.end():_block(context, match.end() - 1)]) if match else []
        self.modules["bpy.context"] = {n: {"kind": "context"} for n in names}
        # Methods every bpy_struct has (get, keys, property_unset, bl_system_properties_get, ...).
        rna = self.read("source/blender/python/intern/bpy_rna.cc")
        self.structs["bpy_struct"] = {"base": None, "properties": {}, "functions": {},
                                      "py_members": sorted(set(table(rna, "pyrna_struct_methods"))
                                                           | {"bl_rna", "rna_type", "id_data"})}
        # bpy.data.user_map(), batch_remove(), orphans_purge(), ... are C-Python methods of BlendData.
        id_collection = self.read("source/blender/python/intern/bpy_rna_id_collection.cc")
        extra = set(re.findall(r"PyMethodDef\s+\w+\s*=\s*\{\s*\"(\w+)\"", id_collection))
        if extra:
            blend_data = self.structs["BlendData"]
            blend_data["py_members"] = sorted(set(blend_data.get("py_members", [])) | extra)
        if "\"temp_override\"" in self.read("source/blender/python/intern/bpy_rna_context.cc"):
            context_struct = self.structs["Context"]
            context_struct["py_members"] = sorted(set(context_struct.get("py_members", [])) | {"temp_override"})

    def dump(self):
        self.modules = {}
        self.parse_rna()
        self.parse_c_operators()
        self.resolve_placeholders()
        self.parse_python()
        self.parse_misc()
        version = re.search(r"#define BLENDER_VERSION (\d+)", self.read(
            "source/blender/blenkernel/BKE_blender_version.h"))
        patch = re.search(r"#define BLENDER_VERSION_PATCH (\d+)", self.read(
            "source/blender/blenkernel/BKE_blender_version.h"))
        cycle = re.search(r"#define BLENDER_VERSION_CYCLE (\w+)", self.read(
            "source/blender/blenkernel/BKE_blender_version.h"))
        label = (f"{int(version.group(1)) // 100}.{int(version.group(1)) % 100}.{patch.group(1)} {cycle.group(1)}"
                 if version and patch and cycle else "?")
        return {
            "format": FORMAT, "format_version": 1, "kind": "source",
            "blender": {"source": f"{label} (source {self.root.name})", "version_string": f"{label} (source)"},
            "icons": self.icons,
            "structs": {k: v for k, v in sorted(self.structs.items())},
            "operators": {k: v for k, v in sorted(self.ops.items())},
            "modules": self.modules,
            "py_modules": self.py_modules,
        }


# =============================================================================================== CLI


def write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                               default=list), encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="api_diff", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("dump", help="dump a Blender binary's API surface")
    d.add_argument("--blender", required=True)
    d.add_argument("--out", required=True)
    s = sub.add_parser("scan", help="list what the shell uses")
    s.add_argument("--shell", default=str(SHELL))
    s.add_argument("--json")
    f = sub.add_parser("diff", help="diff two dumps, restricted to what the shell uses")
    f.add_argument("old")
    f.add_argument("new")
    f.add_argument("--shell", default=str(SHELL))
    f.add_argument("--json")
    f.add_argument("--verbose", action="store_true")
    c = sub.add_parser("compare", help="dump two Blender binaries and diff them")
    c.add_argument("--old", required=True)
    c.add_argument("--new", required=True)
    c.add_argument("--keep", help="keep the dumps in this folder")
    c.add_argument("--shell", default=str(SHELL))
    c.add_argument("--json")
    c.add_argument("--verbose", action="store_true")
    r = sub.add_parser("source-dump", help="static, partial dump of a Blender source tree")
    r.add_argument("--source", required=True)
    r.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "dump":
        out = dump_binary(args.blender, args.out)
        print(f"api_diff: wrote {out}")
        return 0
    if args.command == "source-dump":
        data = SourceDumper(args.source).dump()
        write_json(args.out, data)
        print(f"api_diff: wrote {args.out} ({len(data['structs'])} structs, {len(data['operators'])} operators, "
              f"from source {args.source})")
        return 0
    if args.command == "scan":
        scan = ShellScanner(args.shell).scan()
        if args.json:
            write_json(args.json, scan)
        counts = collections.Counter(u["kind"] for u in scan["usages"])
        print(f"api_diff: {scan['files']} files, {len(scan['usages'])} usages: "
              + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
        return 0

    scan = ShellScanner(args.shell).scan()
    if args.command == "compare":
        folder = pathlib.Path(args.keep) if args.keep else pathlib.Path(tempfile.mkdtemp(prefix="gs_api_diff_"))
        old_dump = load_dump(dump_binary(args.old, folder / "old.json"))
        new_dump = load_dump(dump_binary(args.new, folder / "new.json"))
    else:
        old_dump, new_dump = load_dump(args.old), load_dump(args.new)
    if old_dump.get("kind") != new_dump.get("kind"):
        print("api_diff: WARNING: comparing a binary dump with a source dump; expect noise", file=sys.stderr)
    report = diff(old_dump, new_dump, scan)
    print_report(report, verbose=args.verbose)
    if args.json:
        write_json(args.json, report)
    return 1 if any(f["severity"] in ("BREAK", "CHANGE") for f in report["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
