# SPDX-License-Identifier: GPL-3.0-or-later
"""The extension editor added by seams S05-S08 and editor/native (GameStudio Editor builds only).

On stock Blender every test here skips: the shell then falls back to sidebar tabs
(gamestudio.host reports no "extension_editors" capability).
"""

import shutil
import unittest

import bpy

import contract_support as cs


@cs.requires_fork
class ExtensionEditorTest(unittest.TestCase):
    def test_space_extension_editor_type_exists(self):
        """`bpy.types.SpaceExtensionEditor` exists, derives from Space and has `provider_idname`.

        Breaks if changed: seam S07 (rna_space.cc); Python providers read
        `context.space_data.provider_idname` to decide what to draw.
        """
        cls = getattr(bpy.types, "SpaceExtensionEditor", None)
        self.assertIsNotNone(cls)
        self.assertEqual(cls.bl_rna.base.identifier, "Space")
        self.assertIn("provider_idname", cls.bl_rna.properties.keys())

    def test_area_type_enum_has_extension_editor(self):
        """'EXTENSION_EDITOR' is an Area.type value (and a valid bl_space_type).

        Breaks if changed: seams S05/S07; Python panels and headers with
        bl_space_type='EXTENSION_EDITOR' fail to register, and the shell cannot open the editor.
        """
        self.assertIn("EXTENSION_EDITOR", cs.enum_identifiers(bpy.types.Area, "type"))
        self.assertIn("EXTENSION_EDITOR", cs.enum_identifiers(bpy.types.Panel, "bl_space_type"))

    def test_provider_idname_round_trip(self):
        """`provider_idname` survives save and load.

        Breaks if changed: SpaceExtension blend write/read (editor/native/blender/space_extension.cc);
        every saved IDE layout would reopen with empty extension editors.
        """
        scratch = cs.make_tmp_dir("gs_contract_roundtrip_")
        try:
            report = cs.run_probe("probe_provider_roundtrip", scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertEqual(report["before"], {"type": "EXTENSION_EDITOR", "provider_idname": "GS_CONTRACT_ROUNDTRIP"})
        self.assertEqual(report["after"], report["before"])

    def test_hidden_editor_types(self):
        """After `_gamestudio.set_hidden_editor_types(['SEQUENCE_EDITOR'])` the editor-type menu omits it.

        Breaks if changed: seam S08 (rna_screen.cc `RNA_area_space_type_hidden_fn`) and the shell's
        editor-visibility policy; hidden editors would reappear in every area's editor-type menu.
        """
        native = cs.native_module()
        window = cs.any_window()
        area = next(a for a in window.screen.areas if a.type not in ("SEQUENCE_EDITOR", "TOPBAR", "STATUSBAR"))
        self.assertIn("SEQUENCE_EDITOR", cs.area_ui_type_items(area))
        native.set_hidden_editor_types(["SEQUENCE_EDITOR"])
        try:
            self.assertNotIn("SEQUENCE_EDITOR", cs.area_ui_type_items(area))
        finally:
            native.set_hidden_editor_types([])
        self.assertIn("SEQUENCE_EDITOR", cs.area_ui_type_items(area))
