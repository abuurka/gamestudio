# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract probe: register bpy.app.timers that would write MARKER, then let the script end.

    blender -b --factory-startup --python probe_timer.py -- REPORT MARKER
"""

import json
import pathlib
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
REPORT, MARKER = pathlib.Path(argv[0]), pathlib.Path(argv[1])


def fire(kind):
    def callback():
        with open(MARKER, "a", encoding="utf-8") as f:
            f.write(kind + "\n")
        return None
    return callback


once = fire("once")
persistent = fire("persistent")
bpy.app.timers.register(once, first_interval=0.0)
bpy.app.timers.register(persistent, first_interval=0.0, persistent=True)
REPORT.write_text(json.dumps({
    "registered": [bpy.app.timers.is_registered(once), bpy.app.timers.is_registered(persistent)],
    "background": bpy.app.background,
}), encoding="utf-8")
