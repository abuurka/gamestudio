# SPDX-License-Identifier: GPL-3.0-or-later
"""Small bpy APIs GameStudio's shell calls directly."""

import unittest

import bpy

import contract_support as cs


class AppApiTest(unittest.TestCase):
    def test_online_access_exists(self):
        """`bpy.app.online_access` exists and is a bool.

        Breaks if changed: assistant/agent.py, assistant/ops.py, assistant/ui.py and prefs.py gate
        every network call on it (extensions policy); host/compat.py `online_access` would then
        silently fall back to True.
        """
        self.assertTrue(hasattr(bpy.app, "online_access"))
        self.assertIsInstance(bpy.app.online_access, bool)

    def test_textbox_signature(self):
        """`UILayout.textbox` exists with the parameters data, property, initial_visible_lines, placeholder.

        Breaks if changed: assistant/ui.py prompt box (it falls back to a one-line field only if
        textbox disappears; a renamed keyword raises TypeError while drawing the panel).
        """
        func = bpy.types.UILayout.bl_rna.functions.get("textbox")
        self.assertIsNotNone(func, "UILayout.textbox is missing")
        params = {p.identifier: p for p in func.parameters}
        for name in ("data", "property", "initial_visible_lines", "placeholder"):
            self.assertIn(name, params)
        self.assertTrue(params["data"].is_required)
        self.assertTrue(params["property"].is_required)
        self.assertFalse(params["initial_visible_lines"].is_required)
        self.assertFalse(params["placeholder"].is_required)

    def test_handlers_used_by_shell_exist(self):
        """Every bpy.app.handlers list the shell appends to exists, and `persistent` is available.

        Breaks if changed: core/live.py, core/registry.py and the other modules' register(); a
        missing list raises AttributeError and the extension fails to enable.
        """
        names = cs.handler_names_used() or {"load_post", "save_post", "depsgraph_update_post",
                                            "undo_post", "redo_post"}
        for name in sorted(names):
            with self.subTest(handler=name):
                self.assertIsInstance(getattr(bpy.app.handlers, name, None), list)
        self.assertTrue(callable(bpy.app.handlers.persistent))
