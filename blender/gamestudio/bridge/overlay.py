# SPDX-License-Identifier: GPL-3.0-or-later
"""2D drawing for render_views: backgrounds, metric rulers, dimension lines and text labels.

Pixels are numpy arrays (height, width, 3) uint8, top row first. Lines and boxes are drawn with
numpy; text is drawn with Blender's UI font (blf) into a GPU offscreen of the sheet's size and
composited, which works in --background too once gpu.init() has run.
"""

import math

import numpy as np

BG_TOP = np.array([0.42, 0.43, 0.45]) * 255
BG_BOTTOM = np.array([0.27, 0.28, 0.30]) * 255
PAPER_MINOR = np.array([0.36, 0.37, 0.39]) * 255
PAPER_MAJOR = np.array([0.47, 0.48, 0.50]) * 255
GROUND = np.array([0.62, 0.60, 0.52]) * 255
DIM = np.array([1.0, 0.86, 0.35]) * 255
HIGHLIGHT = np.array([1.0, 0.55, 0.12]) * 255
TEXT = (1.0, 1.0, 1.0, 1.0)
TEXT_DIM = (1.0, 0.9, 0.55, 1.0)
BOX = (0.06, 0.06, 0.07, 0.72)


def gradient(width: int, height: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, height)[:, None, None]
    img = BG_TOP * (1 - t) + BG_BOTTOM * t
    return np.broadcast_to(img, (height, width, 3)).astype(np.uint8).copy()


def nice_step(span: float, target_lines: float = 6.0) -> float:
    """A 1/2/5 x 10^n spacing that splits `span` into about target_lines parts."""
    raw = max(span, 1e-6) / target_lines
    base = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * base >= raw:
            return m * base
    return 10 * base


def fmt_m(value: float, step: float | None = None) -> str:
    """A metre value with as many decimals as the step needs."""
    if step is not None and step >= 1:
        text = f"{value:.0f}"
    elif step is not None:
        decimals = max(0, -math.floor(math.log10(step) + 1e-9))
        text = f"{value:.{decimals}f}"
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".") if abs(value) < 100 else f"{value:.0f}"
    return "0" if text in ("-0", "-0.0", "-0.00") else text


def hline(img, y: int, x0: int, x1: int, color, width: int = 1) -> None:
    h, w = img.shape[:2]
    x0, x1 = sorted((max(0, int(x0)), min(w - 1, int(x1))))
    for dy in range(width):
        yy = int(y) + dy - width // 2
        if 0 <= yy < h and x1 >= x0:
            img[yy, x0:x1 + 1] = color


def vline(img, x: int, y0: int, y1: int, color, width: int = 1) -> None:
    h, w = img.shape[:2]
    y0, y1 = sorted((max(0, int(y0)), min(h - 1, int(y1))))
    for dx in range(width):
        xx = int(x) + dx - width // 2
        if 0 <= xx < w and y1 >= y0:
            img[y0:y1 + 1, xx] = color


def composite(base: np.ndarray, rgba: np.ndarray) -> np.ndarray:
    """Straight-alpha RGBA (uint8) over an RGB base."""
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    out = rgba[:, :, :3].astype(np.float32) * alpha + base.astype(np.float32) * (1 - alpha)
    return out.clip(0, 255).astype(np.uint8)


def ghost(base: np.ndarray, rgba: np.ndarray, strength: float = 0.45) -> np.ndarray:
    """Context objects, faded towards the background (focus mode)."""
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0 * strength
    grey = rgba[:, :, :3].astype(np.float32).mean(axis=2, keepdims=True) * 0.6 + 70
    out = grey * alpha + base.astype(np.float32) * (1 - alpha)
    return out.clip(0, 255).astype(np.uint8)


