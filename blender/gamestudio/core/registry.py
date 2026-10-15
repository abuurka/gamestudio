# SPDX-License-Identifier: GPL-3.0-or-later
"""Component registry: .gamestudio/components.json -> PropertyGroups (§3, §7.1, §7.2).

For every component in the registry, a PropertyGroup class GSC_<Name> is
generated and attached as Object.gsc_<Name>. For every game shader (the
registry's optional `shaders` list), a class GSS_<Name> is generated the same
way and attached as Material.gss_<Name>; Material.gs.shader names the shader
that draws the material in the game. Field values are IDProperties keyed by
field name, so they survive regeneration as long as the field keeps its name
and type. Enum items get numeric values derived from a hash of the option
string, so reordering or adding options keeps stored choices. Fields whose
type changes are migrated (coerced) by hand.

The registry comes from the file (read at load, on save into a project, and
whenever its mtime changes) or from the live link's `registry` message.
"""

import copy
import json
import zlib

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import link, project, schema
from .log import get_logger
from .util import tag_redraw

log = get_logger(__name__)

OVERRIDABLE = {"LIBRARY_OVERRIDABLE"}
PROP_PREFIX = "gsc_"
CLASS_PREFIX = "GSC_"
SHADER_PROP_PREFIX = "gss_"
SHADER_CLASS_PREFIX = "GSS_"
WATCH_INTERVAL = 1.0


def _stored(id_data, key: str) -> bool:
    """Whether id_data holds values under key (a generated group's property name)."""
    try:
        stored = id_data.bl_system_properties_get()
    except (AttributeError, TypeError, RuntimeError):
        return False
    return stored is not None and key in stored


class _Kind:
    """One family of generated PropertyGroups: components on objects, game shaders on materials."""

    def __init__(self, label, owner, prop_prefix, class_prefix, data, keeps_values, update=None):
        self.label = label                # "component" or "shader"
        self.owner = owner                # the bpy.types class the groups are attached to
        self.prop_prefix = prop_prefix
        self.class_prefix = class_prefix
        self.data = data                  # () -> the bpy.data collection of owners
        self.keeps_values = keeps_values  # (id, name) -> whether id's values for name are migrated
        self.update = update              # field update callback, or None
        self.entries: list = []           # validated schemas, registry order
        self.by_name: dict = {}           # name -> schema (shared with entries)
        self.classes: dict = {}           # name -> registered PropertyGroup class
        self.signatures: dict = {}        # name -> JSON signature of the schema

    def prop_name(self, name: str) -> str:
        return self.prop_prefix + name

    def names(self) -> list:
        return [e["name"] for e in self.entries]

    def group(self, id_data, name: str):
        if name not in self.classes:
            return None
        return getattr(id_data, self.prop_name(name), None)


def _on_field_update(self, _context):
    from . import live

    live.mark_components_dirty(self.id_data)


def _on_shader_update(self, _context):
    """A game shader field of a material, or the material's shader (Material.gs.shader), changed: the live
    link sends the values to the game at once, or re-exports for a new shader (live.py)."""
    from . import live

    live.mark_material_dirty(self.id_data)


_COMPONENTS = _Kind("component", bpy.types.Object, PROP_PREFIX, CLASS_PREFIX, lambda: bpy.data.objects,
                    lambda obj, name: any(ref.name == name for ref in obj.gs.components), _on_field_update)
# A material keeps the values of every shader it had (switching back restores them), so all of them migrate.
_SHADERS = _Kind("shader", bpy.types.Material, SHADER_PROP_PREFIX, SHADER_CLASS_PREFIX, lambda: bpy.data.materials,
                 lambda mat, name: _stored(mat, SHADER_PROP_PREFIX + name), _on_shader_update)

_warnings: list = []        # warnings from the last validation
_source = {"root": None, "mtime": None, "origin": "none"}


# --- Queries -------------------------------------------------------------------


def schemas() -> list:
    """Component entries (§3), in registry order. Returns copies."""
    return copy.deepcopy(_COMPONENTS.entries)


def get_schema(name: str) -> dict | None:
    """The component entry for name (a copy), or None."""
    entry = _COMPONENTS.by_name.get(name)
    return copy.deepcopy(entry) if entry is not None else None


