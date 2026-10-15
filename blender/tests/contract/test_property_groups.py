# SPDX-License-Identifier: GPL-3.0-or-later
"""PropertyGroup behaviour behind GameStudio's component storage.

Components live in PropertyGroups: static ones in core/props.py (Object.gs) and generated ones in
core/registry.py (Object.gsc_<Name>, rebuilt whenever components.json changes). Their values are
stored as IDProperties on the object, so they must survive regeneration and library overrides.
"""

import unittest

import bpy
from bpy.props import CollectionProperty, EnumProperty, FloatProperty, PointerProperty

import contract_support as cs

OVERRIDABLE = {"LIBRARY_OVERRIDABLE"}


def _group(name, annotations):
    return type(name, (bpy.types.PropertyGroup,), {"__annotations__": annotations, "__module__": __name__})


class PropertyGroupTest(unittest.TestCase):
    def setUp(self):
        cs.factory_reset(use_empty=True)
        self.obj = bpy.data.objects.new("GSContractHolder", None)
        bpy.context.scene.collection.objects.link(self.obj)
        self._cleanup = []

    def tearDown(self):
        for fn in reversed(self._cleanup):
            fn()
        cs.factory_reset(use_empty=True)

    def _attach(self, cls, attr, **kwargs):
        bpy.utils.register_class(cls)
        setattr(bpy.types.Object, attr, PointerProperty(type=cls, **kwargs))

        def detach():
            if hasattr(bpy.types.Object, attr):
                delattr(bpy.types.Object, attr)
            if cls.is_registered:
                bpy.utils.unregister_class(cls)

        self._cleanup.append(detach)
        return detach

    def test_library_overridable_flag_accepted(self):
        """bpy.props accept `override={'LIBRARY_OVERRIDABLE'}` and the property reports it.

        Breaks if changed: every component field (core/registry.py `_make_property`) and
        Object.gs (core/props.py) is declared with it; without the flag, prefab instance edits
        are silently reverted to the library value on reload (research/04 section 2.4).
        """
        cls = _group("GSContractOverridable", {"speed": FloatProperty(default=1.0, override=OVERRIDABLE)})
        self._attach(cls, "gs_contract_overridable", override=OVERRIDABLE)
        self.assertTrue(cls.bl_rna.properties["speed"].is_overridable)
        self.assertTrue(bpy.types.Object.bl_rna.properties["gs_contract_overridable"].is_overridable)

    def test_collection_use_insertion_accepted(self):
        """CollectionProperty accepts `override={'LIBRARY_OVERRIDABLE', 'USE_INSERTION'}`.

        Breaks if changed: core/props.py GSObjectProps.components (a prefab instance can no longer
        add components of its own).
        """
        item = _group("GSContractItem", {"value": FloatProperty(override=OVERRIDABLE)})
        bpy.utils.register_class(item)
        self._cleanup.append(lambda: bpy.utils.unregister_class(item))
        holder = _group("GSContractList", {
            "items": CollectionProperty(type=item, override={"LIBRARY_OVERRIDABLE", "USE_INSERTION"})})
        self._attach(holder, "gs_contract_list", override=OVERRIDABLE)
        self.assertTrue(holder.bl_rna.properties["items"].is_overridable)

    def test_bpy_props_stored_apart_from_custom_properties(self):
        """`bl_system_properties_get()` exists and holds bpy.props values; `obj.keys()` does not.

        Breaks if changed: core/components.py `remove_component` (drops raw values of
        unregistered components through bl_system_properties_get), host/compat.py
        `system_properties`, and the glTF `extras` behaviour assumed by core/export.py.
        """
        cls = _group("GSContractStored", {"speed": FloatProperty(default=1.0)})
        self._attach(cls, "gs_contract_stored")
        self.obj.gs_contract_stored.speed = 4.0
        self.assertTrue(callable(getattr(self.obj, "bl_system_properties_get", None)))
        stored = self.obj.bl_system_properties_get()
        self.assertIsNotNone(stored)
        self.assertIn("gs_contract_stored", stored.keys())
        self.assertNotIn("gs_contract_stored", self.obj.keys())

    def test_values_survive_reregistration(self):
        """A generated PropertyGroup unregistered and registered again under the same names keeps stored values.

        Breaks if changed: core/registry.py `_apply` (editing components.json re-registers the
        changed GSC_<Name> classes; values would reset to defaults on every registry change).
        """
        def make():
            return _group("GSContractRegen", {"speed": FloatProperty(default=1.0, override=OVERRIDABLE)})

        first = make()
        detach = self._attach(first, "gs_contract_regen", override=OVERRIDABLE)
        self.obj.gs_contract_regen.speed = 7.5
        detach()
        self.assertIn("gs_contract_regen", self.obj.bl_system_properties_get().keys())

        second = make()
        self._attach(second, "gs_contract_regen", override=OVERRIDABLE)
        self.assertAlmostEqual(self.obj.gs_contract_regen.speed, 7.5, places=5)

    def test_enum_choice_kept_by_item_number(self):
        """An EnumProperty stores the item *number*, so reordering items keeps the choice.

        Breaks if changed: core/registry.py `_enum_items` (numbers derived from a hash of the
        option text so that reordering or adding options in components.json keeps choices).
        """
        def make(items):
            return _group("GSContractEnum", {"mode": EnumProperty(items=items, default=items[0][0])})

        first = make([("A", "A", "", 10), ("B", "B", "", 20)])
        detach = self._attach(first, "gs_contract_enum")
        self.obj.gs_contract_enum.mode = "B"
        detach()
        second = make([("C", "C", "", 5), ("B", "B", "", 20), ("A", "A", "", 10)])
        self._attach(second, "gs_contract_enum")
        self.assertEqual(self.obj.gs_contract_enum.mode, "B")

    def test_property_unset_restores_default(self):
        """`property_unset(name)` removes a stored value so the default shows again.

        Breaks if changed: core/components.py `remove_component` and core/registry.py `_restore`
        (stale component values would come back when a component is re-added).
        """
        cls = _group("GSContractUnset", {"speed": FloatProperty(default=1.0)})
        self._attach(cls, "gs_contract_unset")
        self.obj.gs_contract_unset.speed = 3.0
        self.obj.property_unset("gs_contract_unset")
        self.assertAlmostEqual(self.obj.gs_contract_unset.speed, 1.0, places=5)
