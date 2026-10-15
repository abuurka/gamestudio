# SPDX-License-Identifier: GPL-3.0-or-later
"""Component schemas: registry validation, defaults and value coercion (§3).

This module holds the pure rules for the registry format. registry.py turns
validated schemas into PropertyGroups, and components.py uses coerce_value()
to validate every write.
"""

import copy
import difflib
import math
import re

import bpy

from . import ids

COMPONENT_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,40}$")
FIELD_NAME_RE = re.compile(r"^[a-z][a-zA-Z0-9_]{0,40}$")

FIELD_TYPES = ("float", "int", "bool", "string", "enum", "vec2", "vec3", "color", "entity", "asset")
VECTOR_SIZES = {"vec2": 2, "vec3": 3, "color": 4}
FLOAT_UNITS = ("none", "distance", "angle", "time")

# Names that would clash with PropertyGroup internals.
RESERVED_FIELDS = {"rna_type", "bl_rna", "id_data"}

INT_MIN, INT_MAX = -(2**31), 2**31 - 1


class RegistryError(ValueError):
    """The registry document itself is unusable (not just one bad entry)."""


# --- Defaults ------------------------------------------------------------------


def _type_default(ftype: str):
    return {
        "float": 0.0,
        "int": 0,
        "bool": False,
        "string": "",
        "enum": None,
        "vec2": [0.0, 0.0],
        "vec3": [0.0, 0.0, 0.0],
        "color": [1.0, 1.0, 1.0, 1.0],
        "entity": None,
        "asset": "",
    }[ftype]


def default_value(field: dict):
    """The field's default as a JSON value (entity defaults are always None)."""
    ftype = field["type"]
    if ftype == "entity":
        return None
    if ftype == "enum":
        return field.get("default", field["options"][0])
    value = field.get("default", _type_default(ftype))
    return copy.deepcopy(value)


def default_values(schema: dict) -> dict:
    return {f["name"]: default_value(f) for f in schema["fields"]}


# --- Registry validation -------------------------------------------------------


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_field(comp_name: str, raw, warnings: list) -> dict | None:
    where = f"{comp_name}.{raw.get('name', '?') if isinstance(raw, dict) else '?'}"
    if not isinstance(raw, dict):
        warnings.append(f"{comp_name}: field entries must be objects")
        return None
    name = raw.get("name")
    ftype = raw.get("type")
    if not isinstance(name, str) or not FIELD_NAME_RE.match(name) or name in RESERVED_FIELDS:
        warnings.append(f"{where}: invalid field name (must match {FIELD_NAME_RE.pattern})")
        return None
    if ftype not in FIELD_TYPES:
        warnings.append(f"{where}: unknown field type {ftype!r}")
        return None

    field = copy.deepcopy(raw)
    if not isinstance(field.get("description", ""), str):
        field["description"] = ""
    for key in ("min", "max", "step"):
        if key in field and not _is_number(field[key]):
            warnings.append(f"{where}: ignoring non-numeric {key!r}")
            del field[key]
    if "min" in field and "max" in field and field["min"] > field["max"]:
        warnings.append(f"{where}: min is greater than max; ignoring both")
        del field["min"], field["max"]

    if ftype == "enum":
        options = field.get("options")
        if (not isinstance(options, list) or not options
                or not all(isinstance(o, str) and o for o in options) or len(set(options)) != len(options)):
            warnings.append(f"{where}: enum fields need a non-empty list of unique option strings")
            return None
    if ftype == "float" and field.get("unit", "none") not in FLOAT_UNITS:
        warnings.append(f"{where}: unknown unit {field.get('unit')!r}; using 'none'")
        field["unit"] = "none"
    if ftype == "asset":
        exts = field.get("extensions")
        if exts is not None and not (isinstance(exts, list) and all(isinstance(e, str) for e in exts)):
            warnings.append(f"{where}: 'extensions' must be a list of strings; ignoring it")
            del field["extensions"]

    if "default" in field and ftype != "entity":
        try:
            value = coerce_value(field, field["default"], check_range=False)
        except ValueError as exc:
            warnings.append(f"{where}: bad default ({exc}); using the type default")
            del field["default"]
        else:
            field["default"] = list(value) if isinstance(value, tuple) else value
    if ftype == "entity":
        field.pop("default", None)
    if "default" in field and ("min" in field or "max" in field) and ftype in ("float", "int", "vec2", "vec3"):
        lo, hi = field.get("min", -math.inf), field.get("max", math.inf)
        values = field["default"] if isinstance(field["default"], list) else [field["default"]]
        if any(v < lo or v > hi for v in values):
            warnings.append(f"{where}: default is outside min/max")
    if ftype == "color" and "default" in field:
        field["default"] = [min(1.0, max(0.0, c)) for c in field["default"]]
    return field


