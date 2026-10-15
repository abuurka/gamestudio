# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI bridge methods (docs/GAME-UI.md §8): thin wrappers over gs.gameui for the MCP tools of the
same names.

  ui_list    canvas?                                   gameui.describe(canvas)
  ui_open    create? (true), save? (false)             opens ui/ui.blend here, creating it when missing;
                                                       refuses unsaved changes unless save; {file, canvases}
  ui_canvas  name, kind?, open?, pause?, pause_menu?, back?, scale_mode?, backdrop?, layer?, size?, focus?,
             transition?                               create or update; the canvas's description (focus:
                                                       a button of the canvas; null or "" clears it;
                                                       transition {open?, close?, duration?, loop?}: the
                                                       gameui.animate presets, keys not given kept; null
                                                       removes them)
  ui_add     canvas, kind, name, at, + that kind's gs.gameui keyword arguments (every kind: parent, anchor,
             z, visible, loop)                         create or update; the element's description, its
                                                       own parts, and the parts the change removed
  ui_set     canvas, name, + any of gameui.SET_KEYS    the element's description, parts and removed parts
  ui_remove  canvas, name?                             an element with its contents, or the whole canvas
  ui_strings locale?                                   the UI's translatable texts (the open UI file's, else
                                                       the exported ones), each once with the elements that
                                                       show it, and with a locale its translation or null and
                                                       the table's unused strings (gameui.strings)
  ui_translate locale, name?, strings, remove?         create or update ui/locales/<locale>.json (name needed
                                                       when creating); the counts; re-exports the UI's locales
                                                       (gameui.translate)

