# SPDX-License-Identifier: GPL-3.0-or-later
"""Component API used by operators, the UI, export, live link and the AI assistant (§7).

Attachment order lives in obj.gs.components; field values live in the
generated obj.gsc_<Name> groups. Every write is validated first and then
applied, so a failing call leaves the object unchanged. All writes go through
bpy.data (never bpy.ops) and must happen on the main thread.
"""

import math
import os
import pathlib

import bpy

from . import ids, project, registry, schema
from .schema import coerce_value  # noqa: F401  (part of the public API, §7)


class ReadOnlyObjectError(ValueError):
    """The object is linked from a library or is a system override."""


# --- Permissions ---------------------------------------------------------------


def is_writable(obj) -> bool:
    if obj.library is not None:
        return False
    override = obj.override_library
    return not (override is not None and override.is_system_override)


def check_writable(obj) -> None:
    if obj.library is not None:
        raise ReadOnlyObjectError(
            f"Object '{obj.name}' is linked from {obj.library.filepath} and is read-only. "
            "Make it a library override to edit it")
    override = obj.override_library
    if override is not None and override.is_system_override:
        raise ReadOnlyObjectError(
            f"Object '{obj.name}' is a non-editable library override. Make it editable first")


# --- Lookup helpers ------------------------------------------------------------


def _check_object(obj) -> None:
    if not isinstance(obj, bpy.types.Object):
        raise ValueError(f"Expected a Blender object, got {type(obj).__name__}")


def _known_schema(name: str) -> dict:
    entry = registry.schema_ref(name)
    if entry is None:
        known = registry.component_names()
        if not known:
            raise ValueError(
                f"Unknown component {name!r}. The registry is empty; start the dev server "
                "or reload .gamestudio/components.json")
        raise ValueError(
            f"Unknown component {name!r}. Known: {', '.join(known)}.{schema.suggest(str(name), known)}")
    return entry


def _ref_index(obj, name: str) -> int:
    for i, ref in enumerate(obj.gs.components):
        if ref.name == name:
            return i
    return -1


def _field(entry: dict, field_name: str) -> dict:
    for f in entry["fields"]:
        if f["name"] == field_name:
            return f
    names = [f["name"] for f in entry["fields"]]
    raise ValueError(
        f"Component '{entry['name']}' has no field {field_name!r}. "
        f"Fields: {', '.join(names) or '(none)'}.{schema.suggest(str(field_name), names)}")


def _notify(obj) -> None:
    from . import live

    live.mark_components_dirty(obj)


# --- Queries -------------------------------------------------------------------


def list_components(obj) -> list:
    """Attached component names, in order (including disabled and missing ones)."""
    return [ref.name for ref in obj.gs.components]


def has_component(obj, name: str) -> bool:
    return _ref_index(obj, name) >= 0


def is_enabled(obj, name: str) -> bool:
    i = _ref_index(obj, name)
    return i >= 0 and obj.gs.components[i].enabled


def missing_components(obj) -> list:
    """Attached components that the current registry does not define."""
    return [ref.name for ref in obj.gs.components if registry.schema_ref(ref.name) is None]


def _clean_float(value: float) -> float:
    # Values are stored as float32; trim the noise (0.05 instead of 0.0500000007).
    if not math.isfinite(value):
        return 0.0
    return float(f"{value:.7g}")


def _asset_for_export(path: str) -> str:
    if not path:
        return ""
    if path.startswith("//"):
        path = bpy.path.abspath(path)
    root = project.project_root()
    if root is not None and os.path.isabs(path):
        try:
            path = os.path.relpath(path, root)
        except ValueError:  # different drive on Windows
            pass
    return pathlib.PurePath(path).as_posix()


def _export_value(field: dict, value, for_export: bool):
    ftype = field["type"]
    if ftype == "float":
        return _clean_float(value)
    if ftype == "int":
        return int(value)
    if ftype == "bool":
        return bool(value)
    if ftype in schema.VECTOR_SIZES:
        return [_clean_float(v) for v in value]
    if ftype == "entity":
        if value is None:
            return None
        return ids.ensure_id(value) if for_export else value.name
    if ftype == "asset":
        return _asset_for_export(value) if for_export else value
    return str(value)  # string, enum


