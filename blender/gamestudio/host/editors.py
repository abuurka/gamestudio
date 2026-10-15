# SPDX-License-Identifier: GPL-3.0-or-later
"""host.editors: GameStudio's custom editors, real ones on the fork and sidebar tabs on stock Blender.

GameStudio Editor has one native editor type, the Extension Editor
(`SPACE_EXTENSION`, RNA `SpaceExtensionEditor`, enum `'EXTENSION_EDITOR'`). Its
behaviour comes from *providers* (docs/fork/ARCHITECTURE.md section 3). A provider is
a plain class:

    class InspectorEditor:
        idname = "GAMESTUDIO_INSPECTOR"      # [A-Za-z][A-Za-z0-9_]{0,62}
        label = "Inspector"                  # name in the editor-type menu
        icon = "PROPERTIES"
        description = "Edit the selected entity and its components"
        panels = [editors.panel("components", "Components", draw_components)]
        header_draw = staticmethod(draw_header)   # optional, (layout, context)
        sidebar_fallback = True                    # optional, see "Stock Blender"

    host.editors.register(InspectorEditor)

Each panel body is a function `draw(layout, context)`, written once. This module
generates the `bpy.types.Panel` subclasses for whichever host is running:

GameStudio Editor (`host.has("extension_editors")`)
    `_gamestudio.register_editor(idname, label, icon, description)` adds the
    provider to the editor-type menu. Panels get `bl_space_type =
    'EXTENSION_EDITOR'`, `bl_context = idname` (the native side draws a panel
    only when this matches the editor's `provider_idname`) and the region of
    their spec ('WINDOW' by default, or 'UI' for the sidebar). One shared header,
    `EXTENSION_EDITOR_HT_header`, draws the editor-type selector followed by the
    active provider's header, looked up by `space_data.provider_idname`.
    `set_header()` gives native providers, such as the Game View, a Python header.

Stock Blender
    Panels fall back to the 3D View sidebar, in a tab named after the provider
    (`bl_category = label`), which is how the add-on's UI has always worked. A
    provider whose content the shell already shows in a sidebar sets
    `sidebar_fallback = False`, so stock Blender does not show it twice.
"""

import dataclasses
import logging
import re
import threading
from typing import Callable

import bpy

from . import capabilities, native

_log = logging.getLogger("gamestudio.editors")

SPACE_TYPE = "EXTENSION_EDITOR"
HEADER_IDNAME = "EXTENSION_EDITOR_HT_header"
FALLBACK_SPACE = "VIEW_3D"
FALLBACK_REGION = "UI"
REGIONS = ("WINDOW", "UI")

_IDNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,62}")
_PANEL_ID = re.compile(r"[a-z0-9_]+")
_MAX_BL_IDNAME = 63  # Blender's identifier buffers hold 64 bytes including the terminator


# --- Panel specs ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PanelSpec:
    """One panel of a provider. Turned into a Panel subclass per host by panel_classes()."""

    id: str                                  # lower_snake_case, unique within the provider
    label: str
    draw: Callable                           # draw(layout, context)
    poll: Callable | None = None             # poll(context) -> bool
    draw_header: Callable | None = None      # draw_header(layout, context)
    parent: str | None = None                # id of the parent panel in the same provider
    region: str = "WINDOW"                   # on the fork: "WINDOW" (main area) or "UI" (sidebar)
    options: frozenset = frozenset()         # bl_options, e.g. {"HIDE_HEADER"}, {"DEFAULT_CLOSED"}


def panel(id, label, draw, *, poll=None, draw_header=None, parent=None, region="WINDOW", options=()):
    """Describe a provider panel. `draw(layout, context)` is shared by every host."""
    if not _PANEL_ID.fullmatch(id or ""):
        raise ValueError(f"Panel id {id!r} must be lower_snake_case")
    if region not in REGIONS:
        raise ValueError(f"Panel region must be one of {REGIONS}, not {region!r}")
    return PanelSpec(id=id, label=label, draw=draw, poll=poll, draw_header=draw_header, parent=parent,
                     region=region, options=frozenset(options))


# --- Host detection -------------------------------------------------------------------


def available() -> bool:
    """True when this Blender has the native Extension Editor (GameStudio Editor builds)."""
    return "extension_editors" in capabilities()


def in_editor(context) -> bool:
    """True when drawing inside an Extension Editor, False in a sidebar fallback."""
    space = getattr(context, "space_data", None)
    return getattr(space, "type", "") == SPACE_TYPE


# --- Panel class generation -----------------------------------------------------------


def _panel_idname(provider, spec_id: str) -> str:
    return f"{provider.idname}_PT_{spec_id}"


def _draw(self, context):
    self.gs_spec.draw(self.layout, context)


def _draw_header(self, context):
    self.gs_spec.draw_header(self.layout, context)


