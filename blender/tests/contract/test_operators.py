# SPDX-License-Identifier: GPL-3.0-or-later
"""Operator registration options used by GameStudio's operators."""

import unittest

import bpy


class CONTRACT_OT_undo_probe(bpy.types.Operator):
    bl_idname = "gs_contract.undo_probe"
    bl_label = "GameStudio Contract Undo Probe"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return {"FINISHED"}


class OperatorOptionsTest(unittest.TestCase):
    def test_register_undo_options_accepted(self):
        """An operator with `bl_options = {'REGISTER', 'UNDO'}` registers, runs and keeps both flags.

        Breaks if changed: ops.py and assistant/ops.py (component edits and applied AI proposals
        must each be one undo step, docs/AI-ASSISTANT.md); an unknown flag makes register_class
        raise and the whole extension fails to enable.
        """
        bpy.utils.register_class(CONTRACT_OT_undo_probe)
        try:
            self.assertEqual(bpy.ops.gs_contract.undo_probe(), {"FINISHED"})
            self.assertEqual(set(bpy.ops.gs_contract.undo_probe.bl_options), {"REGISTER", "UNDO"})
        finally:
            bpy.utils.unregister_class(CONTRACT_OT_undo_probe)
