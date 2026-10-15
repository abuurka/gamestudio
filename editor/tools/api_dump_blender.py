# SPDX-License-Identifier: GPL-3.0-or-later
"""Dump Blender's Python API surface to JSON (run *inside* Blender by editor/tools/api_diff.py).

    blender --background --factory-startup --app-template default --python-exit-code 1 \
            --python editor/tools/api_dump_blender.py -- OUT.json

What is dumped (format "gamestudio.api_dump", version 1):
  structs    every bpy.types class: RNA base, own RNA properties and functions (parameters,
             return values, static enum items), Python members not inherited from bpy_struct,
             and, for Python-defined classes (bl_ui menus, headers, ...), module and bl_* attributes
  operators  every bpy.ops operator: its properties with type, static enum items and default
  modules    names and signatures in bpy.app, bpy.app.handlers, bpy.app.timers, bpy.props (keyword
             names), bpy.utils, bpy.path, bpy.msgbus, bpy.context, mathutils, bmesh, gpu
  py_modules Blender's own Python modules a shell may import (bl_ui.*, bl_operators.*,
             bl_app_override, addon_utils, _bpy_restrict_state, bpy_extras.*, ...): top-level
             functions with parameters, classes with bases and members
  icons      the icon enum (shared by every `icon=` parameter, referenced as "@icons")

The dump is deterministic (sorted keys, no addresses), so dumping the same build twice gives
identical files. Run only with --factory-startup so enabled add-ons match the factory set.
"""

import importlib
import inspect
import json
import re
import sys

import bpy

FORMAT = "gamestudio.api_dump"
FORMAT_VERSION = 1
PY_MODULE_PREFIXES = ("bl_ui", "bl_operators", "bl_app_override", "bl_app_template_utils", "addon_utils",
                      "_bpy_restrict_state", "bpy_extras", "bl_keymap_utils", "bpy_types", "_bpy_types",
                      "bl_ui_utils", "rna_prop_ui", "bpy.utils", "bl_math")
MODULES = ("bpy.app", "bpy.app.handlers", "bpy.app.timers", "bpy.app.translations", "bpy.app.icons",
           "bpy.props", "bpy.utils", "bpy.utils.units", "bpy.path", "bpy.msgbus", "mathutils",
           "mathutils.geometry", "mathutils.noise", "bmesh", "bmesh.ops", "bmesh.types", "bmesh.utils", "gpu",
           "gpu.types", "gpu.shader", "gpu.state", "gpu.matrix", "gpu.platform", "gpu_extras.batch", "blf")
BIG_ENUM = 300


def jsonable(value):
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (set, frozenset)):
        return sorted(jsonable(v) for v in value)
    try:
        return [jsonable(v) for v in value]
    except TypeError:
        return repr(value)


