# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract probe (GameStudio Editor only): save and reload an extension editor's provider id.

    blender -b --factory-startup --python probe_provider_roundtrip.py -- REPORT SCRATCH_DIR
"""

import json
import pathlib
import sys

import bpy

import _gamestudio

PROVIDER = "GS_CONTRACT_ROUNDTRIP"


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    report, scratch = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    _gamestudio.register_editor(PROVIDER, "Contract Round Trip")
    window = bpy.context.window_manager.windows[0]
    screen = window.screen
    area = screen.areas[0]
    with bpy.context.temp_override(window=window, screen=screen, area=area):
        area.type = "EXTENSION_EDITOR"
    area.spaces.active.provider_idname = PROVIDER
    before = {"type": area.type, "provider_idname": area.spaces.active.provider_idname}

    path = scratch / "provider_roundtrip.blend"
    screen_name = screen.name
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=True)
    area = bpy.data.screens[screen_name].areas[0]
    after = {"type": area.type, "provider_idname": getattr(area.spaces.active, "provider_idname", None)}
    report.write_text(json.dumps({"before": before, "after": after}), encoding="utf-8")


main()