The write methods need the open file to be a UI file; otherwise they fail with data.code
"ui_file_not_open" and a hint to call ui_open. Each write is one undo step ("Agent: ui_add"). Locked
objects (Lock from AI) are refused. Errors of gs.gameui carry their code in data.code too. ui_strings and
ui_translate work from any file of the project: they read and write JSON files, not the scene (no undo step).
"""

import difflib
import pathlib

import bpy

from ..core import gameui as core, project, registry
from .. import gameui
from ..gameui import build, files, locales
from . import api
from .api import method
from .server import INVALID_PARAMS, BridgeError

# ui_add: the keyword arguments of each kind's gs.gameui function (besides canvas, name and at).
COMMON = ("parent", "anchor", "z", "visible", "loop")
ADD_KEYS = {
    "text": ("body", "size", "font", "color", "align", "valign", "box", "outline", "outline_color", "shadow",
             "project_name"),
    "image": ("path", "width", "height", "opacity"),
    "panel": ("size", "color", "radius", "border", "border_color", "image", "slice", "shader", "shader_values"),
    "button": ("label", "size", "action", "target", "font", "text_size", "color", "text_color", "focus_color",
               "focus_text_color", "radius", "order", "sound", "focus_sound"),
    "bar": ("size", "value", "max", "color", "back_color", "radius", "direction"),
    "group": (),
    "icons": ("count", "value", "image", "empty", "size", "spacing"),
    "slider": ("size", "value", "min", "max", "step", "color", "back_color", "handle", "handle_color", "radius",
               "direction", "order", "sound"),
    "toggle": ("value", "size", "on_color", "off_color", "knob_color", "order", "sound"),
    "model": ("size", "source", "kit", "kit_params", "asset", "view", "zoom", "spin", "clip", "light", "background"),
}
CANVAS_KEYS = ("name", "kind", "open", "pause", "pause_menu", "back", "scale_mode", "backdrop", "layer", "size",
               "focus", "transition")
TRANSITION_KEYS = ("open", "close", "duration", "loop")
TRANSLATE_KEYS = ("locale", "name", "strings", "remove")
OPEN_HINT = "Call ui_open to open ui/ui.blend (it is created when missing), then retry."


def check_keys(params: dict, allowed, what: str) -> None:
    """Refuse unknown parameters, naming the valid ones."""
    unknown = sorted(set(params) - set(allowed))
    if not unknown:
        return
    close = difflib.get_close_matches(unknown[0], list(allowed), n=1)
    raise BridgeError(f"{what}: unknown parameter{'s' if len(unknown) > 1 else ''} {', '.join(unknown)}."
                      + (f" Did you mean '{close[0]}'?" if close else ""),
                      hint=f"Parameters: {', '.join(allowed)}.", code=INVALID_PARAMS)


def require_ui_file() -> None:
    if not core.is_ui_file():
        open_file = bpy.data.filepath or "an unsaved file"
        raise BridgeError(f"The open file is not a UI file ({files.project_relative(open_file)} is open); the "
                          "game's screens are in ui/ui.blend.", hint=OPEN_HINT, data={"code": "ui_file_not_open"})


def _call(fn, *args, **kwargs):
    """Run a gs.gameui call; its GameUIError becomes a BridgeError with the same code and hint."""
    try:
        return fn(*args, **kwargs)
    except build.GameUIError as exc:
        hint = exc.hint or (OPEN_HINT if exc.code == "ui_file_not_open" else None)
        raise BridgeError(exc.message, hint=hint, data={"code": exc.code} if exc.code else None) from None


def _locked(objs) -> None:
    for obj in objs:
        api.check_writable(obj)


def _with_parts(obj) -> list:
    """obj, everything inside it, and the elements it is a part of: locking a button locks its label too."""
    out, part = [obj, *obj.children_recursive], obj
    while part.get(build.PART_PROP) and part.parent is not None:
        part = part.parent
        out.append(part)
    return out


def _part_names(obj) -> list[str]:
    return [part.name for part in build.own_parts(obj)] if obj is not None else []


def _element_result(scene, obj, before=(), **extra) -> dict:
    """{canvas, ..., element, parts?, removed?}: the element, its own parts (not those of elements inside it), and
    the names of its parts the change deleted (before: its part names before the change)."""
    out = {"canvas": scene.name, **extra, "element": build.describe_element(scene, obj)}
    parts = build.own_parts(obj)
    if parts:
        out["parts"] = [build.describe_element(scene, part) for part in parts]
    now = {part.name for part in parts}
    removed = [name for name in before if name not in now]
    if removed:
        out["removed"] = removed
    return out


@method("ui_list")
def ui_list(params):
    check_keys(params, ("canvas",), "ui_list")
    canvas = api.param(params, "canvas", str, None)
    if canvas is not None and not core.is_ui_file():
        require_ui_file()
    return _call(gameui.describe, canvas)


def _summary(scene) -> dict:
    settings = build.canvas_settings(scene)
    return {"name": scene.name, "kind": settings.get("kind", "menu"), "open": settings.get("open", "manual"),
            "elements": sum(1 for o in scene.objects if build.is_element(o, scene))}


@method("ui_open")
def ui_open(params):
    check_keys(params, ("create", "save"), "ui_open")
    create = api.param(params, "create", bool, True)
    save = api.param(params, "save", bool, False)
    root = project.project_root()
    if root is None:
        raise BridgeError("No GameStudio project is open, so there is no ui/ui.blend to open.",
                          hint="Open a level of the project first (open_scene).", data={"code": "no_project"})
    path = core.ui_file(root)
    rel = files.project_relative(path)
    already = bool(bpy.data.filepath) and pathlib.Path(bpy.data.filepath).resolve() == path.resolve()
    created = False
    if not already:
        if not path.exists() and not create:
            raise BridgeError(f"{rel} does not exist.", hint="Pass create: true to make it with an empty menu.",
                              data={"code": "ui_file_missing"})
        if api.is_dirty():
            if not save:
                raise BridgeError(f"The open file ({files.project_relative(bpy.data.filepath) or 'unsaved'}) has "
                                  "unsaved changes.", hint="Pass save: true to save it first, or call save.",
                                  data={"code": "unsaved_changes"})
            if not bpy.data.filepath:
                raise BridgeError("The open file was never saved, so save: true cannot save it.",
                                  hint="save({path}) it into the project first.", data={"code": "unsaved_changes"})
            bpy.ops.wm.save_mainfile()
        if not path.exists():
            _call(files.new_ui_file, root)
            created = True
        if not (bpy.data.filepath and pathlib.Path(bpy.data.filepath).resolve() == path.resolve()):
            bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=not created)
        project.invalidate_cache()
        registry.reload_registry()
    return {"file": rel, "created": created, "canvases": [_summary(s) for s in core.canvas_scenes()]}


def _transition(params, scene):
    """ui_canvas transition -> gameui.animate's arguments: the keys given over the canvas's current presets (the
    animate defaults when it has none); null removes them all. Checked before anything changes."""
    value = params["transition"]
    if value is None:
        settings = {"open": "none", "close": "none", "duration": build.ANIMATE_DEFAULTS["duration"], "loop": None}
    elif isinstance(value, dict):
        check_keys(value, TRANSITION_KEYS, "ui_canvas transition")
        current = build.animation_settings(scene) if scene is not None else None
        given = {k: v for k, v in value.items() if v is not None or k == "loop"}  # loop: null is no loop
        settings = {**build.ANIMATE_DEFAULTS, **(current or {}), **given}
    else:
        raise BridgeError(f"ui_canvas: transition must be an object {{open?, close?, duration?, loop?}} or null, got "
                          f"{value!r}.", hint='Example: {"open": "pop", "close": "fade", "duration": 0.25}.',
                          code=INVALID_PARAMS)
    return _call(build.check_animation, **settings)


@method("ui_canvas", mutating=True)
def ui_canvas(params):
    check_keys(params, CANVAS_KEYS, "ui_canvas")
    require_ui_file()
    name = api.param(params, "name", str)
    scene = bpy.data.scenes.get(name)
    if scene is not None and scene.camera is not None:
        _locked([scene.camera])
    transition = _transition(params, scene) if "transition" in params else None
    if transition is not None and scene is not None and scene.camera is not None:
        _locked([*build.top_level(scene), *build.own_loops(scene)])  # animate keys these
    kwargs = {k: params[k] for k in CANVAS_KEYS[1:-1] if params.get(k) is not None}
    if "focus" in params and params["focus"] is None:
        kwargs["focus"] = ""  # null clears it: the first button in navigation order is selected first
    scene = _call(gameui.canvas, name, **kwargs)
    if transition is not None:
        _call(gameui.animate, scene, **transition)
    return _call(build.describe_canvas, scene)


@method("ui_add", mutating=True)
def ui_add(params):
    kind = api.param(params, "kind", str, choices=tuple(ADD_KEYS))
    check_keys(params, ("canvas", "kind", "name", "at") + ADD_KEYS[kind] + COMMON, f"ui_add ({kind})")
    require_ui_file()
    canvas = api.param(params, "canvas", str)
    name = api.param(params, "name", str)
    if "at" not in params or params["at"] is None:
        raise BridgeError("Missing parameter 'at': the element's origin, [x, y] in canvas px (y down).",
                          code=INVALID_PARAMS)
    existing = bpy.data.objects.get(name)
    if existing is not None:
        _locked(_with_parts(existing))
    before = _part_names(existing)
    kwargs = {k: params[k] for k in ADD_KEYS[kind] + COMMON if params.get(k) is not None}
    if isinstance(kwargs.get("anchor"), list):
        kwargs["anchor"] = tuple(kwargs["anchor"])
    fn = getattr(gameui, kind)
    if kind == "text":
        obj = _call(fn, canvas, name, kwargs.pop("body", None), params["at"], **kwargs)
    elif kind == "image":
        obj = _call(fn, canvas, name, kwargs.pop("path", None), params["at"], **kwargs)
    elif kind == "button":
        obj = _call(fn, canvas, name, kwargs.pop("label", None), params["at"], **kwargs)
    elif kind in ("panel", "bar"):
        obj = _call(fn, canvas, name, params["at"], kwargs.pop("size", None), **kwargs)
    elif kind == "toggle":
        obj = _call(fn, canvas, name, params["at"], kwargs.pop("value", None), **kwargs)
    else:  # group, icons, slider, model
        obj = _call(fn, canvas, name, params["at"], **kwargs)
    return _element_result(build.get_canvas(canvas), obj, before, created=existing is None)


@method("ui_set", mutating=True)
def ui_set(params):
    check_keys(params, ("canvas", "name") + build.SET_KEYS, "ui_set")
    require_ui_file()
    canvas = api.param(params, "canvas", str)
    name = api.param(params, "name", str)
    changes = {k: v for k, v in params.items() if k not in ("canvas", "name")}
    for key in [k for k, v in changes.items() if v is None and k != "parent"]:
        del changes[key]  # null: not given (parent: null makes the element top-level)
    if not changes:
        raise BridgeError("ui_set: nothing to change.", hint=f"Pass any of: {', '.join(build.SET_KEYS)}.",
                          code=INVALID_PARAMS)
    scene = _call(build.get_canvas, canvas)
    target = _call(build.find_element, scene, name)
    _locked(_with_parts(target))
    before = _part_names(target)
    obj = _call(gameui.update, scene, name, **changes)
    return _element_result(scene, obj, before)


@method("ui_remove", mutating=True)
def ui_remove(params):
    check_keys(params, ("canvas", "name"), "ui_remove")
    require_ui_file()
    canvas = api.param(params, "canvas", str)
    name = api.param(params, "name", str, None)
    scene = _call(build.get_canvas, canvas)
    if name is None:
        _locked([o for o in scene.objects if len(o.users_scene) == 1])
        removed = _call(gameui.remove, scene)
        return {"removed_canvas": canvas, "removed": removed, "canvases": build.canvas_names()}
    _locked(_with_parts(_call(build.find_element, scene, name)))
    return {"canvas": canvas, "removed": _call(gameui.remove, scene, name)}


@method("ui_strings")
def ui_strings(params):
    check_keys(params, ("locale",), "ui_strings")
    locale = api.param(params, "locale", str, None)
    return _call(locales.strings, locale)


@method("ui_translate")
def ui_translate(params):
    check_keys(params, TRANSLATE_KEYS, "ui_translate")
    locale = api.param(params, "locale", str)
    name = api.param(params, "name", str, None)
    strings = api.param(params, "strings", dict)
    remove = api.param(params, "remove", list, [])
    return _call(locales.translate, locale, strings, name=name, remove=remove)
