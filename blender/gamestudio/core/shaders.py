# SPDX-License-Identifier: GPL-3.0-or-later
"""Game shaders on materials (§3, §7.2): which game shader draws a material in the game, and its values.

A material names its shader in mat.gs.shader ("" = none), a string rather than an enum, so a
shader deleted from code keeps its name and is reported. Values live in the generated
mat.gss_<Name> groups (see registry.py), one per shader, and stay stored when the material
switches shader, like component values. Every write is validated first and then applied, so a
failing call leaves the material unchanged. Linked (library) materials are read-only, like
linked objects. Writes go through bpy.data and must happen on the main thread; an operator or a
bridge call around them makes them one undo step. Every write reaches a running game through the
live link (live.mark_material_dirty): new values at once, a new shader with a re-export.
"""

import math

import bpy

from . import registry, schema
from .components import _export_value


class ReadOnlyMaterialError(ValueError):
    """The material is linked from a library or is a system override."""


# --- Permissions ---------------------------------------------------------------


def is_writable(mat) -> bool:
    if mat.library is not None:
        return False
    override = mat.override_library
    return not (override is not None and override.is_system_override)


def check_writable(mat) -> None:
    if mat.library is not None:
        raise ReadOnlyMaterialError(
            f"Material '{mat.name}' is linked from {mat.library.filepath} and is read-only. "
            "Make it local (or a library override) to change its game shader")
    override = mat.override_library
    if override is not None and override.is_system_override:
        raise ReadOnlyMaterialError(
            f"Material '{mat.name}' is a non-editable library override. Make it editable first")


# --- Lookup helpers ------------------------------------------------------------


def _check_material(mat) -> None:
    if not isinstance(mat, bpy.types.Material):
        raise ValueError(f"Expected a Blender material, got {type(mat).__name__}")


def _unknown(name: str) -> ValueError:
    known = registry.shader_names()
    return ValueError(f"Unknown game shader {name!r}. Known: {', '.join(known)}.{schema.suggest(str(name), known)}")


def _notify(mat) -> None:
    from . import live  # live uses this module

    live.mark_material_dirty(mat)


def _check_name(name) -> None:
    if not isinstance(name, str) or not schema.COMPONENT_NAME_RE.match(name):
        raise ValueError(f"Game shader names look like 'Triplanar' (a capital letter, then letters and digits); "
                         f"got {name!r}")


def _coerce_all(entry: dict, values) -> dict:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError(f"Values for game shader '{entry['name']}' must be a dict of field -> value")
    names = [f["name"] for f in entry["fields"]]
    coerced = {}
    for fname, value in values.items():
        field = registry.get_shader_field(entry["name"], fname)
        if field is None:
            raise ValueError(
                f"Game shader '{entry['name']}' has no field {fname!r}. "
                f"Fields: {', '.join(names) or '(none)'}.{schema.suggest(str(fname), names)}")
        coerced[fname] = schema.coerce_value(field, value)
    return coerced


def _prepare(mat, name, values) -> tuple:
    """Check a set_shader() call without changing anything: (schema or None, coerced values, warnings)."""
    _check_material(mat)
    check_writable(mat)
    if name is None or name == "":
        if values:
            raise ValueError("Values need a game shader: pass the shader's name with them")
        return None, {}, []
    _check_name(name)
    entry = registry.shader_schema_ref(name)
    if entry is not None:
        return entry, _coerce_all(entry, values), []
    if registry.shader_names():
        raise _unknown(name)
    if values:
        raise ValueError(
            f"Cannot check values for game shader '{name}': the registry has no game shaders yet. Start the dev "
            "server (it writes .gamestudio/components.json from src/shaders), or set only the shader")
    return None, {}, [f"The registry has no game shaders yet, so '{name}' could not be checked. It is stored on "
                      f"'{mat.name}' and works once a src/shaders file defines it (start the dev server)."]


# --- Queries -------------------------------------------------------------------


def get_shader(mat) -> str | None:
    """The name of the game shader that draws mat in the game, or None (it may be missing from the registry)."""
    _check_material(mat)
    return mat.gs.shader or None


