# SPDX-License-Identifier: GPL-3.0-or-later
"""Benchmark GameStudio's Play/export pipeline on a heavy, realistic level.

  python tools/bench_play.py                         # defaults: 12 rigs (9 share a skeleton), 7 clips, 200 props
  python tools/bench_play.py --rigs 4 --props 60     # a smaller level
  python tools/bench_play.py --profile               # also cProfile a warm full and a mesh-edit export
  python tools/bench_play.py --json out.json         # where the results go (default: <temp>/gs_bench_play.json)
  python tools/bench_play.py --browser               # also time the game load in a headless browser

A scratch Blender (--background --factory-startup, its own throw-away profile and TEMP; never your
settings) runs tools/bench/play_blender.py. It creates a temporary GameStudio project the way the
add-on tests do, builds a level (kit props with palette, procedural and 2048² image materials,
prefab instances, the rigged kit character and Mixamo-like rigs with shared skeletons and clips),
saves it as scenes/main.blend, opens it again and measures:

  a  cold full export (export_scene_to, empty caches)    e  export_scene() after a component edit
  b  warm full export (caches populated)                 f  ... after editing one mesh
  c  export_scene() with nothing changed                 g  ... after changing one material colour
  d  ... after moving one object                         h  ... after editing one action's keyframe

each as wall time, the tier export_scene() chose, the result's own timings (bake, clips, sizes) and
the time spent in the main export phases. With --browser, tools/bench/browser_load.mjs serves the
project with its own Vite dev server (its own free port and hub ports; never yours) and times
manifest + GLBs -> game ready in headless Edge/Chrome. Everything is created under one temp folder
that is deleted afterwards (node_modules junctions are unlinked first).
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import blender_dev

HERE = pathlib.Path(__file__).resolve().parent
BLENDER_SCRIPT = HERE / "bench" / "play_blender.py"
BROWSER_SCRIPT = HERE / "bench" / "browser_load.mjs"
FULL_ROWS = ("a", "b", "f", "g", "h", "h2")


# --- Cleanup -------------------------------------------------------------------------------------------


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def remove_tree(path: pathlib.Path) -> None:
    """rmtree that unlinks junctions and symlinks first and never follows them into their targets."""
    if not path.exists():
        return
    for dirpath, dirnames, _files in os.walk(path, topdown=True, followlinks=False):
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if _is_link(full):
                try:
                    os.rmdir(full) if sys.platform == "win32" else os.unlink(full)
                except OSError:
                    os.unlink(full)
                dirnames.remove(name)
    shutil.rmtree(path, ignore_errors=True)


# --- Running --------------------------------------------------------------------------------------------


def run_blender(exe, base: pathlib.Path, args) -> tuple:
    profile = base / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    env = blender_dev.dev_env(profile)
    env["GAMESTUDIO_TEMPLATES"] = str(profile / "templates")  # never the user's templates
    env["GS_EDITOR_KIND"] = "stock"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = base / "result.json"
    cmd = [str(exe), "--background", "--factory-startup", "--python-exit-code", "1",
           "--python", str(BLENDER_SCRIPT), "--",
           "--root", str(base / "work"), "--out", str(result),
           "--rigs", str(args.rigs), "--clips", str(args.clips), "--props", str(args.props),
           "--procedural", str(args.procedural)]
    if args.shared is not None:
        cmd += ["--shared", str(args.shared)]
    if args.profile:
        cmd += ["--profile"]
    log_path = base / "blender.log"
    started = time.perf_counter()
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, env=env, cwd=blender_dev.REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            log.write(line)
            if line.startswith("BENCH ") or "Traceback" in line or "Error" in line:
                print(f"  {line.rstrip()}", flush=True)
        code = proc.wait(timeout=args.timeout)
    seconds = time.perf_counter() - started
    data = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else None
    if code != 0 or data is None:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        print("[bench] Blender failed; the end of its log:\n  " + "\n  ".join(tail), flush=True)
    return code, data, seconds


def run_browser(data: dict, base: pathlib.Path, args) -> dict:
    node = shutil.which("node")
    if node is None:
        return {"error": "n/a: node not found"}
    if not BROWSER_SCRIPT.is_file():
        return {"error": "n/a: tools/bench/browser_load.mjs missing"}
    out = base / "browser.json"
    cmd = [node, str(BROWSER_SCRIPT), "--project", data["project"], "--scene", "main",
           "--loads", str(args.loads), "--out", str(out)]
    print("[bench] browser: " + " ".join(cmd[1:]), flush=True)
    try:
        proc = subprocess.run(cmd, cwd=blender_dev.REPO, text=True, capture_output=True, timeout=600,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": "browser run timed out"}
    for line in (proc.stdout or "").splitlines():
        print(f"  {line}")
    if proc.returncode != 0 or not out.is_file():
        tail = ((proc.stderr or "") + (proc.stdout or "")).splitlines()[-15:]
        return {"error": f"browser run failed ({proc.returncode})", "log": tail}
    return json.loads(out.read_text(encoding="utf-8"))


# --- Reporting ------------------------------------------------------------------------------------------


def _ph(row, *names) -> float | None:
    phases = row.get("phases") or {}
    found = [phases[n]["s"] for n in names if n in phases]
    return sum(found) if found else None


def _fmt(value, digits=2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _mb(n) -> str:
    return "n/a" if not isinstance(n, (int, float)) else f"{n / 1048576:.1f}"


def row_cells(row: dict) -> list:
    if row.get("error") and "wall" not in row:
        return [row["key"], row.get("label", ""), "-", row["error"]] + [""] * 8
    bake = row.get("bake") or {}
    clips = row.get("clips")
    clip_cell = "n/a"
    if isinstance(clips, dict):
        clip_cell = (f"{_fmt(clips.get('seconds'))} ({clips.get('fast', '?')}f/{clips.get('sampled', '?')}s/"
                     f"{clips.get('cached', '?')}c)")
    bake_cell = "n/a"
    if bake:
        bake_cell = f"{_fmt(bake.get('seconds'))} ({bake.get('baked') or 0}b/{bake.get('cached') or 0}c)"
    size = row.get("size") or {}
    tier = row.get("tier") or ("full*" if row["key"] in ("a", "b") else "n/a")
    if tier in ("reuse", "layout"):  # their bake and clips entries are the last full export's
        bake_cell = clip_cell = "-"
    return [
        row["key"], row.get("label", ""), _fmt(row.get("wall")), tier,
        _fmt(_ph(row, "gltf exporter: scene")), _fmt(_ph(row, "gltf exporter: prefabs")),
        bake_cell, clip_cell,
        _fmt(_ph(row, "fidelity.process_textures")),
        _fmt(_ph(row, "export._slim_glb", "fidelity.instance_meshes", "export.inject_ids", "fidelity.size_report")),
        _fmt(_ph(row, "journal.plan", "journal._snapshot")),
        _mb(size.get("bytes")),
    ]


HEADER = ["", "step", "wall s", "tier", "gltf scene", "gltf prefab", "bake s (b/c)", "clips s (f/s/c)",
          "textures", "post", "journal", "MB"]


def print_table(rows: list) -> None:
    table = [HEADER] + [row_cells(r) for r in rows]
    widths = [max(len(str(r[i])) for r in table) for i in range(len(HEADER))]
    for k, r in enumerate(table):
        print("  ".join(str(c).ljust(widths[i]) if i < 2 else str(c).rjust(widths[i]) for i, c in enumerate(r)))
        if k == 0:
            print("  ".join("-" * w for w in widths))


def print_report(data: dict, browser: dict | None) -> None:
    level = data.get("level", {})
    notes = data.get("notes", {})
    print(f"\nBlender {data.get('blender')}  level: {json.dumps(level)}")
    print(f"props: {notes.get('props_unique')} built + {notes.get('props_copies')} linked copies from "
          f"{', '.join(notes.get('kits', []))}; procedural: {len(notes.get('procedural_objects', []))}; "
          f"prefab instances: {notes.get('prefab_instances')}; rigs: {len(notes.get('rigs', []))} "
          f"({notes.get('shared_rigs')} share a skeleton); built in {notes.get('build_seconds')} s")
    print("features: " + ", ".join(f"{k}: {'yes' if v else 'n/a'}" for k, v in data.get("features", {}).items()))
    print()
    print_table(data.get("rows", []))
    print("  (full* = export_scene_to, which has no tier; post = slim/prune + instancing + ids + size report; "
          "journal = plan + snapshot)")
    for row in data.get("rows", []):
        extra = []
        if row.get("edit"):
            extra.append(row["edit"])
        if row.get("reason"):
            extra.append(f"reason: {row['reason']}")
        if row.get("error") and "wall" in row:
            extra.append(f"ERROR {row['error']}")
        if extra:
            print(f"  {row['key']}: " + "; ".join(extra))
    for row in data.get("rows", []):
        if row["key"] in FULL_ROWS and row.get("phases") and row.get("tier", "full") in (None, "full"):
            top = list(row["phases"].items())[:12]
            print(f"\nphases of {row['key']} ({row.get('label')}), inclusive s (nested phases overlap):")
            for name, item in top:
                print(f"  {item['s']:8.3f}  x{item['calls']:<5d} {name}")
    extras = data.get("extras") or {}
    if extras:
        print("\nextras: " + json.dumps(extras))
    for key in ("profile_b", "profile_f"):
        prof = data.get(key)
        if not prof or "profile" not in prof:
            continue
        print(f"\ncProfile of {prof['key']} ({prof['label']}), wall {prof.get('wall')} s; top 25 by cumulative:")
        print(f"  {'cum s':>8} {'self s':>8} {'calls':>9}  function")
        for item in prof["profile"]["cumulative"]:
            print(f"  {item['cum']:8.3f} {item['tot']:8.3f} {item['calls']:9d}  {item['func']}")
        print("  top 12 by own time:")
        for item in prof["profile"]["tottime"][:12]:
            print(f"  {item['cum']:8.3f} {item['tot']:8.3f} {item['calls']:9d}  {item['func']}")
    if browser is not None:
        if browser.get("error"):
            print(f"\nbrowser: {browser['error']}")
        else:
            print(f"\nbrowser ({browser.get('browser')}, headless, software WebGL): page ready cold "
                  f"{_fmt(browser.get('cold_ready_s'))} s, warm {_fmt(browser.get('warm_ready_s'))} s; scene load "
                  f"(manifest request -> game ready) cold {_fmt(browser.get('cold_scene_load_s'))} s, warm "
                  f"{_fmt(browser.get('warm_scene_load_s'))} s")
            for item in (browser.get("loads") or [{}])[-1].get("files", []):
                print(f"  {item['file']}: {_mb(item['bytes'])} MB in {item['seconds']:.3f} s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rigs", type=int, default=12, help="Mixamo-like rigs (default 12)")
    parser.add_argument("--shared", type=int, default=None, help="rigs sharing one skeleton and clips (default 3/4)")
    parser.add_argument("--clips", type=int, default=7, help="clips per rig (default 7)")
    parser.add_argument("--props", type=int, default=200, help="kit props (default 200)")
    parser.add_argument("--procedural", type=int, default=4, help="props with procedural materials (default 4)")
    parser.add_argument("--profile", action="store_true", help="cProfile a warm full export and a mesh-edit export")
    parser.add_argument("--browser", action="store_true", help="also time the game load in a headless browser")
    parser.add_argument("--loads", type=int, default=3, help="browser page loads (the first is cold; default 3)")
    parser.add_argument("--json", help="result file (default: <temp>/gs_bench_play.json)")
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    parser.add_argument("--keep", action="store_true", help="keep the temp folder (project, profile, logs)")
    parser.add_argument("--timeout", type=float, default=3600, help="seconds before Blender is given up on")
    args = parser.parse_args()
    exe = blender_dev.find_blender(args.blender)
    json_path = pathlib.Path(args.json) if args.json else pathlib.Path(tempfile.gettempdir()) / "gs_bench_play.json"
    base = pathlib.Path(tempfile.mkdtemp(prefix="gs_bench_play_"))
    print(f"[bench] {exe}\n[bench] temp folder {base}", flush=True)
    try:
        code, data, seconds = run_blender(exe, base, args)
        if data is None:
            return 1
        data["bench_seconds"] = round(seconds, 1)
        browser = None
        if args.browser:
            browser = run_browser(data, base, args)
            data["browser"] = browser
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print_report(data, browser)
        print(f"\n[bench] {seconds:.0f} s in Blender; results in {json_path}")
        return 0 if code == 0 else 1
    finally:
        if args.keep:
            print(f"[bench] kept {base}")
        else:
            remove_tree(base)


if __name__ == "__main__":
    sys.exit(main())
