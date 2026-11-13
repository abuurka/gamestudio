# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for editor/tools/api_diff.py's enum checks (no Blender needed).

    python editor/tests/tools/test_api_diff.py          (system Python 3.12, no pytest)
"""

import importlib.util
import pathlib
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "api_diff.py"
spec = importlib.util.spec_from_file_location("api_diff", TOOL)
api_diff = importlib.util.module_from_spec(spec)
sys.modules["api_diff"] = api_diff
spec.loader.exec_module(api_diff)


class EnumValueTests(unittest.TestCase):
    def test_valid_in_both(self):
        self.assertEqual(api_diff._enum_value("A", ["A", "B"], ["A"]), [])

    def test_removed_value_is_a_break(self):
        self.assertEqual(api_diff._enum_value("B", ["A", "B"], ["A"])[0][0], "BREAK")

    def test_run_time_enum_is_info(self):
        # RenderSettings.engine: static items list only BLENDER_EEVEE in 5.2 and 5.3 alike.
        found = api_diff._enum_value("BLENDER_WORKBENCH", ["BLENDER_EEVEE"], ["BLENDER_EEVEE"])
        self.assertEqual([severity for severity, _ in found], ["INFO"])

    def test_dynamic_enum_is_not_checked(self):
        self.assertEqual(api_diff._enum_value("X", None, None), [])


if __name__ == "__main__":
    unittest.main()
