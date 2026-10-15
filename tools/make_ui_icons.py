#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the starter template's HUD icons and 9-slice UI art: blender/gamestudio/project_templates/starter/assets/ui/.

  python tools/make_ui_icons.py              # write them
  python tools/make_ui_icons.py --check      # exit 1 if a file's pixels differ from what this script makes
  python tools/make_ui_icons.py --out DIR    # write them somewhere else

Everything is drawn here with the standard library (no downloaded art, so no licences). Shapes are signed
distance functions; pixels on an edge are supersampled 4 x 4, and layers are mixed in linear light. The
output is the same on every run. Genre templates get these through their base template (the starter).

Icons: 128 x 128 RGBA PNGs on a transparent background (heart, heart-empty, star, star-empty, coin, gem,
key), in flat colours with a darker outline, a shaded lower-right rim and a small highlight, so they read
on dark and light HUDs alike.

9-slice art, for textured panels with UiSlice (docs/GAME-UI.md section 10.6). Each fills its image to the
edges, and everything that changes along an axis stays inside the insets, so the middle row and column are
uniform and stretch cleanly. It is drawn at 1 image px = 1 design px of the 1920 x 1080 frame; the game draws
the corners at their image size times the canvas scale (2/3 at 1280 x 720), so details are sized to stay
readable there. Insets are [left, right, top, bottom] image px, as `slice=` takes them:

  panel.png         128 x 128  slice=[32, 32, 32, 32]  a card like the starter's menus: #12161F at 90 %
                                                       with a 2 px light border, 28 px corner radius (it
                                                       looks like the flat cards, see-through over a bright
                                                       game, since translucency mixes in linear light)
  panel-solid.png   128 x 128  slice=[44, 44, 44, 44]  the starter's card made opaque: #12161F, a 2 px light
                                                       border, 40 px radius like the cards (UI_CARD)
  panel-framed.png  128 x 128  slice=[32, 32, 32, 32]  opaque navy (#17223D) in a 2 px dark outline and an
                                                       8 px bevelled gold frame with a soft shadow inside
                                                       it, a 2 px gold hairline 18 px in and an 18 px gold
                                                       stud on each corner, 8 px radius; light text
  panel-light.png   128 x 128  slice=[32, 32, 32, 32]  opaque parchment (#F5EDDC) on a 6 px darker lip in
                                                       a 3 px brown outline, warmer towards its edges, a
                                                       2 px hairline 12 px in, 22 px radius; dark text
                                                       (#2B2118)
  button.png         96 x  96  slice=[44, 44, 44, 44]  a light button body: 3 px dark outline, a 5 px
                                                       darker lip along the bottom, a shine along the top;
                                                       38 px radius, so at 88 px tall it is almost a pill.
                                                       Use dark text, about 2 px above centre (the face's middle)
  slider-track.png   64 x  32  slice=[14, 14, 14, 14]  the slider's dark, translucent groove (12 px radius)
  slider-fill.png    64 x  32  slice=[14, 14, 14, 14]  its filled part, in the starter's accent yellow
  slider-knob.png    48 x  48  no slice                a round handle in the button's colours

For example gameui.panel("Options", "Card", (960, 540), (760, 590), image="assets/ui/panel-framed.png",
slice=32). A panel smaller than its two insets shrinks the corners to fit, so a 28 px slider takes the 14 px
insets whole.
"""

import argparse
import math
import pathlib
import struct
import sys
import zlib

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "blender" / "gamestudio" / "project_templates" / "starter" / "assets" / "ui"
SIZE = 128
SAMPLES = 4  # per side, in pixels on an edge
BAND = 1.0  # px: pixels whose centre is closer than this to an edge are supersampled
OUTLINE = 6.0  # px around every shape
SHADE = (5.0, 6.0)  # px: the shaded rim is where the shape ends within this offset down and to the right


# --- Colours (hex sRGB in, linear floats inside) ---------------------------------------------


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def colour(hex_rgb: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    h = hex_rgb.lstrip("#")
    return (*(_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)), alpha)


# --- Shapes: signed distance functions of a pixel point (x right, y down; < 0 inside) ----------


def circle(cx, cy, r):
    return lambda x, y: math.sqrt((x - cx) ** 2 + (y - cy) ** 2) - r


def ring(cx, cy, r, half_width):
    c = circle(cx, cy, r)
    return lambda x, y: abs(c(x, y)) - half_width


def capsule(ax, ay, bx, by, r):
    ex, ey = bx - ax, by - ay
    length2 = ex * ex + ey * ey

    def sdf(x, y):
        wx, wy = x - ax, y - ay
        t = min(1.0, max(0.0, (wx * ex + wy * ey) / length2))
        return math.sqrt((wx - ex * t) ** 2 + (wy - ey * t) ** 2) - r
    return sdf


def box(cx, cy, half_w, half_h, radius=0.0):
    """A rectangle with rounded corners."""
    def sdf(x, y):
        qx = abs(x - cx) - half_w + radius
        qy = abs(y - cy) - half_h + radius
        outside = math.sqrt(max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2)
        return outside + min(max(qx, qy), 0.0) - radius
    return sdf


def polygon(points):
    """Any simple polygon (exact distance, after Inigo Quilez's sdPolygon)."""
    pts = list(points)

    def sdf(x, y):
        d = (x - pts[0][0]) ** 2 + (y - pts[0][1]) ** 2
        inside = False
        j = len(pts) - 1
        for i, (vx, vy) in enumerate(pts):
            ex, ey = pts[j][0] - vx, pts[j][1] - vy
            wx, wy = x - vx, y - vy
            t = min(1.0, max(0.0, (wx * ex + wy * ey) / (ex * ex + ey * ey)))
            bx, by = wx - ex * t, wy - ey * t
            d = min(d, bx * bx + by * by)
            c = (y >= vy, y < pts[j][1], ex * wy > ey * wx)
            if all(c) or not any(c):
                inside = not inside
            j = i
        return -math.sqrt(d) if inside else math.sqrt(d)
    return sdf


def heart(cx, top, width, roundness=0.0):
    """A heart `width` px wide (roundness included) whose top is at `top` (Inigo Quilez's exact sdHeart:
    in its unit space the tip is at the origin, y up, and it is 1.2071 wide and 1.1036 tall)."""
    scale = (width - 2 * roundness) / (2 * (0.25 + math.sqrt(2) / 4))
    tip = top + roundness + 1.1036 * scale

    def unit(px, py):
        px = abs(px)
        if py + px > 1.0:
            return math.sqrt((px - 0.25) ** 2 + (py - 0.75) ** 2) - math.sqrt(2) / 4
        a = px * px + (py - 1.0) ** 2
        t = 0.5 * max(px + py, 0.0)
        b = (px - t) ** 2 + (py - t) ** 2
        return math.sqrt(min(a, b)) * (1.0 if px > py else -1.0 if px < py else 0.0)
    return lambda x, y: unit((x - cx) / scale, (tip - y) / scale) * scale - roundness


def star(cx, cy, outer, inner_ratio, roundness=0.0, points=5):
    """A star with `points` tips, the first pointing up, its tips rounded by `roundness` px."""
    r = outer - roundness
    corners = []
    for i in range(2 * points):
        a = -math.pi / 2 + math.pi * i / points
        rr = r if i % 2 == 0 else r * inner_ratio
        corners.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return grow(polygon(corners), roundness)


def union(*sdfs):
    return lambda x, y: min(s(x, y) for s in sdfs)


def intersect(a, b):
    return lambda x, y: max(a(x, y), b(x, y))


def subtract(a, b):
    return lambda x, y: max(a(x, y), -b(x, y))


def grow(sdf, d):
    return lambda x, y: sdf(x, y) - d


def shifted(sdf, dx, dy):
    """The shape moved by (-dx, -dy): it holds a point when the original holds that point + (dx, dy)."""
    return lambda x, y: sdf(x + dx, y + dy)


def rotated(sdf, cx, cy, degrees):
    """`sdf` drawn in a frame turned `degrees` clockwise about (cx, cy) (y is down)."""
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return lambda x, y: sdf(cx + c * (x - cx) + s * (y - cy), cy - s * (x - cx) + c * (y - cy))


# --- Drawing ------------------------------------------------------------------------------------

_OFFSETS = [(i + 0.5) / SAMPLES for i in range(SAMPLES)]


def coverage(sdf, x: int, y: int) -> float:
    d = sdf(x + 0.5, y + 0.5)
    if d >= BAND:
        return 0.0
    if d <= -BAND:
        return 1.0
    hits = sum(1 for oy in _OFFSETS for ox in _OFFSETS if sdf(x + ox, y + oy) < 0)
    return hits / (SAMPLES * SAMPLES)


def draw(layers, width: int = SIZE, height: int = SIZE) -> bytes:
    """RGBA bytes (straight alpha, sRGB) of (sdf, linear colour) layers drawn bottom to top."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            r = g = b = a = 0.0  # premultiplied, linear
            for sdf, (cr, cg, cb, ca) in layers:
                k = coverage(sdf, x, y) * ca
                if k:
                    r, g, b, a = cr * k + r * (1 - k), cg * k + g * (1 - k), cb * k + b * (1 - k), k + a * (1 - k)
            if a <= 0.0:
                out += b"\0\0\0\0"
                continue
            out += bytes(round(255 * min(1.0, max(0.0, _srgb(c / a)))) for c in (r, g, b))
            out.append(round(255 * a))
    return bytes(out)


def token(shape, palette, highlight=None):
    """The icon style: a darker outline around the shape, the fill, a shaded lower-right rim and a
    highlight (kept 3 px inside the shape)."""
    layers = [(grow(shape, OUTLINE), palette["outline"]), (shape, palette["fill"]),
              (subtract(shape, shifted(shape, *SHADE)), palette["shade"])]
    if highlight is not None:
        layers.append((intersect(highlight, grow(shape, -3.0)), palette["highlight"]))
    return layers


RED = {"fill": colour("#FF4D5E"), "outline": colour("#9E1B32"), "shade": colour("#DB2F48"),
       "highlight": colour("#FFC2C8")}
GOLD = {"fill": colour("#FFC83D"), "outline": colour("#A3610C"), "shade": colour("#F09F1F"),
        "highlight": colour("#FFF3C4")}
GREY = {"fill": colour("#5A6273"), "outline": colour("#262B35"), "shade": colour("#4A5161"),
        "highlight": colour("#8A93A6", 0.7)}
INNER_GOLD = colour("#F4AE2A")  # the coin's face
SLOT_GOLD = colour("#FFE48C")  # the coin's raised bar
TEAL = {"outline": colour("#0B5670"), "highlight": colour("#FFFFFF", 0.9)}
# The starter's gems (the collectible kit's gem material, #23B5D8), facets lit from the upper left.
TEAL_FACETS = {"top": ("#9BEAF8", "#62D5EF", "#38C2E2"), "band": ("#4CCBE8", "#23B5D8", "#1A9BC0"),
               "bottom": ("#1FA6CA", "#178EB2", "#107193")}


# --- The icons ----------------------------------------------------------------------------------


def _heart_shape():
    return heart(64, 14, 108, roundness=4)


def _heart_highlight():
    return capsule(34, 41, 44, 31, 5.5)


def heart_full():
    return token(_heart_shape(), RED, _heart_highlight())


def heart_empty():
    return token(_heart_shape(), GREY, _heart_highlight())


def _star_shape():
    return star(64, 68, 56, 0.5, roundness=6)


def _star_highlight():
    return capsule(50, 42, 57, 31, 4.5)


def star_full():
    return token(_star_shape(), GOLD, _star_highlight())


def star_empty():
    return token(_star_shape(), GREY, _star_highlight())


def coin():
    shape = circle(64, 64, 54)
    layers = token(shape, GOLD)
    layers += [(circle(64, 64, 37), INNER_GOLD), (box(64, 64, 8.5, 22, 8.5), SLOT_GOLD),
               (intersect(capsule(28, 54, 37, 33, 4.5), grow(shape, -3.0)), GOLD["highlight"])]
    return layers


def gem():
    """An upright crystal like the starter's gem pickups: a six-sided band with pointed ends."""
    cx, top, bottom, half_w, band = 64, 10, 118, 40, 11
    mid = (top + bottom) / 2
    xs = (cx - half_w, cx - half_w / 2, cx + half_w / 2, cx + half_w)
    y0, y1 = mid - band, mid + band
    shape = polygon([(cx, top), (xs[3], y0), (xs[3], y1), (cx, bottom), (xs[0], y1), (xs[0], y0)])
    layers = [(grow(shape, OUTLINE), TEAL["outline"]), (shape, colour(TEAL_FACETS["band"][1]))]
    for i in range(3):
        a, b = xs[i], xs[i + 1]
        layers += [(polygon([(cx, top), (b, y0), (a, y0)]), colour(TEAL_FACETS["top"][i])),
                   (polygon([(a, y0), (b, y0), (b, y1), (a, y1)]), colour(TEAL_FACETS["band"][i])),
                   (polygon([(a, y1), (b, y1), (cx, bottom)]), colour(TEAL_FACETS["bottom"][i]))]
    layers.append((intersect(capsule(46, 38, 52, 28, 3.5), grow(shape, -3.0)), TEAL["highlight"]))
    return layers


def key():
    """A gold key lying diagonally: the bow (a ring) at the upper left, the teeth at the lower right."""
    # Drawn along +x in units of s px about (cx, cy), then turned 45 degrees clockwise (+y is the side
    # of the teeth) and moved by (dx, dy) to centre it.
    cx, cy, s, dx, dy = 64, 64, 1.15, 6, 1

    def at(u, v):
        return cx + s * u, cy + s * v
    bow = ring(*at(-31, 0), 18 * s, 7.5 * s)
    shaft = box(*at(14, 0), 33 * s, 6.5 * s, 3 * s)
    teeth = union(box(*at(39, 10), 6.5 * s, 10 * s, 2 * s), box(*at(22, 8.5), 5 * s, 8.5 * s, 2 * s))
    shape = shifted(rotated(union(bow, shaft, teeth), cx, cy, 45), -dx, -dy)
    shine = shifted(rotated(capsule(*at(-2, -2.5), *at(22, -2.5), 2.5 * s), cx, cy, 45), -dx, -dy)
    return token(shape, GOLD, shine)


ICONS = {"heart": heart_full, "heart-empty": heart_empty, "star": star_full, "star-empty": star_empty,
         "coin": coin, "gem": gem, "key": key}


# --- 9-slice art --------------------------------------------------------------------------------

CARD = {"fill": colour("#12161F", 0.9), "border": colour("#FFFFFF", 0.16)}  # the starter's UI_CARD
SOLID = {"fill": colour("#12161F"), "border": colour("#FFFFFF", 0.12)}  # UI_CARD, opaque
LIGHT = {"outline": colour("#1B2130"), "lip": colour("#AEB9CD"), "face": colour("#F4F6FB"),
         "shine": colour("#FFFFFF", 0.9)}
GROOVE = {"fill": colour("#0B0E14", 0.55), "border": colour("#FFFFFF", 0.14)}
ACCENT = {"lip": colour("#E3A600"), "face": colour("#FFD400"), "shine": colour("#FFF4B0", 0.85)}  # UI_ACCENT
FRAMED = {"outline": colour("#090C14"), "gold": colour("#D6A23F"), "gold_light": colour("#F8DC8E"),
          "gold_dark": colour("#7D4F12"), "body": colour("#17223D"), "shadow": colour("#04060C", 0.5),
          "line": colour("#D6A23F", 0.55)}
STUD = {"outline": colour("#3A2406"), "fill": colour("#F2BE4E"), "shade": colour("#C2852A"),
        "highlight": colour("#FFF3C4")}
PARCHMENT = {"outline": colour("#5B4129"), "lip": colour("#C7B08A"), "face": colour("#F5EDDC"),
             "edge": colour("#B48D57", 0.4), "line": colour("#A27E4B", 0.6)}


def band(shape, outer, inner):
    """The part of `shape` from `outer` to `inner` px inside its edge."""
    return subtract(grow(shape, -outer), grow(shape, -inner))


def inner_shadow(shape, start, width, tint):
    """A soft tint inside `shape`, at full strength `start` px inside its edge and gone `width` px further in:
    1 px bands stacked, so it fades linearly (all of it stays within `start + width` of the edge)."""
    n = max(1, round(width))
    r, g, b, a = tint
    share = 1 - (1 - a) ** (1 / n)  # n layers of this alpha add up to `a`
    return [(band(shape, start, start + width * (i + 1) / n), (r, g, b, share)) for i in range(n)]


def card(width, height, radius, palette, border=2.0):
    """A fill with a light border along its edge, filling the image."""
    shape = box(width / 2, height / 2, width / 2, height / 2, radius)
    return [(shape, palette["fill"]), (subtract(shape, grow(shape, -border)), palette["border"])]


def raised(shape, palette, lip, outline=0.0, shine=None):
    """A face lifted `lip` px above a darker lip, inside an optional outline, with a shine near its top."""
    body = grow(shape, -outline) if outline else shape
    layers = [(shape, palette["outline"])] if outline else []
    layers += [(body, palette["lip"]), (intersect(body, shifted(body, 0, lip)), palette["face"])]
    if shine is not None:
        layers.append((intersect(shine, grow(body, -2.0)), palette["shine"]))
    return layers


def panel():
    return card(128, 128, 28, CARD)


def panel_solid():
    return card(128, 128, 40, SOLID)


def panel_framed():
    """Navy inside a bevelled gold frame, a gold hairline 18 px in and a stud on each corner."""
    shape = box(64, 64, 64, 64, 8)
    corners = [(x, y) for x in (11, 117) for y in (11, 117)]
    studs = union(*(circle(x, y, 9) for x, y in corners))
    shine = union(*(capsule(x - 4, y - 0.5, x - 0.5, y - 4, 2) for x, y in corners))
    return ([(shape, FRAMED["outline"]), (band(shape, 2, 10), FRAMED["gold"]),
             (band(shape, 2, 4), FRAMED["gold_light"]), (band(shape, 8.5, 10), FRAMED["gold_dark"]),
             (grow(shape, -10), FRAMED["body"])]
            + inner_shadow(shape, 10, 12, FRAMED["shadow"])
            + [(band(shape, 18, 20), FRAMED["line"]),
               (grow(studs, 1.5), STUD["outline"]), (studs, STUD["fill"]),
               (subtract(studs, shifted(studs, 2, 2.5)), STUD["shade"]),
               (intersect(shine, grow(studs, -2)), STUD["highlight"])])


def panel_light():
    """A parchment card lifted on a darker lip, in a brown outline, its edges warmer, a hairline 12 px in."""
    shape = box(64, 64, 64, 64, 22)
    body = grow(shape, -3)
    face = intersect(body, shifted(body, 0, 6))
    return ([(shape, PARCHMENT["outline"]), (body, PARCHMENT["lip"]), (face, PARCHMENT["face"])]
            + inner_shadow(face, 0, 10, PARCHMENT["edge"])
            + [(band(face, 12, 14), PARCHMENT["line"])])


def button():
    return raised(box(48, 48, 48, 48, 38), LIGHT, lip=5, outline=3, shine=capsule(26, 11, 70, 11, 2.5))


def slider_track():
    return card(64, 32, 12, GROOVE)


def slider_fill():
    return raised(box(32, 16, 32, 16, 12), ACCENT, lip=2, shine=capsule(10, 6, 54, 6, 1.5))


def slider_knob():
    return raised(circle(24, 24, 24), LIGHT, lip=3, outline=3, shine=capsule(16, 13, 21, 10, 2.5))


# name: (make, (width, height), 9-slice insets [left, right, top, bottom] or None), as the docstring lists them
SLICES = {"panel": (panel, (128, 128), (32, 32, 32, 32)),
          "panel-solid": (panel_solid, (128, 128), (44, 44, 44, 44)),
          "panel-framed": (panel_framed, (128, 128), (32, 32, 32, 32)),
          "panel-light": (panel_light, (128, 128), (32, 32, 32, 32)),
          "button": (button, (96, 96), (44, 44, 44, 44)),
          "slider-track": (slider_track, (64, 32), (14, 14, 14, 14)),
          "slider-fill": (slider_fill, (64, 32), (14, 14, 14, 14)), "slider-knob": (slider_knob, (48, 48), None)}


# --- PNG ----------------------------------------------------------------------------------------


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _filtered(rgba: bytes, width: int, height: int) -> bytes:
    """Scanlines with a filter byte each: per line the filter with the smallest sum of signed bytes
    (the usual heuristic; a choice made from the pixels alone, so the same every run)."""
    stride, bpp = width * 4, 4
    out, prev = bytearray(), bytes(stride)
    for y in range(height):
        line = rgba[y * stride:(y + 1) * stride]
        left = bytes(bpp) + line[:-bpp]
        up_left = bytes(bpp) + prev[:-bpp]
        candidates = [
            line,
            bytes((line[i] - left[i]) & 255 for i in range(stride)),
            bytes((line[i] - prev[i]) & 255 for i in range(stride)),
            bytes((line[i] - ((left[i] + prev[i]) >> 1)) & 255 for i in range(stride)),
            bytes((line[i] - _paeth(left[i], prev[i], up_left[i])) & 255 for i in range(stride)),
        ]
        cost = [sum(v if v < 128 else 256 - v for v in c) for c in candidates]
        kind = cost.index(min(cost))
        out.append(kind)
        out += candidates[kind]
        prev = line
    return bytes(out)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_bytes(rgba: bytes, width: int = SIZE, height: int = SIZE) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(_filtered(rgba, width, height), 9)) + _chunk(b"IEND", b""))


def png_pixels(data: bytes) -> tuple[int, int, bytes]:
    """(width, height, RGBA bytes) of an 8-bit RGBA PNG, as this script writes them."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour_type, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if (depth, colour_type, interlace) != (8, 6, 0):
                raise ValueError("not an 8-bit RGBA PNG without interlacing")
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw, stride, bpp = zlib.decompress(idat), width * 4, 4
    out, prev = bytearray(), bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            c = prev[i - bpp] if i >= bpp else 0
            predictor = (0, a, prev[i], (a + prev[i]) >> 1, _paeth(a, prev[i], c))[kind]
            line[i] = (line[i] + predictor) & 255
        out += line
        prev = line
    return width, height, bytes(out)


def render() -> dict[str, bytes]:
    """File name -> PNG bytes, for every icon and 9-slice image."""
    files = {f"{name}.png": png_bytes(draw(make())) for name, make in ICONS.items()}
    for name, (make, (width, height), _insets) in SLICES.items():
        files[f"{name}.png"] = png_bytes(draw(make(), width, height), width, height)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help=f"folder to write (default: {OUT})")
    parser.add_argument("--check", action="store_true", help="compare with the files instead of writing")
    args = parser.parse_args()
    files = render()
    if args.check:  # pixels, not bytes: another zlib compresses the same pixels differently
        stale = [name for name, data in files.items()
                 if not (args.out / name).is_file() or png_pixels((args.out / name).read_bytes()) != png_pixels(data)]
        print(f"Out of date: {', '.join(stale)}" if stale else f"{len(files)} images up to date in {args.out}")
        return 1 if stale else 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (args.out / name).write_bytes(data)
        print(f"{args.out / name}  {len(data) / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