def validate_registry(data) -> tuple[list, list]:
    """Validate a components.json document.

    Returns (components, warnings). Bad components and fields are skipped with
    a warning so one mistake does not hide the rest of the registry. Raises
    RegistryError only if the document as a whole is unusable.
    """
    if not isinstance(data, dict):
        raise RegistryError("The registry must be a JSON object")
    if data.get("format", "gamestudio.registry") != "gamestudio.registry":
        raise RegistryError(f"Unexpected registry format {data.get('format')!r}")
    if data.get("formatVersion", 1) != 1:
        raise RegistryError(f"Unsupported registry formatVersion {data.get('formatVersion')!r}")
    raw_components = data.get("components")
    if not isinstance(raw_components, list):
        raise RegistryError("The registry has no 'components' list")

    warnings: list = []
    components: list = []
    seen: set = set()
    for raw in raw_components:
        if not isinstance(raw, dict):
            warnings.append("Skipping a component entry that is not an object")
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not COMPONENT_NAME_RE.match(name):
            warnings.append(f"Skipping component with invalid name {name!r}")
            continue
        if name in seen:
            warnings.append(f"Skipping duplicate component {name!r}")
            continue
        raw_fields = raw.get("fields", [])
        if not isinstance(raw_fields, list):
            warnings.append(f"{name}: 'fields' must be a list")
            raw_fields = []
        fields: list = []
        field_names: set = set()
        for raw_field in raw_fields:
            field = _validate_field(name, raw_field, warnings)
            if field is None:
                continue
            if field["name"] in field_names:
                warnings.append(f"{name}.{field['name']}: duplicate field skipped")
                continue
            field_names.add(field["name"])
            fields.append(field)
        component = {k: copy.deepcopy(v) for k, v in raw.items() if k != "fields"}
        component["fields"] = fields
        component.setdefault("description", "")
        component.setdefault("category", "")
        component["builtin"] = bool(component.get("builtin", False))
        seen.add(name)
        components.append(component)
    return components, warnings


# --- Value coercion ------------------------------------------------------------


def _range_text(field: dict) -> str:
    lo, hi = field.get("min"), field.get("max")
    if lo is not None and hi is not None:
        return f" between {_fmt(lo)} and {_fmt(hi)}"
    if lo is not None:
        return f" >= {_fmt(lo)}"
    if hi is not None:
        return f" <= {_fmt(hi)}"
    return ""


def _fmt(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value) if isinstance(value, str) else str(value)


def _got(value) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _to_number(value):
    if _is_number(value):
        return value
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        if math.isfinite(number):
            return int(number) if number.is_integer() and "." not in value and "e" not in value.lower() else number
    return None


def _in_range(field: dict, value) -> bool:
    lo, hi = field.get("min"), field.get("max")
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _coerce_float(field, value, check_range):
    number = _to_number(value)
    if number is None or (check_range and not _in_range(field, number)):
        raise ValueError(f"Field '{field['name']}' expects a number{_range_text(field)} (got {_got(value)})")
    return float(number)


def _coerce_int(field, value, check_range):
    number = _to_number(value)
    if number is not None and isinstance(number, float) and number.is_integer():
        number = int(number)
    if (not isinstance(number, int) or not INT_MIN <= number <= INT_MAX
            or (check_range and not _in_range(field, number))):
        raise ValueError(f"Field '{field['name']}' expects a whole number{_range_text(field)} (got {_got(value)})")
    return number


