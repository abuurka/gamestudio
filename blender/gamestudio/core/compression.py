# SPDX-License-Identifier: GPL-3.0-or-later
"""Smaller builds: the "compression" section of gamestudio.json (docs/WORKFLOWS.md, "Smaller builds").

    "compression": {"geometry": "meshopt", "textures": "webp"}

- geometry ("meshopt", the default, or "none") is applied by `npm run build`: the dev server's Vite
  plugin compresses the scene GLBs it copies into dist/ with EXT_meshopt_compression (lossless; see
  runtime/packages/devserver/src/compress.js). Exports for development stay plain, so exporting and
  hot reloading stay fast. measure() runs the same compressor as a dry run, for export_scene's
  `compress` option: the sizes the build will ship, with the export left as it is.
- textures ("webp" or "none", the default) is applied by the export itself (fidelity.process_textures),
  because Blender has the WebP encoder: colour maps become lossy WebP (export.webpQuality), data maps
  (normal, roughness, occlusion) lossless WebP. Conversions are cached by image hash, so re-exports
  cost nothing, and the game looks in development the way it ships.
"""

import json
import os
import pathlib
import subprocess

from . import project
from .log import get_logger

log = get_logger(__name__)

DEFAULTS = {"geometry": "meshopt", "textures": "none"}
MODES = {"geometry": ("meshopt", "none"), "textures": ("webp", "none")}
COMPRESSOR = pathlib.Path("@gamestudio", "devserver", "bin", "gamestudio-compress.js")
TIMEOUT = 180.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def settings(root=None) -> dict:
    """DEFAULTS overridden by the project's "compression" section (bad values are logged and ignored)."""
    out = dict(DEFAULTS)
    section = project.read_config(root).get("compression") if root is not None else None
    if section is None:
        return out
    if not isinstance(section, dict):
        log.warning("gamestudio.json: \"compression\" must be an object; using %r", out)
        return out
    for key, modes in MODES.items():
        value = section.get(key)
        if value is None:
            continue
        if value in modes:
            out[key] = value
        else:
            log.warning("gamestudio.json: compression.%s = %r is not one of %s; using %r", key, value,
                        ", ".join(modes), out[key])
    return out


def apply_to_export(export_settings: dict, compression: dict) -> dict:
    """Export settings with the texture compression turned on: "webp" sets export.webp (colour maps)
    and webpData (data maps, lossless)."""
    if compression.get("textures") == "webp":
        export_settings["webp"] = True
        export_settings["webpData"] = True
    return export_settings


def compressor(root) -> pathlib.Path | None:
    """gamestudio-compress.js of the project's @gamestudio/devserver (or the GameStudio runtime's)."""
    candidates = [pathlib.Path(root) / "node_modules" / COMPRESSOR] if root is not None else []
    try:
        from . import toolchain

        for sdk in (toolchain.sdk(root), toolchain.sdk()):  # the project's runtime, else this extension's
            if sdk is not None:
                candidates.append(pathlib.Path(sdk.packages) / "devserver" / "bin" / COMPRESSOR.name)
    except Exception as exc:  # noqa: BLE001 - only a fallback location
        log.debug("No GameStudio runtime for the compressor: %s", exc)
    return next((c for c in candidates if c.is_file()), None)


def measure(root, files) -> dict:
    """The sizes `npm run build` will ship for the given GLBs: {"geometry", "files": [{file, before,
    after, ...}], "bytes_before", "bytes_after"} or {"error": ...}. A dry run: nothing is written."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    mode = settings(root)["geometry"]
    files = [pathlib.Path(f) for f in files if pathlib.Path(f).is_file()]
    if mode == "none":
        sizes = [f.stat().st_size for f in files]
        return {"geometry": mode, "files": [{"file": f.name, "before": s, "after": s} for f, s in zip(files, sizes)],
                "bytes_before": sum(sizes), "bytes_after": sum(sizes)}
    from . import trust

    if root is not None and not trust.is_trusted(root):  # the compressor may be the project's (its node_modules)
        return {"geometry": mode, "error": trust.ASK_USER}
    script = compressor(root)
    try:
        from . import toolchain

        node, env = toolchain.node_executable(root), toolchain.child_env(start=root)
    except Exception:  # noqa: BLE001 - outside the extension (tests): the system's node
        node, env = "node", dict(os.environ)
    if script is None or node is None:
        return {"geometry": mode, "error": "The compressor needs Node.js and the project's packages "
                                           "(@gamestudio/devserver), which GameStudio sets up when the project "
                                           "opens or plays"}
    try:
        proc = subprocess.run([node, str(script), "--dry-run", "--json", *map(str, files)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, env=env,
                              cwd=str(root), creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"geometry": mode, "error": f"The compressor did not run: {exc}"}
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
    if proc.returncode != 0 or line is None:
        tail = " ".join((proc.stderr or proc.stdout).strip().splitlines()[-3:])
        return {"geometry": mode, "error": f"The compressor failed (exit {proc.returncode}): {tail}"}
    report = json.loads(line)
    out = [{k: item.get(k) for k in ("file", "before", "after", "geometryBefore", "geometryAfter", "skipped")
            if item.get(k) is not None} for item in report.get("files", [])]
    return {"geometry": mode, "files": out, "bytes_before": sum(i["before"] for i in out),
            "bytes_after": sum(i["after"] for i in out)}
