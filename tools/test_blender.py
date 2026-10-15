# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the GameStudio extension's Blender test suites against a Blender 5.2+ install.

  python tools/test_blender.py                     # every suite, Blender found automatically
  python tools/test_blender.py --blender PATH      # a specific blender(.exe)
  python tools/test_blender.py addon contract      # only some suites
  python tools/test_blender.py addon -- -k export  # pass arguments to the suite

Each suite runs in background mode with --factory-startup and its own throw-away profile, so it
never reads or changes your Blender settings (or your GameStudio user templates). The extension loads from blender/gamestudio: the
add-on and contract suites import it, the shell suite enables it as a system extension
(BLENDER_SYSTEM_EXTENSIONS) the way users get it, with no app template.
"""

import argparse
import pathlib
import subprocess
import sys
import tempfile

import blender_dev

# name: (runner, app template or None)
SUITES = {
    "addon": (blender_dev.TESTS / "addon" / "run_tests.py", "default"),
    "shell": (blender_dev.TESTS / "shell" / "run_shell_tests.py", None),
    "contract": (blender_dev.TESTS / "contract" / "run_contract.py", "default"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("suites", nargs="*", metavar="suite", help=f"{', '.join(SUITES)} (default: all)")
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    parser.add_argument("--kind", default="stock", choices=("stock", "fork"),
                        help="GS_EDITOR_KIND for the suites (fork: a GameStudio Editor build)")
    # Everything after the first "--" goes to the suites (argparse would take it for suite names).
    argv = sys.argv[1:]
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    args = parser.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    unknown = [s for s in args.suites if s not in SUITES]
    if unknown:
        parser.error(f"unknown suite(s): {', '.join(unknown)}; choose from {', '.join(SUITES)}")
    exe = blender_dev.find_blender(args.blender)
    failed = []
    for name in args.suites or list(SUITES):
        runner, template = SUITES[name]
        print(f"[test] {name}: {runner.relative_to(blender_dev.REPO)} with {exe}", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"gs_test_{name}_") as profile:
            env = blender_dev.dev_env(profile)
            env["GS_EDITOR_KIND"] = args.kind
            env["GAMESTUDIO_TEMPLATES"] = str(pathlib.Path(profile) / "templates")  # never the user's templates
            cmd = [str(exe), "--background", "--factory-startup"]
            if template is not None:
                cmd += ["--app-template", template]
            cmd += ["--python-exit-code", "1", "--python", str(runner)]
            if rest:
                cmd += ["--", *rest]
            if subprocess.run(cmd, env=env, cwd=blender_dev.REPO).returncode != 0:
                failed.append(name)
    if failed:
        print(f"[test] FAILED: {', '.join(failed)}")
        return 1
    print("[test] All suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
