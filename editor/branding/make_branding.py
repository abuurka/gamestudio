# SPDX-License-Identifier: GPL-3.0-or-later
"""Render GameStudio's splash screen and application icon with Blender.

  python editor/tools/gsfork.py branding

Writes editor/branding/generated/{splash.png, icon_256.png, gamestudio.ico} and
copies them into editor/branding/overlay/, which gsfork applies over the upstream
tree. The mark is an isometric "game block" of three faces. It is deliberately
nothing like Blender's logo: GameStudio may say it is *based on Blender's open
source code*, but it must not use Blender's marks (docs/fork/ARCHITECTURE.md section 9).
"""

import math
import pathlib
import struct
import sys
import tempfile

import bpy
from mathutils import Vector

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "generated"
OVERLAY = HERE / "overlay"

ORANGE = (0.98, 0.45, 0.06, 1)
ORANGE_LIGHT = (1.0, 0.68, 0.25, 1)
TEAL = (0.05, 0.62, 0.66, 1)
NIGHT = (0.035, 0.04, 0.07)


ISO = (math.radians(35.264), 0, math.radians(45))  # shows a cube's top, left and right faces


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "MATERIAL"  # one flat colour per face material
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    return scene


def front_camera(scene, ortho_scale, center=(0, 0)):
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = ortho_scale
    scene.collection.objects.link(cam)
    cam.location = (center[0], -40, center[1])
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam
    return cam


def iso_group(scene, name, location):
    """An empty rotated to an isometric view; blocks parented to it read as a 3D stack."""
    root = bpy.data.objects.new(name, None)
    root.location = location
    root.rotation_mode = "ZXY"  # turn 45 degrees about Z first, then tilt toward the camera
    root.rotation_euler = ISO
    scene.collection.objects.link(root)
    return root


def block(scene, name, location, size, top, left, right, parent=None):
    """A cube with one flat colour per visible face (top/left/right) under an iso group."""
    s = size / 2
    verts = [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s), (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]
    faces = [(4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (0, 3, 2, 1), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    colors = [top, left, right, right, left, left]
    for i, color in enumerate(colors):
        mat = bpy.data.materials.new(f"{name}_{i}")
        mat.diffuse_color = color
        mesh.materials.append(mat)
        mesh.polygons[i].material_index = i
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.parent = parent
    scene.collection.objects.link(obj)
    return obj


def shade(color, k):
    return (color[0] * k, color[1] * k, color[2] * k, 1)


def mark(scene, location, size=1.0):
    """The GameStudio mark: an L of three orange blocks crowned by a teal one."""
    root = iso_group(scene, "Mark", location)
    g = size * 1.06
    for i, (x, y, z, color) in enumerate([
        (0, 0, 0, ORANGE), (g, 0, 0, ORANGE), (0, g, 0, ORANGE), (0, 0, g, TEAL),
    ]):
        top = ORANGE_LIGHT if color is ORANGE else (0.35, 0.85, 0.86, 1)
        # Centre the stack on the group origin so it frames nicely.
        block(scene, f"Mark{i}", (x - g / 2, y - g / 2, z - g / 2), size, top, shade(color, 0.8),
              shade(color, 0.6), parent=root)
    return root


def render(scene, path, width, height, transparent):
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = transparent
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def make_icon():
    scene = reset()
    mark(scene, (0, 0, 0), size=1.0)
    front_camera(scene, 3.7, center=(0, -0.12))
    path = OUT / "icon_256.png"
    render(scene, path, 256, 256, transparent=True)
    return path


def text(scene, body, location, size, color, align="CENTER"):
    curve = bpy.data.curves.new(body[:12], "FONT")
    curve.body = body
    curve.size = size
    curve.align_x = align
    obj = bpy.data.objects.new(body[:12], curve)
    obj.location = location
    obj.color = color
    scene.collection.objects.link(obj)
    return obj


def make_splash():
    scene = reset()
    scene.world = bpy.data.worlds.new("Night")
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = NIGHT
    # The camera sees 16 x 8 units: the mark on the left, the name on the right,
    # and a low field of level tiles along the bottom edge.
    front_camera(scene, 16.0)
    mark(scene, (-4.9, 0, 0.55), size=1.15)
    tiles = iso_group(scene, "Tiles", (3.0, 8, -6.3))
    for i in range(-7, 8):
        for j in range(-7, 8):
            h = (math.sin(i * 0.9) + math.cos(j * 1.3) + 2) / 4
            if (i * 7 + j * 3) % 4 == 0:
                base = (0.06 + 0.04 * h, 0.07 + 0.04 * h, 0.12 + 0.06 * h, 1)
                block(scene, f"Tile{i}_{j}", (i * 1.0, j * 1.0, h * 0.5), 0.96, shade(base, 1.3),
                      shade(base, 0.95), shade(base, 0.72), parent=tiles)
    white = (0.96, 0.96, 0.98, 1)
    soft = (0.64, 0.68, 0.78, 1)
    for obj in [text(scene, "GameStudio", (-2.7, -2, 0.45), 1.7, white, "LEFT"),
                text(scene, "Game engine IDE  ·  based on Blender's open source code", (-2.62, -2, -0.45), 0.36,
                     soft, "LEFT")]:
        obj.rotation_euler = (math.radians(90), 0, 0)
        mat = bpy.data.materials.new(obj.name)
        mat.diffuse_color = obj.color
        obj.data.materials.append(mat)
    path = OUT / "splash.png"
    render(scene, path, 1000, 500, transparent=False)
    return path


def write_ico(png_256: pathlib.Path, target: pathlib.Path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """A Windows .ico with PNG-compressed images (supported since Windows Vista)."""
    images = []
    with tempfile.TemporaryDirectory() as tmp:
        for size in sizes:
            if size == 256:
                images.append((size, png_256.read_bytes()))
                continue
            img = bpy.data.images.load(str(png_256), check_existing=False)
            img.pixels[0]  # images load lazily; touch the buffer before scaling
            img.scale(size, size)
            out = pathlib.Path(tmp) / f"icon_{size}.png"
            img.filepath_raw = str(out)
            img.file_format = "PNG"
            img.save()
            images.append((size, out.read_bytes()))
            bpy.data.images.remove(img)
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    target.write_bytes(header + entries + blobs)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icon = make_icon()
    splash = make_splash()
    ico = OUT / "gamestudio.ico"
    write_ico(icon, ico)
    targets = {
        splash: OVERLAY / "release" / "datafiles" / "splash.png",
        ico: OVERLAY / "release" / "windows" / "icons" / "winblender.ico",
    }
    for src, dst in targets.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    print(f"BRANDING_OK {splash} {ico}")


try:
    main()
except Exception as exc:  # make failures visible to gsfork
    import traceback

    traceback.print_exc()
    print(f"BRANDING_ERROR {exc}")
    sys.exit(1)
