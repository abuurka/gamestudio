# SPDX-License-Identifier: GPL-3.0-or-later
"""Static property groups (docs/SPEC.md §7.1).

Object.gs holds the ordered list of attached components and the AI flags.
Field values live in the generated Object.gsc_<Name> groups (see registry.py).
Scene.gs holds per-scene editor settings such as the live-link toggle.
"""

import bpy
from bpy.props import BoolProperty, CollectionProperty, PointerProperty, StringProperty

from . import ids

OVERRIDABLE = {"LIBRARY_OVERRIDABLE"}


def _on_ref_update(self, _context):
    # Toggling "enabled" changes the exported component set.
    from . import live

    live.mark_components_dirty(self.id_data)


class GSComponentRef(bpy.types.PropertyGroup):
    name: StringProperty(
        name="Component",
        description="Name of the attached component (matches the registry)",
        override=OVERRIDABLE,
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Export this component. Disable instead of removing to keep its values",
        default=True,
        override=OVERRIDABLE,
        update=_on_ref_update,
    )


class GSObjectProps(bpy.types.PropertyGroup):
    components: CollectionProperty(
        type=GSComponentRef,
        override={"LIBRARY_OVERRIDABLE", "USE_INSERTION"},
    )
    ai_lock: BoolProperty(
        name="Lock from AI",
        description="The AI assistant never proposes changes to this object",
        default=False,
        override=OVERRIDABLE,
    )
    ai_touched: StringProperty(
        name="Last AI proposal",
        description="ID of the last AI proposal applied to this object",
        default="",
        override=OVERRIDABLE,
    )


class GSSceneProps(bpy.types.PropertyGroup):
    live_enabled: BoolProperty(
        name="Live Link",
        description="Stream edits (transforms, components, geometry) to the running game",
        default=True,
    )


_CLASSES = (GSComponentRef, GSObjectProps, GSSceneProps)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.gs = PointerProperty(type=GSObjectProps, override=OVERRIDABLE)
    bpy.types.Scene.gs = PointerProperty(type=GSSceneProps)
    ids.register()


def unregister():
    ids.unregister()
    del bpy.types.Scene.gs
    del bpy.types.Object.gs
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
