# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the add-on test suite inside Blender (headless).

    blender --background --factory-startup --python-exit-code 1 --python blender/tests/addon/run_tests.py
    blender ... --python blender/tests/addon/run_tests.py -- -k export      # only tests matching "export"

The add-on is imported from this checkout (editor/shell/extensions/system/ is put on sys.path) and
registered with gamestudio.register(); it is never installed into Blender.
Set GS_TEST_TMP to choose where temporary projects are created.
Exits non-zero if any test fails or the add-on fails to unregister.
"""

import logging
import os
import pathlib
import sys
import traceback
import unittest

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

HERE = pathlib.Path(__file__).resolve().parent
EXTENSIONS = HERE.parents[1]   # blender/, which holds the gamestudio package
sys.path.insert(0, str(EXTENSIONS))
sys.path.insert(0, str(HERE))

import gamestudio  # noqa: E402


def _patterns(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    patterns = []
    while args:
        arg = args.pop(0)
        if arg == "-k" and args:
            patterns.append(f"*{args.pop(0)}*")
    return patterns


def trust_temporary_folders() -> None:
    """The projects the tests make in temporary folders run their scripts, as New Project's do: their
    folders are trusted for this session only (nothing is written to the trust record, core/trust.py)."""
    import tempfile

    from gamestudio.core import trust

    for folder in {tempfile.gettempdir(), os.environ.get("GS_TEST_TMP", "")} - {""}:
        trust.trust(folder, "tests", save=False)


def main() -> int:
    # INFO logs are useful in Blender but noisy here; GS_TEST_VERBOSE=1 shows them.
    if not os.environ.get("GS_TEST_VERBOSE"):
        logging.getLogger("gamestudio").setLevel(logging.WARNING)
    gamestudio.register()
    trust_temporary_folders()
    loader = unittest.TestLoader()
    patterns = _patterns(sys.argv)
    if patterns:
        loader.testNamePatterns = patterns
    suite = loader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    ok = result.wasSuccessful()
    try:
        gamestudio.unregister()
    except Exception:
        traceback.print_exc()
        print("FAILED: gamestudio.unregister() raised")
        ok = False
    print(f"\nRan {result.testsRun} tests: {len(result.failures)} failures, {len(result.errors)} errors, "
          f"{len(result.skipped)} skipped -> {'OK' if ok else 'FAILED'}")
    sys.stdout.flush()
    return 0 if ok else 1


sys.exit(main())