def outline(img: np.ndarray, mask: np.ndarray, color, thickness: int = 2) -> None:
    """Draw a ring around the mask (focus highlight)."""
    grown = mask.copy()
    for _ in range(thickness):
        g = grown.copy()
        g[1:, :] |= grown[:-1, :]
        g[:-1, :] |= grown[1:, :]
        g[:, 1:] |= grown[:, :-1]
        g[:, :-1] |= grown[:, 1:]
        grown = g
    img[grown & ~mask] = color


class Labels:
    """Text items in sheet coordinates (top-left origin), drawn in one GPU pass."""

    def __init__(self):
        self.items = []
        self.boxes = []  # placed label rectangles, for collision checks

    def add(self, x: float, y: float, text: str, *, size: int = 13, color=TEXT, box: bool = True,
            anchor: str = "left", avoid: bool = False) -> bool:
        width = len(text) * size * 0.56 + 6
        height = size + 5
        left = x - width / 2 if anchor == "center" else (x - width if anchor == "right" else x)
        top = y - height / 2
        rect = (left, top, left + width, top + height)
        if avoid and any(not (rect[2] < b[0] or rect[0] > b[2] or rect[3] < b[1] or rect[1] > b[3])
                         for b in self.boxes):
            return False
        self.boxes.append(rect)
        self.items.append({"x": x, "y": y, "text": text, "size": size, "color": color, "box": box,
                           "anchor": anchor})
        return True

    def draw(self, img: np.ndarray) -> bool:
        """Composite the labels onto img. False when the GPU (and so text) is unavailable."""
        if not self.items:
            return True
        try:
            rgba = _draw_text_layer(img.shape[1], img.shape[0], self.items)
        except Exception:
            return False
        img[:] = _composite_premultiplied(img, rgba)
        return True


def _composite_premultiplied(base: np.ndarray, rgba: np.ndarray) -> np.ndarray:
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    out = rgba[:, :, :3].astype(np.float32) + base.astype(np.float32) * (1 - alpha)
    return out.clip(0, 255).astype(np.uint8)


def ensure_gpu() -> None:
    """In --background the GPU module must be initialised once before drawing."""
    import bpy
    import gpu

    if bpy.app.background and not getattr(ensure_gpu, "done", False):
        try:
            gpu.init()
        except (SystemError, RuntimeError, AttributeError):
            pass  # already initialised, or no GPU: callers fall back
        ensure_gpu.done = True


def _draw_text_layer(width: int, height: int, items: list) -> np.ndarray:
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix

    ensure_gpu()
    font = 0
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0))
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(Matrix(((2 / width, 0, 0, -1), (0, 2 / height, 0, -1),
                                                          (0, 0, 1, 0), (0, 0, 0, 1))))
                gpu.state.blend_set("ALPHA")
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                placed = []
                for item in items:
                    blf.size(font, item["size"])
                    tw, _th = blf.dimensions(font, item["text"])
                    th = item["size"] * 0.72
                    x = item["x"] - (tw / 2 if item["anchor"] == "center" else tw if item["anchor"] == "right" else 0)
                    baseline = height - item["y"] - th / 2
                    placed.append((x, baseline, tw, th, item))
                    if item["box"]:
                        pad = max(2, item["size"] // 4)
                        x0, y0, x1, y1 = x - pad, baseline - pad - 1, x + tw + pad, baseline + th + pad + 1
                        batch = batch_for_shader(shader, "TRIS", {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y0),
                                                                          (x1, y1), (x0, y1)]})
                        shader.uniform_float("color", BOX)
                        batch.draw(shader)
                for x, baseline, _tw, _th, item in placed:
                    blf.size(font, item["size"])
                    blf.color(font, *item["color"])
                    blf.position(font, x, baseline, 0)
                    blf.draw(font, item["text"])
                gpu.state.blend_set("NONE")
            buf = fb.read_color(0, 0, width, height, 4, 0, "UBYTE")
        buf.dimensions = width * height * 4
        return np.frombuffer(bytes(buf), dtype=np.uint8).reshape(height, width, 4)[::-1]
    finally:
        offscreen.free()