def _poll(cls, context):
    return bool(cls.gs_spec.poll(context))


def panel_classes(provider, native_editor: bool) -> list[type]:
    """Generate (without registering) the Panel subclasses of a provider for one kind of host."""
    _validate(provider)
    classes = []
    regions = {}  # panel id -> region
    for order, spec in enumerate(getattr(provider, "panels", ())):
        if spec.id in regions:
            raise ValueError(f"{provider.idname}: duplicate panel id {spec.id!r}")
        if spec.parent is not None and spec.parent not in regions:
            raise ValueError(f"{provider.idname}: panel {spec.id!r} must come after its parent {spec.parent!r}")
        if spec.parent is not None and regions[spec.parent] != spec.region:
            # Blender only nests panels within one region. Checked on both hosts, although
            # stock Blender puts every panel in the same sidebar.
            raise ValueError(f"{provider.idname}: panel {spec.id!r} must use its parent's region "
                             f"({regions[spec.parent]!r})")
        regions[spec.id] = spec.region
        idname = _panel_idname(provider, spec.id)
        if len(idname) > _MAX_BL_IDNAME:
            raise ValueError(f"Panel idname {idname!r} is longer than {_MAX_BL_IDNAME} characters")
        attrs = {
            "bl_idname": idname,
            "bl_label": spec.label,
            "bl_order": order,
            "gs_provider": provider.idname,
            "gs_spec": spec,
            "draw": _draw,
            "__doc__": f"{provider.label}: {spec.label}",  # Blender uses it as the tooltip
        }
        if native_editor:
            attrs["bl_space_type"] = SPACE_TYPE
            attrs["bl_region_type"] = spec.region
            attrs["bl_context"] = provider.idname
            if spec.region == "UI":
                attrs["bl_category"] = provider.label  # the sidebar shows category tabs
        else:
            attrs["bl_space_type"] = FALLBACK_SPACE
            attrs["bl_region_type"] = FALLBACK_REGION
            attrs["bl_category"] = provider.label
        options = set(spec.options)
        if not native_editor:
            # A sidebar tab needs its header; HIDE_HEADER panels also sort above the built-in tabs.
            options.discard("HIDE_HEADER")
        if options:
            attrs["bl_options"] = options
        if spec.parent is not None:
            attrs["bl_parent_id"] = _panel_idname(provider, spec.parent)
        if spec.poll is not None:
            attrs["poll"] = classmethod(_poll)
        if spec.draw_header is not None:
            attrs["draw_header"] = _draw_header
        classes.append(type(idname, (bpy.types.Panel,), attrs))
    return classes


def _validate(provider) -> None:
    idname = getattr(provider, "idname", "")
    if not isinstance(idname, str) or not _IDNAME.fullmatch(idname):
        raise ValueError(f"Editor idname {idname!r} must match [A-Za-z][A-Za-z0-9_]{{0,62}}")
    if not getattr(provider, "label", ""):
        raise ValueError(f"Editor {idname} needs a label")
    for spec in getattr(provider, "panels", ()):
        if not isinstance(spec, PanelSpec):
            raise TypeError(f"{idname}.panels must hold host.editors.panel(...) specs, got {spec!r}")


# --- The shared header (fork only) ----------------------------------------------------

_headers: dict[str, Callable] = {}  # provider idname -> header_draw(layout, context)


class EXTENSION_EDITOR_HT_header(bpy.types.Header):
    """Header of every Extension Editor: the editor-type selector, then the provider's own header."""

    bl_space_type = SPACE_TYPE

    def draw(self, context):
        layout = self.layout
        layout.template_header()
        idname = getattr(context.space_data, "provider_idname", "")
        draw = _headers.get(idname)
        if draw is not None:
            draw(layout, context)


def _ensure_header() -> None:
    if available() and not EXTENSION_EDITOR_HT_header.is_registered:
        bpy.utils.register_class(EXTENSION_EDITOR_HT_header)


def _release_header() -> None:
    if EXTENSION_EDITOR_HT_header.is_registered and not _headers and not _registered:
        bpy.utils.unregister_class(EXTENSION_EDITOR_HT_header)


def set_header(idname: str, draw: Callable | None) -> None:
    """Draw `draw(layout, context)` after the editor-type selector in `idname`'s header.

    Works for Python and native providers alike (the Game View is native). None
    removes it. On stock Blender there is no editor header, so this only records it.
    """
    if draw is None:
        _headers.pop(idname, None)
        _release_header()
        return
    _headers[idname] = draw
    _ensure_header()


def header(idname: str) -> Callable | None:
    return _headers.get(idname)


# --- Registration ---------------------------------------------------------------------


@dataclasses.dataclass
class _Registration:
    provider: type
    native_editor: bool
    classes: list


