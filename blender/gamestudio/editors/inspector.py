# SPDX-License-Identifier: GPL-3.0-or-later
"""The Inspector editor (GAMESTUDIO_INSPECTOR): the selected entity and its components.

The Game tab's Entity and Components panels promoted to an editor. Both draw
with ui.py's functions, so the Inspector looks and works the same everywhere.
"""

from .. import host, ui

panel = host.editors.panel


def _has_object(context) -> bool:
    return getattr(context, "active_object", None) is not None


def draw_entity(layout, context) -> None:
    ui.draw_entity_panel(layout, context, editable_name=True)


def draw_components(layout, context) -> None:
    ui.draw_components_panel(layout, context)


def draw_header(layout, context) -> None:
    obj = getattr(context, "active_object", None)
    if obj is not None:
        layout.label(text=obj.name, icon=ui.object_icon(obj))
    layout.separator_spacer()
    layout.operator("gamestudio.reload_registry", text="", icon="FILE_REFRESH")


class InspectorEditor:
    idname = "GAMESTUDIO_INSPECTOR"
    label = "Inspector"
    icon = "PROPERTIES"
    description = "The selected entity: its name, transform and game components"
    # On stock Blender the Game panel of Object properties (ui.py) already is this UI.
    sidebar_fallback = False
    panels = [
        panel("entity", "Entity", draw_entity),
        panel("components", "Components", draw_components, poll=_has_object),
    ]
    header_draw = staticmethod(draw_header)


def register():
    host.editors.register(InspectorEditor)


def unregister():
    host.editors.unregister(InspectorEditor)
