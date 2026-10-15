# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI export (docs/GAME-UI.md §3, §4): the canvases of a UI file (ui/*.blend) become
.gamestudio/export/ui/<Canvas>.ui.json, listed in .gamestudio/export/ui/index.json.

extract_canvas(scene) reads one canvas of the open UI file into the §3.2 object (format.ts
UiCanvasData) without writing anything. export_canvas(scene) writes one canvas file and its index
entry (the live link); export_ui_file() every canvas of the open UI file and the index;
export_ui_file_background(path) another UI file, in a background Blender; export_all_ui() every UI
file of the project (export.export_all() calls it); export_stale() the UI files whose canvases are
missing, older than their .blend or written from unsaved changes (Play; see unsaved_canvases()). Without
the UI components in the registry, exports refuse. play_export() is what Play and the bridge's export_scene do
in a UI file: export it, then return the start level's export (made in a background Blender only
when it is missing).

Phase 2 (§10): Object.color[3] is every element's "opacity" (written when below 1; on an image it
stands in for the image's own "opacity", which keeps its Phase 1 value). Timeline markers open, close
and loop name animations (§10.5): each runs to the next marker or the scene's end, and the rest pose (the
static transforms) is the scene where open ends, else at the current frame. Tracks are sampled once per
frame at the scene's rate from the F-curves of the active actions (_Sampler), never by changing the
scene's frame: frame_set()
writes animated values back into the objects, so an unkeyed edit would snap back on every live export.
Constraints, drivers and NLA strips are not sampled (export warns). A mesh with UiSlice and an image
texture gets mesh.slice (§10.6); its size is the builder's gs_ui_size property, else the mesh bounds. A
UiButton next to a UiSlider or UiToggle is dropped with a warning.

