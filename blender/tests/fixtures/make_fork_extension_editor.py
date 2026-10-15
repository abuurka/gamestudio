# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the fork-file fixture used by blender/tests/contract/test_file_compat.py.

Output (next to this script):
  fork_extension_editor.blend   saved by a GameStudio Editor build
  fork_extension_editor.json    what each area of its "GSContract" workspace must open as,
                                in the fork and in stock Blender

The fixture backs the data guarantee in docs/fork/ARCHITECTURE.md section 8: "Fork-saved files open in
stock Blender. Extension areas become Info editors and nothing crashes" (research/10 section 3).
It contains, in the window's active workspace (renamed "GSContract"):
  area 0  an Extension Editor as the *active* space, showing provider GAMESTUDIO_CONTRACT
          -> stock Blender: read as an empty area (type ''), which the UI opens as a 3D Viewport
  area 1  an Extension Editor in the area's *history*, with an Outliner active on top
          -> stock Blender: still an Outliner (the history entry is dropped)
  others  untouched factory areas -> the same type in both
It contains no workspace tools for the extension editor (research/10 section 3.3).

How to make it (GameStudio Editor build only; never with your real profile):
  1. Build the editor with the extension-editor seams (S05-S08): `python editor/tools/gsfork.py build`.
  2. Run, from the repository root:
       set BLENDER_USER_RESOURCES=%TEMP%\\gs_fixture_profile
       <gs-build>\\bin\\blender.exe --background --factory-startup --app-template default ^
           --python-exit-code 1 --python blender/tests/fixtures/make_fork_extension_editor.py
  3. Check both files in. Re-make them only when the SpaceExtension DNA changes; the point of
     the fixture is that *old* fork files keep opening, so never regenerate it to make a test pass.

The contract suite runs the stock-Blender check whenever the .blend exists: on stock Blender
with the running binary, on a fork build with GS_STOCK_BLENDER (or the default 5.x install).
"""

import json
import os
import pathlib
import sys

import bpy

HERE = pathlib.Path(__file__).resolve().parent
BLEND = HERE / "fork_extension_editor.blend"
SIDECAR = HERE / "fork_extension_editor.json"
WORKSPACE = "GSContract"
PROVIDER = "GAMESTUDIO_CONTRACT"


def fail(msg):
    print(f"FIXTURE_ERROR {msg}")
    sys.exit(1)


def set_type(window, screen, area, space_type):
    with bpy.context.temp_override(window=window, screen=screen, area=area):
        area.type = space_type


def main():
    if not os.environ.get("BLENDER_USER_RESOURCES"):
        fail("set BLENDER_USER_RESOURCES to a scratch folder (never use your real profile)")
    try:
        import _gamestudio
    except ImportError:
        fail("this is stock Blender; run the script with a GameStudio Editor build")
    _gamestudio.register_editor(PROVIDER, "Contract Fixture", "WINDOW", "Provider used by the contract fixture")

    window = bpy.context.window_manager.windows[0]
    workspace = window.workspace
    workspace.name = WORKSPACE
    screen = workspace.screens[0]
    areas = list(screen.areas)
    if len(areas) < 2:
        fail("the factory workspace needs at least two areas")
    original = [area.type for area in areas]

    active_ext, history_ext = areas[0], areas[1]
    set_type(window, screen, active_ext, "EXTENSION_EDITOR")
    active_ext.spaces.active.provider_idname = PROVIDER

    set_type(window, screen, history_ext, "EXTENSION_EDITOR")
    history_ext.spaces.active.provider_idname = PROVIDER
    set_type(window, screen, history_ext, "OUTLINER")

    for tool_owner in bpy.data.workspaces:
        for tool in getattr(tool_owner, "tools", []):
            if getattr(tool, "space_type", "") == "EXTENSION_EDITOR":
                fail("workspace tools for the extension editor would crash stock Blender (research/10 3.3)")

    expected = []
    for index, area in enumerate(screen.areas):
        entry = {"index": index, "fork_type": area.type, "stock_type": area.type}
        if area == active_ext:
            # Stock Blender reads an unknown space type as an empty area (blenkernel screen.cc,
            # area read: SPACE_EMPTY), shown as '' here; ED_area_init then opens it as a 3D Viewport.
            entry.update(stock_type="", stock_ui_type="VIEW_3D", provider_idname=PROVIDER)
        elif area == history_ext:
            entry.update(history=[space.type for space in area.spaces])
        else:
            entry["stock_type"] = original[index]
        expected.append(entry)

    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND), copy=True)
    SIDECAR.write_text(json.dumps({
        "workspace": WORKSPACE,
        "provider_idname": PROVIDER,
        "areas": expected,
        "made_with": bpy.app.version_string,
        "gamestudio": _gamestudio.version(),
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"FIXTURE_OK {BLEND.name} areas={[(e['fork_type'], e['stock_type']) for e in expected]}")


main()
