# SPDX-License-Identifier: GPL-3.0-or-later
"""Generated source meshes the builders instance: a fence post, a lamp post and the modular
building parts of each style. They live in the excluded "Procedural Sources/Parts" collection, so
they show only through their instances, and are made once per file (by name).

Building parts are PART_WIDTH wide and PART_HEIGHT tall (groups.py scales them to each module):
x across, the outer face at y = 0 and the wall going inward to +y, z up from the floor.
"""

import math

import bpy
from mathutils import Matrix

from ..kits.geometry import MeshBuilder
from . import groups

PARTS_COLLECTION = "Parts"
PART_PROP = "gs_procedural_part"
WALL = 0.25  # wall thickness

# Per style: materials (kits presets or modifiers.EXTRA_MATERIALS) and the default roof.
STYLES = {
    "cottage": {"wall": "plaster", "base": "stone_dark", "frame": "wood_dark", "door": "wood_dark",
                "shutter": "#4E7B5B", "roof": "roof", "roof_shape": "gable", "gable": "plaster"},
    "stone": {"wall": "stone", "base": "stone_dark", "frame": "wood", "door": "wood", "shutter": None,
              "roof": "slate", "roof_shape": "gable", "gable": "stone"},
    "brick": {"wall": "brick", "base": "concrete", "frame": "plaster", "door": "wood_dark", "shutter": None,
              "roof": "slate", "roof_shape": "hip", "gable": "brick"},
    "timber": {"wall": "wood", "base": "stone", "frame": "wood_dark", "door": "wood_dark", "shutter": "#8A4A30",
               "roof": "thatch", "roof_shape": "gable", "gable": "wood"},
}
GENERIC = ("fence_post", "lamp_post")


def _slot(m, name):
    from .modifiers import EXTRA_MATERIALS

    if name in EXTRA_MATERIALS:
        preset, overrides = EXTRA_MATERIALS[name]
        return m.slot(preset, **overrides)
    return m.slot(name)


def _box(m, lo, hi, slot):
    m.box(lo, hi, slot)


def _fence_post(m):
    s = 0.12
    rr = s / math.sqrt(2)
    m.lathe([(rr, 0.0), (rr, 1.0 - 0.45 * s), (0.0, 1.0)], 4, m.slot("wood_dark"), smooth=False, angle=math.pi / 4)


def _lamp_post(m):
    iron, glow = m.slot("iron"), m.slot("emissive", color="#FFD58A")
    _box(m, (-0.18, -0.18, 0.0), (0.18, 0.18, 0.3), m.slot("stone_dark"))
    m.lathe([(0.07, 0.3), (0.05, 3.9), (0.0, 4.0)], 6, iron, smooth=False)
    _box(m, (-0.03, -0.75, 3.62), (0.03, 0.0, 3.68), iron)                        # arm toward the front (-Y)
    m.lathe([(0.0, 3.25), (0.16, 3.3), (0.2, 3.5), (0.0, 3.62)], 6, glow, smooth=False,
            matrix=Matrix.Translation((0.0, -0.7, 0.0)))
    m.lathe([(0.24, 3.5), (0.05, 3.66), (0.0, 3.68)], 6, iron, smooth=False, matrix=Matrix.Translation((0.0, -0.7, 0.0)))


def _band(m, x0, x1, slot):
    _box(m, (x0, -0.05, 0.0), (x1, WALL, 0.25), slot)


def _wall(m, style):
    w, h = groups.PART_WIDTH / 2, groups.PART_HEIGHT
    _box(m, (-w, 0.0, 0.25), (w, WALL, h), _slot(m, style["wall"]))
    _band(m, -w, w, _slot(m, style["base"]))