_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _coerce_bool(field, value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise ValueError(f"Field '{field['name']}' expects true or false (got {_got(value)})")


def _coerce_enum(field, value):
    options = field["options"]
    if isinstance(value, str):
        if value in options:
            return value
        lowered = {o.lower(): o for o in options}
        if value.lower() in lowered:
            return lowered[value.lower()]
    raise ValueError(f"Field '{field['name']}' expects one of: {', '.join(options)} (got {_got(value)})")


def _coerce_vector(field, value, check_range):
    size = VECTOR_SIZES[field["type"]]
    items = None
    if isinstance(value, dict):
        keys = ("x", "y", "z", "w")[:size] if field["type"] != "color" else ("r", "g", "b", "a")
        if all(k in value for k in keys[:3 if size >= 3 else 2]):
            items = [value.get(k, 1.0) for k in keys]
    elif not isinstance(value, (str, bytes)):
        try:
            items = list(value)
        except TypeError:
            items = None
    if items is not None and field["type"] == "color" and len(items) == 3:
        items = items + [1.0]
    numbers = [_to_number(v) for v in items] if items is not None else None
    if field["type"] == "color":
        lo, hi = 0.0, 1.0
        range_text = " between 0 and 1"
    else:
        lo, hi = field.get("min"), field.get("max")
        range_text = _range_text(field)
    ok = numbers is not None and len(numbers) == size and all(n is not None for n in numbers)
    if ok and check_range:
        ok = all((lo is None or n >= lo) and (hi is None or n <= hi) for n in numbers)
    if not ok:
        example = {"vec2": "[0, 1]", "vec3": "[0, 1, 0]", "color": "[1, 0.5, 0, 1]"}[field["type"]]
        what = "an RGBA color" if field["type"] == "color" else f"a list of {size} numbers"
        raise ValueError(
            f"Field '{field['name']}' expects {what}{range_text}, like {example} (got {_got(value)})")
    return tuple(float(n) for n in numbers)


def _coerce_entity(field, value):
    if value is None or value == "":
        return None
    if isinstance(value, bpy.types.Object):
        return value
    if isinstance(value, str):
        if ids.is_valid_id(value):
            obj = ids.find_by_id(value)
            if obj is not None:
                return obj
        obj = bpy.data.objects.get(value)
        if obj is not None:
            return obj
        raise ValueError(
            f"Field '{field['name']}' expects an object name or gs_id; no object {value!r} exists")
    raise ValueError(f"Field '{field['name']}' expects an object name, a gs_id or null (got {_got(value)})")


def _coerce_asset(field, value):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"Field '{field['name']}' expects a project-relative file path (got {_got(value)})")
    value = value.replace("\\", "/")
    exts = [e.lower().lstrip(".") for e in field.get("extensions") or []]
    if value and exts and value.rsplit(".", 1)[-1].lower() not in exts:
        raise ValueError(
            f"Field '{field['name']}' expects a file ending in {', '.join('.' + e for e in exts)} (got {_got(value)})")
    return value


def coerce_value(field_schema: dict, value, check_range: bool = True):
    """Validate value for a field and return it in the form Blender stores.

    float -> float, int -> int, bool -> bool, string/enum/asset -> str,
    vec2/vec3/color -> tuple of floats, entity -> bpy.types.Object or None.
    Entity values may be an Object, an object name or a gs_id.
    Raises ValueError with a message meant for people (and the AI assistant).
    """
    ftype = field_schema.get("type")
    if ftype == "float":
        return _coerce_float(field_schema, value, check_range)
    if ftype == "int":
        return _coerce_int(field_schema, value, check_range)
    if ftype == "bool":
        return _coerce_bool(field_schema, value)
    if ftype == "string":
        if not isinstance(value, str):
            raise ValueError(f"Field '{field_schema['name']}' expects text (got {_got(value)})")
        return value
    if ftype == "enum":
        return _coerce_enum(field_schema, value)
    if ftype in VECTOR_SIZES:
        return _coerce_vector(field_schema, value, check_range)
    if ftype == "entity":
        return _coerce_entity(field_schema, value)
    if ftype == "asset":
        return _coerce_asset(field_schema, value)
    raise ValueError(f"Field '{field_schema.get('name')}' has unknown type {ftype!r}")


def suggest(name: str, known: list) -> str:
    """' Did you mean 'X'?' or an empty string."""
    close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    if not close:
        close = [k for k in known if k.lower() == name.lower()]
    return f" Did you mean '{close[0]}'?" if close else ""
