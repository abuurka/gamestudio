# SPDX-License-Identifier: GPL-3.0-or-later
"""bpy.app.timers in --background.

Every GameStudio module that does periodic work (core/registry, link, live, devserver, play,
assistant/agent) runs it from a `pump()` function that a bpy.app.timers callback calls. Headless
Blender never runs timers, so the add-on tests and headless tools call `pump()` themselves.
"""

import shutil
import unittest

import contract_support as cs


class BackgroundTimersTest(unittest.TestCase):
    def test_timers_do_not_fire_in_background(self):
        """Timers registered from a `--background --python` script never fire; Blender exits first.

        Breaks if changed: the add-on test suite and headless tools assume that only their own
        explicit `pump()` calls run the live link, registry watch and dev-server polling. If
        timers start firing headless, those pumps run twice and on their own schedule, and tests
        that count messages or rebuilds (blender/tests/addon/test_live.py, test_link.py) get flaky.
        """
        folder = cs.make_tmp_dir("gs_contract_timer_")
        marker = folder / "fired.txt"
        try:
            report = cs.run_probe("probe_timer", marker)
            self.assertTrue(report["background"])
            self.assertEqual(report["registered"], [True, True], "timers were not registered")
            fired = marker.read_text(encoding="utf-8").split() if marker.exists() else []
            self.assertEqual(fired, [], f"timers fired in --background: {fired}")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