def is_missing(mat) -> bool:
    """True if mat names a game shader the current registry does not define."""
    name = get_shader(mat)
    return name is not None and registry.shader_schema_ref(name) is None


def get_values(mat, for_export: bool = False) -> dict:
    """The shader's field values as a JSON-compatible dict, defaults included; {} when mat has no shader or
    the registry does not define it. for_export=True gives asset fields as project-relative paths, the form
    component asset fields take in the manifest."""
    name = get_shader(mat)
    entry = registry.shader_schema_ref(name) if name else None
    if entry is None:
        return {}
    grp = registry.shader_group(mat, name)
    return {f["name"]: _export_value(f, getattr(grp, f["name"]), for_export) for f in entry["fields"]}


def _same(value, default) -> bool:
    if isinstance(value, float) and isinstance(default, (int, float)) and not isinstance(default, bool):
        return math.isclose(value, default, rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(value, list) and isinstance(default, list) and len(value) == len(default):
        return all(_same(a, b) for a, b in zip(value, default))
    return value == default


def changed_values(mat) -> dict:
    """The values that differ from their field's default (what get_object and the twin show)."""
    name = get_shader(mat)
    entry = registry.shader_schema_ref(name) if name else None
    if entry is None:
        return {}
    values = get_values(mat)
    return {f["name"]: values[f["name"]] for f in entry["fields"]
            if not _same(values[f["name"]], schema.default_value(f))}


def users(mat) -> list:
    """Objects whose material slots hold mat, by name."""
    return [o.name for o in bpy.data.objects if any(s.material == mat for s in o.material_slots)]


def shader_materials(scene=None) -> list:
    """Materials with a game shader that the scene's export can draw: in the material slots of the scene's
    objects, of what their collection instances show (nested), and of the prefab collections. First use
    first; each material once."""
    scene = scene or bpy.context.scene
    from .export import prefab_collections  # export uses this module

    found, listed, seen = [], set(), set()

    def visit(objects):
        for obj in objects:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is not None and mat not in listed and mat.gs.shader:
                    listed.add(mat)
                    found.append(mat)
            coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
            if coll is not None and coll not in seen:
                seen.add(coll)
                visit(coll.all_objects)

    visit(scene.objects)
    for coll in prefab_collections():
        if coll not in seen:
            seen.add(coll)
            visit(coll.all_objects)
    return found


# --- Writes --------------------------------------------------------------------


def validate_shader(mat, name, values=None) -> list:
    """Check what set_shader(mat, name, values) would do, changing nothing. Returns its warnings;
    raises ValueError like set_shader."""
    return _prepare(mat, name, values)[2]


def set_shader(mat, name, values=None) -> list:
    """Make game shader `name` draw mat in the game (None or "" clears it), then apply `values`.

    The values of every shader the material had stay stored, so switching back restores them.
    Raises ValueError for an unknown shader (listing the known ones), bad values or a read-only
    material; nothing is changed then. When the registry has no shaders at all, the name is stored
    unchecked and the returned list holds a warning saying so. Returns the warnings.
    """
    entry, coerced, found = _prepare(mat, name, values)
    mat.gs.shader = name or ""
    if entry is not None:
        grp = registry.shader_group(mat, name)
        for fname, value in coerced.items():
            setattr(grp, fname, value)
    _notify(mat)
    return found


def set_values(mat, values: dict) -> None:
    """Validate every value for the material's shader first, then write them all. Raises ValueError."""
    _check_material(mat)
    check_writable(mat)
    name = get_shader(mat)
    if name is None:
        raise ValueError(f"Material '{mat.name}' has no game shader; set one first")
    entry = registry.shader_schema_ref(name)
    if entry is None:
        raise _unknown(name) if registry.shader_names() else ValueError(
            f"Cannot check values for game shader '{name}': the registry has no game shaders yet")
    coerced = _coerce_all(entry, values)
    grp = registry.shader_group(mat, name)
    for fname, value in coerced.items():
        setattr(grp, fname, value)
    _notify(mat)
