#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write GameStudio's logo: docs/images/logo-mark.svg (the cube), logo.svg (for light backgrounds) and
logo-dark.svg (for dark ones).

    python tools/make_logo.py [--font PATH]

The mark is an isometric cube, a building block, with a play button on its front face. The wordmark
is outlined from Inter 4 (SIL Open Font License 1.1); by default the Inter.woff2 that ships with
Blender 5.2+ (datafiles/fonts), found the way tools/blender_dev.py finds Blender. Needs fontTools
and brotli.
"""

import argparse
import pathlib
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "images"
INK, TEAL, MID, MINT, AMBER, WHITE = "#17313a", "#087b78", "#26ab9f", "#8ee1d1", "#edaa4f", "#ffffff"


def blender_inter() -> pathlib.Path:
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev

    blender = pathlib.Path(blender_dev.find_blender())
    found = sorted(blender.parent.glob("*/datafiles/fonts/Inter.woff2"))
    if not found:
        raise SystemExit(f"no datafiles/fonts/Inter.woff2 next to {blender}; pass --font")
    return found[-1]


def poly(points, fill, extra=""):
    d = "M" + " ".join(f"{x:.2f} {y:.2f}" for x, y in points) + "Z"
    return f'<path fill="{fill}" d="{d}"{extra}/>'


def mark() -> str:
    """The cube in a 64 x 64 box: mint top, mid left, teal right with an amber play button."""
    c, r = (32.0, 32.0), 27.0
    dx, dy = r * 0.8660254, r / 2
    top, bottom = (c[0], c[1] - r), (c[0], c[1] + r)
    ul, ur = (c[0] - dx, c[1] - dy), (c[0] + dx, c[1] - dy)
    ll, lr = (c[0] - dx, c[1] + dy), (c[0] + dx, c[1] + dy)
    # The play button faces the viewer (projected onto the face it reads as a wedge), its centroid
    # on the right face's centre.
    fx, fy = c[0] + dx / 2, c[1] + r / 4
    h = r * 0.56
    w = h * 0.8660254
    play = [(fx - w / 3, fy - h / 2), (fx - w / 3, fy + h / 2), (fx + 2 * w / 3, fy)]
    rounded = f' stroke="{AMBER}" stroke-width="2.4" stroke-linejoin="round"'
    return "".join([
        poly([top, ur, c, ul], MINT),
        poly([ul, c, bottom, ll], MID),
        poly([c, ur, lr, bottom], TEAL),
        poly(play, AMBER, rounded),
    ])


def kerning(font):
    """(left glyph, right glyph) -> x advance adjustment, from the GPOS 'kern' feature."""
    gpos = font["GPOS"].table
    indices = {i for fr in gpos.FeatureList.FeatureRecord if fr.FeatureTag == "kern"
               for i in fr.Feature.LookupListIndex}
    tables = []
    for i in sorted(indices):
        lookup = gpos.LookupList.Lookup[i]
        for st in lookup.SubTable:
            st = st.ExtSubTable if lookup.LookupType == 9 else st
            if getattr(st, "LookupType", lookup.LookupType) == 2:
                tables.append(st)

    def value(a, b):
        for st in tables:
            if a not in st.Coverage.glyphs:
                continue
            if st.Format == 1:
                for rec in st.PairSet[st.Coverage.glyphs.index(a)].PairValueRecord:
                    if rec.SecondGlyph == b and rec.Value1 is not None:
                        return getattr(rec.Value1, "XAdvance", 0) or 0
            elif st.Format == 2:
                rec = st.Class1Record[st.ClassDef1.classDefs.get(a, 0)].Class2Record[st.ClassDef2.classDefs.get(b, 0)]
                if rec.Value1 is not None and getattr(rec.Value1, "XAdvance", 0):
                    return rec.Value1.XAdvance
        return 0

    return value


def wordmark(font, parts, x, baseline, cap):
    """parts: [(text, colour)] -> (svg paths, end x). cap: the cap height in px."""
    scale = cap / font["OS/2"].sCapHeight
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    kern = kerning(font)
    out, prev = [], None
    for text, colour in parts:
        pen = SVGPathPen(glyphs)
        for ch in text:
            name = cmap[ord(ch)]
            if prev:
                x += kern(prev, name) * scale
            glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
            x += hmtx[name][0] * scale
            prev = name
        out.append(f'<path fill="{colour}" d="{pen.getCommands()}"/>')
    return "".join(out), x


def svg(width, body, label="GameStudio"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} 64" width="{width:.0f}" height="64" '
            f'role="img" aria-label="{label}"><title>{label}</title>{body}</svg>\n')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", type=pathlib.Path, help="Inter 4 variable font (default: Blender's)")
    args = parser.parse_args(argv)
    font = instancer.instantiateVariableFont(TTFont(args.font or blender_inter()), {"wght": 760, "opsz": 32})
    cap = 29
    outputs = {"logo-mark.svg": svg(64, mark())}
    for name, (game, studio) in {"logo.svg": (INK, TEAL), "logo-dark.svg": (WHITE, MINT)}.items():
        words, end = wordmark(font, [("Game", game), ("Studio", studio)], 78, 32 + cap / 2, cap)
        outputs[name] = svg(end + 2, mark() + words)
    for name, text in outputs.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"[logo] {(OUT / name).relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
