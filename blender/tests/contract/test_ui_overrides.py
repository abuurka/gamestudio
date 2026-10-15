# SPDX-License-Identifier: GPL-3.0-or-later
"""UI override semantics the GameStudio app template is built on.

The template (editor/shell/scripts/startup/bl_app_templates_system/GameStudio/__init__.py)
replaces a few upstream Python UI classes by registering classes with the same bl_idname, and
appends to upstream menus. Every test here restores the stock UI before it returns.
"""

import ast
import importlib
import unittest

import bpy
from bpy.types import Header, Menu

import contract_support as cs

# (upstream module, class) pairs the template replaces or extends.
OVERRIDDEN = (
    ("bl_ui.space_topbar", "TOPBAR_HT_upper_bar"),
    ("bl_ui.space_topbar", "TOPBAR_MT_editor_menus"),
    ("bl_operators.wm", "WM_MT_splash"),
    ("bl_operators.wm", "WM_MT_splash_about"),
    ("bl_ui.space_topbar", "TOPBAR_MT_file"),
    ("bl_ui.space_topbar", "TOPBAR_MT_help"),
)


def _upstream(module, name):
    return getattr(importlib.import_module(module), name)


def _restore(upstream_cls, *ours):
    for cls in ours:
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    if not upstream_cls.is_registered:
        bpy.utils.register_class(upstream_cls)


def _subclass_of_upper_bar():
    """A fresh copy of the template's pattern: subclass upstream, override draw_right."""
    base = _upstream("bl_ui.space_topbar", "TOPBAR_HT_upper_bar")

    class TOPBAR_HT_upper_bar(base):
        def draw_right(self, context):
            super().draw_right(context)

    return base, TOPBAR_HT_upper_bar


class UpstreamClassesTest(unittest.TestCase):
    def test_overridden_classes_exist_upstream(self):
        """The upstream classes the template overrides still exist, registered, with the same idnames.

        Breaks if changed: GameStudio/__init__.py `_REPLACED` and its menu appends; a renamed
        class means the template either fails to import or silently leaves the stock UI in place
        (for example the stock splash instead of the project launcher).
        """
        for module, name in OVERRIDDEN:
            with self.subTest(cls=name):
                cls = _upstream(module, name)
                self.assertEqual(getattr(cls, "bl_idname", cls.__name__), name)
                self.assertTrue(cls.is_registered, f"{module}.{name} is not registered at startup")
                self.assertIs(getattr(bpy.types, name), cls)

    def test_template_upstream_references_resolve(self):
        """Every `upstream_*.<Class>` and `bpy.types.<Menu>.append/prepend` in the template resolves.

        Breaks if changed: the template module itself (an AttributeError at import or register
        means Blender falls back to the stock UI with a traceback at startup).
        """
        path = cs.TEMPLATE_SRC / "__init__.py"
        if not path.is_file():
            self.skipTest(f"template source not found: {path}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname and alias.name.startswith(("bl_ui", "bl_operators")):
                        aliases[alias.asname] = alias.name
        refs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                refs.add((aliases[node.value.id], node.attr))
            if (isinstance(node, ast.Attribute) and node.attr in ("append", "prepend")
                    and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Attribute)
                    and node.value.value.attr == "types"):
                refs.add(("bpy.types", node.value.attr))
        self.assertTrue(refs, "found no upstream references in the template; did its imports change?")
        for module, name in sorted(refs):
            with self.subTest(ref=f"{module}.{name}"):
                owner = bpy.types if module == "bpy.types" else importlib.import_module(module)
                self.assertTrue(hasattr(owner, name), f"{module}.{name} no longer exists")

    def test_upper_bar_draw_calls_draw_left_and_draw_right(self):
        """TOPBAR_HT_upper_bar still has draw_left/draw_right, and its draw() calls both.

        Breaks if changed: the template's TOPBAR_HT_upper_bar subclass only overrides
        draw_right() to add the Play controls; if upstream inlines or renames these, the Play
        buttons disappear from the top bar without any error.
        """
        cls = _upstream("bl_ui.space_topbar", "TOPBAR_HT_upper_bar")
        self.assertTrue(callable(getattr(cls, "draw_left", None)))
        self.assertTrue(callable(getattr(cls, "draw_right", None)))
        called = set(cls.draw.__code__.co_names)
        self.assertIn("draw_left", called)
        self.assertIn("draw_right", called)


