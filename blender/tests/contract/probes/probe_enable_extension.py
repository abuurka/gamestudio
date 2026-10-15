# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract probe: enable an extension by module name and report what Blender loaded.

    blender -b --factory-startup --python probe_enable_extension.py -- REPORT MODULE
"""

import json
import pathlib
import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    report, module = pathlib.Path(argv[0]), argv[1]
    prefs = bpy.context.preferences
    repos = [{"name": r.name, "module": r.module, "directory": r.directory, "source": r.source}
             for r in prefs.extensions.repos]
    result = bpy.ops.preferences.addon_enable(module=module)
    loaded = sys.modules.get(module)
    data = {
        "repos": repos,
        "result": sorted(result),
        "enabled": module in prefs.addons,
        "in_sys_modules": loaded is not None,
        "module_file": getattr(loaded, "__file__", None),
        "registered": bool(getattr(loaded, "REGISTERED", False)),
        "package": getattr(loaded, "__package__", None),
    }
    report.write_text(json.dumps(data, indent=1), encoding="utf-8")


main()
