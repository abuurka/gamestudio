# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading and writing a geometry-nodes modifier's inputs by socket name, with values checked
against the group's input specs (groups.py) and references resolved by name."""

import bpy

from ..kits import materials as kit_materials
from ..kits.errors import KitError
from .errors import ProceduralError

# Materials the builders use beyond the kits' presets: name -> (preset, overrides).
EXTRA_MATERIALS = {
    "asphalt": ("stone_dark", {"color": "#4C4E54", "roughness": 0.95}),
    "road_line": ("plaster", {"color": "#F2EFE4", "roughness": 0.7}),
    "slate": ("roof", {"color": "#5A626E", "roughness": 0.85}),
    "thatch": ("wood", {"color": "#B89A5C", "roughness": 1.0}),
    "collider": ("glass", {"alpha": 0.0}),
}


def material(value):
    """A Material from a Material, a material name, one of EXTRA_MATERIALS or a kits preset."""
    if value is None or isinstance(value, bpy.types.Material):
        return value
    if not isinstance(value, str) or not value:
        raise ProceduralError(f"A material must be a preset name or a material name, got {value!r}")
    found = bpy.data.materials.get(value)
    if found is not None:
        return found
    if value in EXTRA_MATERIALS:
        name = "GS_" + "".join(p.title() for p in value.split("_"))
        found = bpy.data.materials.get(name)
        if found is None:
            preset, overrides = EXTRA_MATERIALS[value]
            found = kit_materials.material(preset, name=name, **overrides)
            if value == "collider":
                found.diffuse_color = (0.2, 0.9, 0.4, 0.0)
        return found
    try:
        return kit_materials.material(value)
    except KitError:
        names = sorted([*kit_materials.preset_names(), *EXTRA_MATERIALS])
        raise ProceduralError(f"Unknown material {value!r}: use a material name or a preset "
                              f"({', '.join(names)})") from None


def group_inputs(tree) -> list:
    """The interface input sockets a modifier shows (the object's own Geometry left out)."""
    return [i for i in tree.interface.items_tree
            if i.item_type == "SOCKET" and i.in_out == "INPUT" and i.socket_type != "NodeSocketGeometry"]


def _slot(mod, item):
    props = getattr(mod, "properties", None)
    if props is not None:
        return getattr(props.inputs, item.identifier)
    return None


def get(mod, item):
    slot = _slot(mod, item)
    if slot is not None:
        value = slot.value
    else:  # Blender < 5.0 kept the values as ID properties
        value = mod[item.identifier]
    if hasattr(value, "to_tuple"):
        return tuple(value)
    if hasattr(value, "__len__") and not isinstance(value, str) and not isinstance(value, bpy.types.ID):
        return tuple(value)
    return value


def put(mod, item, value) -> None:
    slot = _slot(mod, item)
    if slot is not None:
        slot.value = value
    else:
        mod[item.identifier] = value


def read_inputs(mod) -> dict:
    """{socket name: value} with ID references as datablocks."""
    if mod.node_group is None:
        return {}
    return {item.name: get(mod, item) for item in group_inputs(mod.node_group)}


def write_inputs(mod, values: dict, strict=True) -> None:
    """Set inputs by socket name. strict: unknown names raise; else they are skipped (migrations)."""
    items = {item.name: item for item in group_inputs(mod.node_group)}
    for name, value in values.items():
        item = items.get(name)
        if item is None:
            if strict:
                raise ProceduralError(f"{mod.name} has no input {name!r}")
            continue
        try:
            put(mod, item, value)
        except (TypeError, ValueError):
            if strict:
                raise
    mod.id_data.update_tag()  # writes through mod.properties do not tag the object for evaluation


def coerce(spec, value, where: str):
    """A value for `spec` (graph.Input) from what an agent passes: numbers in range, names for
    objects, collections and materials, case-insensitive menu items."""
    kind = spec.kind
    label = f"{where}.{spec.key}"
    if kind in ("FLOAT", "INT"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProceduralError(f"{label} must be a number, got {value!r}")
        if kind == "INT":
            if float(value) != int(value):
                raise ProceduralError(f"{label} must be a whole number, got {value!r}")
            value = int(value)
        else:
            value = float(value)
        if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
            raise ProceduralError(f"{label} must be between {spec.min} and {spec.max}, got {value}")
        return value
    if kind == "BOOL":
        if not isinstance(value, bool):
            raise ProceduralError(f"{label} must be true or false, got {value!r}")
        return value
    if kind == "STRING":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ProceduralError(f"{label} must be a string, got {value!r}")
        return value
    if kind == "MENU":
        options = {o.lower(): o for o in spec.options}
        if not isinstance(value, str) or value.lower() not in options:
            raise ProceduralError(f"{label} must be one of {', '.join(o.lower() for o in spec.options)}; "
                                  f"got {value!r}")
        return options[value.lower()]
    if kind == "VECTOR":
        try:
            x, y, z = (float(c) for c in value)
        except (TypeError, ValueError):
            raise ProceduralError(f"{label} must be three numbers, got {value!r}") from None
        return (x, y, z)
    if kind == "OBJECT":
        return resolve_object(value, label)
    if kind == "COLLECTION":
        if value is None or isinstance(value, bpy.types.Collection):
            return value
        if isinstance(value, str):
            found = bpy.data.collections.get(value)
            if found is None:
                raise ProceduralError(f"{label}: no collection named {value!r}")
            return found
        raise ProceduralError(f"{label} must be a collection or its name, got {value!r}")
    if kind == "MATERIAL":
        return material(value)
    raise ProceduralError(f"{label}: unsupported input kind {kind}")


def resolve_object(value, label="object", allow_none=True):
    if value is None and allow_none:
        return None
    if isinstance(value, bpy.types.Object):
        return value
    if isinstance(value, str):
        found = bpy.data.objects.get(value)
        if found is None:
            from ..core import ids

            found = ids.find_by_id(value) if value.startswith("gs_") else None
        if found is None:
            raise ProceduralError(f"{label}: no object named {value!r}")
        return found
    raise ProceduralError(f"{label} must be an object or its name, got {value!r}")


def plain(spec, value):
    """A value as params() reports it: names for datablocks, lower-case menu items."""
    if isinstance(value, bpy.types.ID):
        return value.name
    if spec.kind == "MENU" and isinstance(value, str):
        return value.lower()
    if spec.kind == "FLOAT" and isinstance(value, float):
        return round(value, 6)
    if isinstance(value, tuple):
        return [round(v, 6) if isinstance(v, float) else v for v in value]
    return value