def schema_ref(name: str) -> dict | None:
    """Like get_schema() but without copying. Callers must not modify it."""
    return _COMPONENTS.by_name.get(name)


def component_names() -> list:
    return _COMPONENTS.names()


def _field_of(entry: dict | None, field: str) -> dict | None:
    for f in entry["fields"] if entry is not None else ():
        if f["name"] == field:
            return f
    return None


def get_field(component: str, field: str) -> dict | None:
    return _field_of(_COMPONENTS.by_name.get(component), field)


def prop_name(component: str) -> str:
    return PROP_PREFIX + component


def component_class(component: str):
    """The registered GSC_<component> PropertyGroup class, or None."""
    return _COMPONENTS.classes.get(component)


def group(obj, component: str):
    """The object's GSC_<component> PropertyGroup, or None if not registered."""
    return _COMPONENTS.group(obj, component)


def shader_schemas() -> list:
    """Game shader entries (the registry's `shaders`, §3), in registry order. Returns copies."""
    return copy.deepcopy(_SHADERS.entries)


def get_shader_schema(name: str) -> dict | None:
    """The game shader entry for name (a copy), or None."""
    entry = _SHADERS.by_name.get(name)
    return copy.deepcopy(entry) if entry is not None else None


def shader_schema_ref(name: str) -> dict | None:
    """Like get_shader_schema() but without copying. Callers must not modify it."""
    return _SHADERS.by_name.get(name)


def shader_names() -> list:
    return _SHADERS.names()


def get_shader_field(shader: str, field: str) -> dict | None:
    return _field_of(_SHADERS.by_name.get(shader), field)


def shader_prop_name(shader: str) -> str:
    return SHADER_PROP_PREFIX + shader


def shader_class(shader: str):
    """The registered GSS_<shader> PropertyGroup class, or None."""
    return _SHADERS.classes.get(shader)


def shader_group(mat, shader: str):
    """The material's GSS_<shader> PropertyGroup, or None if not registered."""
    return _SHADERS.group(mat, shader)


# The shader library's categories (built-in shaders with `category`), in menu order. Built-in shaders
# without one, or with another, are listed under OTHER_CATEGORY.
SHADER_CATEGORIES = ("Water", "Nature", "Magic & Sci-fi", "Surfaces", "Transformations", "Gameplay", "Style")
OTHER_CATEGORY = "Other"
SHADER_CATEGORY_ICONS = {"Water": "MOD_OCEAN", "Nature": "FORCE_WIND", "Magic & Sci-fi": "SHADERFX",
                         "Surfaces": "MATERIAL", "Transformations": "MOD_REMESH", "Gameplay": "CON_ACTION",
                         "Style": "BRUSHES_ALL", OTHER_CATEGORY: "SHADING_RENDERED"}
PROJECT_SHADER_ICON = "FILE_SCRIPT"


def shader_category(entry: dict) -> str:
    """The library category a shader entry is listed under: one of SHADER_CATEGORIES, else "Other"."""
    category = entry.get("category")
    return category if category in SHADER_CATEGORIES else OTHER_CATEGORY


def shader_groups() -> tuple[list, list]:
    """The registry's shaders as the Game Shader menu lists them: (project names, [(category, names)]).
    The project's own shaders (src/shaders) come first, then the built-in library by category, in
    SHADER_CATEGORIES order and "Other" last; names A-Z. A project shader that replaces a library one
    of the same name is one registry entry (builtin false), so it is listed once, as the project's."""
    project_names, library = [], {}
    for entry in _SHADERS.entries:
        if entry["builtin"]:
            library.setdefault(shader_category(entry), []).append(entry["name"])
        else:
            project_names.append(entry["name"])
    order = SHADER_CATEGORIES + (OTHER_CATEGORY,)
    return (sorted(project_names, key=str.lower),
            [(c, sorted(library[c], key=str.lower)) for c in order if c in library])


def shader_icon(name) -> str:
    """A shader's icon in the UI: its library category's, the project's, or ERROR when the registry lacks it."""
    entry = _SHADERS.by_name.get(name)
    if entry is None:
        return "ERROR"
    return SHADER_CATEGORY_ICONS[shader_category(entry)] if entry["builtin"] else PROJECT_SHADER_ICON


