#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio UI smoke test: tier 6 in research/11 section 6.2, step 6 of the upgrade procedure
(docs/fork/ARCHITECTURE.md section 7). Plain Python 3.12, standard library only.

It starts stock Blender *with a GUI* and lets ui_smoke_driver.py walk the GameStudio extension:
Blender's splash, the GameStudio launcher, Add GameStudio Workspaces and each GameStudio
workspace, every top-bar menu (opened, plus a deep draw of each submenu, GameStudio's included),
every 3D View sidebar tab GameStudio adds, and a small scenario (a project with an entity,
components, game logs and an assistant conversation). It catches what headless tests cannot: a
Python error while drawing a panel or menu, a broken workspace, play shortcuts missing from
menus, and sidebar tabs the workspaces did not open.

    python blender/tests/ui/run_ui_smoke.py --blender <blender.exe> [--out DIR]

Blender runs with --factory-startup and a fresh scratch BLENDER_USER_RESOURCES (and TEMP), so it
never touches the user's profile, and loads the extension from this checkout as a system
extension (BLENDER_SYSTEM_EXTENSIONS, tools/blender_dev.py); no app template. The driver enables
it as a user would. --enable-event-simulate makes the session ignore the real mouse and keyboard;
--no-window-focus keeps it from stealing focus.

Fails (exit 1) on any traceback or error line in Blender's output, any error the driver recorded,
or a missing or blank screenshot, and exits 2 on a timeout (the process tree is killed). Writes
<out>/index.html with every screenshot.
"""

import argparse
import html
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools"))
import blender_dev  # noqa: E402
DRIVER = HERE / "ui_smoke_driver.py"

EXPECTED_WORKSPACES = ("Layout", "Level", "Debug")  # Blender's first, then GameStudio's

# A screenshot is "blank" below these (driver stats, 0-255 luminance scale).
MIN_LUMA_STD = 4.0
MIN_COLORS = 24
MAX_DOMINANT_FRACTION = 0.97
# Runner-side check on the PNG itself: distinct byte values in the filtered scanlines. A
# single-colour image has a handful whatever the PNG filter; a UI screenshot has hundreds.
MIN_FILTERED_DISTINCT = 24

# Output lines that match ERROR_LINE but are not errors: (regex, why). Keep it short and explained.
# Blender 5.2 + the shell print none today. "Warning:" lines (e.g. wm.redraw_timer's) never match.
KNOWN_NOISE = []

# "Error: ...", "RuntimeError: ...", CLOG "ERROR" lines.
ERROR_LINE = re.compile(r"\b\w*Error:|\bERROR\b")
INJECTED = ("ui-smoke injected fault: appended menu draw", "ui-smoke injected fault: panel draw")
TRACEBACK = "Traceback (most recent call last)"

OUR_FILES = ("report.json", "report.json.tmp", "blender_stdout.txt", "blender_stderr.txt", "index.html",
             "summary.json")


# --- Running Blender ------------------------------------------------------------------


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_blender(args, out: pathlib.Path, profile: pathlib.Path) -> dict:
    width, height = (int(v) for v in args.size.lower().split("x"))
    tmp = profile / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for var in ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_EXTENSIONS",
                "BLENDER_USER_DATAFILES"):
        env.pop(var, None)  # each would override part of the scratch profile
    env.pop("BLENDER_SYSTEM_SCRIPTS", None)
    env.update({
        # The extension from this checkout, as a system extension (tools/blender_dev.py).
        "BLENDER_SYSTEM_EXTENSIONS": str(blender_dev.dev_extensions_dir()),
        # Never the user's profile, nor their quit.blend / autosave in the system temp folder.
        "BLENDER_USER_RESOURCES": str(profile),
        "TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp),
    })
    t0 = time.time()
    cmd = [str(args.blender), "--factory-startup", "--enable-event-simulate",
           "--window-geometry", "0", "0", str(width), str(height)]
    if not args.focus:
        cmd.append("--no-window-focus")
    cmd += ["--python-exit-code", "1", "--python", str(DRIVER), "--",
            "--out", str(out), "--kind", "stock" if args.stock else "fork",
            "--t0", f"{t0:.3f}", "--timeout", str(args.timeout)]
    if args.inject_fault:
        cmd.append("--inject-fault")
    print("[ui-smoke] " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    stdout_path, stderr_path = out / "blender_stdout.txt", out / "blender_stderr.txt"
    with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
        popen_kw = {} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen(cmd, env=env, stdout=so, stderr=se, stdin=subprocess.DEVNULL, **popen_kw)
        timed_out = False
        try:
            proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "seconds": round(time.time() - t0, 1),
        "stdout": stdout_path.read_text(encoding="utf-8", errors="replace"),
        "stderr": stderr_path.read_text(encoding="utf-8", errors="replace"),
    }


# --- Checks ---------------------------------------------------------------------------


def scan_log(text: str, stream: str) -> list:
    """Traceback blocks and error lines in Blender's output, minus KNOWN_NOISE."""
    problems, block = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith(TRACEBACK) or (block is None and TRACEBACK in line):
            block = {"stream": stream, "line": number, "text": [line]}
            continue
        if block is not None:
            block["text"].append(line)
            if line and not line[0].isspace():  # the exception line ends the traceback
                problems.append({"stream": stream, "line": block["line"], "kind": "traceback",
                                 "message": line.strip(), "text": "\n".join(block["text"])})
                block = None
            continue
        if line.startswith("[ui-smoke]"):
            continue  # the driver's own echo of an error that is already in report.json
        if ERROR_LINE.search(line) and not any(re.search(rx, line) for rx, _ in KNOWN_NOISE):
            problems.append({"stream": stream, "line": number, "kind": "error line", "message": line.strip(),
                             "text": line})
    if block is not None:
        problems.append({"stream": stream, "line": block["line"], "kind": "traceback",
                         "message": "(truncated traceback)", "text": "\n".join(block["text"])})
    return problems


def png_check(path: pathlib.Path) -> dict:
    """Validate a PNG with zlib/struct: chunk CRCs, size, scanline length, and a blank heuristic."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise ValueError(f"bad CRC in {ctype.decode('latin-1')} chunk")
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if ihdr is None or not idat:
        raise ValueError("missing IHDR or IDAT")
    width, height, depth, color, _comp, _filter, interlace = ihdr
    raw = zlib.decompress(b"".join(idat))
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if channels is None:
        raise ValueError(f"unknown PNG colour type {color}")
    if not interlace:
        expected = height * (1 + (width * channels * depth + 7) // 8)
        if len(raw) != expected:
            raise ValueError(f"image data is {len(raw)} bytes, expected {expected}")
    return {"png_width": width, "png_height": height, "filtered_distinct": len(set(raw[::3])),
            "compression": round(len(data) / max(1, len(raw)), 4)}


def check(args, run: dict, report: dict | None, out: pathlib.Path) -> list:
    """Every assertion; returns a list of failure messages."""
    fails = []
    if run["timed_out"]:
        step = (report or {}).get("steps", [{}])[-1:] or [{}]
        fails.append(f"timeout after {args.timeout}s (last finished step: {step[0].get('name', 'none')})")
    elif run["returncode"] != 0:
        fails.append(f"Blender exited with code {run['returncode']}")

    for problem in run["log_problems"]:
        fails.append(f"{problem['kind']} in {problem['stream']} line {problem['line']}: {problem['message']}")

    if report is None:
        # --python runs last at startup, so this is a hang or crash in Blender startup, or a machine
        # too loaded to start Blender within --timeout.
        fails.append("Blender never ran the driver (no report.json): startup hung or crashed, or the machine "
                     "is too loaded (see blender_stdout.txt / blender_stderr.txt)")
        return fails
    if not report.get("finished"):
        fails.append(f"the driver did not finish ({report.get('aborted') or 'no finish step'})")
    for error in report.get("errors", []):
        fails.append(f"driver: {error['source']} error in step {error['step']}: {error['message']}")
    if report.get("app_template"):
        fails.append(f"app template is {report.get('app_template')!r}; GameStudio needs none")
    if report.get("isolated_profile") is not True:
        fails.append("Blender did not use the scratch profile")

    native = report.get("native")
    if args.stock and native:
        fails.append("--stock given, but the _gamestudio native module is present")
    if not args.stock:
        if native is not True:
            fails.append("fork run, but `import _gamestudio` failed: this build has no native module "
                         "(seams S05-S07 / WITH_GS_NATIVE missing?); custom editors were tested as sidebar tabs")
        titles = report.get("window_titles", {})
        all_titles = titles.get("start", []) + titles.get("end", [])
        if os.name == "nt" and not any("GameStudio" in t for t in all_titles):
            fails.append(f"window title does not contain 'GameStudio': {all_titles}")
        if report.get("providers", {}).get("mode") != "extension_editor":
            fails.append("providers were not opened as Extension Editors")

    visited = report.get("workspaces", {}).get("visited", [])
    for name in EXPECTED_WORKSPACES:
        if name not in visited:
            fails.append(f"workspace {name!r} was not visited")
    if not report.get("menus", {}).get("top_bar"):
        fails.append("no top bar menus were opened")
    if not report.get("providers", {}).get("visited"):
        fails.append("no custom editor / sidebar tab was shown")

    shots = report.get("screenshots", [])
    names = {s["name"] for s in shots}
    required = (["01_launcher"] + [f"10_ws_{w.lower()}" for w in EXPECTED_WORKSPACES]
                + ["40_level_entity", "41_sidebar_game_entity", "42_assistant_chat"])
    for name in required:
        if name not in names:
            fails.append(f"screenshot {name} is missing")
    if not any(n.startswith("20_menu_") for n in names):
        fails.append("no top bar menu screenshot")
    if not any(n.startswith("30_") for n in names):
        fails.append("no custom editor / sidebar screenshot")
    for shot in shots:
        path = out / shot["file"]
        if not path.is_file():
            fails.append(f"screenshot {shot['name']} is missing on disk")
            continue
        try:
            shot.update(png_check(path))
        except (ValueError, zlib.error, struct.error) as exc:
            fails.append(f"screenshot {shot['name']} is not a valid PNG: {exc}")
            continue
        blank = []
        if shot.get("luma_std", 0) < MIN_LUMA_STD:
            blank.append(f"luminance std {shot.get('luma_std')}")
        if shot.get("colors", 0) < MIN_COLORS:
            blank.append(f"{shot.get('colors')} colours")
        if shot.get("dominant_fraction", 1) > MAX_DOMINANT_FRACTION:
            blank.append(f"{shot.get('dominant_fraction'):.0%} one colour")
        if shot["filtered_distinct"] < MIN_FILTERED_DISTINCT:
            blank.append(f"{shot['filtered_distinct']} distinct filtered bytes")
        if shot["png_width"] < 200 or shot["png_height"] < 200:
            blank.append(f"only {shot['png_width']}x{shot['png_height']} px")
        if blank:
            shot["blank"] = blank
            fails.append(f"screenshot {shot['name']} looks blank: {', '.join(blank)}")
    return fails


# --- Index page -----------------------------------------------------------------------


def write_index(out: pathlib.Path, args, run: dict, report: dict | None, fails: list) -> pathlib.Path:
    e = html.escape
    report = report or {}
    status = "PASS" if not fails else "FAIL"
    host = report.get("host_info") or {}
    rows = [
        ("Result", status),
        ("Editor", "stock Blender + the GameStudio extension" if args.stock else "GameStudio Editor (fork)"),
        ("Binary", str(args.blender)),
        ("Blender", (report.get("blender") or {}).get("version_string", "?")),
        ("host.info()", json.dumps(host)),
        ("App template", str(report.get("app_template"))),
        ("Window titles", "; ".join((report.get("window_titles") or {}).get("end", [])) or "(none)"),
        ("Top bar menus", ", ".join((report.get("menus") or {}).get("top_bar", []))),
        ("Menus drawn (deep)", str(len((report.get("menus") or {}).get("deep_drawn", [])))),
        ("Providers / tabs", ", ".join((report.get("providers") or {}).get("visited", []))),
        ("Play shortcuts", ", ".join(f"{k}: {v}" for k, v in (report.get("shortcuts") or {}).items())),
        ("Workspace editors", "; ".join(f"{k}: {v}" for k, v in (report.get("layouts") or {}).items())),
        ("Run time", f"{run['seconds']} s (Blender exit code {run['returncode']}"
                     f"{', TIMED OUT' if run['timed_out'] else ''})"),
    ]
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>UI Smoke {status}</title><style>",
        ":root{--bg:#fff;--fg:#1d1d1f;--muted:#666;--card:#f4f4f6;--bad:#b3261e;--good:#1b7f3b}",
        "@media (prefers-color-scheme: dark){:root{--bg:#161618;--fg:#e8e8ea;--muted:#9a9aa0;--card:#232327;"
        "--bad:#ff8a80;--good:#7bd88f}}",
        "body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}",
        "h1{margin:0 0 8px}.PASS{color:var(--good)}.FAIL{color:var(--bad)}",
        "table{border-collapse:collapse;margin:8px 0 16px}td,th{padding:3px 10px;text-align:left;"
        "vertical-align:top;border-bottom:1px solid var(--card)}",
        "code,pre{font:12px/1.4 ui-monospace,Consolas,monospace}pre{background:var(--card);padding:8px;"
        "overflow:auto;max-height:240px}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}",
        "figure{margin:0;background:var(--card);padding:8px;border-radius:6px}",
        "figure img{width:100%;height:auto;display:block}figcaption{margin-top:6px}",
        ".muted{color:var(--muted)}.bad{color:var(--bad)}",
        "</style></head><body>",
        f"<h1>GameStudio UI smoke: <span class='{status}'>{status}</span></h1>",
        "<table>" + "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows) + "</table>",
    ]
    if fails:
        parts.append("<h2 class='bad'>Failures</h2><ul>" + "".join(f"<li>{e(f)}</li>" for f in fails) + "</ul>")
    errors = report.get("errors", []) + run["log_problems"]
    if errors:
        parts.append("<h2>Errors with tracebacks</h2>")
        for err in errors:
            where = err.get("step") or f"{err.get('stream')} line {err.get('line')}"
            parts.append(f"<p><b>{e(where)}</b>: {e(err['message'])}</p><pre>{e(err['text'])}</pre>")
    parts.append("<h2>Screenshots</h2><div class='grid'>")
    for shot in report.get("screenshots", []):
        stats = (f"{shot.get('width')}x{shot.get('height')}, luma std {shot.get('luma_std')}, "
                 f"{shot.get('colors')} colours")
        flag = f" <span class='bad'>BLANK? {e(', '.join(shot['blank']))}</span>" if shot.get("blank") else ""
        parts.append(f"<figure><a href='{e(shot['file'])}'><img loading='lazy' src='{e(shot['file'])}' "
                     f"alt='{e(shot['caption'])}'></a><figcaption><b>{e(shot['caption'])}</b>{flag}<br>"
                     f"<span class='muted'>{e(shot['name'])}.png, workspace {e(str(shot.get('workspace')))}, "
                     f"{e(stats)}</span></figcaption></figure>")
    parts.append("</div><h2>Steps</h2><table><tr><th>Step</th><th>OK</th><th>Seconds</th></tr>")
    for step in report.get("steps", []):
        parts.append(f"<tr><td>{e(step['name'])}</td><td>{'yes' if step['ok'] else '<b class=bad>no</b>'}</td>"
                     f"<td>{step['seconds']}</td></tr>")
    parts.append("</table>")
    notes = report.get("notes", []) + (report.get("menus") or {}).get("discovery_notes", [])
    if notes:
        parts.append("<h2>Notes</h2><ul class='muted'>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>")
    parts.append("<p class='muted'>Logs: <a href='blender_stdout.txt'>stdout</a>, "
                 "<a href='blender_stderr.txt'>stderr</a>, <a href='report.json'>report.json</a></p>")
    parts.append("</body></html>")
    index = out / "index.html"
    index.write_text("\n".join(parts), encoding="utf-8")
    return index


# --- Main -----------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blender", required=True, type=pathlib.Path, help="blender.exe to test")
    parser.add_argument("--stock", action="store_true", default=True,
                        help="stock Blender with the extension (the default): expect no native module")
    parser.add_argument("--fork", dest="stock", action="store_false",
                        help="a GameStudio Editor build (shelved): expect the native module")
    parser.add_argument("--out", type=pathlib.Path, help="output folder (default: system temp)")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="hard limit in seconds, Blender startup included (default 120). A run takes "
                             "about 25 s on an idle machine; under a parallel C++ build startup alone took "
                             "8-45 s, sometimes more")
    parser.add_argument("--size", default="1280x800", help="window size, WxH (default 1280x800)")
    parser.add_argument("--focus", action="store_true", help="let the window take focus (default: stays behind)")
    parser.add_argument("--keep-profile", action="store_true", help="keep the scratch profile for debugging")
    parser.add_argument("--inject-fault", action="store_true",
                        help="harness self-test: the driver adds a broken menu item and panel; passes only "
                             "if both errors are caught")
    args = parser.parse_args(argv)

    if not args.blender.is_file():
        print(f"[ui-smoke] no Blender at {args.blender}", file=sys.stderr)
        return 4
    out = (args.out or pathlib.Path(tempfile.gettempdir()) / "gamestudio-ui-smoke" /
           ("stock" if args.stock else "fork")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / "shots", ignore_errors=True)  # a stale shot must never pass for a new one
    for name in OUR_FILES:
        (out / name).unlink(missing_ok=True)

    profile = pathlib.Path(tempfile.mkdtemp(prefix="gs_ui_profile_"))
    try:
        run = run_blender(args, out, profile)
    finally:
        if args.keep_profile:
            print(f"[ui-smoke] scratch profile kept at {profile}")
        else:
            shutil.rmtree(profile, ignore_errors=True)

    run["log_problems"] = scan_log(run["stdout"], "stdout") + scan_log(run["stderr"], "stderr")
    report_path = out / "report.json"
    report = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            run["log_problems"].append({"stream": "report", "line": 0, "kind": "bad report",
                                        "message": str(exc), "text": str(exc)})
    fails = check(args, run, report, out)
    index = write_index(out, args, run, report, fails)
    summary = {"pass": not fails, "failures": fails, "timed_out": run["timed_out"],
               "returncode": run["returncode"], "seconds": run["seconds"], "cmd": run["cmd"],
               "screenshots": len((report or {}).get("screenshots", []))}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    print(f"[ui-smoke] {summary['screenshots']} screenshots, {run['seconds']} s, index: {index}")
    if args.inject_fault:
        caught = [fault for fault in INJECTED if any(fault in f for f in fails)]
        print(f"[ui-smoke] self-test: {len(fails)} failures reported; injected faults caught: {caught}")
        if len(caught) == len(INJECTED) and not run["timed_out"]:
            print("[ui-smoke] SELF-TEST PASS (the harness catches draw errors)")
            return 0
        print("[ui-smoke] SELF-TEST FAIL: an injected fault went unnoticed")
        return 1
    if fails:
        print(f"[ui-smoke] FAIL ({len(fails)}):")
        for fail in fails:
            print(f"  - {fail}")
        return 2 if run["timed_out"] else 1
    print("[ui-smoke] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
