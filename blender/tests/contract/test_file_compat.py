# SPDX-License-Identifier: GPL-3.0-or-later
"""Data guarantee: files saved by GameStudio Editor open in stock Blender (docs/fork/ARCHITECTURE.md section 8).

An area whose space type stock Blender does not know (the fork's SpaceExtension) must open without
crashing: it is read as an empty area, which the UI opens as a 3D Viewport. An extension editor
kept in an area's history is dropped. This needs
a file saved by the fork: blender/tests/fixtures/fork_extension_editor.blend, made by
blender/tests/fixtures/make_fork_extension_editor.py (its docstring says how). Until a fork build
exists, the fixture is absent and these tests skip.
"""

import json
import unittest

import contract_support as cs

FIXTURE = cs.FIXTURES / "fork_extension_editor.blend"
SIDECAR = cs.FIXTURES / "fork_extension_editor.json"
NO_FIXTURE = f"fixture {FIXTURE.name} not made yet (see blender/tests/fixtures/make_fork_extension_editor.py)"


def _expected() -> dict:
    if not SIDECAR.is_file():
        raise AssertionError(f"{SIDECAR.name} is missing; regenerate both fixture files together")
    return json.loads(SIDECAR.read_text(encoding="utf-8"))


@unittest.skipUnless(FIXTURE.is_file(), NO_FIXTURE)
class ForkFileInStockBlenderTest(unittest.TestCase):
    def test_unknown_space_opens_as_empty_area(self):
        """Stock Blender opens a fork file without a crash; every other area is unchanged.

        The active extension editor is read as an empty area (type ''), which Blender's area init
        then opens as a 3D Viewport (`stock_ui_type`). Research/10 predicted an Info editor from
        code reading; this test, against the real binary, showed otherwise.

        Breaks if changed: the G5 data-compatibility promise (users can open GameStudio projects in
        plain Blender), and the choice of a separate DNA struct for SpaceExtension (research/10
        section 3). If Blender starts dropping or crashing on such areas, the native editor design
        must change before the next release.
        """
        stock = cs.stock_blender_for_compat()
        if stock is None:
            self.skipTest("no stock Blender found; set GS_STOCK_BLENDER")
        expected = _expected()
        session = cs.run_probe("probe_session", binary=stock, blend_file=FIXTURE)  # raises on a crash
        areas = session["screens"].get(expected["workspace"])
        self.assertIsNotNone(areas, f"workspace {expected['workspace']!r} missing: {sorted(session['screens'])}")
        self.assertEqual([a["type"] for a in areas], [e["stock_type"] for e in expected["areas"]])


@unittest.skipUnless(FIXTURE.is_file(), NO_FIXTURE)
@cs.requires_fork
class ForkFileInForkTest(unittest.TestCase):
    def test_fork_reopens_its_own_file(self):
        """The fork reopens the fixture with its extension editors and provider ids intact.

        Breaks if changed: SpaceExtension blend read/write (editor/native/blender/space_extension.cc)
        or seams S05/S07; saved IDE layouts would lose their Game View, Inspector and Assistant.
        """
        expected = _expected()
        session = cs.run_probe("probe_session", blend_file=FIXTURE)
        areas = session["screens"][expected["workspace"]]
        self.assertEqual([a["type"] for a in areas], [e["fork_type"] for e in expected["areas"]])
        for area, entry in zip(areas, expected["areas"]):
            if entry.get("provider_idname"):
                self.assertEqual(area["provider_idname"], entry["provider_idname"])
