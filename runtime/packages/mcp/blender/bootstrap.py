# SPDX-License-Identifier: MIT
"""Run by Blender when gamestudio-mcp launches it:

    blender [--background] [scene.blend] --python bootstrap.py -- --gamestudio-bridge [--serve --parent-pid N]

Makes sure the GameStudio extension is enabled for this session (without saving preferences)
and starts its agent bridge. With --serve (background sessions, where timers never fire) it
serves requests until the MCP server that launched it exits or asks it to quit.
"""

import importlib
import sys

import addon_utils
import bpy


def _args() -> list:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _gamestudio_module():
    """The enabled GameStudio add-on module, enabling it for this session if needed."""
    for name in list(bpy.context.preferences.addons.keys()):
        if name == "gamestudio" or name.endswith(".gamestudio"):
            module = sys.modules.get(name)
            if module is not None:
                return module
    for module in addon_utils.modules(refresh=True):
        name = module.__name__
        if name == "gamestudio" or name.endswith(".gamestudio"):
            addon_utils.enable(name, default_set=False, persistent=True)
            if name in sys.modules:
                return sys.modules[name]
    return None


def main() -> int:
    args = _args()
    module = _gamestudio_module()
    if module is None:
        print("gamestudio-mcp bootstrap: the GameStudio extension is not installed in this Blender profile",
              file=sys.stderr, flush=True)
        return 1
    bridge = importlib.import_module(module.__name__ + ".bridge")
    if "--serve" in args:
        parent = None
        if "--parent-pid" in args:
            try:
                parent = int(args[args.index("--parent-pid") + 1])
            except (IndexError, ValueError):
                parent = None
        bridge.serve_forever(parent_pid=parent)
        return 0
    # A session launched for an agent: start the bridge even if the preference is off.
    if bridge.start(force=True) is None:
        print("gamestudio-mcp bootstrap: the agent bridge could not start", file=sys.stderr, flush=True)
        return 1
    return 0


code = main()
if code and bpy.app.background:
    sys.exit(code)