_registered: dict[str, _Registration] = {}


def register(provider) -> None:
    """Register a provider: an editor on the fork, a 3D View sidebar tab on stock Blender.

    Idempotent: registering the same provider again does nothing, and a new class
    with the same idname (an add-on reload) replaces the old one.
    """
    _validate(provider)
    current = _registered.get(provider.idname)
    if current is not None:
        if current.provider is provider:
            return
        unregister(current.provider)

    native_editor = available()
    if native_editor or getattr(provider, "sidebar_fallback", True):
        classes = panel_classes(provider, native_editor=native_editor)  # validates before any side effect
    else:
        classes = []
    if native_editor:
        _native_register(provider)

    done = []
    try:
        for cls in classes:
            bpy.utils.register_class(cls)
            done.append(cls)
    except Exception:
        for cls in reversed(done):
            bpy.utils.unregister_class(cls)
        if native_editor:
            native().unregister_editor(provider.idname)
        raise
    _registered[provider.idname] = _Registration(provider, native_editor, classes)
    header_draw = getattr(provider, "header_draw", None)
    if header_draw is not None:
        set_header(provider.idname, header_draw)
    elif native_editor:
        _ensure_header()


def _native_register(provider) -> None:
    mod = native()
    args = (provider.idname, provider.label, getattr(provider, "icon", "NONE") or "NONE",
            getattr(provider, "description", "") or "")
    try:
        mod.register_editor(*args)
    except (ValueError, RuntimeError):
        if provider.idname not in mod.editors():
            raise
        # Left over from an earlier load of this module (add-on reload): replace it.
        mod.unregister_editor(provider.idname)
        mod.register_editor(*args)


def unregister(provider) -> None:
    """Undo register(). Unregistering a provider that is not registered does nothing."""
    idname = provider if isinstance(provider, str) else provider.idname
    entry = _registered.pop(idname, None)
    if entry is None:
        return
    if _headers.get(idname) is getattr(entry.provider, "header_draw", None):
        _headers.pop(idname, None)
    for cls in reversed(entry.classes):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    if entry.native_editor and native() is not None and idname in native().editors():
        native().unregister_editor(idname)
    _release_header()


def unregister_all() -> None:
    """Unregister every provider and header (the extension is being disabled)."""
    for idname in reversed(list(_registered)):
        unregister(idname)
    _headers.clear()
    _release_header()


def is_registered(idname: str) -> bool:
    return idname in _registered


def providers() -> list[str]:
    """Idnames of the providers registered from Python, in registration order."""
    return list(_registered)


def registered_classes(idname: str) -> list[type]:
    """The Panel classes registered for a provider (empty without a sidebar fallback)."""
    entry = _registered.get(idname)
    return list(entry.classes) if entry is not None else []


def provider(idname: str):
    entry = _registered.get(idname)
    return entry.provider if entry is not None else None


# --- Areas and the editor-type menu ---------------------------------------------------


def open(idname: str, area=None) -> bool:  # noqa: A001 - mirrors host.game_view.open
    """Turn an area (default: the context area) into the editor `idname`.

    Returns False on stock Blender, where there is no editor to switch to.
    """
    if not available():
        return False
    area = area if area is not None else getattr(bpy.context, "area", None)
    if area is None:
        return False
    try:
        area.ui_type = idname
    except TypeError:  # not in the editor-type menu (unknown or unregistered provider)
        return False
    return True


def set_hidden_editor_types(types) -> bool:
    """Hide editor types from the editor-type menu (fork only). Returns False where unsupported.

    Accepts space types ("SEQUENCE_EDITOR") and editor sub-types ("CompositorNodeTree").
    If the native filter rejects the list, it is retried with the plain space types only.
    Never raises: the app template calls this while it registers.
    """
    if "editor_type_filter" not in capabilities():
        return False
    types = [str(t) for t in types]
    try:
        native().set_hidden_editor_types(types)
        return True
    except (TypeError, ValueError) as exc:
        space_types = set(bpy.types.Area.bl_rna.properties["type"].enum_items.keys())
        supported = [t for t in types if t in space_types]
        _log.warning("Hiding editor types %s failed (%s); hiding only %s", types, exc, supported)
    try:
        native().set_hidden_editor_types(supported)
    except (TypeError, ValueError) as exc:
        _log.warning("Could not hide editor types %s: %s", supported, exc)
        return False
    return True


def tag_redraw() -> None:
    """Redraw every GameStudio editor: the Extension Editors on the fork, else the 3D View sidebars."""
    if threading.current_thread() is not threading.main_thread():
        return
    if available():
        native().tag_redraw()
        return
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas if window.screen else ():
            if area.type == FALLBACK_SPACE:
                for region in area.regions:
                    if region.type == FALLBACK_REGION:
                        region.tag_redraw()