def warnings() -> list:
    return list(_warnings)


def source_info() -> dict:
    """Where the current registry came from: {"root", "mtime", "origin"}."""
    return dict(_source)


# --- Property generation -------------------------------------------------------


def _label(field_name: str) -> str:
    words = []
    current = ""
    for ch in field_name.replace("_", " "):
        if ch.isupper() and current and not current[-1].isupper():
            words.append(current)
            current = ch
        elif ch == " ":
            if current:
                words.append(current)
            current = ""
        else:
            current += ch
    if current:
        words.append(current)
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _enum_items(options: list) -> list:
    # Stable numbers: the stored value is an int, so derive it from the option
    # text rather than its position.
    numbers = [zlib.crc32(o.encode("utf-8")) & 0x7FFFFFFF for o in options]
    if len(set(numbers)) != len(numbers):
        numbers = list(range(len(options)))
    return [(o, o[:1].upper() + o[1:], "", n) for o, n in zip(options, numbers)]


_FLOAT_UNITS = {"distance": {"unit": "LENGTH"}, "angle": {"subtype": "ANGLE"}, "time": {"unit": "TIME_ABSOLUTE"}}


def _make_property(field: dict, update=None):
    ftype = field["type"]
    default = schema.default_value(field)
    common = {
        "name": _label(field["name"]),
        "description": field.get("description", ""),
        "override": OVERRIDABLE,
    }
    if update is not None:
        common["update"] = update
    bounds = {}
    if "min" in field:
        bounds["min"] = field["min"]
    if "max" in field:
        bounds["max"] = field["max"]

    if ftype == "float":
        kw = dict(common, default=float(default), **{k: float(v) for k, v in bounds.items()})
        if "step" in field:
            kw["step"] = max(1, int(round(field["step"] * 100)))
        kw.update(_FLOAT_UNITS.get(field.get("unit", "none"), {}))
        return FloatProperty(**kw)
    if ftype == "int":
        return IntProperty(default=int(default), **{k: int(v) for k, v in bounds.items()}, **common)
    if ftype == "bool":
        return BoolProperty(default=bool(default), **common)
    if ftype == "string":
        return StringProperty(default=default, **common)
    if ftype == "enum":
        return EnumProperty(items=_enum_items(field["options"]), default=default, **common)
    if ftype in ("vec2", "vec3"):
        size = schema.VECTOR_SIZES[ftype]
        return FloatVectorProperty(
            size=size, default=tuple(default), subtype="XYZ",
            **{k: float(v) for k, v in bounds.items()}, **common)
    if ftype == "color":
        return FloatVectorProperty(size=4, subtype="COLOR", default=tuple(default), min=0.0, max=1.0, **common)
    if ftype == "entity":
        return PointerProperty(type=bpy.types.Object, **common)
    if ftype == "asset":
        return StringProperty(default=default, subtype="FILE_PATH", **common)
    raise ValueError(f"Unknown field type {ftype!r}")


def _make_class(kind: _Kind, entry: dict):
    annotations = {f["name"]: _make_property(f, kind.update) for f in entry["fields"]}
    return type(
        kind.class_prefix + entry["name"],
        (bpy.types.PropertyGroup,),
        {
            "__annotations__": annotations,
            "__module__": __name__,
            "__doc__": entry.get("description") or f"GameStudio {kind.label} {entry['name']}",
            f"gs_{kind.label}": entry["name"],
        },
    )


def _register_entry(kind: _Kind, entry: dict) -> None:
    cls = _make_class(kind, entry)
    bpy.utils.register_class(cls)
    setattr(kind.owner, kind.prop_name(entry["name"]), PointerProperty(type=cls, override=OVERRIDABLE))
    kind.classes[entry["name"]] = cls


def _unregister_entry(kind: _Kind, name: str) -> None:
    # The stored IDProperties stay on the objects (materials); only the RNA definition goes.
    cls = kind.classes.pop(name)
    try:
        delattr(kind.owner, kind.prop_name(name))
    except AttributeError:
        pass
    bpy.utils.unregister_class(cls)


