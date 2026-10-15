# SPDX-License-Identifier: GPL-3.0-or-later
"""AI assistant: typed tools, staged proposals, and a human-approved apply step.

The assistant is optional and off by default. It can read the scene, the
project and the running game, but every change it suggests is a proposal that
the developer applies, or rejects, with a click. See research/07-ai-agents.md.
"""

import importlib

_SUBMODULES = ("ops", "ui", "agent")
_loaded = []


def register():
    for name in _SUBMODULES:
        mod = importlib.import_module(f".{name}", __package__)
        mod.register()
        _loaded.append(mod)


def unregister():
    for mod in reversed(_loaded):
        mod.unregister()
    _loaded.clear()
