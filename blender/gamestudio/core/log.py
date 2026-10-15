# SPDX-License-Identifier: GPL-3.0-or-later
"""Logging for the add-on.

Every core module logs through a child of the "gamestudio" logger, so failures
that cannot be reported through an operator still reach Blender's console.
"""

import logging
import sys

ROOT_NAME = "gamestudio"


def _configure():
    root = logging.getLogger(ROOT_NAME)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("GameStudio %(levelname)s [%(name)s]: %(message)s"))
        root.addHandler(handler)
        if root.level == logging.NOTSET:  # keep a level chosen by the host (e.g. the test runner)
            root.setLevel(logging.INFO)
        root.propagate = False
    return root


_configure()


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the add-on's root logger ("gamestudio.<short name>")."""
    short = name.rsplit(".", 1)[-1]
    return logging.getLogger(f"{ROOT_NAME}.{short}")