# --- Migration of fields whose type changed ------------------------------------


def _needs_migration(old: dict, new: dict) -> bool:
    if old["type"] != new["type"]:
        return True
    if new["type"] == "enum":
        return bool(set(old["options"]) - set(new["options"]))
    return False


def _read_raw(grp, fname):
    value = getattr(grp, fname)
    if isinstance(value, (bpy.types.ID, str, int, float, bool)) or value is None:
        return value
    try:
        return [float(v) for v in value]
    except TypeError:
        return value


def _snapshot(kind: _Kind, name: str, fields: list) -> list:
    attr = kind.prop_name(name)
    snapshot = []
    for owner in kind.data():
        if owner.library is not None or not kind.keeps_values(owner, name):
            continue
        grp = getattr(owner, attr, None)
        if grp is None:
            continue
        snapshot.append((owner, {f: _read_raw(grp, f) for f in fields}))
    return snapshot


def _lenient(field: dict, old):
    """Best-effort conversion of a value stored under a different field type."""
    ftype = field["type"]
    if ftype == "int" and isinstance(old, float):
        return int(round(old))
    if ftype in schema.VECTOR_SIZES and isinstance(old, list):
        size = schema.VECTOR_SIZES[ftype]
        pad = 1.0 if ftype == "color" else 0.0
        return (old + [pad] * size)[:size]
    if ftype in ("string", "asset") and isinstance(old, (int, float)) and not isinstance(old, bool):
        return str(old)
    if ftype == "bool" and isinstance(old, (int, float)):
        return bool(old)
    return old


def _restore(kind: _Kind, name: str, fields: dict, snapshot: list) -> None:
    attr = kind.prop_name(name)
    for owner, values in snapshot:
        grp = getattr(owner, attr, None)
        if grp is None:
            continue
        for fname, old in values.items():
            field = fields[fname]
            try:
                setattr(grp, fname, schema.coerce_value(field, _lenient(field, old), check_range=False))
            except (ValueError, TypeError):
                grp.property_unset(fname)
                log.info("%s.%s on '%s': old value %r does not fit the new definition; reset to default",
                         name, fname, owner.name, old)


# --- Validating the shaders list -----------------------------------------------


