# SPDX-License-Identifier: GPL-3.0-or-later
"""Write GameStudio's monochrome SVG marks, which replace Blender's built-in logo icons.

  python editor/branding/make_logo_svg.py      (system Python with fontTools and brotli)

Blender compiles release/datafiles/icons_svg/*.svg into the binary and draws them as
single-colour masks, tinted by the theme. Two of them are Blender's marks:
  * blender_logo_large.svg  the wordmark in the About box (ICON_BLENDER_LOGO_LARGE)
  * blender.svg             the small logo (ICON_BLENDER)
This script writes GameStudio replacements to generated/ and to the overlay, so the fork
never shows Blender's marks (docs/fork/ARCHITECTURE.md section 9).

The mark is the "game block" from make_branding.py: an isometric cube whose three faces are
separated by gaps (the gaps carry the shading a single-colour icon cannot). The wordmark is
set in Inter Bold, taken from the Blender tree itself (Inter is under the SIL Open Font
License). The SVG icon renderer (nanosvg) does not draw <text>, so glyphs become paths.
"""

import math
import pathlib
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "generated"
ICONS = HERE / "overlay" / "release" / "datafiles" / "icons_svg"
WORDMARK = "GameStudio"


def cube_faces(cx, cy, r, gap):
    """Top, left and right faces of an isometric cube of circumradius r, each shrunk by `gap`."""
    dx = r * math.cos(math.radians(30))
    top, bottom = (cx, cy - r), (cx, cy + r)
    upper_left, upper_right = (cx - dx, cy - r / 2), (cx + dx, cy - r / 2)
    lower_left, lower_right = (cx - dx, cy + r / 2), (cx + dx, cy + r / 2)
    center = (cx, cy)
    faces = [
        (top, upper_right, center, upper_left),
        (upper_left, center, bottom, lower_left),
        (center, upper_right, lower_right, bottom),
    ]
    shrunk = []
    for face in faces:
        fx = sum(p[0] for p in face) / 4
        fy = sum(p[1] for p in face) / 4
        k = 1 - gap / r
        shrunk.append([(fx + (x - fx) * k, fy + (y - fy) * k) for x, y in face])
    return shrunk


def polygon_path(points):
    head, *rest = points
    return f"M{head[0]:.1f} {head[1]:.1f}" + "".join(f"L{x:.1f} {y:.1f}" for x, y in rest) + "Z"


def mark_paths(cx, cy, r):
    return [polygon_path(face) for face in cube_faces(cx, cy, r, gap=r * 0.09)]


def bold_inter(blender_tree: pathlib.Path) -> TTFont:
    font = TTFont(str(blender_tree / "release" / "datafiles" / "fonts" / "Inter.woff2"))
    if "fvar" in font:
        from fontTools.varLib import instancer

        font = instancer.instantiateVariableFont(font, {"wght": 700})
    return font


def text_path(font: TTFont, text: str, x: float, baseline: float, cap_height: float) -> tuple[str, float]:
    """SVG path data for `text` with its cap height scaled to `cap_height`; returns (d, width)."""
    os2 = font["OS/2"]
    caps = getattr(os2, "sCapHeight", 0) or font["head"].unitsPerEm * 0.7
    scale = cap_height / caps
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    pen = SVGPathPen(glyphs)
    pen_x = 0.0
    for char in text:
        name = cmap[ord(char)]
        # Font units are y-up; SVG is y-down.
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + pen_x * scale, baseline)))
        pen_x += hmtx[name][0]
    return pen.getCommands(), pen_x * scale


def svg(width, height, paths):
    body = "".join(f'<path d="{d}"/>' for d in paths)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}"><g fill="#fff" fill-rule="nonzero">{body}</g></svg>\n')


def main():
    blender_tree = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[2].parent / "gs-blender"
    font = bold_inter(blender_tree)

    # Wordmark: about the proportions of the logo it replaces (5236 x 1600).
    height = 1600.0
    r = height * 0.42
    cap = height * 0.42
    text_d, text_w = text_path(font, WORDMARK, x=height * 1.08, baseline=height / 2 + cap / 2, cap_height=cap)
    width = height * 1.08 + text_w + height * 0.04
    wordmark = svg(width, height, [*mark_paths(height / 2, height / 2, r), text_d])

    # Small logo: the mark alone, square.
    small = svg(1600, 1600, mark_paths(800, 800, 760))

    OUT.mkdir(exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    for name, data in (("blender_logo_large.svg", wordmark), ("blender.svg", small)):
        for folder in (OUT, ICONS):
            (folder / name).write_text(data, encoding="utf-8", newline="\n")
    print(f"wordmark {width:.0f}x{height:.0f}, mark 1600x1600 -> {ICONS}")


if __name__ == "__main__":
    main()
