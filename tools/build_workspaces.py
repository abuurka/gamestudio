# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate blender/gamestudio/resources/workspaces.blend, the Game workspace that GameStudio adds
(blender/gamestudio/workspaces.py).

    python tools/build_workspaces.py [--blender PATH]

The file is a build output: reproducible, generated from Blender's factory startup file (rerun
this after a Blender upgrade) and never saved by hand. It holds stock editors only, so any Blender
5.2+ opens it as saved. The Game workspace is Blender's Layout with its Timeline turned into a Text
Editor, the game's Console:

  3D Viewport (sidebar closed) | Outliner
  -----------------------------| Properties (Object tab)
  Text Editor: Game Console    |

Some settings only take at runtime (the Console's text, its height, the Object tab when an object
is active), so the workspace stores them (workspaces.LAYOUT_PROP) and the extension applies them
when it is added or first shown. It also stores workspaces.VERSION (STATE_PROP): bump VERSION in
workspaces.py when this layout changes, and GameStudio refreshes the copies users have not changed.

Run with plain Python, it starts Blender in the background with factory settings and a scratch
profile, and runs itself inside it.
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = REPO / "blender" / "gamestudio" / "resources" / "workspaces.blend"
WORKSPACES_PY = REPO / "blender" / "gamestudio" / "workspaces.py"
LAYOUT_PROP = "gamestudio_layout"
STATE_PROP = "gamestudio_workspace"
SOURCE, NAME = "Layout", "Game"
# Area keys (workspaces.find_area): "TYPE" is the largest area of that type.
LAYOUT = {"PROPERTIES": {"context": "OBJECT"}, "TEXT_EDITOR": {"console": True, "height": 0.27}}
AREAS = {"VIEW_3D", "OUTLINER", "PROPERTIES", "TEXT_EDITOR"}


def version() -> int:
    """workspaces.VERSION, read from the source (the extension is not loaded here)."""
    match = re.search(r"^VERSION = (\d+)", WORKSPACES_PY.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise SystemExit(f"BUILD_WORKSPACES_ERROR no VERSION in {WORKSPACES_PY}")
    return int(match.group(1))


def build_in_blender(output: pathlib.Path) -> None:
    import bpy

    def fail(message):
        print(f"BUILD_WORKSPACES_ERROR {message}")
        sys.exit(1)

    window = bpy.context.window_manager.windows[0]
    if SOURCE not in bpy.data.workspaces:
        fail(f"factory workspace '{SOURCE}' is missing (Blender changed its startup file?)")
    window.workspace = bpy.data.workspaces[SOURCE]
    bpy.data.batch_remove(ids=[ws for ws in bpy.data.workspaces if ws.name != SOURCE])
    ws = bpy.data.workspaces[SOURCE]
    ws.name = NAME

    # --background switches a window's workspace later, in the event loop it never runs; the area
    # changes happen on the workspace's own screen.
    screen = ws.screens[0]
    timeline = next((a for a in screen.areas if a.type == "DOPESHEET_EDITOR"), None)
    if timeline is None:
        fail("the Layout workspace has no Timeline (Blender changed its startup file?)")
    with bpy.context.temp_override(window=window, screen=screen, area=timeline):
        timeline.ui_type = "TEXT_EDITOR"
    console = timeline.spaces.active
    console.show_line_numbers = False
    console.show_syntax_highlight = False
    console.show_word_wrap = True
    console.show_region_footer = False
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = False
        elif area.type == "PROPERTIES":
            area.spaces.active.context = "OBJECT"  # while the factory Cube is active
    kinds = sorted(a.type for a in screen.areas)
    if set(kinds) != AREAS or len(kinds) != len(AREAS):
        fail(f"workspace '{NAME}' has the areas {kinds}, expected one each of {sorted(AREAS)}")
    ws[LAYOUT_PROP] = LAYOUT
    ws[STATE_PROP] = {"version": version()}

    # Workspaces are appended on their own; keep the file free of scene data they could drag along.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), copy=True, compress=True)
    layout = {w.name: [a.type for a in w.screens[0].areas] for w in bpy.data.workspaces}
    print(f"BUILD_WORKSPACES_OK {json.dumps(layout)} v{version()} -> {output}")


def run_blender(blender: str | None) -> int:
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev

    exe = blender_dev.find_blender(blender)
    with tempfile.TemporaryDirectory(prefix="gs_build_workspaces_") as profile:
        env = dict(os.environ, BLENDER_USER_RESOURCES=profile)
        cmd = [str(exe), "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(pathlib.Path(__file__).resolve()), "--", "--output", str(OUTPUT)]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [line for line in (result.stdout + result.stderr).splitlines() if "BUILD_WORKSPACES" in line]
    print("\n".join(lines) or result.stdout + result.stderr)
    return 0 if result.returncode == 0 and any("BUILD_WORKSPACES_OK" in line for line in lines) else 1


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    try:
        import bpy  # noqa: F401
    except ImportError:
        import argparse

        parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        parser.add_argument("--blender", help="blender executable (default: found automatically)")
        return run_blender(parser.parse_args(argv).blender)
    output = pathlib.Path(argv[argv.index("--output") + 1]) if "--output" in argv else OUTPUT
    build_in_blender(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
