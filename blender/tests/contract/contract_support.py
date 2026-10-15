# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the Blender contract tests (docs/fork/ARCHITECTURE.md section 7, step 4).

The contract tests pin down the Blender behaviours GameStudio depends on. Each test asserts one
behaviour and its docstring says what in GameStudio breaks if that behaviour changes, so a failing
test after an upgrade points straight at the code to fix.

Some behaviours can only be observed in a fresh Blender process (app-template loading, extension
discovery, timers in --background, opening a file). `run_probe()` starts one with the running
binary, always with --factory-startup and a throw-away BLENDER_USER_RESOURCES, so the user's real
Blender profile is never read or written.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

import bpy

HERE = pathlib.Path(__file__).resolve().parent
PROBES = HERE / "probes"
BLENDER_DIR = HERE.parents[1]                              # blender/
SHELL = BLENDER_DIR.parent / "editor" / "shell"            # the fork's app template (shelved)
EXTENSION_SRC = BLENDER_DIR / "gamestudio"
TEMPLATE_SRC = SHELL / "scripts" / "startup" / "bl_app_templates_system" / "GameStudio"
TEMPLATE_BUILDER = SHELL / "tools" / "build_template.py"
FIXTURES = BLENDER_DIR / "tests" / "fixtures"

PROBE_TIMEOUT = 240.0


# --- Which Blender is this? ---------------------------------------------------------------------


def native_module():
    """The `_gamestudio` built-in module of a GameStudio Editor build, or None on stock Blender."""
    try:
        import _gamestudio
    except ImportError:
        return None
    return _gamestudio


def is_fork_build() -> bool:
    """True on a GameStudio Editor build: `_gamestudio` imports, or build_options says so."""
    return native_module() is not None or bool(getattr(bpy.app.build_options, "gamestudio", False))


requires_fork = unittest.skipUnless(
    is_fork_build(), "needs a GameStudio Editor build (no _gamestudio module in this Blender)")


def stock_blender_for_compat():
    """A stock Blender binary for the "fork files open in stock Blender" checks, or None.

    On stock Blender that is the running binary. On a fork build it is GS_STOCK_BLENDER, or the
    default install location of the same Blender version.
    """
    if not is_fork_build():
        return pathlib.Path(bpy.app.binary_path)
    env = os.environ.get("GS_STOCK_BLENDER")
    if env and pathlib.Path(env).is_file():
        return pathlib.Path(env)
    major, minor = bpy.app.version[:2]
    for drive in "CDEF":
        candidate = pathlib.Path(f"{drive}:/Program Files/Blender Foundation/Blender {major}.{minor}/blender.exe")
        if candidate.is_file():
            return candidate
    return None


# --- Temporary files and child Blender processes --------------------------------------------------


def tmp_base() -> str:
    base = os.environ.get("GS_TEST_TMP") or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    return base


def make_tmp_dir(prefix: str) -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=tmp_base()))


class ProbeError(AssertionError):
    pass


def run_blender(script, args=(), *, env=None, app_template="default", blend_file=None, binary=None,
                timeout=PROBE_TIMEOUT) -> subprocess.CompletedProcess:
    """Run `script` in a new headless Blender and return the completed process.

    `env` entries override the inherited environment; a value of None removes the variable.
    BLENDER_USER_RESOURCES always points at a fresh scratch folder that is deleted afterwards.
    """
    profile = make_tmp_dir("gs_contract_profile_")
    full_env = dict(os.environ)
    full_env["BLENDER_USER_RESOURCES"] = str(profile)
    for key, value in (env or {}).items():
        if value is None:
            full_env.pop(key, None)
        else:
            full_env[key] = str(value)
    cmd = [str(binary or bpy.app.binary_path), "--background", "--factory-startup"]
    if app_template is not None:
        cmd += ["--app-template", app_template]
    if blend_file is not None:
        cmd.append(str(blend_file))
    cmd += ["--python-exit-code", "1", "--python", str(script), "--", *[str(a) for a in args]]
    try:
        return subprocess.run(cmd, env=full_env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def run_probe(name, *args, **kwargs) -> dict:
    """Run probes/<name>.py in a new Blender. The probe writes a JSON report; return it.

    The report path is the first argument after `--`, followed by `args`.
    """
    out_dir = make_tmp_dir("gs_contract_probe_")
    report = out_dir / "report.json"
    try:
        result = run_blender(PROBES / f"{name}.py", (report, *args), **kwargs)
        if result.returncode != 0 or not report.is_file():
            tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
            raise ProbeError(f"probe {name} failed (exit code {result.returncode}):\n{tail}")
        data = json.loads(report.read_text(encoding="utf-8"))
        data["_returncode"] = result.returncode
        return data
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# --- Reading what the shell actually uses (so tests follow the code) ------------------------------


def module_constant(path: pathlib.Path, name: str):
    """The literal value of a module-level `name = <literal>` in a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found in {path}")


def handler_names_used() -> set:
    """Names of bpy.app.handlers lists the shell appends to.

    Found as `bpy.app.handlers.<name>` attributes and as the first string of each tuple in a
    module-level `_HANDLERS = (("load_post", fn), ...)`.
    """
    names = set()
    for path in sorted(SHELL.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "handlers" and isinstance(node.value.value, ast.Attribute)
                    and node.value.value.attr == "app" and node.attr != "persistent"):
                names.add(node.attr)
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_HANDLERS"
                                                    for t in node.targets):
                for elt in getattr(node.value, "elts", []):
                    first = getattr(elt, "elts", [None])[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        names.add(first.value)
    return names


# --- Blender state ---------------------------------------------------------------------------------


def factory_reset(use_empty=False) -> None:
    """Reload the factory startup file (and factory preferences) in this process."""
    bpy.ops.wm.read_factory_settings(use_empty=use_empty)


def any_window():
    wm = bpy.context.window_manager
    return wm.windows[0] if wm and wm.windows else None


_ENUM_LIST = re.compile(r"not found in \((.*)\)")
INVALID_ENUM = "__GS_CONTRACT_INVALID__"


def enum_ids_from_error(exc) -> list:
    """Valid identifiers listed in Blender's "enum "X" not found in ('A', 'B')" TypeError."""
    match = _ENUM_LIST.search(str(exc))
    if not match:
        raise exc
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def area_ui_type_items(area) -> list:
    """Identifiers the editor-type menu offers for `area` (Area.ui_type is a dynamic enum).

    Python cannot list dynamic enum items directly, so assign an invalid value and read the
    valid identifiers from the error message.
    """
    try:
        area.ui_type = INVALID_ENUM
    except TypeError as exc:
        return enum_ids_from_error(exc)
    raise AssertionError("Area.ui_type accepted an invalid identifier")


def operator_enum_items(op, prop_name: str) -> list:
    """Identifiers of an operator's enum property, including dynamic (callback) enums.

    For a dynamic enum, call the operator with an invalid value: Blender rejects it while
    converting the arguments, before poll() or execute() can run, and lists the valid ones.
    """
    prop = op.get_rna_type().properties[prop_name]
    items = [item.identifier for item in prop.enum_items]
    if items:
        return items
    try:
        op(**{prop_name: INVALID_ENUM})
    except TypeError as exc:
        return enum_ids_from_error(exc)
    raise AssertionError(f"{prop_name} accepted an invalid identifier")


def enum_identifiers(rna_struct, prop_name: str) -> list:
    return [item.identifier for item in rna_struct.bl_rna.properties[prop_name].enum_items]