class ReplacementTest(unittest.TestCase):
    def test_menu_reregistration_replaces_upstream(self):
        """Registering a Menu whose bl_idname exists replaces the registered class.

        Breaks if changed: the template's TOPBAR_MT_editor_menus, WM_MT_splash and
        WM_MT_splash_about overrides (GameStudio menu bar, project launcher, About box).
        """
        upstream = _upstream("bl_ui.space_topbar", "TOPBAR_MT_editor_menus")

        class TOPBAR_MT_editor_menus(Menu):
            bl_idname = "TOPBAR_MT_editor_menus"
            bl_label = ""

            def draw(self, context):
                pass

        try:
            bpy.utils.register_class(TOPBAR_MT_editor_menus)
            self.assertTrue(TOPBAR_MT_editor_menus.is_registered)
            self.assertFalse(upstream.is_registered)
            self.assertIs(Menu.bl_rna_get_subclass_py("TOPBAR_MT_editor_menus"), TOPBAR_MT_editor_menus)
        finally:
            _restore(upstream, TOPBAR_MT_editor_menus)
        self.assertIs(Menu.bl_rna_get_subclass_py("TOPBAR_MT_editor_menus"), upstream)

    def test_header_reregistration_replaces_upstream(self):
        """Registering a Header whose bl_idname exists replaces the registered class.

        Breaks if changed: the template's TOPBAR_HT_upper_bar subclass (Play/Pause/Stop/Step in
        the top bar).
        """
        upstream, ours = _subclass_of_upper_bar()
        try:
            bpy.utils.register_class(ours)
            self.assertTrue(ours.is_registered)
            self.assertFalse(upstream.is_registered)
            self.assertIs(Header.bl_rna_get_subclass_py("TOPBAR_HT_upper_bar"), ours)
        finally:
            _restore(upstream, ours)
        self.assertIs(Header.bl_rna_get_subclass_py("TOPBAR_HT_upper_bar"), upstream)

    def test_explicit_unregister_keeps_bpy_types_current(self):
        """Unregistering upstream first, then registering a subclass, keeps `bpy.types` current.

        Repeated over two register/unregister cycles, as happens when the user switches app
        templates. Breaks if changed: GameStudio/__init__.py register()/unregister() rely on this
        to make `bpy.types.TOPBAR_HT_upper_bar` the template's class every time
        (blender/tests/shell test_ui_overrides_installed and test_template_unregister_restores_stock_ui).
        """
        upstream, ours = _subclass_of_upper_bar()
        try:
            for cycle in range(2):
                if upstream.is_registered:
                    bpy.utils.unregister_class(upstream)
                bpy.utils.register_class(ours)
                self.assertIs(bpy.types.TOPBAR_HT_upper_bar, ours, f"cycle {cycle}: bpy.types is stale")
                bpy.utils.unregister_class(ours)
                bpy.utils.register_class(upstream)
                self.assertIs(bpy.types.TOPBAR_HT_upper_bar, upstream, f"cycle {cycle}: upstream not restored")
        finally:
            _restore(upstream, ours)

    def test_implicit_replacement_still_leaves_stale_bpy_types(self):
        """Implicit replacement (no explicit unregister) still leaves `bpy.types` stale after a cycle.

        This documents the upstream bug that the template's explicit unregister works around. If
        Blender fixes it, the test skips with a note: the workaround in GameStudio/__init__.py
        register() is then optional (nothing breaks).
        """
        upstream, ours = _subclass_of_upper_bar()
        stale = False
        try:
            for _cycle in range(2):
                bpy.utils.register_class(ours)  # implicit: upstream is still registered
                stale = stale or bpy.types.TOPBAR_HT_upper_bar is not ours
                bpy.utils.unregister_class(ours)
                bpy.utils.register_class(upstream)
        finally:
            _restore(upstream, ours)
        self.assertIs(bpy.types.TOPBAR_HT_upper_bar, upstream)
        if not stale:
            self.skipTest("fixed upstream: implicit re-registration now keeps bpy.types current; the explicit "
                          "unregister in the GameStudio template is no longer required")

    def test_menu_prepend_and_append(self):
        """`Menu.prepend` and `Menu.append` add draw functions at the start and end of a menu.

        Breaks if changed: the template's File > New/Open Game Project entries
        (TOPBAR_MT_file.prepend) and Help > GameStudio Documentation (TOPBAR_MT_help.append).
        """
        file_menu = bpy.types.TOPBAR_MT_file
        help_menu = bpy.types.TOPBAR_MT_help

        def first(self, context):
            pass

        def last(self, context):
            pass

        file_menu.prepend(first)
        help_menu.append(last)
        try:
            self.assertIs(file_menu._dyn_ui_initialize()[0], first)
            self.assertIs(help_menu._dyn_ui_initialize()[-1], last)
        finally:
            file_menu.remove(first)
            help_menu.remove(last)
        self.assertNotIn(first, file_menu._dyn_ui_initialize())
        self.assertNotIn(last, help_menu._dyn_ui_initialize())

    def test_app_override_draw_filter_exists(self):
        """`bl_app_override.ui_draw_filter_register` exists for app templates.

        Breaks if changed: the shell rule in docs/fork/ARCHITECTURE.md section 2 (hide upstream UI
        items with the draw filter instead of copying classes).
        """
        import bl_app_override

        self.assertTrue(callable(getattr(bl_app_override, "ui_draw_filter_register", None)))
