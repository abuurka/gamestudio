# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender half of the template end-to-end test (tools/e2e/templates_e2e.py starts it).

New Project for each project template, the way a user makes one: the GameStudio extension from this
checkout is enabled as a system extension, then projects.create_project() scaffolds the template
and runs the setup job (link the shared packages, `npm install` for anything missing, `npm run
registry`) and, when it is done, the template's level script. Blender timers do not run in
--background, so environment.wait() drives the job.

    blender --background --factory-startup --online-mode --python templates_e2e_blender.py -- OUT [template ...]

Prints "TEMPLATE <id> OK <seconds> <project>" or "TEMPLATE <id> FAIL <why>" per template.
"""

import importlib
import json
import pathlib
import sys
import time

import bpy

EXTENSION = "bl_ext.system.gamestudio"
# The screens a template's own tools/build_ui.py builds (ui/ui.blend), as its export's index lists them.
UI_SCREENS = {"shooter": {"Title", "Pause", "Options", "GameOver", "HUD"},
              "platformer": {"Title", "Pause", "Options", "Win", "GameOver", "HUD"},
              "runner": {"Title", "Pause", "Options", "GameOver", "HUD"},
              "driving": {"Title", "Pause", "Options", "Results", "HUD"}}

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not args:
    sys.exit("usage: ... -- OUT [template ...]")
out, wanted = pathlib.Path(args[0]), set(args[1:])
out.mkdir(parents=True, exist_ok=True)

if EXTENSION not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module=EXTENSION)
projects = importlib.import_module(f"{EXTENSION}.projects")
environment = importlib.import_module(f"{EXTENSION}.environment")


def check(root: pathlib.Path, info: dict) -> str:
    """Why the new project is not ready to play, or ""."""
    config = json.loads((root / "gamestudio.json").read_text(encoding="utf-8"))
    scene = config.get("startScene", "main")
    manifest = root / ".gamestudio" / "export" / f"{scene}.scene.json"
    for path in (root / "scenes" / f"{scene}.blend", manifest, root / ".gamestudio" / "components.json"):
        if not path.is_file():
            return f"missing {path.relative_to(root)}"
    exported = json.loads(manifest.read_text(encoding="utf-8"))
    # The player is in the level, or a prefab the game spawns (the runner's, at its PlayerStart).
    entities = exported.get("entities", []) + [e for p in exported.get("prefabs", []) for e in p.get("entities", [])]
    if not any({"CharacterController", "Car", "Runner"} & set(e.get("components", {})) for e in entities):
        return "the exported scene has no player"
    # The starter's menus and HUD are Blender screens (ui/ui.blend), exported for the game, and so are those
    # of a template with its own tools/build_ui.py; the others keep their code menus and get no UI file.
    index = root / ".gamestudio" / "export" / "ui" / "index.json"
    if info.get("starter_ui"):
        if not index.is_file():
            return "no game UI export (.gamestudio/export/ui/index.json)"
        canvases = set(json.loads(index.read_text(encoding="utf-8")).get("canvases", {}))
        if canvases != {"Title", "Pause", "Options", "Win", "HUD"}:
            return f"the game UI has the screens {sorted(canvases)}"
    elif (root / "tools" / "build_ui.py").is_file():
        if not index.is_file():
            return "no game UI export (.gamestudio/export/ui/index.json)"
        canvases = set(json.loads(index.read_text(encoding="utf-8")).get("canvases", {}))
        if not canvases or canvases != UI_SCREENS.get(info["id"], canvases):
            return f"the game UI has the screens {sorted(canvases)}"
    elif (root / "ui").exists() or index.exists():
        return "a game UI it should not have"
    return ""


failed = 0
for info in projects.templates():
    if wanted and info["id"] not in wanted:
        continue
    root = out / info["id"]
    began = time.monotonic()
    try:
        problem = projects.create_project(root, f"{info['label']} Test", info["id"])
    except (OSError, ValueError) as exc:
        problem = str(exc)
    if not problem:
        if not environment.wait(900):
            problem = environment.JOB.message
        elif not environment.JOB.message.startswith("Project ready"):
            problem = f"the job ended with: {environment.JOB.message}"
        else:
            problem = check(root, info)
    if problem:
        failed += 1
        print(f"TEMPLATE {info['id']} FAIL {problem}", flush=True)
    else:
        print(f"TEMPLATE {info['id']} OK {time.monotonic() - began:.1f} {root}", flush=True)
    environment.JOB.__init__()

sys.exit(1 if failed else 0)