class Dumper:
    def __init__(self):
        label = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"]
        self.icons = sorted(item.identifier for item in label.enum_items)
        self.icon_set = set(self.icons)
        self.base_py = set(dir(bpy.types.bpy_struct))

    # --- RNA -----------------------------------------------------------------------------------

    def enum_items(self, prop):
        items = [item.identifier for item in prop.enum_items]
        if not items:
            return None
        if len(items) > BIG_ENUM and set(items) == self.icon_set:
            return "@icons"
        return sorted(items)

    def prop(self, prop, with_default=False):
        info = {"type": prop.type}
        if prop.type in ("POINTER", "COLLECTION") and prop.fixed_type is not None:
            info["fixed_type"] = prop.fixed_type.identifier
        if prop.type == "COLLECTION" and getattr(prop, "srna", None) is not None:
            info["srna"] = prop.srna.identifier
        if prop.type in ("INT", "FLOAT", "BOOLEAN") and getattr(prop, "array_length", 0):
            info["array_length"] = prop.array_length
        if prop.type == "ENUM":
            items = self.enum_items(prop)
            if items is None:
                info["dynamic_enum"] = True
            else:
                info["enum"] = items
            if prop.is_enum_flag:
                info["enum_flag"] = True
        if prop.is_readonly:
            info["readonly"] = True
        if prop.is_required:
            info["required"] = True
        if with_default:
            default = self.default(prop)
            if default is not None:
                info["default"] = default
        return info

    @staticmethod
    def default(prop):
        if prop.type == "ENUM":
            if not len(prop.enum_items):
                return None  # dynamic: reading the default only produces a warning
            return jsonable(prop.default_flag if prop.is_enum_flag else prop.default)
        if prop.type in ("INT", "FLOAT", "BOOLEAN"):
            return jsonable(prop.default_array if getattr(prop, "array_length", 0) else prop.default)
        if prop.type == "STRING":
            return prop.default
        return None

    def function(self, func):
        params, outputs = [], []
        for parm in func.parameters:
            entry = {"id": parm.identifier, **self.prop(parm)}
            (outputs if parm.is_output else params).append(entry)
        info = {"params": params}
        if outputs:
            info["outputs"] = outputs
        if not func.use_self:
            info["static"] = True
        return info

    def struct(self, cls):
        rna = cls.bl_rna
        base = rna.base
        base_props = set(base.properties.keys()) if base else set()
        base_funcs = set(base.functions.keys()) if base else set()
        info = {
            "base": base.identifier if base else None,
            "properties": {p.identifier: self.prop(p) for p in rna.properties
                           if p.identifier not in base_props and p.identifier != "rna_type"},
            "functions": {f.identifier: self.function(f) for f in rna.functions
                          if f.identifier not in base_funcs},
        }
        rna_names = set(rna.properties.keys()) | set(rna.functions.keys())
        parent_py = set(dir(getattr(bpy.types, base.identifier, object))) if base else set()
        members = sorted(n for n in dir(cls) if not n.startswith("__") and n not in self.base_py
                         and n not in rna_names and n not in parent_py)
        if members:
            info["py_members"] = members
        if cls.__module__ != "bpy.types":
            info["py_module"] = cls.__module__
            info["py_bases"] = [b.__name__ for b in cls.__bases__]
            info["py_attrs"] = {k: jsonable(v) for k, v in sorted(vars(cls).items())
                                if k.startswith("bl_") and k != "bl_rna" and not callable(v)}
            if isinstance(info["py_attrs"].get("bl_file_extensions"), str):
                # Built by joining a set in some file handlers, so the order varies between runs.
                parts = info["py_attrs"]["bl_file_extensions"].split(";")
                info["py_attrs"]["bl_file_extensions"] = ";".join(sorted(parts))
        return info

    def structs(self):
        out = {}
        for name in sorted(dir(bpy.types)):
            cls = getattr(bpy.types, name, None)
            if isinstance(cls, type) and hasattr(cls, "bl_rna"):
                try:
                    out[name] = self.struct(cls)
                except Exception as exc:  # keep going; record why
                    out[name] = {"error": repr(exc)}
        out["bpy_struct"] = {"base": None, "properties": {}, "functions": {},
                             "py_members": sorted(n for n in self.base_py if not n.startswith("__"))}
        return out

    def operators(self):
        out = {}
        for mod_name in sorted(dir(bpy.ops)):
            if mod_name.startswith("_"):
                continue
            mod = getattr(bpy.ops, mod_name)
            for op_name in sorted(dir(mod)):
                if op_name.startswith("_"):
                    continue
                op = getattr(mod, op_name)
                try:
                    rna = op.get_rna_type()
                except Exception:
                    continue
                out[f"{mod_name}.{op_name}"] = {
                    "properties": {p.identifier: self.prop(p, with_default=True) for p in rna.properties
                                   if p.identifier != "rna_type"},
                }
        return out

    # --- Python modules --------------------------------------------------------------------------

    @staticmethod
    def signature(fn):
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            doc = getattr(fn, "__doc__", None) or ""
            match = re.search(r"\.\. (?:function|method|classmethod|staticmethod):: \w+\((.*?)\)\s*$", doc, re.M)
            if not match:
                return None
            names = []
            for part in match.group(1).split(","):
                name = part.strip().split("=")[0].strip()
                if name and name not in ("*", "/") and re.match(r"^\*{0,2}\w+$", name):
                    names.append(name)
            return names
        return [("*" if p.kind is p.VAR_POSITIONAL else "**" if p.kind is p.VAR_KEYWORD else "") + p.name
                for p in sig.parameters.values()]

    def module_members(self, module):
        out = {}
        for name in sorted(dir(module)):
            if name.startswith("__"):
                continue
            try:
                value = getattr(module, name)
            except Exception:
                continue
            if inspect.ismodule(value):
                out[name] = {"kind": "module"}
            elif inspect.isclass(value):
                out[name] = {"kind": "class", "members": sorted(n for n in dir(value) if not n.startswith("__"))}
            elif callable(value):
                entry = {"kind": "function"}
                params = self.signature(value)
                if params is not None:
                    entry["params"] = params
                out[name] = entry
            else:
                out[name] = {"kind": type(value).__name__}
        return out

    def modules(self):
        out = {}
        for path in MODULES:
            try:
                module = importlib.import_module(path)
            except ImportError:
                try:  # attributes that are not importable modules, e.g. bpy.app.handlers
                    head, _, tail = path.rpartition(".")
                    module = getattr(importlib.import_module(head), tail)
                except Exception:
                    continue
            out[path] = self.module_members(module)
        out["bpy.context"] = {name: {"kind": "context"} for name in sorted(dir(bpy.context))
                              if not name.startswith("__")}
        return out

    def py_modules(self):
        names = sorted(n for n in list(sys.modules) if n.startswith(PY_MODULE_PREFIXES))
        out = {}
        for name in names:
            module = sys.modules.get(name)
            if module is None:
                continue
            entries = {}
            for attr, value in sorted(vars(module).items()):
                if attr.startswith("__"):
                    continue
                if inspect.isclass(value) and getattr(value, "__module__", None) == name:
                    entries[attr] = {
                        "kind": "class",
                        "bases": [b.__name__ for b in value.__bases__],
                        "members": sorted(n for n in vars(value) if not n.startswith("__")),
                        "all_members": sorted(n for n in dir(value) if not n.startswith("__")
                                              and n not in self.base_py),
                    }
                elif inspect.isfunction(value) and value.__module__ == name:
                    entries[attr] = {"kind": "function", "params": self.signature(value) or []}
            out[name] = entries
        return out

    def dump(self):
        return {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "kind": "binary",
            "blender": {
                "version": list(bpy.app.version),
                "version_string": bpy.app.version_string,
                "build_hash": bpy.app.build_hash.decode("ascii", "replace"),
            },
            "icons": self.icons,
            "structs": self.structs(),
            "operators": self.operators(),
            "modules": self.modules(),
            "py_modules": self.py_modules(),
        }


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender -b --factory-startup --python api_dump_blender.py -- OUT.json")
    data = Dumper().dump()
    with open(argv[0], "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    print(f"API_DUMP_OK structs={len(data['structs'])} operators={len(data['operators'])} -> {argv[0]}")


main()
