# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge method for save_template: Save as Template (projects.save_as_template) for the agent.

It copies the open project into the user templates folder, outside the project, and changes neither
the scene nor the project's files, so it is no undo step. It never replaces a template unless asked
to (overwrite)."""

from ..core import project
from .api import BridgeError, method, param
from .server import INVALID_PARAMS


@method("save_template")
def save_template(params):
    from .. import projects

    root = project.project_root()
    if root is None:
        raise BridgeError("The open .blend is not inside a GameStudio project (no gamestudio.json above it).",
                          hint="open_scene a scene of the project first.")
    overwrite = param(params, "overwrite", bool, False)
    for key in ("label", "description"):
        if not isinstance(params.get(key) or "", str):
            raise BridgeError(f"Parameter '{key}' must be str.", code=INVALID_PARAMS)
    try:
        result = projects.save_as_template(root, param(params, "id", str), params.get("label") or "",
                                           params.get("description") or "", overwrite=overwrite)
    except projects.TemplateError as exc:
        raise BridgeError(str(exc), hint=exc.hint or None) from None
    except OSError as exc:
        raise BridgeError(f"Could not save the template: {exc}",
                          hint="Check that the templates folder is writable and no file in it is open.") from None
    result["hint"] = ("New Project (GameStudio menu) lists it under Your templates; a project made from it opens "
                      "its own copy of the start scene.")
    return result