def get_values(obj, name: str, for_export: bool = False) -> dict:
    """The component's field values as a JSON-compatible dict, defaults included.

    Entity fields give the target's name, or its gs_id when for_export=True
    (the target is given an ID if it has none). Vectors are plain lists.
    """
    _check_object(obj)
    entry = _known_schema(name)
    if not has_component(obj, name):
        raise ValueError(f"Object '{obj.name}' has no component '{name}'")
    grp = registry.group(obj, name)
    return {f["name"]: _export_value(f, getattr(grp, f["name"]), for_export) for f in entry["fields"]}


def export_components(obj) -> dict:
    """{Name: values} for the enabled components the registry knows (manifest and live link)."""
    result = {}
    for ref in obj.gs.components:
        if ref.enabled and registry.schema_ref(ref.name) is not None:
            result[ref.name] = get_values(obj, ref.name, for_export=True)
    return result


# --- Writes --------------------------------------------------------------------


def _coerce_all(entry: dict, values) -> dict:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError(f"Values for '{entry['name']}' must be a dict of field -> value")
    return {fname: coerce_value(_field(entry, fname), value) for fname, value in values.items()}


def add_component(obj, name: str, values: dict | None = None) -> None:
    """Attach a component (with defaults, then `values`) and make sure obj has an ID.

    If the component is already attached, it is re-enabled and `values` are
    applied. Raises ValueError for unknown components, bad values, or
    read-only objects; nothing is changed in that case.
    """
    _check_object(obj)
    entry = _known_schema(name)
    check_writable(obj)
    coerced = _coerce_all(entry, values)

    ids.ensure_id(obj)
    grp = registry.group(obj, name)
    i = _ref_index(obj, name)
    if i < 0:
        for f in entry["fields"]:
            grp.property_unset(f["name"])  # start from defaults, not stale data
        ref = obj.gs.components.add()
        ref.name = name
        ref.enabled = True
    elif not obj.gs.components[i].enabled:
        obj.gs.components[i].enabled = True
    for fname, value in coerced.items():
        setattr(grp, fname, value)
    _notify(obj)


def _inherited_from_override(obj, name: str) -> bool:
    override = obj.override_library
    if override is None or override.reference is None:
        return False
    reference = override.reference
    return any(ref.name == name for ref in reference.gs.components)


def remove_component(obj, name: str) -> None:
    """Detach a component and clear its stored values.

    Works for components missing from the registry too. Raises ValueError if
    the component is not attached, the object is read-only, or the component
    is inherited from a prefab (disable it instead).
    """
    _check_object(obj)
    check_writable(obj)
    i = _ref_index(obj, name)
    if i < 0:
        raise ValueError(f"Object '{obj.name}' has no component '{name}'")
    if _inherited_from_override(obj, name):
        raise ValueError(
            f"'{name}' on '{obj.name}' comes from the prefab and cannot be removed here; disable it instead")
    obj.gs.components.remove(i)
    key = registry.prop_name(name)
    if registry.group(obj, name) is not None:
        obj.property_unset(key)
    else:
        # Not registered right now: drop the raw stored values instead.
        stored = obj.bl_system_properties_get()
        if stored is not None and key in stored:
            del stored[key]
    _notify(obj)


def set_enabled(obj, name: str, enabled: bool) -> None:
    _check_object(obj)
    check_writable(obj)
    i = _ref_index(obj, name)
    if i < 0:
        raise ValueError(f"Object '{obj.name}' has no component '{name}'")
    obj.gs.components[i].enabled = bool(enabled)


def set_field(obj, name: str, field: str, value) -> None:
    """Validate, coerce and write one field. Raises ValueError."""
    _check_object(obj)
    entry = _known_schema(name)
    check_writable(obj)
    if not has_component(obj, name):
        raise ValueError(f"Object '{obj.name}' has no component '{name}'; add it first")
    coerced = coerce_value(_field(entry, field), value)
    setattr(registry.group(obj, name), field, coerced)


def set_values(obj, name: str, values: dict) -> None:
    """Validate every value first, then write them all. Raises ValueError."""
    _check_object(obj)
    entry = _known_schema(name)
    check_writable(obj)
    if not has_component(obj, name):
        raise ValueError(f"Object '{obj.name}' has no component '{name}'; add it first")
    coerced = _coerce_all(entry, values)
    grp = registry.group(obj, name)
    for fname, value in coerced.items():
        setattr(grp, fname, value)
