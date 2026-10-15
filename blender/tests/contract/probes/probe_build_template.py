# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract probe: build a minimal app template from factory data.

    blender -b --factory-startup --python probe_build_template.py -- REPORT TEMPLATE_DIR

Writes TEMPLATE_DIR/startup.blend (workspace "Layout" renamed to "GSContractWorkspace", plus an
empty named "GSContractMarker"), TEMPLATE_DIR/userpref.blend (ui_scale 1.25) and an __init__.py
whose register() writes {"registered": true, "data_type": ...} to $GS_CONTRACT_TEMPLATE_MARKER.
Run only with a scratch BLENDER_USER_RESOURCES: it saves preferences.
"""

import json
import os
import pathlib
import shutil
import sys

import bpy

INIT = '''
import json
import os

import bpy


def register():
    path = os.environ.get("GS_CONTRACT_TEMPLATE_MARKER")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"registered": True, "data_type": type(bpy.data).__name__}, f)


def unregister():
    pass
'''


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    report, template = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    if not os.environ.get("BLENDER_USER_RESOURCES"):
        raise SystemExit("refusing to save preferences without a scratch BLENDER_USER_RESOURCES")
    template.mkdir(parents=True, exist_ok=True)

    bpy.data.workspaces["Layout"].name = "GSContractWorkspace"
    marker = bpy.data.objects.new("GSContractMarker", None)
    bpy.context.scene.collection.objects.link(marker)
    bpy.ops.wm.save_as_mainfile(filepath=str(template / "startup.blend"), copy=True)

    bpy.context.preferences.view.ui_scale = 1.25
    bpy.ops.wm.save_userpref()
    saved = pathlib.Path(bpy.utils.user_resource("CONFIG")) / "userpref.blend"
    shutil.copy2(saved, template / "userpref.blend")

    (template / "__init__.py").write_text(INIT, encoding="utf-8")
    report.write_text(json.dumps({"files": sorted(p.name for p in template.iterdir())}), encoding="utf-8")


main()