A canvas file is rewritten only when its content changed (its rev and exportedAt aside), so saving or
playing again does not bump revisions. Revisions count up per canvas file, from the file on disk and
this session's highest, and are never stored in the .blend (exporting on save would dirty it again).
Asset files are copied into export/files/ as for levels (export.copy_assets); packed and generated
images are written as PNG into files/generated/ (packed fonts likewise, as they are), and Blender's
built-in font is exported as its bundled Inter.woff2 under the key assets/fonts/Inter.woff2. URLs in
"assets" are relative to the canvas file. Each exported font gets its licence file next to it
(<font name>-OFL.txt: the one next to the font, else, for Blender's Inter, resources/fonts/Inter-OFL.txt).

Text layout, measured in Blender 5.2 (font units below are Blender units at size 1). The runtime needs
three numbers per text that it cannot get from the browser, exported next to the §3.2 fields:
  fontSize  the CSS font size in px that draws the glyphs as big as Blender does. Blender scales a
            font by 1 / (head.yMax - head.yMin), not by 1 / unitsPerEm, so fontSize = size * upem /
            (yMax - yMin) px. null when the font file cannot be read (WOFF2 other than Blender's Inter).
  ascent,   Blender's ascent and descent in px: FreeType's ascender / descender (the hhea values;
  descent   OS/2 when those are 0) over (yMax - yMin), times size.
Lines advance by lineSpacing * size exactly. With letter spacing s and w = wordSpacing for a space
(1 for other characters), a character advances by advance * (w + s - 1) when s < 1, else by
advance * w + (s - 1) * size / 2; a line's width leaves out the last character's (s - 1) * size / 2.
Blender applies no font kerning. Vertical alignment, y down from the origin, with L lines, T = ascent + descent +
(L - 1) * lineSpacing * size: top-baseline 0, top +ascent, center ascent - T / 2, bottom
-descent - (L - 1) * lineSpacing * size, bottom-baseline -(L - 1) * lineSpacing * size. A text box
with a height has its frame one size above Blender's text box y; "box" is that frame [x, y, w, h], and
in it: top-baseline y + size, top y + ascent, center y + h / 2 - T / 2 + ascent, bottom
y + h - descent - (L - 1) * lineSpacing * size, bottom-baseline y + h - (L - 1) * lineSpacing * size.
A box without height ([x, y, w, 0], y = Blender's box y) only wraps; the rules without a box apply
about y. Lines align left / center / right within the box width (around x without a box), wrap at
spaces when wider than the box (a longer word is not broken), and shear slants each line about its
own baseline. offset moves the box and the text together.

Phase 3 (§11):
* Models (§11.1): the objects under an empty with UiModel are not elements. They go into <Canvas>.ui.glb
  (gameui.model_file) through Blender's glTF exporter, as levels do: a temporary scene (gs_ui_models)
  holds, per model, an empty at the origin (its root node, named after the element id) with copies of
  the model's objects under it. The copies share their data, actions and materials, sit in the UiModel
  empty's space (its own transform left out) and point their modifiers, constraints and drivers at
  each other, so the user's objects are never changed and the exporter's frame changes touch only the
  copies. Armature clips are sampled by clips.py, procedural materials baked (bake.py), textures
  processed as for levels; game shaders go into the canvas's "materials" (export.shader_section). The
  element is {"type": "model", "model": {glb, node, clips}}; the GLB's hash is "files" in the canvas and
  its index entry. A signature of everything the GLB depends on (_model_signature: objects, their
  local transforms, mesh data, modifiers, materials, actions, images, instanced collections, settings),
  kept in memory and in the GLB's asset extras, skips the exporter while it is unchanged: the live link
  re-exports the GLB only when model content changed.
* Game shaders (§11.4): a UI mesh's material with a game shader writes mesh.material.shader {name,
  values}; asset values are listed in "assets".
* Variable fonts (§11.5): text.fontAxes is the instance Blender draws. Measured in Blender 5.2 (Inter,
  Sitka, Segoe UI Variable, Bahnschrift): text objects are laid out at optical size 16 whatever their
  size (the advances match HVAR at opsz 16), every other axis at its default. TTF, OTF and WOFF fonts
  are read (fvar); WOFF2 needs Brotli, which Blender's Python lacks, so only Blender's own Inter is known
  (INTER_AXES) and other variable WOFF2 fonts warn.
* Translations (§11.3): export_locales() checks ui/locales/<code>.json and copies them to
  .gamestudio/export/ui/locales/, and the index gets sourceLocale (gamestudio.json "locale", default
  "en") and locales {code: {file, name}}.
"""

import contextlib
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import struct
import subprocess
import time
import zlib

import bpy
from bpy_extras import anim_utils
from mathutils import Euler, Matrix, Quaternion, Vector

from . import components, export, gameui, ids, project, registry
from .log import get_logger

log = get_logger(__name__)

MAX_TRIANGLES = 20000
GENERATED_DIR = "generated"
BUILTIN_FONT = "<builtin>"
BUILTIN_FONT_KEY = "assets/fonts/Inter.woff2"
# Inter is under the SIL Open Font License 1.1, which wants its copyright notice and licence with every
# copy, and Blender's datafiles/fonts has none: this is Inter's LICENSE.txt (github.com/rsms/inter, v4).
INTER_LICENCE = pathlib.Path(__file__).resolve().parents[1] / "resources" / "fonts" / "Inter-OFL.txt"
# A font's licence next to it, by the name GameStudio gives it (<font name>-OFL.txt) or a shared one.
FONT_LICENCE_NAMES = ("{stem}-OFL.txt", "{stem}-LICENSE.txt", "{stem}-LICENCE.txt", "OFL.txt", "LICENSE.txt")
EXPORT_LINE = "GS_UI_EXPORT "
LEVEL_EXPORT_TIMEOUT = 600.0

DEFAULT_SETTINGS = {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
                    "scaleMode": "fit", "backdrop": [0.0, 0.0, 0.0, 0.0], "layer": 0, "focus": None}
DEFAULT_MESH_COLOR = [0.8, 0.8, 0.8, 1.0]  # a material-less mesh, as Blender shows it
WHITE = [1.0, 1.0, 1.0, 1.0]
MESH_TYPES = ("MESH", "CURVE", "SURFACE")
SILENT_TYPES = ("LIGHT",)
# Animated properties a track is sampled from (§10.5): the transform, and the colour alpha (opacity).
TRANSFORM_PATHS = frozenset(("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                             "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale"))
OPACITY_CURVE = ("color", 3)
ROTATION_PATHS = {"QUATERNION": ("rotation_quaternion", "delta_rotation_quaternion"),
                  "AXIS_ANGLE": ("rotation_axis_angle",)}  # by rotation mode; the Euler orders use the others
TRACK_DIGITS = {"x": 3, "y": 3, "rotation": 6, "scaleX": 6, "scaleY": 6, "opacity": 4}
CONTROLS = ("UiSlider", "UiToggle")       # focusable controls; a UiButton next to one is dropped (§10.4)
VALUE_COMPONENTS = ("UiShow", "UiSlider", "UiToggle")  # need a value path (§10.1)

# Blender's built-in text font (a Type 1 font compiled into Blender, not Inter) has the default
# vertical metrics 0.8 / 0.2. The game draws Blender's Inter in its place, sized so that capitals are
# as tall as Blender draws them: 0.682 font units for the built-in font, 1490 / 2048 em for Inter.
BUILTIN_METRICS = (0.8, 0.2, 0.682 / (1490 / 2048))
INTER_ASCENDER = 1984 / 2048  # hhea ascender / unitsPerEm of Blender's bundled Inter (WOFF2: measured)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FONT_EXTENSIONS = {b"wOF2": ".woff2", b"wOFF": ".woff", b"OTTO": ".otf", b"ttcf": ".ttc"}
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".woff", ".woff2")

# Variable fonts (§11.5): the optical size Blender lays text objects out at, and the axes of its bundled
# Inter.woff2 (tag, min, default, max), read with a Brotli decoder outside Blender.
OPTICAL_SIZE = 16.0
INTER_AXES = (("opsz", 14.0, 14.0, 32.0), ("wght", 100.0, 400.0, 900.0))
WOFF2_FVAR = 47  # 'fvar' in WOFF2's table of known tags (flags & 63)
WOFF2_GLYF_LOCA = (10, 11)

# Models (§11.1).
MODEL_SCENE = "gs_ui_models"   # the temporary scene the models are exported from
MODEL_ROOT = "gs_ui_model"     # each model's root empty in it (its node is renamed to the element id)
MODEL_FORMAT = 1               # in the signature: bump when the GLB changes for the same content
LINK_PROP = "gs_ui_link"       # gameui/build.py: a model's collection of objects linked from a level (§11.1)
LINK_BASIS_PROP = "gs_ui_link_basis"  # gameui/build.py: on its instance, the linked root's place and turn it cancels

# Translations (§11.3): a table's file name is its language code (en, fr, pt-BR, zh_Hant, ...).
LOCALE_CODE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*$")

_revisions: dict = {}     # canvas file path -> highest rev written in this session
_font_cache: dict = {}    # font key -> (ascent, descent, em or None)
_axes_cache: dict = {}    # font key -> (fontAxes or None, problem or None)
_png_cache: dict = {}     # image key -> PNG bytes
_models_cache: dict = {}  # models GLB path -> what _export_models() wrote ("signature", "stat", "hash", ...)
_inter_data = {}          # "data": the bytes of Blender's Inter.woff2, read once
_extracting = False


def is_extracting() -> bool:
    """True while a canvas is read (the live link ignores the depsgraph noise)."""
    return _extracting


def clear_caches() -> None:
    """Forget measured fonts, encoded images and exported models (another file was opened)."""
    _font_cache.clear()
    _axes_cache.clear()
    _png_cache.clear()
    _models_cache.clear()


# --- Small helpers ---------------------------------------------------------------------------------


def _r(value: float, digits: int = 3) -> float:
    value = round(float(value), digits)
    return 0.0 if value == 0 else value  # no -0.0 in the files


def _now() -> str:
    return (datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    export._write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))


def _read_json(path: pathlib.Path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def digest(data: dict) -> str:
    """A canvas's content without rev and exportedAt (what decides whether it changed)."""
    body = {k: v for k, v in data.items() if k not in ("rev", "exportedAt")}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class _Extraction:
    """What reading one canvas collects besides the canvas object."""

    def __init__(self, scene):
        self.scene = scene
        self.warnings = []
        self.assets = []        # asset values (project-relative paths) in first-seen order
        self.generated = {}     # "generated/<file>" -> bytes to write
        self.builtin_font = False
        self.triangles = 0
        self.fonts = {}         # VectorFont (or None) -> (asset key, metrics, axes): texts share a few fonts
        self.materials = {}     # (Material or None, default colour) -> material_info()
        self.shaders = {}       # Material -> its game shader block (§11.4) or None
        self.models = []        # §11.1: {"object", "id", "members", "element"} per UiModel empty

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def asset(self, value: str) -> str:
        if value and value not in self.assets:
            self.assets.append(value)
        return value


# --- Fonts ------------------------------------------------------------------------------------------


def _sfnt_tables(data: bytes) -> dict | None:
    """head, hhea and OS/2 of a TrueType/OpenType font, a collection's first font or a WOFF file."""
    tables = {}
    try:
        if data[:4] == b"wOFF":
            count = struct.unpack(">H", data[12:14])[0]
            for i in range(count):
                tag, offset, length, original, _sum = struct.unpack(">4sIIII", data[44 + 20 * i:64 + 20 * i])
                raw = data[offset:offset + length]
                tables[tag] = zlib.decompress(raw) if length < original else raw
            return tables
        base = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" else 0
        if data[base:base + 4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            return None
        count = struct.unpack(">H", data[base + 4:base + 6])[0]
        for i in range(count):
            record = base + 12 + 16 * i
            tag, _sum, offset, length = struct.unpack(">4sIII", data[record:record + 16])
            tables[tag] = data[offset:offset + length]
        return tables
    except (struct.error, zlib.error):
        return None


def sfnt_metrics(data: bytes) -> tuple | None:
    """(ascent, descent, em) in Blender's font units (see the module docstring), or None for a file
    this cannot read (WOFF2, Type 1)."""
    tables = _sfnt_tables(data)
    if not tables or b"head" not in tables or b"hhea" not in tables:
        return None
    try:
        head, hhea = tables[b"head"], tables[b"hhea"]
        upem = struct.unpack(">H", head[18:20])[0]
        y_min, y_max = struct.unpack(">h", head[38:40])[0], struct.unpack(">h", head[42:44])[0]
        ascender, descender = struct.unpack(">hh", hhea[4:8])
        os2 = tables.get(b"OS/2")
        if os2 is not None and len(os2) >= 72 and struct.unpack(">H", os2[62:64])[0] & 128:
            # USE_TYPO_METRICS (OS/2 fsSelection bit 7): FreeType takes the typographic metrics instead
            # of hhea's (measured in Blender: OptimisticText-Bold, typo 1460 / -540, hhea 2044 / -533).
            ascender, descender = struct.unpack(">hh", os2[68:72])
        elif not (ascender or descender) and os2 is not None and len(os2) >= 78:
            # FreeType's fallbacks: the typographic metrics, then the Windows ones.
            ascender, descender = struct.unpack(">hh", os2[68:72])
            if not (ascender or descender):
                win_ascent, win_descent = struct.unpack(">HH", os2[74:78])
                ascender, descender = win_ascent, -win_descent
    except struct.error:
        return None
    height = y_max - y_min
    if height <= 0 or upem <= 0:
        return None
    return ascender / height, -descender / height, upem / height


def _base128(data: bytes, at: int) -> tuple:
    """A WOFF2 UIntBase128 value and the offset after it."""
    value = 0
    for _i in range(5):
        byte = data[at]
        at += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return value, at


def _woff2_tags(data: bytes) -> list | None:
    """The table tags of a WOFF2 file, from its table directory (the tables are Brotli-compressed)."""
    try:
        count = struct.unpack(">H", data[12:14])[0]
        at, tags = 48, []
        for _i in range(count):
            flags = data[at]
            at += 1
            index = flags & 0x3F
            if index == 0x3F:
                tag = data[at:at + 4]
                at += 4
            else:
                tag = b"fvar" if index == WOFF2_FVAR else bytes([index])  # only fvar's tag matters here
            _length, at = _base128(data, at)
            version = flags >> 6
            if (version == 0) if index in WOFF2_GLYF_LOCA else (version != 0):  # transformed: a second length
                _length, at = _base128(data, at)
            tags.append(tag)
        return tags
    except (IndexError, struct.error):
        return None


def sfnt_axes(data: bytes) -> list | None:
    """[(tag, min, default, max)] of a font's variation axes (fvar): [] for a static font, None when they
    cannot be read (a variable WOFF2 font: Blender's Python has no Brotli; a file that is not a font)."""
    if data[:4] == b"wOF2":
        tags = _woff2_tags(data)
        return None if tags is None or b"fvar" in tags else []
    tables = _sfnt_tables(data)
    if tables is None:
        return None
    fvar = tables.get(b"fvar")
    if fvar is None:
        return []
    try:
        offset, _reserved, count, size = struct.unpack(">HHHH", fvar[4:12])
        axes = []
        for i in range(count):
            tag, low, default, high = struct.unpack(">4siii", fvar[offset + i * size:offset + i * size + 16])
            axes.append((tag.decode("latin-1"), low / 65536, default / 65536, high / 65536))
        return axes
    except struct.error:
        return None


def drawn_axes(axes) -> dict:
    """{tag: value} of the instance Blender draws a variable font at: optical size 16 (within the axis's
    range), every other axis at its default (see the module docstring)."""
    return {tag: _r(min(max(OPTICAL_SIZE, low), high) if tag == "opsz" else default, 4)
            for tag, low, default, high in axes}


def _font_path(vfont) -> str:
    return bpy.path.abspath(vfont.filepath, library=vfont.library)


def _font_bytes(vfont) -> bytes | None:
    if vfont.packed_file is not None:
        return bytes(vfont.packed_file.data)
    try:
        return pathlib.Path(_font_path(vfont)).read_bytes()
    except OSError:
        return None


def _font_key(vfont):
    if vfont is None or vfont.filepath == BUILTIN_FONT:
        return BUILTIN_FONT
    if vfont.packed_file is not None:
        return ("packed", vfont.name_full, vfont.packed_file.size)
    path = _font_path(vfont)
    try:
        st = os.stat(path)
    except OSError:
        return ("missing", path)
    return (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)


def _bundled_inter() -> pathlib.Path | None:
    """Blender's own Inter.woff2 (datafiles/fonts), the font the game draws for the built-in one."""
    datafiles = bpy.utils.system_resource("DATAFILES")
    path = pathlib.Path(datafiles) / "fonts" / "Inter.woff2" if datafiles else None
    return path if path is not None and path.is_file() else None


def _is_bundled_inter(data: bytes | None) -> bool:
    """data is Blender's own Inter.woff2 (a copy of it in the project, say)."""
    if not data or data[:4] != b"wOF2":
        return False
    if "data" not in _inter_data:
        inter = _bundled_inter()
        try:
            _inter_data["data"] = inter.read_bytes() if inter is not None else None
        except OSError:
            _inter_data["data"] = None
    return _inter_data["data"] == data


def font_licence(font) -> pathlib.Path | None:
    """The licence file of a font file: one next to it (FONT_LICENCE_NAMES), else INTER_LICENCE when the
    font is Blender's own Inter (a copy anywhere). None when there is neither."""
    font = pathlib.Path(font)
    for name in FONT_LICENCE_NAMES:
        candidate = font.with_name(name.format(stem=font.stem))
        if candidate.is_file():
            return candidate
    inter = _bundled_inter()
    try:
        if inter is not None and font.stat().st_size == inter.stat().st_size and _is_bundled_inter(font.read_bytes()):
            return INTER_LICENCE if INTER_LICENCE.is_file() else None
    except OSError:
        pass
    return None


def font_licence_name(font_name: str, licence: pathlib.Path) -> str:
    """What a font's licence is called next to a copy of the font: <font name>-OFL.txt, or -LICENSE.txt."""
    kind = "OFL" if "ofl" in licence.name.lower() else "LICENSE"
    return f"{pathlib.PurePosixPath(font_name).stem}-{kind}.txt"


def _measure(vfont) -> tuple | None:
    """(ascent, descent) as Blender lays the font out: where a glyph's baseline goes for align_y TOP
    and BOTTOM. Loose curves and objects, never linked to a scene, removed again."""
    for body in ("H", "x", "0", "A"):
        lows = {}
        for align in ("TOP_BASELINE", "TOP", "BOTTOM"):
            curve = bpy.data.curves.new("gs_ui_measure", "FONT")
            obj = None
            try:
                curve.font = vfont
                curve.body = body
                curve.align_y = align
                obj = bpy.data.objects.new("gs_ui_measure", curve)
                mesh = obj.to_mesh()
                if len(mesh.vertices):
                    lows[align] = min(v.co.y for v in mesh.vertices)
                obj.to_mesh_clear()
            finally:
                if obj is not None:
                    bpy.data.objects.remove(obj)
                bpy.data.curves.remove(curve)
        if len(lows) == 3:
            base = lows["TOP_BASELINE"]
            return base - lows["TOP"], lows["BOTTOM"] - base
    return None


def font_metrics(vfont) -> tuple:
    """(ascent, descent, em) of a text font in Blender's font units: ascent and descent as Blender
    aligns text, em the CSS font size per unit of Blender's size (None when the file cannot be read).
    The built-in font gives Blender's defaults, with the em of the Inter the game draws instead."""
    key = _font_key(vfont)
    if key == BUILTIN_FONT:
        return BUILTIN_METRICS
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    data = _font_bytes(vfont)
    metrics = sfnt_metrics(data) if data else None
    if metrics is None:
        measured = _measure(vfont) if data else None
        if measured is None:
            metrics = BUILTIN_METRICS[:2] + (None,)
        else:
            metrics = measured + ((measured[0] / INTER_ASCENDER) if _is_bundled_inter(data) else None,)
    _font_cache[key] = metrics
    return metrics


def font_axes(vfont) -> tuple:
    """(text.fontAxes, problem) of a text font (§11.5): {tag: value} as Blender draws a variable font
    (drawn_axes), None for a static font or one that cannot be read; problem says why a variable font's
    axes are unknown (a WOFF2 other than Blender's Inter), else None. The built-in font is drawn as Inter."""
    key = _font_key(vfont)
    if key == BUILTIN_FONT:
        return drawn_axes(INTER_AXES), None
    cached = _axes_cache.get(key)
    if cached is not None:
        return cached
    data = _font_bytes(vfont)
    axes = sfnt_axes(data) if data else []
    if axes is None and data[:4] == b"wOF2":
        result = (drawn_axes(INTER_AXES), None) if _is_bundled_inter(data) else (
            None, f"Font '{vfont.name}' is a variable WOFF2 font, whose axes Blender's Python cannot read: the game "
                  "may draw it at another weight or optical size than Blender. Use its TTF or OTF file instead")
    else:
        result = (drawn_axes(axes) if axes else None, None)
    _axes_cache[key] = result
    return result


def _font_asset(vfont, ex: _Extraction) -> str:
    if vfont is None or vfont.filepath == BUILTIN_FONT:
        ex.builtin_font = True
        return BUILTIN_FONT_KEY
    if vfont.packed_file is not None:
        data = bytes(vfont.packed_file.data)
        suffix = pathlib.Path(vfont.filepath).suffix.lower() or FONT_EXTENSIONS.get(data[:4], ".ttf")
        key = f"{GENERATED_DIR}/{export._sanitize(pathlib.Path(vfont.name).stem)}-" \
              f"{hashlib.sha1(data).hexdigest()[:10]}{suffix}"
        ex.generated[key] = data
        return key
    path = _font_path(vfont)
    if not os.path.isfile(path):
        ex.warn(f"Font file not found: {path} (font '{vfont.name}'); the game uses a fallback font")
    return ex.asset(components._asset_for_export(path))


# --- Images ----------------------------------------------------------------------------------------


def _png(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[row * stride:(row + 1) * stride] for row in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def image_png(image) -> bytes | None:
    """A packed or generated image as PNG bytes: packed PNG files as they are, anything else from its
    pixels (float images converted to sRGB). None when the image has no pixels."""
    import numpy as np

    if image.packed_file is not None and bytes(image.packed_file.data[:8]) == PNG_SIGNATURE:
        return bytes(image.packed_file.data)
    width, height = image.size
    channels = image.channels
    if width <= 0 or height <= 0 or channels <= 0:
        return None
    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)
    rgba = np.ones((height, width, 4), dtype=np.float32)
    if channels >= 3:
        rgba[..., :3] = pixels[..., :3]
    else:
        rgba[..., :3] = pixels[..., :1]
    if channels in (2, 4):
        rgba[..., 3] = pixels[..., channels - 1]
    if image.is_float and image.colorspace_settings.name not in ("Non-Color", "Raw"):
        rgb = np.clip(rgba[..., :3], 0.0, 1.0)
        rgba[..., :3] = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    data = (np.clip(rgba, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)[::-1]  # Blender's rows go bottom-up
    return _png(width, height, data.tobytes())


def _image_key(image) -> tuple:
    packed = image.packed_file.size if image.packed_file is not None else 0
    return (image.name_full, image.session_uid, tuple(image.size), image.source, packed, image.generated_type,
            tuple(image.generated_color), image.generated_width, image.generated_height)


def _image_asset(image, ex: _Extraction, owner: str) -> str:
    """The asset key of an image: its file, or a generated PNG for a packed or generated one."""
    if image is None:
        return ""
    path = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ""
    if image.packed_file is None and image.source == "FILE" and path and os.path.isfile(path):
        return ex.asset(components._asset_for_export(path))
    if image.packed_file is None and image.source not in ("GENERATED", "FILE"):
        ex.warn(f"{owner}: image '{image.name}' is a {image.source.lower()}; only still images are exported")
    key = _image_key(image)
    data = None if image.is_dirty else _png_cache.get(key)
    if data is None:
        try:
            data = image_png(image)
        except (RuntimeError, ValueError) as exc:
            log.debug("Reading image %s failed: %s", image.name, exc)
            data = None
        if data is None:
            ex.warn(f"{owner}: image '{image.name}' has no pixels (file not found: {path or 'none'})")
            return ""
        if not image.is_dirty:
            _png_cache[key] = data
    stem = export._sanitize(pathlib.Path(image.name).stem)
    name = f"{GENERATED_DIR}/{stem}-{hashlib.sha1(data).hexdigest()[:10]}.png"
    ex.generated[name] = data
    return name


def _has_alpha(image) -> bool:
    return image is not None and image.alpha_mode != "NONE" and image.depth in (16, 32, 64, 128) and \
        image.channels in (2, 4)


# --- Materials (§3.5) -------------------------------------------------------------------------------


def _from_node(socket):
    """The node linked into an input socket (through reroutes), or None."""
    while socket is not None and socket.is_linked:
        node = socket.links[0].from_node
        if node.type != "REROUTE":
            return node, socket.links[0].from_socket
        socket = node.inputs[0] if node.inputs else None
    return None, None


def _output_node(tree):
    outputs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    active = [n for n in outputs if n.is_active_output and n.target in ("ALL", "EEVEE")]
    return (active or outputs or [None])[0]


def _color_input(socket, ex: _Extraction, mat_name: str) -> tuple:
    """(rgba, image) of a colour input: its value, or an Image Texture linked into it."""
    node, _out = _from_node(socket)
    if node is None:
        return list(socket.default_value), None
    if node.type == "TEX_IMAGE" and node.image is not None:
        return [1.0, 1.0, 1.0, 1.0], node.image
    ex.warn(f"Material '{mat_name}': only a value or an Image Texture is read from '{socket.name}' "
            f"(it is linked to {node.bl_label})")
    return list(socket.default_value), None


def _alpha_input(socket) -> tuple:
    """(alpha, from a texture) of an alpha or factor input."""
    node, _out = _from_node(socket)
    if node is None:
        return float(socket.default_value), False
    return 1.0, True


def material_info(mat, ex: _Extraction, default=DEFAULT_MESH_COLOR) -> dict:
    """{"color": linear RGBA, "image": Image or None, "texture_alpha": bool} as §3.5 reads a material
    (default: the colour without a material)."""
    if mat is None:
        return {"color": list(default), "image": None, "texture_alpha": False}
    tree = mat.node_tree
    output = _output_node(tree) if tree is not None else None
    shader, _out = _from_node(output.inputs.get("Surface")) if output is not None else (None, None)
    alpha, texture_alpha = 1.0, False
    if shader is not None and shader.type == "MIX_SHADER":
        first, _o1 = _from_node(shader.inputs[1])
        second, _o2 = _from_node(shader.inputs[2])
        factor, linked = _alpha_input(shader.inputs[0])
        if first is not None and first.type == "BSDF_TRANSPARENT":
            shader, alpha, texture_alpha = second, factor, linked
        elif second is not None and second.type == "BSDF_TRANSPARENT":
            shader, alpha, texture_alpha = first, 1.0 - factor, linked
        else:
            ex.warn(f"Material '{mat.name}': a Mix Shader is read only with a Transparent BSDF in it")
            shader = first
    image = None
    if shader is not None and shader.type in ("EMISSION", "BACKGROUND"):
        rgba, image = _color_input(shader.inputs["Color"], ex, mat.name)
        strength = shader.inputs["Strength"]
        power = float(strength.default_value) if not strength.is_linked else 1.0
        color = [min(max(c * power, 0.0), 1.0) for c in rgba[:3]] + [alpha]
    elif shader is not None and shader.type == "BSDF_PRINCIPLED":
        rgba, image = _color_input(shader.inputs["Base Color"], ex, mat.name)
        own_alpha, from_texture = _alpha_input(shader.inputs["Alpha"])
        color = list(rgba[:3]) + [alpha * own_alpha]
        texture_alpha = texture_alpha or from_texture
    else:
        color = list(mat.diffuse_color)
        color[3] *= alpha
    return {"color": [_r(c, 4) for c in color], "image": image, "texture_alpha": texture_alpha}


def _slot_material(obj):
    for slot in obj.material_slots:
        return slot.material
    return None


def _material_of(obj, ex: _Extraction, default=DEFAULT_MESH_COLOR) -> dict:
    """material_info() of obj's first material, read once per material in an extraction (panels share them)."""
    key = (_slot_material(obj), tuple(default))
    if key not in ex.materials:
        ex.materials[key] = material_info(key[0], ex, default=default)
    return ex.materials[key]


# --- Elements -----------------------------------------------------------------------------------------


def _element_type(obj, camera) -> str | None:
    if obj == camera:
        return None
    if obj.type == "FONT":
        return "text"
    if obj.type == "EMPTY":
        return "image" if obj.empty_display_type == "IMAGE" and obj.data is not None else "group"
    if obj.type in MESH_TYPES:
        return "mesh"
    return None


def _decompose(matrix, mirrored: bool) -> tuple:
    """(x, y, rotation, scale x, scale y) of a matrix's XY part, y up, rotation counter-clockwise.
    A mirrored object keeps its negative scale on X rather than a half turn."""
    a, b = matrix[0][0], matrix[1][0]
    c, d = matrix[0][1], matrix[1][1]
    sx = math.hypot(a, b)
    if sx < 1e-12:
        return matrix[0][3], matrix[1][3], 0.0, 0.0, math.hypot(c, d)
    theta = math.atan2(b, a)
    sy = (a * d - b * c) / sx
    if sy < 0 and mirrored:
        theta, sx, sy = theta + math.pi, -sx, -sy
        theta = math.atan2(math.sin(theta), math.cos(theta))
    return matrix[0][3], matrix[1][3], theta, sx, sy


def _tilted(matrix) -> bool:
    """Rotated about X or Y: an XY axis leaves the plane."""
    for col in (0, 1):
        length = math.sqrt(sum(matrix[row][col] ** 2 for row in range(3))) or 1.0
        if abs(matrix[2][col]) > 1e-4 * length:
            return True
    return False


def _sheared(matrix) -> bool:
    """The XY axes are not perpendicular (a rotated child of an unevenly scaled parent, kept in place by its
    parent inverse): translate · rotate · scale cannot write that skew."""
    ax, ay, bx, by = matrix[0][0], matrix[1][0], matrix[0][1], matrix[1][1]
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    return la > 1e-9 and lb > 1e-9 and abs(ax * bx + ay * by) > 1e-4 * la * lb


def _transform(matrix, mirrored: bool, top_level: bool, center, k: float, size) -> tuple:
    """(x, y, rotation, scale x, scale y) of an element's local matrix as §3.2 writes them: canvas px for a
    top-level element (§2.1), else px in its parent's frame; y down, rotation clockwise. Not rounded."""
    x, y, theta, sx, sy = _decompose(matrix, mirrored)
    if top_level:
        return (x - center[0]) * k + size[0] / 2, (center[1] - y) * k + size[1] / 2, -theta, sx, sy
    return x * k, -y * k, -theta, sx, sy


def _anchor_point(x: float, y: float, size) -> str:
    width, height = size
    col = "left" if x < width / 3 else "right" if x > 2 * width / 3 else "center"
    row = "top" if y < height / 3 else "bottom" if y > 2 * height / 3 else "center"
    if row == "center":
        return col
    return row if col == "center" else f"{row}-{col}"


def _text_block(obj, k: float, ex: _Extraction) -> dict:
    curve = obj.data
    size = curve.size
    size_px = size * k
    if curve.font not in ex.fonts:  # its file looked up once per extraction
        axes, problem = font_axes(curve.font)
        if problem:
            ex.warn(problem)
        ex.fonts[curve.font] = (_font_asset(curve.font, ex), font_metrics(curve.font), axes)
    font_key, (ascent, descent, em), axes = ex.fonts[curve.font]
    box = None
    if len(curve.text_boxes):
        tb = curve.text_boxes[0]
        if tb.width > 0:
            top = tb.y + (size if tb.height > 0 else 0.0)
            box = [_r(tb.x * k), _r(-top * k), _r(tb.width * k), _r(tb.height * k)]
        if len(curve.text_boxes) > 1:
            ex.warn(f"Text '{obj.name}': only its first text box is used")
    if any(f.use_bold or f.use_italic for f in curve.body_format):
        ex.warn(f"Text '{obj.name}': bold and italic characters are drawn regular; choose a bold or italic "
                "font for the whole text instead")
    info = _material_of(obj, ex, default=WHITE)
    block = {
        "body": curve.body,
        "font": font_key,
        "size": _r(size_px),
        "color": list(info["color"]),
        "align": curve.align_x.lower(),
        "valign": curve.align_y.lower().replace("_", "-"),
        "lineSpacing": _r(curve.space_line, 4),
        "letterSpacing": _r(curve.space_character, 4),
        "wordSpacing": _r(curve.space_word, 4),
        "box": box,
        "offset": [_r(curve.offset_x * k), _r(-curve.offset_y * k)],
        "shear": _r(curve.shear, 4),
        "fontSize": _r(em * size_px) if em is not None else None,
        "ascent": _r(ascent * size_px),
        "descent": _r(descent * size_px),
        "overflow": curve.overflow.lower(),
    }
    if axes:  # a variable font: the instance Blender draws (§11.5)
        block["fontAxes"] = dict(axes)
    return block


def _image_block(obj, k: float, ex: _Extraction) -> dict:
    image = obj.data
    width, height = image.size
    if width <= 0 or height <= 0:
        ex.warn(f"Image '{obj.name}': '{image.name}' has no pixels; drawn square")
        width = height = 1
    size = obj.empty_display_size
    scale = size / max(width, height)
    w, h = width * scale, height * scale
    ox, oy = obj.empty_image_offset
    return {
        "src": _image_asset(image, ex, f"Image '{obj.name}'"),
        "rect": [_r(ox * w * k), _r(-(oy * h + h) * k), _r(w * k), _r(h * k)],
        # Phase 1's alpha; the element's "opacity" (the same colour alpha) stands in for it where present (§10.5)
        "opacity": _r(obj.color[3], 4) if obj.use_empty_image_alpha else 1.0,
        "pixelated": False,  # image empties have no interpolation setting in Blender
    }


def _mesh_block(obj, depsgraph, k: float, ex: _Extraction) -> dict:
    import numpy as np

    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        count = len(mesh.loop_triangles)
        ex.triangles += count
        loops = np.empty(count * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", loops)
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        uv_layer = mesh.uv_layers.active
        uvs = None
        if uv_layer is not None and len(mesh.loops):
            uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            uv_layer.uv.foreach_get("vector", uvs)
            uvs = uvs.reshape(-1, 2)
        colors = None
        attribute = mesh.color_attributes.active_color
        if attribute is not None and attribute.domain in ("POINT", "CORNER"):
            colors = np.empty(len(attribute.data) * 4, dtype=np.float32)
            attribute.data.foreach_get("color", colors)
            colors = colors.reshape(-1, 4)
        per_corner = uvs is not None or (colors is not None and attribute.domain == "CORNER")
        if per_corner:
            # One vertex per face corner: UVs and corner colours differ between the faces of a vertex.
            corner_vertex = np.empty(len(mesh.loops), dtype=np.int32)
            mesh.loops.foreach_get("vertex_index", corner_vertex)
            used = np.unique(loops)
            remap = np.full(len(mesh.loops), -1, dtype=np.int64)
            remap[used] = np.arange(len(used))
            indices = remap[loops]
            points = co[corner_vertex[used]]
            if uvs is not None:
                uvs = uvs[used]
            if colors is not None:
                colors = colors[used] if attribute.domain == "CORNER" else colors[corner_vertex[used]]
        else:
            vertices = np.empty(count * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", vertices)
            used = np.unique(vertices)
            remap = np.full(len(mesh.vertices), -1, dtype=np.int64)
            remap[used] = np.arange(len(used))
            indices = remap[vertices]
            points = co[used]
            if colors is not None:
                colors = colors[used]
        xy = np.round(np.stack([points[:, 0] * k, -points[:, 1] * k], axis=1).astype(np.float64), 3) + 0.0
        bounds = [_r(xy[:, 0].min()), _r(xy[:, 1].min()), _r(xy[:, 0].max()), _r(xy[:, 1].max())] \
            if len(xy) else [0.0, 0.0, 0.0, 0.0]
        if not count:
            ex.warn(f"Mesh '{obj.name}' has no faces; nothing is drawn")
        info = _material_of(obj, ex)
        image = info["image"]
        texture = _image_asset(image, ex, f"Mesh '{obj.name}'") if image is not None else ""
        mat = _slot_material(obj)
        if mat is not None and mat.name.endswith(".add"):
            blend = "additive"
        elif info["color"][3] >= 0.9999 and not info["texture_alpha"] and not (texture and _has_alpha(image)) \
                and colors is None:
            blend = "opaque"
        else:
            blend = "alpha"
        material = {"color": list(info["color"]), "texture": texture, "blend": blend}
        shader = _shader_block(mat, ex)
        if shader is not None:
            material["shader"] = shader
        return {
            "positions": xy.reshape(-1).tolist(),
            "indices": indices.astype(np.int64).tolist(),
            "uvs": (np.round(uvs.astype(np.float64), 5) + 0.0).reshape(-1).tolist() if uvs is not None else None,
            "colors": (np.round(colors.astype(np.float64), 4) + 0.0).reshape(-1).tolist()
            if colors is not None else None,
            "bounds": bounds,
            "material": material,
        }
    finally:
        evaluated.to_mesh_clear()


def _shader_block(mat, ex: _Extraction) -> dict | None:
    """§11.4: {"name", "values"} of a UI mesh material's game shader (Material › Game), every field's value with
    asset values as project-relative paths (listed for "assets"), or None. Read once per material."""
    from . import shaders

    if mat is None:
        return None
    if mat not in ex.shaders:
        name = shaders.get_shader(mat)
        block = None
        if name:
            schema = registry.shader_schema_ref(name)
            if schema is None and registry.source_info()["origin"] != "none":
                ex.warn(f'Material "{mat.name}" uses game shader "{name}", which no src/shaders file defines')
            values = shaders.get_values(mat, for_export=True)
            for f in (schema or {}).get("fields", []):
                value = values.get(f["name"]) if f["type"] == "asset" else None
                if isinstance(value, str) and value:
                    ex.asset(value)
            block = {"name": name, "values": values}
        ex.shaders[mat] = block
    block = ex.shaders[mat]
    return json.loads(json.dumps(block)) if block is not None else None


def _slice_block(obj, values: dict, mesh: dict, ex: _Extraction) -> dict | None:
    """§10.6: {"insets": [l, r, t, b] image px, "image": [w, h] texture px, "size": [w, h] the panel's design px at
    scale 1} of a mesh with UiSlice, or None (warned) when it has no image texture."""
    image = _material_of(obj, ex)["image"]
    if image is None:
        ex.warn(f"'{obj.name}': UiSlice needs a textured panel (an Image Texture in its material); ignored")
        return None
    width, height = (int(v) for v in image.size)
    if width <= 0 or height <= 0:
        ex.warn(f"'{obj.name}': UiSlice image '{image.name}' has no pixels; ignored")
        return None
    insets = [_r(max(float(values.get(side) or 0.0), 0.0)) for side in ("left", "right", "top", "bottom")]
    if insets[0] + insets[1] > width or insets[2] + insets[3] > height:
        ex.warn(f"'{obj.name}': UiSlice insets {insets} are larger than its image ({width}x{height} px)")
    size = obj.get(gameui.SIZE_PROP)
    try:
        size = [float(size[0]), float(size[1])] if size is not None and len(size) == 2 else None
    except (TypeError, ValueError):
        size = None
    if size is None or min(size) <= 0:  # not built by gs.gameui: the mesh's own extent
        bounds = mesh["bounds"]
        size = [bounds[2] - bounds[0], bounds[3] - bounds[1]]
    return {"insets": insets, "image": [width, height], "size": [_r(size[0]), _r(size[1])]}


def _check_controls(obj, comps: dict, ex: _Extraction) -> None:
    """§10.4 and §10.1: a UiButton next to a slider or toggle is dropped; empty value paths and a slider
    without a fill warn."""
    control = next((c for c in CONTROLS if c in comps), None)
    if control is not None and "UiButton" in comps:
        ex.warn(f"'{obj.name}' has both UiButton and {control}; a control is not a button too, so its UiButton is "
                "ignored. Remove one of them")
        del comps["UiButton"]
    for comp in VALUE_COMPONENTS:
        if comp in comps and not str(comps[comp].get("value") or "").strip():
            ex.warn(f"'{obj.name}': {comp}.value is empty; set the value path it "
                    f"{'shows and writes' if comp in CONTROLS else 'compares'} (lives, save.best, audio.music, ...)")
    if "UiSlider" in comps and not comps["UiSlider"].get("fill"):
        ex.warn(f"'{obj.name}': UiSlider has no fill; set fill to the child that grows with the value (its bounds "
                "are the slider's range)")


def _keyed_curves(obj) -> dict:
    """{(data path, index): F-curve} of obj's active action (its slot), unmuted: the transform curves and the
    colour alpha's."""
    anim = obj.animation_data
    action = anim.action if anim is not None else None
    if action is None:
        return {}
    bag = anim_utils.action_get_channelbag_for_slot(action, anim.action_slot)
    curves = {}
    for fc in bag.fcurves if bag is not None else ():
        if fc.mute or (fc.group is not None and fc.group.mute):
            continue
        key = (fc.data_path, fc.array_index)
        if fc.data_path in TRANSFORM_PATHS or key == OPACITY_CURVE:
            curves[key] = fc
    return curves


def _moves(curves: dict) -> bool:
    return any(path in TRANSFORM_PATHS for path, _i in curves)


class _Sampler:
    """Elements' local transforms and opacity at any frame (§10.5), from the F-curves of the active actions.
    The scene's frame is never changed (see the module docstring), so nothing in the file changes. Local
    transforms come from the objects between an element and its element parent: matrix_parent_inverse @
    basis each, the basis from loc / rot / scale and the deltas as Blender composes them, with keyed channels
    evaluated at the frame. Constraints, drivers and NLA strips are not applied; export warns about them.
    Objects are read once; a frame of an animated object is evaluated once for every element it moves."""

    def __init__(self, members, parents, ex: _Extraction):
        self.chains = {}     # element -> [(object, matrix_parent_inverse or None)] making its local matrix, top down
        self.moving = set()  # elements whose local transform is animated
        self.fading = {}     # element -> the F-curve of its opacity
        self._keyed = {}     # animated object -> {data path: [(index, F-curve)]} of its transform
        self._props = {}     # animated object -> {data path: values} of its transform, read once
        self._still = {}     # object without transform curves -> matrix_basis
        self._frames = {}    # (animated object, frame) -> (basis matrix, mirrored)
        checked = set()
        for obj in members:
            chain, node = [], obj
            while node is not None and node is not parents[obj]:
                chain.append(node)
                node = node.parent
            chain.reverse()
            for node in chain:
                if node not in checked:
                    checked.add(node)
                    self._check(node, obj, ex)
            self.chains[obj] = [(node, node.matrix_parent_inverse.copy() if node.parent is not None else None)
                                for node in chain]
            if any(node in self._keyed for node in chain):
                self.moving.add(obj)
                for node in chain:
                    if any(not c.mute for c in node.constraints):
                        ex.warn(f"'{node.name}': constraints are not applied to UI animations")
                    if node.parent is not None and node.parent_type != "OBJECT":
                        ex.warn(f"'{node.name}' is parented to a {node.parent_type.lower()}; UI animations place it "
                                "relative to its parent object")

    def _check(self, obj, element, ex: _Extraction) -> None:
        curves = _keyed_curves(obj)
        keyed = {}
        for (path, index), fc in curves.items():
            if path in TRANSFORM_PATHS:
                keyed.setdefault(path, []).append((index, fc))
        if keyed:
            self._keyed[obj] = keyed
            rotation = ROTATION_PATHS.get(obj.rotation_mode, ("rotation_euler", "delta_rotation_euler"))
            paths = ("location", "delta_location", "scale", "delta_scale") + rotation
            self._props[obj] = {path: tuple(getattr(obj, path)) for path in paths}
        if obj is element and OPACITY_CURVE in curves:
            self.fading[obj] = curves[OPACITY_CURVE]
        anim = obj.animation_data
        if anim is None:
            return
        if any(d.data_path in TRANSFORM_PATHS or (d.data_path, d.array_index) == OPACITY_CURVE
               for d in anim.drivers if not d.mute):
            ex.warn(f"'{obj.name}': drivers are not sampled for UI animations; key its transform or opacity instead")
        if any(not track.mute and len(track.strips) for track in anim.nla_tracks):
            ex.warn(f"'{obj.name}': NLA strips are not exported in UI animations, only the active action")

    def _frame(self, obj, frame: int) -> tuple:
        """(basis matrix, mirrored) of an animated object at a frame (BKE_object_to_mat4 with the keyed
        channels evaluated)."""
        key = (obj, frame)
        cached = self._frames.get(key)
        if cached is not None:
            return cached
        keyed = self._keyed[obj]
        v = {}
        for path, values in self._props[obj].items():
            pairs = keyed.get(path)
            if pairs:
                values = list(values)
                for index, fc in pairs:
                    values[index] = fc.evaluate(frame)
            v[path] = values
        mode = obj.rotation_mode
        if mode == "QUATERNION":
            delta, rot = (q.normalized().to_matrix() if q.magnitude > 1e-12 else Matrix.Identity(3)
                          for q in (Quaternion(v["delta_rotation_quaternion"]), Quaternion(v["rotation_quaternion"])))
            rot = delta @ rot
        elif mode == "AXIS_ANGLE":  # Blender's axis-angle delta is not in the Python API: none
            angle, *axis = v["rotation_axis_angle"]
            axis = Vector(axis)
            rot = Matrix.Rotation(angle, 3, axis.normalized()) if axis.length > 1e-12 else Matrix.Identity(3)
        else:
            rot = Euler(v["delta_rotation_euler"], mode).to_matrix() @ Euler(v["rotation_euler"], mode).to_matrix()
        scale = [a * b for a, b in zip(v["scale"], v["delta_scale"])]
        matrix = (rot @ Matrix.Diagonal(scale)).to_4x4()
        matrix.translation = Vector(v["location"]) + Vector(v["delta_location"])
        self._frames[key] = cached = (matrix, scale[0] < 0)
        return cached

    def basis(self, obj, frame: int) -> Matrix:
        if obj in self._keyed:
            return self._frame(obj, frame)[0]
        if obj not in self._still:
            self._still[obj] = obj.matrix_basis.copy()
        return self._still[obj]

    def local(self, obj, frame: int) -> Matrix:
        """An element's matrix in its element parent's space (the world for a top-level one) at a frame."""
        matrix = None
        for node, parent_inverse in self.chains[obj]:
            step = self.basis(node, frame)
            if parent_inverse is not None:
                step = parent_inverse @ step
            matrix = step if matrix is None else matrix @ step
        return matrix

    def mirrored(self, obj, frame: int) -> bool:
        return self._frame(obj, frame)[1] if obj in self._keyed else obj.scale.x < 0

    def opacity(self, obj, frame: int) -> float:
        return min(max(self.fading[obj].evaluate(frame), 0.0), 1.0)

    def tracks(self, elements, start: int, end: int, transform) -> dict:
        """{element id: {channel: [one value per frame, start to end]}} with the channels that differ from the
        element's rest pose somewhere in the range. elements: (object, element) pairs; transform(obj, matrix,
        mirrored) -> _transform()'s tuple."""
        frames = range(start, end + 1)
        out = {}
        for obj, element in elements:
            moving, fading = obj in self.moving, obj in self.fading
            if not moving and not fading:
                continue
            series = {}
            if moving:
                values = [transform(obj, self.local(obj, f), self.mirrored(obj, f)) for f in frames]
                for i, channel in enumerate(("x", "y", "rotation", "scaleX", "scaleY")):
                    series[channel] = [v[i] for v in values]
                series["rotation"] = _unwrapped(series["rotation"])
            if fading:
                series["opacity"] = [self.opacity(obj, f) for f in frames]
            rest = dict(element["transform"], opacity=element.get("opacity", 1.0))
            track = {}
            for channel, values in series.items():
                values = [_r(v, TRACK_DIGITS[channel]) for v in values]
                if any(_differs(channel, v, rest[channel]) for v in values):
                    track[channel] = values
            if track:
                out[element["id"]] = track
        return out


def _unwrapped(angles: list) -> list:
    """Angles without jumps of a turn between frames (a spin keeps turning past ±π)."""
    out = []
    for angle in angles:
        if out:
            angle = out[-1] + math.remainder(angle - out[-1], math.tau)
        out.append(angle)
    return out


def _differs(channel: str, value: float, rest: float) -> bool:
    if channel == "rotation":  # whole turns look the same
        return abs(math.remainder(value - rest, math.tau)) > 5e-6
    return value != rest


def _fps(fps: float):
    return int(round(fps)) if abs(fps - round(fps)) < 1e-9 else round(fps, 4)


def _depsgraph(scene):
    """The evaluated depsgraph of a canvas, shown in the window or not."""
    view_layer = export._export_view_layer(scene)
    len(view_layer.objects)  # brings the layer's object list up to date with its collections first
    view_layer.update()
    return view_layer.depsgraph


def _settings(scene, element_ids: set, ex: _Extraction) -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    camera = scene.camera
    if camera is not None:
        values = components.export_components(camera).get("UiCanvas")
        if values:
            settings.update({k: v for k, v in values.items() if k in DEFAULT_SETTINGS})
    focus = settings.get("focus")
    if focus is not None and focus not in element_ids:
        target = ids.find_by_id(focus)
        ex.warn(f"UI screen '{scene.name}': the focus button {target.name if target else focus} is not in this screen")
        settings["focus"] = None
    return settings


def _entity_fields(components_values: dict, element_ids: set, obj, ex: _Extraction) -> None:
    for comp, values in components_values.items():
        entry = registry.schema_ref(comp) or {}
        for field in entry.get("fields", []):
            value = values.get(field["name"])
            if field["type"] == "entity" and value is not None and value not in element_ids:
                target = ids.find_by_id(value)
                ex.warn(f"'{obj.name}': {comp}.{field['name']} points to "
                        f"'{target.name if target else value}', which is not an element of this screen")


def _models(in_layer, camera, ex: _Extraction) -> dict:
    """{UiModel empty: [its objects]} (§11.1): the outermost UiModel empties of a canvas with the view layer's
    objects under them, parents before children. A UiModel inside a model is part of that model (warned)."""
    found = {obj for obj in in_layer if obj != camera and gameui.is_model(obj)}
    if not found:
        return {}

    def outermost(obj):  # the outermost UiModel empty above obj, or None
        model, node = None, obj.parent
        while node is not None:
            if node in found:
                model = node
            node = node.parent
        return model

    def depth(obj):
        n, node = 0, obj.parent
        while node is not None:
            n, node = n + 1, node.parent
        return n

    models = {obj: [] for obj in found if outermost(obj) is None}
    for obj in in_layer:
        model = outermost(obj)
        if model is not None:
            models[model].append(obj)
            if obj in found:
                ex.warn(f"'{obj.name}' has UiModel inside the model '{model.name}': it is part of that model")
    for objs in models.values():
        objs.sort(key=lambda o: (depth(o), o.name_full))
    return models


def _model_clips(objs) -> list:
    """The clips the GLB has for a model's objects, as the exporter names them: their active actions and
    single-strip NLA tracks (shape keys' too), and those of what their collection instances show."""
    found = []
    stack, seen = list(objs), set()
    while stack:
        obj = stack.pop(0)
        if obj.as_pointer() in seen:
            continue
        seen.add(obj.as_pointer())
        keys = getattr(obj.data, "shape_keys", None) if obj.type == "MESH" else None
        for anim in (obj.animation_data, keys.animation_data if keys is not None else None):
            if anim is None:
                continue
            actions = [anim.action] if anim.action is not None else []
            for track in anim.nla_tracks:
                strips = [s for s in track.strips if s.action is not None and not s.mute]
                if len(strips) == 1:
                    actions.append(strips[0].action)
            for action in actions:
                if action.name not in found:
                    found.append(action.name)
        if obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
            stack += sorted(obj.instance_collection.all_objects, key=lambda o: o.name_full)
    return found


def extract_canvas(scene) -> tuple:
    """(canvas, extraction): the §3.2 object of a canvas of the open UI file, with "rev" 0, no
    "exportedAt" and "assets" still to be filled in (asset values in extraction.assets, generated files
    in extraction.generated, extraction.builtin_font), and the warnings. Model elements (§11.1) have
    model.glb "" and the clips their objects' actions give until the GLB is written (extraction.models).
    Assigns missing entity IDs."""
    global _extracting
    _extracting = True
    try:
        return _extract(scene)
    finally:
        _extracting = False


def _extract(scene) -> tuple:
    ex = _Extraction(scene)
    # Transforms come from the evaluated scene: a canvas the window does not show is evaluated here,
    # and the matrices of its objects may be older than their last edit.
    depsgraph = _depsgraph(scene)
    for problem in gameui.camera_problems(scene):
        ex.warn(problem)
    size = gameui.design_size(scene)
    k = gameui.px_per_unit(scene)
    camera = scene.camera
    cx, cy = 0.0, 0.0
    if camera is not None:
        cam_at = camera.evaluated_get(depsgraph).matrix_world.translation
        cx, cy = float(cam_at.x), float(cam_at.y)
    view_layer = export._export_view_layer(scene)
    in_layer = list(view_layer.objects)
    models = _models(in_layer, camera, ex)  # §11.1: their objects are the GLB's, not elements
    inside = {obj for objs in models.values() for obj in objs}
    members = [obj for obj in in_layer if obj not in inside and (obj in models or _element_type(obj, camera))]
    member_set = set(members)
    skipped = sorted(f"{obj.type.replace('_', ' ').title()} '{obj.name}'" for obj in in_layer
                     if obj not in member_set and obj not in inside and obj != camera and obj.type not in SILENT_TYPES)
    if skipped:
        ex.warn(f"UI screen '{scene.name}': not exported (screens draw texts, image empties, meshes, curves and "
                f"empties): {', '.join(skipped)}")

    def element_parent(obj):
        parent = obj.parent
        while parent is not None and parent not in member_set:
            parent = parent.parent
        return parent

    parents = {obj: element_parent(obj) for obj in members}
    children = {}
    for obj in members:
        children.setdefault(parents[obj], []).append(obj)
    ordered = []  # outliner order: parents before children, then by name

    def visit(parent):
        for child in sorted(children.get(parent, ()), key=lambda o: o.name):
            ordered.append(child)
            visit(child)

    visit(None)
    position = {obj: i for i, obj in enumerate(ordered)}
    world = {obj: obj.evaluated_get(depsgraph).matrix_world.copy() for obj in ordered}
    draw = sorted(ordered, key=lambda o: (round(world[o].translation.z, 6), position[o]))
    order = {obj: i for i, obj in enumerate(draw)}
    # Duplicated elements (Shift+D, copy and paste) carry the original's gs_id: parents, focus and fill
    # are ids, so the copy gets its own first, as the level export does.
    ids.repair_duplicate_ids(scene)
    element_ids = {obj: ids.ensure_id(obj) for obj in ordered}
    id_set = set(element_ids.values())
    # Animations (§10.5), only with markers: then the static transforms are the rest pose, sampled from the
    # F-curves of animated elements at the rest frame like their tracks; others read the evaluated scene.
    problems = []
    ranges = gameui.animation_ranges(scene, problems)
    for problem in problems:
        ex.warn(problem)
    sampler = _Sampler(ordered, parents, ex) if ranges else None
    rest = gameui.rest_frame(scene) if sampler is not None else None
    if sampler is not None and camera is not None and _moves(_keyed_curves(camera)):
        ex.warn(f"UI screen '{scene.name}': camera animation is not exported; animate the elements instead")

    def transform(obj, matrix, mirrored):
        return _transform(matrix, mirrored, parents[obj] is None, (cx, cy), k, size)

    elements = []
    for obj in ordered:
        kind = "model" if obj in models else _element_type(obj, camera)
        parent = parents[obj]
        if sampler is not None and obj in sampler.moving:
            matrix, mirrored = sampler.local(obj, rest), sampler.mirrored(obj, rest)
        else:
            matrix = world[obj] if parent is None else world[parent].inverted_safe() @ world[obj]
            mirrored = obj.scale.x < 0
        if _tilted(matrix):
            ex.warn(f"'{obj.name}' is rotated about X or Y; only its rotation about Z is used")
        elif _sheared(matrix):
            ex.warn(f"'{obj.name}' is skewed: it is rotated inside a parent scaled unevenly, and the game draws it "
                    "without the skew. Apply the parent's scale (Ctrl+A), or rotate the parent instead")
        x, y, rotation, sx, sy = transform(obj, matrix, mirrored)
        comps = components.export_components(obj)
        _entity_fields(comps, id_set, obj, ex)
        _check_controls(obj, comps, ex)
        element = {
            "id": element_ids[obj], "name": obj.name,
            "parent": element_ids[parent] if parent is not None else None,
            "type": kind, "visible": not obj.hide_render, "order": order[obj],
            "transform": {"x": _r(x), "y": _r(y), "rotation": _r(rotation, 6), "scaleX": _r(sx, 6),
                          "scaleY": _r(sy, 6)},
        }
        if sampler is not None and obj in sampler.fading:
            opacity = sampler.opacity(obj, rest)
        else:
            opacity = min(max(obj.evaluated_get(depsgraph).color[3], 0.0), 1.0)
        if _r(opacity, 4) < 1.0:  # the colour alpha (§10.5); 1 when left out
            element["opacity"] = _r(opacity, 4)
        if parent is None:
            anchor = comps.get("UiAnchor") or {}
            point = anchor.get("anchor", "auto")
            element["anchor"] = _anchor_point(x, y, size) if point in ("auto", "", None) else point
            element["stretch"] = anchor.get("stretch") or "none"
        elif "UiAnchor" in comps:
            ex.warn(f"'{obj.name}': UiAnchor works on top-level elements only (it has a parent); ignored")
            del comps["UiAnchor"]
        element["components"] = comps
        if kind == "text":
            element["text"] = _text_block(obj, k, ex)
        elif kind == "image":
            element["image"] = _image_block(obj, k, ex)
        elif kind == "mesh":
            element["mesh"] = _mesh_block(obj, depsgraph, k, ex)
        elif kind == "model":
            # glb and clips are filled in when the GLB is written (_write_models); clips predicted until then
            element["model"] = {"glb": "", "node": element_ids[obj], "clips": _model_clips(models[obj])}
            ex.models.append({"object": obj, "id": element_ids[obj], "members": models[obj], "element": element})
            if not models[obj] and not (obj.instance_type == "COLLECTION" and obj.instance_collection is not None):
                ex.warn(f"'{obj.name}' has UiModel but no objects under it: parent the model's objects to it")
        elif obj.type == "EMPTY" and obj.empty_display_type == "IMAGE":
            ex.warn(f"Image empty '{obj.name}' has no image; it is a group")
        if "UiSlice" in comps:  # in the mesh block, where the runtime rebuilds the geometry
            block = _slice_block(obj, comps["UiSlice"], element["mesh"], ex) if kind == "mesh" else None
            if block is not None:
                element["mesh"]["slice"] = block
            elif kind != "mesh":
                ex.warn(f"'{obj.name}': UiSlice works on textured panels (meshes); ignored")
        if obj.type == "EMPTY" and obj.instance_type == "COLLECTION" and obj.instance_collection is not None \
                and kind != "model":
            ex.warn(f"'{obj.name}' instances a collection; collection instances are drawn in UI screens only as "
                    "models: give it UiModel, or put it under an empty with UiModel")
        elements.append(element)
    if ex.triangles > MAX_TRIANGLES:
        ex.warn(f"UI screen '{scene.name}' has {ex.triangles} triangles (more than {MAX_TRIANGLES}); simplify "
                "its meshes and curves")
    for element in elements:
        ex.assets.extend(v for v in export._asset_values([element]) if v not in ex.assets)
    settings = _settings(scene, id_set, ex)
    canvas = {
        "format": gameui.CANVAS_FORMAT,
        "formatVersion": gameui.FORMAT_VERSION,
        "canvas": scene.name,
        "source": "",
        "rev": 0,
        "size": [int(size[0]), int(size[1])],
        "settings": settings,
        "elements": elements,
    }
    if sampler is not None:
        fps = gameui.scene_fps(scene)
        pairs = list(zip(ordered, elements))
        canvas["animations"] = {
            name: {"duration": _r((end - start) / fps, 6), "fps": _fps(fps),
                   "tracks": sampler.tracks(pairs, start, end, transform)}
            for name, (start, end) in ranges.items()}
    canvas["assets"] = {}
    return canvas, ex


# --- Files -----------------------------------------------------------------------------------------


def _copy_file(src: pathlib.Path, out: pathlib.Path, rel: str) -> str:
    """Copy src to <out>/files/<rel> unless the same file is there; its URL with a version."""
    st = src.stat()
    dest = out / export.ASSET_DIR / rel
    if not export._same_file(st, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(src.read_bytes())
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        export._replace(str(tmp), str(dest))
    version = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]
    return f"{export.ASSET_DIR}/{rel}?v={version}"


def _write_generated(out: pathlib.Path, key: str, data: bytes) -> str:
    dest = out / export.ASSET_DIR / key
    if not dest.is_file() or dest.stat().st_size != len(data):
        dest.parent.mkdir(parents=True, exist_ok=True)
        export._write_atomic(dest, data)
    return f"{export.ASSET_DIR}/{key}?v={hashlib.sha1(data).hexdigest()[:10]}"


def _copy_font_licence(out: pathlib.Path, rel: str, font: pathlib.Path | None = None,
                       licence: pathlib.Path | None = None) -> None:
    """A font copied to export/files/<rel> (from `font`) gets its licence next to it (<font name>-OFL.txt):
    the OFL wants it with every copy, and the dev server lists it in a build's THIRD-PARTY-NOTICES.md."""
    if pathlib.PurePosixPath(rel).suffix.lower() not in FONT_SUFFIXES:
        return
    if licence is None and font is not None:
        licence = font_licence(font)
    if licence is not None:
        _copy_file(licence, out, str(pathlib.PurePosixPath(rel).with_name(font_licence_name(rel, licence))))


def _assets(root: pathlib.Path, ex: _Extraction) -> dict:
    """Copy what a canvas uses into export/files/: {asset path: URL relative to the canvas file}. Fonts
    get their licence files next to them (_copy_font_licence)."""
    out = project.export_dir(root)
    values = list(ex.assets)
    urls = {}
    if ex.builtin_font:
        if (root / BUILTIN_FONT_KEY).is_file():
            values.append(BUILTIN_FONT_KEY)
        else:
            inter = _bundled_inter()
            if inter is None:
                ex.warn("Blender's Inter.woff2 was not found; texts in Blender's built-in font use a fallback font")
            else:
                urls[BUILTIN_FONT_KEY] = _copy_file(inter, out, BUILTIN_FONT_KEY)
                _copy_font_licence(out, BUILTIN_FONT_KEY, inter)
    copied = export.copy_assets(out, values, root)
    for value in values:
        if value in copied:
            urls[value] = copied[value]
            source = pathlib.Path(value) if os.path.isabs(value) else root / value
            _copy_font_licence(out, copied[value].split("?")[0][len(export.ASSET_DIR) + 1:], source)
        elif not value.startswith(("http:", "https:", "data:", "blob:", "/")):
            ex.warn(f"Asset file not found: {value}")
    for key, data in ex.generated.items():
        urls[key] = _write_generated(out, key, data)
        if _is_bundled_inter(data) and INTER_LICENCE.is_file():  # Blender's Inter, packed into the .blend
            _copy_font_licence(out, key, licence=INTER_LICENCE)
    return {key: f"../{url}" if not url.startswith(("http:", "https:", "data:", "blob:", "/")) else url
            for key, url in urls.items()}


# --- Models (§11.1) --------------------------------------------------------------------------------


def _matrix_key(matrix) -> tuple:
    return tuple(round(v, 6) + 0.0 for row in matrix for v in row)


def _id_name(value):
    """What a modifier, constraint or driver points at, by name (signatures)."""
    return value.name_full if isinstance(value, bpy.types.ID) else value


def _id_props(struct) -> list:
    """The keys of a struct's ID properties (a geometry nodes modifier's inputs); [] where it has none."""
    try:
        return sorted(struct.keys())
    except TypeError:
        return []


def _struct_targets(struct) -> tuple:
    """The names of the datablocks a struct points at (a modifier's object, a constraint's target)."""
    out = []
    for prop in struct.bl_rna.properties:
        if prop.type == "POINTER" and not prop.is_readonly:
            value = getattr(struct, prop.identifier, None)
            if isinstance(value, bpy.types.ID):
                out.append((prop.identifier, value.name_full))
    return tuple(out)


def _material_images(mat, found: dict) -> None:
    """The images a material's node tree (and its groups) uses, into found {name_full: Image}."""
    trees, seen = [mat.node_tree] if mat is not None and mat.node_tree is not None else [], set()
    while trees:
        tree = trees.pop()
        if tree.name_full in seen:
            continue
        seen.add(tree.name_full)
        for node in tree.nodes:
            image = getattr(node, "image", None)
            if isinstance(image, bpy.types.Image):
                found[image.name_full] = image
            group = getattr(node, "node_tree", None)
            if group is not None:
                trees.append(group)


def _model_signature(scene, ex: _Extraction, settings: dict) -> str:
    """A hash of everything a canvas's models GLB is made from, stable across sessions: per model its element
    id, and per object its name, parent, local matrices, visibility, data (mesh attributes and shape keys,
    armature bones and pose, curves as meshes), materials (node trees), modifiers, constraints, animation
    (actions by their keys), instancing; the collections the models instance; the images the materials use;
    and the export settings, frame rate, exporter settings and versions."""
    from . import bake as baking
    from . import journal

    h = hashlib.sha1(json.dumps([MODEL_FORMAT, bpy.app.version_string, journal._version(), export.GLTF_SETTINGS,
                                 settings, scene.render.fps, scene.render.fps_base], sort_keys=True,
                                default=str).encode("utf-8"))
    memo, images, collections, objects = {}, {}, {}, []

    def object_part(obj, model) -> str:
        objects.append(obj)
        data = obj.data
        parent = obj.parent
        coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
        if coll is not None:
            collections.setdefault(coll.name_full, coll)
        slots = []
        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None:
                _material_images(mat, images)
            slots.append((slot.link, mat.name_full if mat else None,
                          journal._material_digest(mat, memo) if mat else None))
        modifiers = []
        for mod in obj.modifiers:
            group = getattr(mod, "node_group", None)
            modifiers.append((mod.name, mod.type, mod.show_viewport, mod.show_render, journal._struct_digest(mod),
                              _struct_targets(mod), tuple((k, repr(_id_name(mod[k]))) for k in _id_props(mod)),
                              baking._tree_digest(group, memo) if group is not None else None))
        constraints = tuple((c.type, c.mute, journal._struct_digest(c), _struct_targets(c),
                             tuple(_struct_targets(t) for t in getattr(c, "targets", ()))) for c in obj.constraints)
        drivers = ()
        if obj.animation_data is not None:
            drivers = tuple((d.data_path, d.array_index, d.mute, d.driver.expression,
                             tuple((v.name, v.type, tuple((_id_name(t.id), t.data_path, t.bone_target, t.transform_type)
                                                          for t in v.targets)) for v in d.driver.variables))
                            for d in obj.animation_data.drivers)
        part = (obj.name_full, obj.type, parent.name_full if parent is not None and parent != model else None,
                obj.parent_type, obj.parent_bone, tuple(obj.parent_vertices), _matrix_key(obj.matrix_basis),
                _matrix_key(obj.matrix_parent_inverse), obj.hide_viewport, obj.hide_render,
                data.name_full if data is not None else None, journal._data_digest(obj) if data is not None else None,
                tuple(slots), tuple(modifiers), constraints, drivers, journal._anim_signature(obj),
                obj.instance_type, coll.name_full if coll is not None else None, obj.show_instancer_for_render,
                tuple(p.settings.name_full for p in obj.particle_systems))
        return repr(part)

    for model in ex.models:
        empty = model["object"]
        coll = empty.instance_collection if empty.instance_type == "COLLECTION" else None
        if coll is not None:
            collections.setdefault(coll.name_full, coll)
        h.update(f"model {model['id']} {coll.name_full if coll is not None else ''}\n".encode("utf-8"))
        for obj in model["members"]:
            h.update(object_part(obj, empty).encode("utf-8"))
    done = set()
    while len(done) < len(collections):  # instanced collections (assets), nested ones too, once each
        name = sorted(n for n in collections if n not in done)[0]
        done.add(name)
        coll = collections[name]
        h.update(f"collection {name} {tuple(coll.instance_offset)}\n".encode("utf-8"))
        for obj in sorted(coll.all_objects, key=lambda o: o.name_full):
            h.update(object_part(obj, None).encode("utf-8"))
    for action in sorted(journal._actions(objects).values(), key=lambda a: a.name_full):
        h.update(f"action {action.name_full} {_action_digest(action)}\n".encode("utf-8"))
    for name in sorted(images):
        image = images[name]
        packed = image.packed_file
        h.update(repr((name, image.source, image.filepath,
                       journal._stamp(image.filepath, image.library) if image.source == "FILE" else None,
                       packed.size if packed is not None else None, image.is_dirty, image.colorspace_settings.name,
                       image.alpha_mode, tuple(image.size), image.generated_type, tuple(image.generated_color)))
                 .encode("utf-8"))
    return h.hexdigest()


def _action_digest(action) -> str:
    """A hash of an action's keys, every slot's: clips.action_digest() reads each curve's keyframes in one go
    (several times faster than the journal's per-property reads, which remain the fallback)."""
    from . import clips, journal

    try:
        return f"{action.use_frame_range}," + ",".join(f"{slot.identifier}:{clips.action_digest(action, slot)}"
                                                       for slot in action.slots)
    except Exception:  # noqa: BLE001 - an action this cannot read is hashed the slow way
        return journal._action_digest(action)


def _remap_references(mapping: dict) -> None:
    """Point the copies' modifiers, constraints and drivers at the copies of the objects they name (an armature
    modifier at the copied armature) and at the model's root for its UiModel empty. mapping: original object
    pointer -> the object that stands for it (copies by their originals' pointers, and each root)."""
    def mapped(value):
        return mapping.get(value.as_pointer()) if isinstance(value, bpy.types.Object) else None

    def remap(struct):
        for prop in struct.bl_rna.properties:
            if prop.type != "POINTER" or prop.is_readonly or prop.fixed_type.identifier != "Object":
                continue
            new = mapped(getattr(struct, prop.identifier, None))
            if new is not None:
                try:
                    setattr(struct, prop.identifier, new)
                except (AttributeError, TypeError, RuntimeError, ValueError):
                    pass

    copies = {id(o): o for o in mapping.values()}.values()
    for copy in copies:
        for mod in copy.modifiers:
            remap(mod)
            for key in _id_props(mod):  # geometry nodes inputs
                new = mapped(mod[key])
                if new is not None:
                    mod[key] = new
        for con in copy.constraints:
            remap(con)
            for target in getattr(con, "targets", ()):
                remap(target)
        anim = copy.animation_data
        for fc in anim.drivers if anim is not None else ():
            for var in fc.driver.variables:
                for target in var.targets:
                    new = mapped(target.id) if target.id_type == "OBJECT" else None
                    if new is not None:
                        target.id = new


def _basis_world(obj) -> Matrix:
    """obj's world matrix from its own transforms and parents: linked objects that are only instanced may never
    have been evaluated."""
    m = obj.matrix_basis.copy()
    if obj.parent is not None:
        m = _basis_world(obj.parent) @ obj.matrix_parent_inverse @ m
    return m


def _link_correction(obj):
    """A model's instance of a level object linked for it (gs.gameui.model source "scenes/main.blend:Player",
    §11.1: a collection tagged LINK_PROP; the instance holds LINK_BASIS_PROP, the root's place and turn it cancels):
    the matrix that moves the instance from where the root was then to where its level has it now, so the model
    keeps showing it at its origin; None for other objects, or when the root has not moved."""
    coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
    stored = obj.get(LINK_BASIS_PROP)
    tag = coll.get(LINK_PROP) if coll is not None and coll.library is None else None
    if not isinstance(tag, str) or stored is None or len(stored) != 16:
        return None
    name = tag[tag.lower().rfind(".blend:") + len(".blend:"):]
    root = next((o for o in coll.objects if o.name == name), None)
    if root is None:
        return None
    loc, rot, _scale = _basis_world(root).decompose()
    now = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
    old = Matrix([list(stored)[i * 4:i * 4 + 4] for i in range(4)])
    if all(abs(a - b) < 1e-6 for a, b in zip((v for row in old for v in row), (v for row in now for v in row))):
        return None
    return old @ now.inverted_safe()


@contextlib.contextmanager
def _model_scene(scene, models: list):
    """A temporary scene (MODEL_SCENE) for exporting models: per model an empty at the origin (its root,
    instancing the UiModel empty's collection when it instances one) with copies of the model's objects under
    it. The copies share their originals' data, actions and materials; their transforms put them in the UiModel
    empty's space without its own transform. The user's objects are not changed. Yields (scene, {root name: element
    id}, {copy name_full: original name}); the copies, roots and scene are removed afterwards."""
    tmp = bpy.data.scenes.new(MODEL_SCENE)
    made = []
    try:
        tmp.render.fps, tmp.render.fps_base = scene.render.fps, scene.render.fps_base
        tmp.frame_start, tmp.frame_end = scene.frame_start, scene.frame_end
        tmp.frame_current = scene.frame_current  # the exporter restores this frame afterwards
        roots, names, mapping = {}, {}, {}
        for model in models:
            empty = model["object"]
            root = bpy.data.objects.new(MODEL_ROOT, None)
            made.append(root)
            tmp.collection.objects.link(root)
            if empty.instance_type == "COLLECTION" and empty.instance_collection is not None:
                root.instance_type = "COLLECTION"
                root.instance_collection = empty.instance_collection
            roots[root.name] = model["id"]
            mapping[empty.as_pointer()] = root
            copies = {}
            for obj in model["members"]:
                copy = obj.copy()
                made.append(copy)
                tmp.collection.objects.link(copy)
                copies[obj.as_pointer()] = copy
                names[copy.name_full] = obj.name
                correction = _link_correction(obj)
                if correction is not None:
                    copy.matrix_basis = obj.matrix_basis @ correction
            for obj in model["members"]:
                copy, parent = copies[obj.as_pointer()], obj.parent
                if parent is not None and parent.as_pointer() in copies:
                    copy.parent = copies[parent.as_pointer()]  # parent type, bone and inverse are copied
                elif parent == empty:
                    copy.parent = root  # root @ inverse @ basis: the object in the empty's space
                else:  # parented to an object outside the view layer: it stays where it is, seen from the empty
                    copy.parent = root
                    copy.parent_type = "OBJECT"
                    copy.matrix_parent_inverse = (empty.matrix_world.inverted_safe() @ obj.matrix_world
                                                  @ obj.matrix_basis.inverted_safe())
            mapping.update(copies)
        _remap_references(mapping)
        yield tmp, roots, names
    finally:
        try:
            bpy.data.batch_remove(made)
        except (RuntimeError, ReferenceError):
            for obj in made:
                with contextlib.suppress(RuntimeError, ReferenceError):
                    bpy.data.objects.remove(obj)
        bpy.data.scenes.remove(tmp)


def _glb_info(path: pathlib.Path, signature: str) -> dict | None:
    """What _export_models() returns, read back from a models GLB written with this signature (an earlier
    session's: the signature is in its asset extras), or None."""
    try:
        gltf = export.read_glb_json(path)
        data = path.read_bytes()
        st = path.stat()
    except (OSError, ValueError, export.ExportError):
        return None
    if ((gltf.get("asset") or {}).get("extras") or {}).get("gs_signature") != signature:
        return None
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes") or [{}]
    roots = {i: nodes[i].get("name") for i in scenes[gltf.get("scene", 0)].get("nodes", [])}
    return {"signature": signature, "stat": (st.st_size, st.st_mtime_ns), "hash": export.file_hash(data),
            "clips": export.clips_by_entity(export.gather_animations(gltf, roots)),
            "materials": export.glb_material_names(gltf), "warnings": [], "bytes": len(data), "exported": False}


def _export_models(scene, ex: _Extraction, root, path: pathlib.Path, signature: str, settings: dict) -> dict:
    """Write the models GLB of a canvas (§11.1) with Blender's glTF exporter, as levels are exported (armature
    clips sampled by clips.py, procedural materials baked, textures processed). Returns {"signature", "stat",
    "hash", "clips": {element id: [clip names]}, "materials": [names in the GLB], "warnings", "bytes",
    "exported": True}. Raises export.ExportError."""
    from . import bake as baking
    from . import clips, fidelity
    from .util import any_window

    window = any_window()
    if window is None:
        raise export.ExportError("The glTF exporter needs a Blender window")
    out = project.export_dir(root)
    cache = export.bake_cache_dir(out)
    raw_path = path.with_name(path.name[:-len(".glb")] + ".raw.glb")
    started = time.perf_counter()
    saved_scene, saved_layer = window.scene, window.view_layer
    for model in ex.models:
        for obj in model["members"]:
            if obj.mode == "EDIT":
                obj.update_from_editmode()  # copies share the mesh: the exporter must see the edit
    report, bake_report = {"textures": [], "sizes": []}, {}
    exporting = export._exporting
    export._exporting = True  # the journal and the live links ignore what the copies cause
    try:
        with export._WarningLog() as warnings:
            try:
                with _model_scene(scene, ex.models) as (tmp, roots, names):
                    window.scene = tmp  # a new scene has no depsgraph until a window shows it (export.py)
                    view_layer = tmp.view_layers[0]
                    view_layer.update()
                    layer_objects = list(view_layer.objects)
                    depsgraph = export._depsgraph(window, tmp, view_layer)
                    counts, sources = export.instance_counts(depsgraph)
                    plan = clips.plan(tmp, layer_objects, cache=clips.cache_dir(out))
                    bake_objects = baking.collect_objects(layer_objects, depsgraph)
                    with baking.export_swap(tmp, window, bake_objects, settings, cache, bake_report,
                                            instanced=sources):
                        with clips.armed(plan.scene):
                            export._gltf(window, tmp, view_layer, str(raw_path), export.gltf_overrides(bool(counts)))
                    gltf, chunks = export.read_glb(raw_path)
                    objects = {o.name_full: (o, "") for o in layer_objects}
                    matched = export.inject_ids(gltf, objects)
                    glb = fidelity.Glb(gltf, chunks)
                    clips.splice(glb, plan.scene, plan, matched, objects)
                    nodes = gltf.get("nodes", [])
                    node_ids = {}
                    for key, index in matched.items():
                        node = nodes[index]
                        extras = node.get("extras") or {}
                        extras.pop("gs_id", None)
                        if extras:
                            node["extras"] = extras
                        else:
                            node.pop("extras", None)
                        name = objects[key][0].name
                        if name in roots:
                            node["name"] = node_ids[index] = roots[name]
                        elif key in names:
                            node["name"] = names[key]
                    for skin in gltf.get("skins", []):
                        if skin.get("name") in names:
                            skin["name"] = names[skin["name"]]
            finally:
                if window.scene != saved_scene:
                    window.scene = saved_scene
                if window.view_layer != saved_layer:
                    with contextlib.suppress(TypeError, ReferenceError):
                        window.view_layer = saved_layer
                raw_path.unlink(missing_ok=True)
            fidelity.rename(gltf, bake_report.get("material_names") or {}, bake_report.get("mesh_names") or {})
            fidelity.prune(glb)
            chunks = export._finish_glb(glb, settings, cache, path.name, report)
            gltf.setdefault("asset", {}).setdefault("extras", {})["gs_signature"] = signature
            data = export.build_glb(gltf, chunks)
            path.parent.mkdir(parents=True, exist_ok=True)
            export._write_atomic(path, data)
        export._export_view_layer(scene).update()  # the depsgraph updates of the swap arrive while _exporting is set
    finally:
        export._exporting = exporting
    st = path.stat()
    info = {"signature": signature, "stat": (st.st_size, st.st_mtime_ns), "hash": export.file_hash(data),
            "clips": export.clips_by_entity(export.gather_animations(gltf, node_ids)),
            "materials": export.glb_material_names(gltf), "warnings": list(warnings.messages), "bytes": len(data),
            "exported": True}
    log.info("Exported the models of UI screen '%s' (%d) to %s in %.2f s", scene.name, len(ex.models), path.name,
             time.perf_counter() - started)
    return info


def _write_models(scene, root, file_name: str, canvas: dict, ex: _Extraction) -> dict:
    """§11.1: the models GLB of a canvas, exported again only when its signature changed (else the GLB on disk is
    kept), then each model element's glb and clips and the canvas's "materials" (game shaders of the materials
    the GLB draws with; their asset values listed for "assets"). Returns {GLB name: hash} for "files", {} for a
    canvas without models (an old GLB of it is deleted)."""
    name = gameui.model_file(file_name)
    path = gameui.export_dir(root) / name
    if not ex.models:
        _models_cache.pop(str(path), None)
        if path.exists():
            _remove_file(root, name)
        return {}
    settings = export.export_settings(root)
    signature = _model_signature(scene, ex, settings)
    info = _models_cache.get(str(path))
    try:
        st = path.stat()
        stat = (st.st_size, st.st_mtime_ns)
    except OSError:
        stat = None
    if info is None or info["signature"] != signature or info["stat"] != stat:
        try:
            info = (_glb_info(path, signature) if stat is not None else None) or \
                _export_models(scene, ex, root, path, signature, settings)
        except Exception as exc:  # noqa: BLE001 - the canvas is still written, its models without a GLB
            log.exception("Exporting the 3D models of UI screen '%s' failed", scene.name)
            ex.warn(f"UI screen '{scene.name}': its 3D models could not be exported ({exc}); the game shows them "
                    "empty")
            _models_cache.pop(str(path), None)
            return {}
        _models_cache[str(path)] = info
    for warning in info["warnings"]:
        ex.warn(warning)
    for model in ex.models:
        model["element"]["model"].update(glb=name, clips=list(info["clips"].get(model["id"], [])))
    objects = [obj for model in ex.models for obj in model["members"]]
    materials, shader_assets = export.shader_section(info["materials"], objects)
    if materials:
        canvas["materials"] = materials
    for value in shader_assets:
        ex.asset(value)
    return {name: info["hash"]}


def _index_path(root) -> pathlib.Path:
    return gameui.export_dir(root) / gameui.INDEX_FILE


def read_index(root) -> dict:
    """The exported index (§3.1), or an empty one."""
    data = _read_json(_index_path(root))
    if not isinstance(data, dict) or not isinstance(data.get("canvases"), dict):
        data = {}
    canvases = {name: entry for name, entry in data.get("canvases", {}).items()
                if isinstance(entry, dict) and isinstance(entry.get("file"), str)}
    index = {"format": gameui.INDEX_FORMAT, "formatVersion": gameui.FORMAT_VERSION, "canvases": canvases}
    if isinstance(data.get("sourceLocale"), str):  # translations (§11.3), written by export_locales()
        index["sourceLocale"] = data["sourceLocale"]
    if isinstance(data.get("locales"), dict):
        index["locales"] = {code: entry for code, entry in data["locales"].items()
                            if isinstance(entry, dict) and isinstance(entry.get("file"), str)}
    return index


def _write_index(root, index: dict) -> None:
    index = {**index, "canvases": dict(sorted(index["canvases"].items()))}
    if _read_json(_index_path(root)) != index:
        _write_json(_index_path(root), index)


# --- Translations (§11.3) ---------------------------------------------------------------------------


def read_locales(root) -> tuple:
    """({code: {"name", "strings"}}, [warnings]): the translation tables in ui/locales, by code, checked. A file
    that is not a JSON object, or whose name is not a language code, is left out; a missing "name" becomes the
    code; strings that are not text are dropped."""
    folder = gameui.locales_dir(root)
    tables, warnings = {}, []
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    for path in paths:
        rel = f"{gameui.UI_DIR}/{gameui.LOCALES_DIR}/{path.name}"
        code = path.stem
        if not LOCALE_CODE.match(code):
            warnings.append(f"{rel}: '{code}' is not a language code (en, fr, pt-BR, ...); it is not exported")
            continue
        if code.lower() in (c.lower() for c in tables):
            warnings.append(f"{rel}: another table is also named '{code}' (letter case aside); it is not exported")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            warnings.append(f"{rel} is not valid JSON ({exc}); its translations are not exported")
            continue
        except OSError as exc:
            warnings.append(f"{rel} could not be read ({exc})")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{rel} must be an object {{\"name\": ..., \"strings\": {{...}}}}; it is not exported")
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"{rel} has no \"name\" (the language's own name, such as \"Français\"); "
                            f"the game shows '{code}'")
            name = code
        strings = data.get("strings")
        if not isinstance(strings, dict):
            if strings is not None:
                warnings.append(f"{rel}: \"strings\" must be an object of authored text -> translation")
            strings = {}
        bad = [k for k, v in strings.items() if not isinstance(v, str)]
        if bad:
            warnings.append(f"{rel}: {len(bad)} translation(s) are not text and are left out: "
                            f"{', '.join(repr(k) for k in bad[:3])}{' ...' if len(bad) > 3 else ''}")
        tables[code] = {"name": name.strip(), "strings": {k: v for k, v in strings.items() if isinstance(v, str)}}
    return tables, warnings


def _locales_export_dir(root) -> pathlib.Path:
    return gameui.export_dir(root) / gameui.LOCALES_DIR


def _locale_fields(root, tables: dict) -> dict:
    """The index's sourceLocale and locales for these tables."""
    return {"sourceLocale": gameui.source_locale(root),
            "locales": {code: {"file": f"{gameui.LOCALES_DIR}/{code}.json", "name": table["name"]}
                        for code, table in tables.items()}}


def exported_locales(root) -> dict:
    """{"sourceLocale", "locales", "tables"} as the export holds them (what a game loaded)."""
    index = read_index(root)
    tables = {}
    for code, entry in (index.get("locales") or {}).items():
        data = _read_json(gameui.export_dir(root) / entry["file"])
        if isinstance(data, dict):
            tables[code] = data
    return {"sourceLocale": index.get("sourceLocale"), "locales": index.get("locales") or {}, "tables": tables}


def export_locales(root=None) -> dict:
    """Copy the translation tables of ui/locales to .gamestudio/export/ui/locales/<code>.json (checked, see
    read_locales(); only those that changed), delete exported tables whose file is gone, and write the index's
    sourceLocale (gamestudio.json "locale", default "en") and locales {code: {file, name}}. Returns
    {"sourceLocale", "locales", "tables", "changed" (anything written or deleted), "warnings"}."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    if root is None or not (gameui.locales_dir(root).is_dir() or _index_path(root).is_file() or gameui.ui_files(root)):
        return {"sourceLocale": gameui.source_locale(root), "locales": {}, "tables": {}, "changed": False,
                "warnings": []}  # no game UI: no index to write
    tables, warnings = read_locales(root)
    for warning in warnings:
        log.warning("%s", warning)
    folder = _locales_export_dir(root)
    changed = False
    for code, table in tables.items():
        path = folder / f"{code}.json"
        if _read_json(path) != table:
            _write_json(path, table)
            changed = True
    if folder.is_dir():
        for path in folder.glob("*.json"):
            if path.stem not in tables:
                _remove_file(root, f"{gameui.LOCALES_DIR}/{path.name}")
                changed = True
    index = read_index(root)
    fields = _locale_fields(root, tables)
    if any(index.get(key) != value for key, value in fields.items()):
        index.update(fields)
        _write_index(root, index)
        changed = True
    return {**fields, "tables": tables, "changed": changed, "warnings": warnings}


def _source_exists(root, source: str) -> bool:
    return bool(source) and (pathlib.Path(root) / source).is_file()


def _sorts_before(root, a: str, b: str) -> bool:
    """True when UI file a comes before UI file b (the first one wins a duplicate canvas name)."""
    return pathlib.Path(root) / a < pathlib.Path(root) / b


def _remove_file(root, file_name: str) -> None:
    path = gameui.export_dir(root) / file_name
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not remove %s: %s", path, exc)


def _remove_canvas_files(root, file_name: str) -> None:
    """A dropped canvas's file and its models GLB."""
    _remove_file(root, file_name)
    glb = gameui.model_file(file_name)
    _models_cache.pop(str(gameui.export_dir(root) / glb), None)
    _remove_file(root, glb)


def _folded(files) -> set:
    """File names as Windows compares them: canvases 'Pause' and 'pause' must not share a file."""
    return {f.lower() for f in files}


def _free_file(name: str, taken: set) -> str:
    base = export._sanitize(name)
    file_name, n = f"{base}{gameui.CANVAS_SUFFIX}", 2
    while file_name.lower() in _folded(taken):
        file_name, n = f"{base}-{n}{gameui.CANVAS_SUFFIX}", n + 1
    return file_name


def _write_canvas(scene, root, source: str, file_name: str) -> dict:
    """Extract a canvas and write its file when the content changed (its models GLB when theirs did). Returns
    the result entry."""
    canvas, ex = extract_canvas(scene)
    canvas["source"] = source
    files = _write_models(scene, root, file_name, canvas, ex)
    canvas["assets"] = _assets(pathlib.Path(root), ex)
    if files:
        canvas["files"] = files  # the models GLB's hash: a model edit changes the canvas (rev, live link)
    path = gameui.export_dir(root) / file_name
    content = digest(canvas)
    old = _read_json(path)
    old_rev = int(old.get("rev", 0) or 0) if isinstance(old, dict) else 0
    if isinstance(old, dict) and old.get("canvas") == scene.name and digest(old) == content:
        canvas = old
        written = False
        try:  # up to date: newer than the .blend just saved, so Play does not export it again (stale_ui_files)
            os.utime(path)
        except OSError as exc:
            log.debug("Could not touch %s: %s", path, exc)
    else:
        canvas["rev"] = max(old_rev, _revisions.get(str(path), 0)) + 1
        canvas["exportedAt"] = _now()
        canvas = {key: canvas[key] for key in ("format", "formatVersion", "canvas", "source", "rev", "exportedAt",
                                               "size", "settings", "elements", "animations", "materials", "assets",
                                               "files")
                  if key in canvas}
        _write_json(path, canvas)
        written = True
    _revisions[str(path)] = max(_revisions.get(str(path), 0), int(canvas["rev"]))
    if written:
        for warning in ex.warnings:
            log.warning("%s", warning)
    return {"canvas": scene.name, "file": file_name, "path": str(path), "rev": canvas["rev"], "written": written,
            "elements": len(canvas["elements"]), "warnings": ex.warnings, "data": canvas, "digest": content}


def _index_entry(result: dict, source: str) -> dict:
    settings = result["data"]["settings"]
    entry = {"file": result["file"], "rev": result["rev"], "source": source, "kind": settings["kind"],
             "open": settings["open"]}
    if result["data"].get("files"):
        entry["files"] = dict(result["data"]["files"])  # {"<Canvas>.ui.glb": hash} (§11.1)
    return entry


def _require_ui_file():
    root = project.project_root()
    if root is None:
        raise export.ExportError("Save the .blend inside a GameStudio project (folder with gamestudio.json)")
    if not gameui.is_ui_file():
        raise export.ExportError(f"{bpy.data.filepath or 'This file'} is not a UI file (a .blend in <project>/ui/)")
    return root


def _require_ui_components(root) -> None:
    """The game UI components are in the registry, loading the project's first. Without them every element
    would be written without its components (buttons, anchors, the canvas settings): refuse instead."""
    if registry.schema_ref("UiCanvas") is None:
        registry.reload_registry(root)
    if registry.schema_ref("UiCanvas") is None:
        raise export.ExportError("The component registry has no game UI components (UiCanvas, UiButton, ...), so "
                                 "the UI screens were not exported: they would lose their buttons, anchors and "
                                 "settings. Start the dev server (it lists the engine's components), then export "
                                 "again")


def _conflict(root, index: dict, name: str, source: str) -> str | None:
    """The other UI file that keeps canvas `name` (it sorts first), or None."""
    other = index["canvases"].get(name)
    if other is None or other.get("source") == source or not _source_exists(root, other.get("source", "")):
        return None
    return other["source"] if _sorts_before(root, other["source"], source) else None


def export_canvas(scene, root=None, unsaved=None) -> dict:
    """Export one canvas of the open UI file (the live link): its file when the content changed, and its
    index entry. Returns {"canvas", "file", "path", "rev", "written", "elements", "warnings", "data",
    "digest"}, or with "skipped" (a reason) when another UI file that sorts first has a canvas of that
    name. unsaved: whether the open file has unsaved changes (None: ask it); see unsaved_canvases()."""
    root = pathlib.Path(root) if root is not None else _require_ui_file()
    source = gameui.source_path(root)
    index = read_index(root)
    name = scene.name
    other = _conflict(root, index, name, source)
    if other is not None:
        message = f"UI screen '{name}' is also in {other}, which wins; rename one of them"
        return {"canvas": name, "skipped": message, "warnings": [message]}
    entry = index["canvases"].get(name)
    taken = {e["file"] for n, e in index["canvases"].items() if n != name}
    file_name = entry["file"] if entry is not None and entry["file"].lower() not in _folded(taken) \
        else _free_file(name, taken)
    result = _write_canvas(scene, root, source, file_name)
    index["canvases"][name] = _index_entry(result, source)
    _write_index(root, index)
    _note_unsaved(root, source, [name], _open_file_dirty() if unsaved is None else unsaved)
    return result


def remove_canvas(name: str, root=None) -> bool:
    """Drop a canvas of the open UI file from the index and delete its file (its scene was removed).
    Returns True when there was one."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    if root is None:
        return False
    index = read_index(root)
    entry = index["canvases"].get(name)
    if entry is None or entry.get("source") != gameui.source_path(root):
        return False
    del index["canvases"][name]
    if entry["file"].lower() not in _folded(e["file"] for e in index["canvases"].values()):
        _remove_canvas_files(root, entry["file"])
    _write_index(root, index)
    _note_unsaved(root, entry["source"], [name], False)
    return True


def prune_index(root) -> list:
    """Drop the index entries whose UI file no longer exists, with their files. Returns their names."""
    index = read_index(root)
    gone = [n for n, e in index["canvases"].items() if not _source_exists(root, e.get("source", ""))]
    if not gone:
        return []
    files = {index["canvases"][n]["file"] for n in gone}
    for name in gone:
        del index["canvases"][name]
    kept = _folded(e["file"] for e in index["canvases"].values())
    for file_name in files:
        if file_name.lower() not in kept:
            _remove_canvas_files(root, file_name)
    _write_index(root, index)
    return gone


# Canvases exported from unsaved changes (the live link, Play or Export in a modified UI file) are listed
# in .gamestudio/ui_unsaved.json, outside the export the game ships: if those changes are discarded (closed
# without saving, Revert), stale_ui_files() still sees that file as stale, and the next Play or Export from
# another file exports the saved .blend again. Exporting the saved file (on save, on open, in a background
# Blender) clears its list.
UNSAVED_FILE = "ui_unsaved.json"


def _open_file_dirty() -> bool:
    """The open file has unsaved changes. In --background Blender never clears is_dirty after a save, so
    the bridge's own record is used there (bridge.api.is_dirty)."""
    try:
        from ..bridge import api
    except ImportError:
        return bool(bpy.data.is_dirty)
    return api.is_dirty()


def unsaved_canvases(root) -> dict:
    """{UI file (project-relative): [canvas names]} exported from unsaved changes of that file."""
    data = _read_json(project.gs_dir(root) / UNSAVED_FILE)
    if not isinstance(data, dict):
        return {}
    return {source: sorted(n for n in names if isinstance(n, str))
            for source, names in data.items() if isinstance(names, list) and names}


def _note_unsaved(root, source: str, names, unsaved: bool, replace: bool = False) -> None:
    """Record (unsaved) or forget canvases of a UI file in the unsaved list; replace: they are its whole list."""
    data = unsaved_canvases(root)
    listed = set() if replace else set(data.get(source, ()))
    listed = listed | set(names) if unsaved else listed - set(names)
    new = {s: n for s, n in data.items() if s != source}
    if listed:
        new[source] = sorted(listed)
    if new == data:
        return
    path = project.gs_dir(root) / UNSAVED_FILE
    try:
        if new:
            _write_json(path, new)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not update %s: %s", path, exc)


def export_ui_file(root=None, unsaved=None) -> dict:
    """Export every canvas of the open UI file and rewrite the index (§3.1). Returns {"file", "canvases":
    [{"canvas", "file", "rev", "written", "elements", "warnings"} or {"canvas", "skipped"}], "removed":
    [canvas names dropped from the index], "locales": [language codes], "warnings"}. Raises export.ExportError
    outside a UI file. unsaved: whether the open file has unsaved changes (None: ask it; False when exporting the
    saved file). Refuses (export.ExportError) when the registry has no UI components. The translation tables
    are exported too (export_locales())."""
    root = pathlib.Path(root) if root is not None else _require_ui_file()
    _require_ui_components(root)
    locales = export_locales(root)
    source = gameui.source_path(root)
    index = read_index(root)
    removed, dropped = [], set()
    for name, entry in list(index["canvases"].items()):
        if not _source_exists(root, entry.get("source", "")):
            removed.append(name)
            dropped.add(entry["file"])
            del index["canvases"][name]
    scenes = sorted(gameui.canvas_scenes(), key=lambda s: s.name)
    names = {scene.name for scene in scenes}
    ours = {n: e for n, e in index["canvases"].items() if e.get("source") == source}
    for name in ours:
        if name not in names:
            removed.append(name)
        del index["canvases"][name]
    results, warnings = [], list(locales["warnings"])
    by_case = {}
    for scene in scenes:
        by_case.setdefault(scene.name.lower(), []).append(scene.name)
    for same in (group for group in by_case.values() if len(group) > 1):
        message = (f"UI screens {' and '.join(repr(n) for n in same)} differ only in letter case: their files "
                   "would collide on Windows (each gets its own), and ctx.ui.open() tells them apart only by "
                   "case. Rename one of them")
        warnings.append(message)
        log.warning("%s", message)
    owned = []
    for scene in scenes:
        other = _conflict(root, index, scene.name, source)
        if other is not None:
            message = f"UI screen '{scene.name}' is also in {other}, which wins; rename one of them"
            results.append({"canvas": scene.name, "skipped": message})
            warnings.append(message)
            log.warning("%s", message)
            continue
        if scene.name in index["canvases"]:
            message = f"UI screen '{scene.name}' is also in {index['canvases'][scene.name]['source']}; this file wins"
            warnings.append(message)
            log.warning("%s", message)
            del index["canvases"][scene.name]
        owned.append(scene)
    taken = {e["file"] for e in index["canvases"].values()}
    for scene in owned:
        previous = ours.get(scene.name)
        file_name = previous["file"] if previous is not None and previous["file"].lower() not in _folded(taken) \
            else _free_file(scene.name, taken)
        taken.add(file_name)
        result = _write_canvas(scene, root, source, file_name)
        index["canvases"][scene.name] = _index_entry(result, source)
        results.append({k: result[k] for k in ("canvas", "file", "rev", "written", "elements", "warnings")})
        warnings.extend(w for w in result["warnings"] if w not in warnings)
    for file_name in dropped | {e["file"] for e in ours.values()}:
        if file_name.lower() not in _folded(taken):  # 'Pause.ui.json' is 'pause.ui.json' on Windows
            _remove_canvas_files(root, file_name)
    _write_index(root, index)
    _note_unsaved(root, source, [s.name for s in owned], _open_file_dirty() if unsaved is None else unsaved,
                  replace=True)
    return {"file": source, "canvases": results, "removed": removed, "locales": sorted(locales["tables"]),
            "warnings": warnings}


# --- Other UI files, Export All and Play ------------------------------------------------------------


def _background_script() -> str:
    package = pathlib.Path(__file__).resolve().parents[1]
    return "\n".join([
        "import json, sys, importlib",
        f"sys.path.insert(0, {str(package.parent)!r})",
        f"gs = importlib.import_module({package.name!r})",
        "gs.register()",
        f"core = importlib.import_module({package.name!r} + '.core')",
        "project, registry = core.project, core.registry",
        f"gameui_export = importlib.import_module({package.name!r} + '.core.gameui_export')",
        "registry.reload_registry(project.project_root())",
        "try:",
        "    out = gameui_export.export_ui_file(unsaved=False)",  # the saved file, just loaded
        "except Exception as exc:",
        "    out = {'error': f'{type(exc).__name__}: {exc}'}",
        f"print({EXPORT_LINE!r} + json.dumps(out), flush=True)",
    ])


def export_ui_file_background(path, blender=None, timeout=600.0) -> dict:
    """Export the UI file at path in a background Blender (the open file stays as it is). Returns
    export_ui_file()'s result. Raises export.ExportError."""
    exe = blender or bpy.app.binary_path
    cmd = [exe, "--background", "--factory-startup", str(path), "--python-exit-code", "1",
           "--python-expr", _background_script()]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=dict(os.environ))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise export.ExportError(f"Could not export {path} in the background: {exc}") from exc
    lines = [line for line in proc.stdout.splitlines() if line.startswith(EXPORT_LINE)]
    if not lines:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
        raise export.ExportError(f"Exporting {path} in the background failed (exit {proc.returncode}): {tail}")
    out = json.loads(lines[-1][len(EXPORT_LINE):])
    if "error" in out:
        raise export.ExportError(f"{pathlib.Path(path).name}: {out['error']}")
    return out


def _is_open(path) -> bool:
    if not bpy.data.filepath:
        return False
    try:
        return pathlib.Path(path).resolve() == pathlib.Path(bpy.data.filepath).resolve()
    except OSError:
        return False


def _export_one(root, path, blender=None) -> dict:
    if _is_open(path):
        return export_ui_file(root)
    return export_ui_file_background(path, blender=blender)


def export_all_ui(root=None, blender=None) -> dict:
    """Export every UI file of the project: the open one here, the others in a background Blender.
    Returns {"ui": [export_ui_file() results], "errors": [{"file", "error"}]}."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    results, errors = [], []
    if root is None:
        return {"ui": results, "errors": errors}
    prune_index(root)
    export_locales(root)
    for path in gameui.ui_files(root):
        try:
            results.append(_export_one(root, path, blender))
        except export.ExportError as exc:
            errors.append({"file": gameui.source_path(root, path), "error": str(exc)})
    for error in errors:
        log.warning("Export of %s failed: %s", error["file"], error["error"])
    return {"ui": results, "errors": errors}


def _library_mtimes() -> list:
    """The modification times of the files the open file links from (a level whose object a model shows)."""
    out = []
    for lib in bpy.data.libraries:
        try:
            out.append(os.path.getmtime(bpy.path.abspath(lib.filepath, library=lib.library)))
        except (OSError, ValueError):
            pass
    return out


def stale_ui_files(root) -> list:
    """The UI files whose canvases are missing from the export, older than the .blend (the open one: or than a
    file it links from), or exported from unsaved changes (unsaved_canvases())."""
    root = pathlib.Path(root)
    by_source = {}
    for entry in read_index(root)["canvases"].values():
        by_source.setdefault(entry.get("source"), []).append(entry)
    unsaved = unsaved_canvases(root)
    stale = []
    for path in gameui.ui_files(root):
        source = gameui.source_path(root, path)
        entries = by_source.get(source)
        if not entries or unsaved.get(source):
            stale.append(path)
            continue
        changed = path.stat().st_mtime
        if _is_open(path):  # its 3D models follow the files they link (a level's player, §11.1)
            changed = max([changed, *_library_mtimes()])
        for entry in entries:
            canvas = gameui.export_dir(root) / entry["file"]
            files = entry.get("files") if isinstance(entry.get("files"), dict) else {}
            if not canvas.is_file() or canvas.stat().st_mtime < changed or \
                    not all((gameui.export_dir(root) / name).is_file() for name in files):  # its models GLB
                stale.append(path)
                break
    return stale


def export_stale(root=None, blender=None, include_open=False) -> dict:
    """Play: export the UI files whose canvases are missing or older than their .blend (include_open:
    the open UI file in any case). Returns export_all_ui()'s shape."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    results, errors = [], []
    if root is None:
        return {"ui": results, "errors": errors}
    prune_index(root)
    export_locales(root)  # cheap: the tables that changed since the last export
    paths = stale_ui_files(root)
    if include_open and gameui.is_ui_file() and not any(_is_open(p) for p in paths):
        paths.insert(0, pathlib.Path(bpy.data.filepath))
    for path in paths:
        try:
            results.append(_export_one(root, path, blender))
        except export.ExportError as exc:
            errors.append({"file": gameui.source_path(root, path), "error": str(exc)})
            log.warning("Export of %s failed: %s", gameui.source_path(root, path), exc)
    return {"ui": results, "errors": errors}


def start_level(root) -> str:
    """The start level's name (gamestudio.json startScene)."""
    return project.read_config(root).get("startScene") or "main"


def play_level(root=None, blender=None, force=False) -> dict:
    """The export Play from a UI file plays: the start level's existing export, or (missing, or force)
    an export of scenes/<startScene>.blend in a background Blender. Returns {"scene", "rev", "entities",
    "manifest", "tier"} ("reuse" for the existing export). Raises export.ExportError."""
    root = pathlib.Path(root) if root is not None else project.project_root()
    if root is None:
        raise export.ExportError("Save the .blend inside a GameStudio project (folder with gamestudio.json)")
    name = start_level(root)
    manifest = project.export_dir(root) / f"{export._sanitize(name)}.scene.json"
    data = None if force else _read_json(manifest)
    if isinstance(data, dict) and data.get("scene") and data.get("rev") is not None:
        return {"scene": data["scene"], "rev": int(data["rev"]), "entities": len(data.get("entities") or []),
                "manifest": str(manifest), "tier": "reuse"}
    path = root / export.SCENES_DIR / f"{name}.blend"
    if not path.is_file():
        raise export.ExportError(f"The start level scenes/{name}.blend does not exist (gamestudio.json startScene); "
                                 "open or create a level to play")
    summary = export.export_file(path, blender=blender, timeout=LEVEL_EXPORT_TIMEOUT)
    return {**summary, "tier": "full", "file": f"{export.SCENES_DIR}/{name}.blend"}


def play_export(root=None, blender=None, force=False) -> dict:
    """What Play and the bridge's export_scene do in a UI file: export the open UI file (and the other UI
    files that are stale), then return play_level()'s result with "ui": {"canvases", "errors",
    "warnings"} added."""
    root = pathlib.Path(root) if root is not None else _require_ui_file()
    ui = export_stale(root, blender=blender, include_open=True)
    result = play_level(root, blender=blender, force=force)
    canvases = [c for r in ui["ui"] for c in r["canvases"]]
    warnings = [w for r in ui["ui"] for w in r.get("warnings", [])]
    result["ui"] = {"canvases": [{k: c[k] for k in ("canvas", "rev", "written") if k in c} for c in canvases],
                    "errors": ui["errors"], "warnings": warnings[:20]}
    return result