def _window(m, style):
    w, h = groups.PART_WIDTH / 2, groups.PART_HEIGHT
    wall, frame = _slot(m, style["wall"]), _slot(m, style["frame"])
    x, z0, z1 = 0.55, 0.95, 2.3
    _box(m, (-w, 0.0, 0.25), (-x, WALL, h), wall)
    _box(m, (x, 0.0, 0.25), (w, WALL, h), wall)
    _box(m, (-x, 0.0, 0.25), (x, WALL, z0), wall)
    _box(m, (-x, 0.0, z1), (x, WALL, h), wall)
    _band(m, -w, w, _slot(m, style["base"]))
    _box(m, (-x - 0.1, -0.05, z0), (-x, 0.08, z1), frame)
    _box(m, (x, -0.05, z0), (x + 0.1, 0.08, z1), frame)
    _box(m, (-x - 0.1, -0.05, z1), (x + 0.1, 0.08, z1 + 0.12), frame)
    _box(m, (-x - 0.15, -0.12, z0 - 0.08), (x + 0.15, 0.1, z0), frame)           # sill
    _box(m, (-x, 0.1, z0), (x, 0.13, z1), m.slot("glass"))
    _box(m, (-0.03, 0.08, z0), (0.03, 0.15, z1), frame)                          # mullions
    _box(m, (-x, 0.08, 1.6), (x, 0.15, 1.66), frame)
    if style["shutter"]:
        shutter = m.slot("wood", color=style["shutter"])
        _box(m, (-x - 0.6, -0.07, z0), (-x - 0.12, -0.03, z1), shutter)
        _box(m, (x + 0.12, -0.07, z0), (x + 0.6, -0.03, z1), shutter)


def _door(m, style):
    w, h = groups.PART_WIDTH / 2, groups.PART_HEIGHT
    wall, frame, base = _slot(m, style["wall"]), _slot(m, style["frame"]), _slot(m, style["base"])
    x, top = 0.6, 2.3
    _box(m, (-w, 0.0, 0.25), (-x, WALL, h), wall)
    _box(m, (x, 0.0, 0.25), (w, WALL, h), wall)
    _box(m, (-x, 0.0, top), (x, WALL, h), wall)
    _band(m, -w, -x, base)
    _band(m, x, w, base)
    _box(m, (-x - 0.1, -0.05, 0.0), (-x, 0.08, top), frame)
    _box(m, (x, -0.05, 0.0), (x + 0.1, 0.08, top), frame)
    _box(m, (-x - 0.1, -0.05, top), (x + 0.1, 0.08, top + 0.15), frame)
    _box(m, (-x, 0.08, 0.0), (x, 0.14, top), _slot(m, style["door"]))
    _box(m, (0.38, 0.03, 1.0), (0.46, 0.08, 1.08), frame)
    _box(m, (-x - 0.2, -0.4, 0.0), (x + 0.2, 0.0, 0.12), base)                   # step


def _corner(m, style):
    _box(m, (-0.2, -0.2, 0.0), (0.2, 0.2, groups.PART_HEIGHT), _slot(m, style["base"] if style["wall"] != "stone"
                                                                      else "stone_dark"))


BUILDING_PARTS = {"wall": _wall, "window": _window, "door": _door, "corner": _corner}


def collection():
    from .builders import sources_root

    root = sources_root()
    coll = root.children.get(PARTS_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.get(PARTS_COLLECTION)
        if coll is None or coll.library is not None:
            coll = bpy.data.collections.new(PARTS_COLLECTION)
        root.children.link(coll)
    return coll


def _make(name, build):
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.get(PART_PROP) and obj.library is None:
        return obj
    m = MeshBuilder()
    build(m)
    obj = m.to_object(name, collection=collection())
    obj[PART_PROP] = name
    return obj


def part(name: str):
    """A generated part object by name: fence_post or lamp_post (made once, then reused)."""
    builders = {"fence_post": ("Fence Post", _fence_post), "lamp_post": ("Lamp Post", _lamp_post)}
    if name not in builders:
        from .errors import ProceduralError

        raise ProceduralError(f"Unknown part {name!r}. Parts: {', '.join(builders)}")
    label, build = builders[name]
    return _make(label, build)


def building_parts(style: str) -> dict:
    """{"wall", "window", "door", "corner"} part objects of a style."""
    look = STYLES[style]
    return {key: _make(f"Building {style.title()} {key.title()}", lambda m, b=build: b(m, look))
            for key, build in BUILDING_PARTS.items()}
