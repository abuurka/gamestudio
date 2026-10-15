# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the Blender contract tests inside Blender (headless).

These tests pin down the Blender behaviours GameStudio relies on, so a Blender upgrade reports
exactly which assumptions broke (docs/fork/ARCHITECTURE.md section 7, step 4). They must pass on stock
Blender and on GameStudio Editor builds; tests of fork-only features skip on stock Blender.

    set BLENDER_USER_RESOURCES=%TEMP%\\gs_contract_profile
    blender --background --factory-startup --app-template default --python-exit-code 1 ^
            --python blender/tests/contract/run_contract.py
    blender ... --python blender/tests/contract/run_contract.py -- -k gltf   # only tests matching "gltf"

`gsfork test` runs this file with a throw-away profile. Tests that need a fresh Blender process
start one with the same binary, always with --factory-startup and their own scratch profile.

Environment:
  GS_TEST_TMP        where temporary files go (default: the system temp folder)
  GS_STOCK_BLENDER   on a fork build: stock Blender used to open fork-saved files
Exits non-zero if any test fails.
"""

import os
import pathlib
import sys
import unittest

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bpy  # noqa: E402

import contract_support  # noqa: E402


def _patterns(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    patterns = []
    while args:
        arg = args.pop(0)
        if arg == "-k" and args:
            patterns.append(f"*{args.pop(0)}*")
    return patterns


def main() -> int:
    kind = "GameStudio Editor (fork)" if contract_support.is_fork_build() else "stock Blender"
    print(f"Contract tests on {kind} {bpy.app.version_string} ({bpy.app.build_hash.decode('ascii', 'replace')})")
    if not os.environ.get("BLENDER_USER_RESOURCES"):
        print("NOTE: BLENDER_USER_RESOURCES is not set. These tests never save preferences, but run them "
              "with a scratch profile (gsfork test does) to be sure the real profile is untouched.")
    loader = unittest.TestLoader()
    patterns = _patterns(sys.argv)
    if patterns:
        loader.testNamePatterns = patterns
    suite = loader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    if result.skipped:
        print("\nSkipped:")
        for test, reason in result.skipped:
            print(f"  {test.id()}: {reason}")
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    ok = result.wasSuccessful()
    print(f"\nContract tests: {result.testsRun} run, {passed} passed, {len(result.failures)} failures, "
          f"{len(result.errors)} errors, {len(result.skipped)} skipped -> {'OK' if ok else 'FAILED'}")
    sys.stdout.flush()
    return 0 if ok else 1


sys.exit(main())
