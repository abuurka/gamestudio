# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge method for bake_lighting (core/lightmap.py): bakes the indirect light of the static meshes into
lightmaps under <project>/.gamestudio/lightmaps/<scene>/, then exports the scene so the game shows them.

Not an undo step, like export_scene: the bake writes generated files only (never the .blend), and they
can be baked again at any time. It runs in Object Mode, so the meshes it bakes are the ones being edited."""

import time

from .api import BridgeError, method, names_param, param, resolve
from .server import INVALID_PARAMS

QUALITIES = ("preview", "final")
TEST_QUALITIES = ("draft",)  # tiny bakes for the tests; not offered to agents
LIST_LIMIT = 50
WARNING_LIMIT = 20


def _lightmap():
    from ..core import lightmap

    return lightmap


def error_hint(exc) -> str | None:
    """How to fix a LightmapError: its own hint when it has one, else one picked from the message."""
    hint = getattr(exc, "hint", None)
    if hint:
        return hint
    text = str(exc).lower()
    if "project" in text:
        return ("Save the .blend inside a GameStudio project (its scenes/ folder): open_scene a file there, or "
                "save({path}) into it.")
    if "cycles" in text:
        return "Lightmaps are baked with Cycles: enable the Cycles add-on (Preferences > Add-ons), then retry."
    if "window" in text:
        return "Open the scene in a Blender session (launch_blender, or open_scene), then bake again."
    if "static" in text or "nothing to bake" in text:
        return ("Only static meshes are baked. Mark objects static: remove the RigidBody or set its type to fixed; "
                "CharacterController, Spin, Animator, animation, armatures and shape keys also make an object move. "
                "An object with custom property gs_lightmap = False is left out.")
    if "quality" in text:
        return "quality is preview (seconds) or final (slower, cleaner)."
    if "object" in text or "name" in text:
        return "Call list_objects for the names; objects takes static mesh objects."
    return None


def _objects(params) -> list[str] | None:
    """The names of the objects to bake (by name or gs_id), or None for every static object."""
    if params.get("objects") is None:
        return None
    return [resolve(ref).name for ref in names_param(params, "objects")]


def _capped(out: dict, key: str, limit: int) -> None:
    value = out.get(key)
    if isinstance(value, list) and len(value) > limit:
        out[key] = value[:limit]
        out[f"{key}_count"] = len(value)


@method("bake_lighting", object_mode=True)
def bake_lighting(params):
    from .methods import _require_root, export_scene_method

    lightmap = _lightmap()
    quality = param(params, "quality", str, "preview")
    if quality not in QUALITIES + TEST_QUALITIES:
        raise BridgeError(f"Parameter 'quality' must be one of {', '.join(QUALITIES)}; got {quality!r}.",
                          code=INVALID_PARAMS)
    force = param(params, "force", bool, False)
    do_export = param(params, "export", bool, True)
    objects = _objects(params)
    root = _require_root()
    started = time.monotonic()
    try:
        summary = lightmap.bake_lighting(quality=quality, objects=objects, root=root, force=force)
    except lightmap.LightmapError as exc:
        raise BridgeError(f"Bake failed: {exc}", hint=error_hint(exc)) from None
    out = dict(summary)
    out["texture_mb"] = round(float(summary.get("texture_bytes") or 0) / (1024 * 1024), 2)
    out["seconds"] = round(float(summary.get("seconds", time.monotonic() - started)), 2)
    _capped(out, "objects", LIST_LIMIT)
    _capped(out, "skipped", LIST_LIMIT)
    _capped(out, "warnings", WARNING_LIMIT)
    if summary.get("up_to_date"):
        out["note"] = "Nothing changed since the last bake, so nothing was baked again; force: true bakes anyway."
    if do_export:
        try:
            out["export"] = export_scene_method({})
        except BridgeError as exc:
            out["export_error"] = exc.message
            out["warnings"] = [*(out.get("warnings") or []), "The lightmaps are baked, but the export failed; fix it "
                               "and call export_scene so the game shows them."]
    return out
