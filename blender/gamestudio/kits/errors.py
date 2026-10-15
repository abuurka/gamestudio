# SPDX-License-Identifier: GPL-3.0-or-later
"""KitError lives in its own module so every kit module can raise it without import cycles."""


class KitError(ValueError):
    """An unknown kit or preset, or invalid params. The message is meant for the user or agent."""
