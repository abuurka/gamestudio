# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract probe: report the state of a freshly started Blender session as JSON.

    blender -b --factory-startup [--app-template NAME] [FILE.blend] --python probe_session.py -- REPORT
"""

import json
import pathlib
import sys

import bpy


def area_info(area):
    return {
        "type": area.type,
        "ui_type": area.ui_type,
        "spaces": [space.type for space in area.spaces],
        "provider_idname": getattr(area.spaces.active, "provider_idname", None) if area.spaces.active else None,
    }


def main():
    report = pathlib.Path(sys.argv[sys.argv.index("--") + 1])
    prefs = bpy.context.preferences
    data = {
        "app_template": prefs.app_template,
        "filepath": bpy.data.filepath,
        "workspaces": sorted(ws.name for ws in bpy.data.workspaces),
        "objects": sorted(ob.name for ob in bpy.data.objects),
        "ui_scale": round(prefs.view.ui_scale, 4),
        "addons": sorted(prefs.addons.keys()),
        "modules": sorted(name for name in sys.modules if name.startswith(("bl_app_templates", "bl_ext."))),
        "screens": {
            ws.name: [area_info(area) for area in ws.screens[0].areas] for ws in bpy.data.workspaces if ws.screens
        },
    }
    report.write_text(json.dumps(data, indent=1), encoding="utf-8")


main()