def _validate_shaders(raw) -> tuple[list, list]:
    """The registry's optional `shaders` list (§3): entries like components, without entity fields, and
    with an optional `category` (the library's). Bad entries and fields are skipped with a warning; a
    missing list is no shaders. A project shader listed next to a built-in one of the same name wins."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["The registry's 'shaders' must be a list; ignoring it"]
    shaders, found = [], []
    for item in raw:
        if not isinstance(item, dict):
            found.append("Skipping a shader entry that is not an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not schema.COMPONENT_NAME_RE.match(name):
            found.append(f"Skipping shader with invalid name {name!r}")
            continue
        builtin = bool(item.get("builtin", False))
        kept = next((i for i, s in enumerate(shaders) if s["name"] == name), None)
        # A project shader and a library one of the same name: the project's replaces it, quietly, in
        # either order. Two of a kind are a duplicate: the first stays.
        if kept is not None and not (shaders[kept]["builtin"] and not builtin):
            if builtin == shaders[kept]["builtin"]:
                found.append(f"Skipping duplicate shader {name!r}")
            continue
        raw_fields = item.get("fields", [])
        if not isinstance(raw_fields, list):
            found.append(f"{name}: 'fields' must be a list")
            raw_fields = []
        fields, field_names = [], set()
        for raw_field in raw_fields:
            field = schema._validate_field(name, raw_field, found)
            if field is None:
                continue
            if field["type"] == "entity":
                found.append(f"{name}.{field['name']}: shaders cannot have entity fields; skipped")
                continue
            if field["name"] in field_names:
                found.append(f"{name}.{field['name']}: duplicate field skipped")
                continue
            field_names.add(field["name"])
            fields.append(field)
        entry = {k: copy.deepcopy(v) for k, v in item.items() if k != "fields"}
        entry["fields"] = fields
        entry.setdefault("description", "")
        entry["builtin"] = builtin
        if not isinstance(entry.get("category"), str) or not entry["category"].strip():
            entry.pop("category", None)
        if kept is not None:
            shaders[kept] = entry
        else:
            shaders.append(entry)
    return shaders, found


# --- Applying a registry -------------------------------------------------------


def _signature(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True)


def _apply_kind(kind: _Kind, entries: list) -> bool:
    """Regenerate one kind's PropertyGroups for a validated entry list.

    Only entries whose schema changed are re-registered. Returns True if the
    kind's part of the registry changed.
    """
    new_by_name = {e["name"]: e for e in entries}
    new_sigs = {name: _signature(e) for name, e in new_by_name.items()}
    old_order = kind.names()
    changed = False

    migrations = {}
    for name in list(kind.classes):
        if name in new_by_name and new_sigs[name] == kind.signatures.get(name):
            continue
        old_entry = kind.by_name.get(name)
        new_entry = new_by_name.get(name)
        if old_entry is not None and new_entry is not None:
            old_fields = {f["name"]: f for f in old_entry["fields"]}
            fields = {f["name"]: f for f in new_entry["fields"]
                      if f["name"] in old_fields and _needs_migration(old_fields[f["name"]], f)}
            if fields:
                migrations[name] = (fields, _snapshot(kind, name, list(fields)))
        _unregister_entry(kind, name)
        changed = True

    for name, entry in new_by_name.items():
        if name in kind.classes:
            continue
        try:
            _register_entry(kind, entry)
        except (TypeError, ValueError, RuntimeError) as exc:
            msg = f"Could not register {kind.label} {name!r}: {exc}"
            log.error(msg)
            _warnings.append(msg)
            migrations.pop(name, None)
            continue
        changed = True

    kind.entries = [e for e in entries if e["name"] in kind.classes]
    kind.by_name.clear()
    kind.by_name.update({e["name"]: e for e in kind.entries})
    kind.signatures.clear()
    kind.signatures.update({e["name"]: new_sigs[e["name"]] for e in kind.entries})

    for name, (fields, snapshot) in migrations.items():
        _restore(kind, name, fields, snapshot)

    changed = changed or old_order != kind.names()
    if changed:
        log.info("%s registry: %d %s(s): %s", kind.label.capitalize(), len(kind.entries), kind.label,
                 ", ".join(kind.names()))
    return changed


def _apply(components: list, shaders: list = ()) -> bool:
    """Regenerate PropertyGroups for validated component and shader lists. Returns True if the
    registry changed."""
    changed = _apply_kind(_COMPONENTS, components)
    changed = _apply_kind(_SHADERS, list(shaders)) or changed
    if changed:
        tag_redraw(("VIEW_3D", "PROPERTIES"), region_type=None)
    return changed


def _validate_and_apply(data) -> None:
    components, found = schema.validate_registry(data)
    shaders, found_shaders = _validate_shaders(data.get("shaders"))
    _warnings[:] = found + found_shaders
    for warning in _warnings:
        log.warning("Registry: %s", warning)
    _apply(components, shaders)


def reload_registry(root=None) -> bool:
    """Read <root>/.gamestudio/components.json and (re)generate PropertyGroups.

    root defaults to the current project. Returns True if a registry was
    loaded. With no project or no file, the registry becomes empty (stored
    component and shader values are kept on the objects and materials). A
    broken file keeps the current registry and returns False.
    """
    if root is None:
        root = project.project_root()
    _source["root"] = root
    if root is None:
        _source.update(mtime=None, origin="none")
        _warnings.clear()
        _apply([])
        return False

    path = project.registry_path(root)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        _source.update(mtime=None, origin="none")
        _warnings.clear()
        _apply([])
        return False

    _source["mtime"] = mtime  # remember even a broken file so it is not re-read every tick
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _validate_and_apply(data)
    except (OSError, ValueError) as exc:
        msg = f"Could not load {path}: {exc}"
        log.error(msg)
        _warnings[:] = [msg]
        return False
    _source["origin"] = "file"
    return True


def set_registry(data: dict) -> None:
    """Apply a registry document received over the live link.

    Raises ValueError (schema.RegistryError) if the document is unusable.
    """
    _validate_and_apply(data)
    _source["origin"] = "link"


# --- Material.gs: the game shader of a material ---------------------------------

NO_SHADER = "__none__"      # the selector's None item (not a valid shader name)
_menu_cache: list = []      # Blender needs the items' strings kept alive


def _shader_menu_items(self, _context):
    """None, the project's shaders, the library's by category (shader_groups), and the stored name when
    the registry lacks it (error icon). Tooltips are the shaders' descriptions. The Material properties
    Game panel draws them grouped, in a menu with a sub-menu per library category."""
    current = self.shader
    items = [(NO_SHADER, "None", "No game shader: the game draws the material as exported", "BLANK1", 0)]
    project_names, library = shader_groups()
    for name in project_names + [n for _category, names in library for n in names]:
        entry = _SHADERS.by_name[name]
        items.append((name, name, entry.get("description") or f"Game shader {name}", shader_icon(name),
                      len(items)))
    if current and current not in _SHADERS.by_name:
        items.append((current, current, f"'{current}' is not in the registry: no src/shaders file or library "
                                         "shader defines it", "ERROR", len(items)))
    _menu_cache[:] = items
    return items


def _get_shader_menu(self) -> int:
    for item in _shader_menu_items(self, None):
        if item[0] == (self.shader or NO_SHADER):
            return item[4]
    return 0


def _set_shader_menu(self, value: int) -> None:
    from . import shaders

    for item in _shader_menu_items(self, None):
        if item[4] == value:
            name = None if item[0] == NO_SHADER else item[0]
            if name != (self.shader or None):
                try:
                    shaders.set_shader(self.id_data, name)
                except ValueError as exc:
                    log.error("%s", exc)
            return


class GSMaterialProps(bpy.types.PropertyGroup):
    shader: StringProperty(
        name="Game Shader",
        description="The game shader (src/shaders or the shader library) that draws this material in the game; "
                    "empty: none. A name, so a shader deleted from code keeps it and is reported",
        default="",
        override=OVERRIDABLE,
        update=_on_shader_update,
    )
    shader_menu: EnumProperty(
        name="Game Shader",
        description="The game shader (src/shaders or the shader library) that draws this material in the game",
        items=_shader_menu_items,
        get=_get_shader_menu,
        set=_set_shader_menu,
    )


# --- Watching, handlers, link --------------------------------------------------


def pump() -> None:
    """Reload when the project changes or components.json is rewritten."""
    root = project.project_root()
    if root != _source["root"]:
        reload_registry(root)
        return
    if root is None:
        return
    try:
        mtime = project.registry_path(root).stat().st_mtime
    except OSError:
        mtime = None
    if mtime != _source["mtime"]:
        reload_registry(root)


def _timer():
    try:
        pump()
    except Exception:  # never let the timer die
        log.exception("Registry watch failed")
    return WATCH_INTERVAL


def _on_link_registry(msg: dict) -> None:
    data = msg.get("registry")
    try:
        set_registry(data)
    except ValueError as exc:
        log.error("Ignoring registry from the dev server: %s", exc)


@persistent
def _on_file_change(*_args):
    project.invalidate_cache()
    reload_registry()


_HANDLERS = (("load_post", _on_file_change), ("save_post", _on_file_change))


def _blend_data_available() -> bool:
    """False while Blender restricts bpy.data, as it does while add-ons register."""
    return type(bpy.data).__name__ != "_RestrictData"


def _initial_load():
    reload_registry()
    return None  # one-shot timer


def register():
    bpy.utils.register_class(GSMaterialProps)
    bpy.types.Material.gs = PointerProperty(type=GSMaterialProps, override=OVERRIDABLE)
    if _blend_data_available():
        reload_registry()
    else:
        # At startup and when the add-on is enabled, bpy.data is off limits during
        # register(); load on the first timer tick instead.
        bpy.app.timers.register(_initial_load, first_interval=0.0)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)
    link.get_link().on("registry", _on_link_registry)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=WATCH_INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    link.get_link().off("registry", _on_link_registry)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _apply([])
    _warnings.clear()
    _source.update(root=None, mtime=None, origin="none")
    del bpy.types.Material.gs
    bpy.utils.unregister_class(GSMaterialProps)
