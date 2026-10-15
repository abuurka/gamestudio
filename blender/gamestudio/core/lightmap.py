# SPDX-License-Identifier: GPL-3.0-or-later
"""Baked lighting: Cycles lightmaps for the static objects of a scene (API.md "Baked lighting").

The scheme is "baked indirect". A lightmap holds all the light a static surface gets except the direct
light of realtime lamps: the world's sky light with its occlusion (ambient occlusion), the bounce light
of every lamp, of the sky and of emissive surfaces, and all the light of lamps marked baked. Realtime
lamps (every sun, point and spot lamp unless its custom property gs_lightmap is "baked") stay in the
game and light everything directly, with realtime shadows. Area lamps are always baked (glTF has none).
In the game a lightmap replaces the fill light, ambient light and the environment's diffuse light of
its mesh, and baked lamps are switched off, so nothing is lit twice.

Static objects: meshes in the view layer, visible and rendered, drawn (not collision-only), local, with
no RigidBody (or a fixed one), no CharacterController, Spin or Animator, no animation, no armature or
shape keys, emitting no instances, no moving ancestor, and not opted out (gs_lightmap = False). Only
static objects take part in a bake: what moves casts no baked shadow.

A bake:
1. copies each receiver's evaluated mesh into world space (a proxy), with a new UV map, GS_Lightmap:
   a Smart UV Project of every proxy of an atlas together, packed into the atlas with a margin;
2. groups the receivers into square atlases by surface area and texel density (texels per metre);
3. bakes Cycles' DIFFUSE light pass (no colour: the light a white surface gets) twice, the indirect
   light with every lamp, then the direct light without the realtime lamps, and adds them;
4. denoises (a bilateral filter guided by a baked normal map, inside each chart), pads the charts and
   stores each atlas as an 8-bit sRGB PNG normalised by a scale (the light of a texel of 1.0).

Files live in <project>/.gamestudio/lightmaps/<scene>/ (lightmap.json, the PNGs, uv/*.npy: each
object's lightmap UVs per loop of its evaluated mesh), never in the .blend. The export
(export_swap, manifest_section) swaps in meshes that carry the stored UVs and writes the manifest's
`lightmaps` section. A bake whose inputs did not change is skipped; `objects` re-bakes only those
objects (into new atlases), other objects keep their lightmaps.
"""

import contextlib
import datetime
import hashlib
import json
import math
import os
import pathlib
import shutil
import time

import bpy

from .log import get_logger

log = get_logger(__name__)

VERSION = 1
LM_UV = "GS_Lightmap"
DIR = "lightmaps"
META = "lightmap.json"
RESULT_LINE = "GS_LIGHTMAP "
PROP = "gs_lightmap"           # object: False opts out; light: "baked" / "realtime"
MOVERS = ("CharacterController", "Spin", "Animator")
QUALITIES = ("draft", "preview", "final")
# density: texels per metre; max_size: largest atlas, px; atlases: most atlases (density drops to fit);
# pad: px between charts.
QUALITY = {
    "draft": {"density": 2.0, "samples": 4, "bounces": 2, "max_size": 256, "atlases": 2, "pad": 2, "denoise": 1},
    "preview": {"density": 5.0, "samples": 24, "bounces": 3, "max_size": 1024, "atlases": 2, "pad": 3,
                "denoise": 2},
    "final": {"density": 10.0, "samples": 192, "bounces": 6, "max_size": 2048, "atlases": 4, "pad": 4,
              "denoise": 2},
}
MIN_SIZE = 64
FILL = 0.55                    # share of an atlas that packed charts cover (to size atlases)
MAX_CHANNEL = 3                # three.js reads uv .. uv3


class LightmapError(RuntimeError):
    pass


# --- Which objects -------------------------------------------------------------------------


def _opted_out(obj) -> bool:
    value = obj.get(PROP, True)
    return value is False or value == 0 or (isinstance(value, str) and value.lower() in ("off", "false", "no"))


def light_mode(obj) -> str:
    """"baked" or "realtime" for a light object (area lights are always baked)."""
    if obj.data.type == "AREA":
        return "baked"
    value = obj.get(PROP)
    return "baked" if isinstance(value, str) and value.lower() == "baked" else "realtime"


def _rigid_type(obj) -> str:
    from . import components

    try:
        return str(components.get_values(obj, "RigidBody").get("type", "dynamic"))
    except Exception:
        return "dynamic"


def _moves(obj) -> str:
    """Why obj (by itself) can move in the game, or ""."""
    enabled = [ref.name for ref in getattr(getattr(obj, "gs", None), "components", ()) if ref.enabled]
    if "RigidBody" in enabled and _rigid_type(obj) != "fixed":
        return f"it has a {_rigid_type(obj)} RigidBody"
    for name in MOVERS:
        if name in enabled:
            return f"it has a {name}"
    anim = obj.animation_data
    if anim is not None and (anim.action is not None or len(anim.nla_tracks)):
        return "it is animated"
    if obj.type == "EMPTY" and obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
        from . import export

        if export.is_prefab(obj.instance_collection):
            return "it is a prefab instance"
    return ""


def _static_reason(obj) -> str:
    """Why obj is not static (moves itself or through an ancestor), or ""."""
    node = obj
    for _ in range(64):
        if node is None:
            return ""
        reason = _moves(node)
        if reason:
            return reason if node is obj else f"its parent '{node.name}' moves ({reason})"
        node = node.parent
    return ""


def _shown(obj, view_layer) -> bool:
    return obj.visible_get(view_layer=view_layer) and not obj.hide_render


def static_objects(scene, view_layer=None) -> tuple:
    """(static mesh objects that get lightmaps, [(object, reason)] meshes left out)."""
    from . import bake, export

    view_layer = view_layer or export._export_view_layer(scene)
    objs = list(view_layer.objects)
    collision = set(export.collision_only(objs))
    prefab_members = {o.name_full for coll in export.prefab_collections() for o in coll.all_objects}
    depsgraph = _evaluated(scene, view_layer)
    instancers = bake._instancers(depsgraph)
    found, skipped = [], []
    for obj in objs:
        if obj.type != "MESH" or obj.data is None or not _shown(obj, view_layer):
            continue
        reason = ""
        if _opted_out(obj):
            reason = f"{PROP} is off"
        elif obj in collision:
            reason = "it is collision-only (not drawn)"
        elif obj.name_full in prefab_members:
            reason = "it is part of a prefab"
        elif obj.library is not None or obj.override_library is not None:
            reason = "it is linked from a library"
        elif any(m.type == "ARMATURE" for m in obj.modifiers) or obj.data.shape_keys is not None:
            reason = "it deforms (armature or shape keys)"
        elif obj.name_full in instancers:
            reason = "it emits instances"
        elif not len(obj.data.polygons) and not any(m.show_render for m in obj.modifiers):
            reason = "it has no faces"
        else:
            reason = _static_reason(obj)
        if reason:
            skipped.append((obj, reason))
        else:
            found.append(obj)
    return found, skipped


def _occluders(scene, view_layer, receivers) -> list:
    """Static objects besides the receivers that the bake renders: they cast shadows and bounce light."""
    from . import export

    names = {o.name_full for o in receivers}
    collision = set(export.collision_only(list(view_layer.objects)))
    out = []
    for obj in view_layer.objects:
        if obj.name_full in names or obj.type in ("CAMERA", "LIGHT", "ARMATURE") or not _shown(obj, view_layer):
            continue
        if obj in collision or _opted_out(obj) or _static_reason(obj):
            continue
        out.append(obj)
    return out


def _lights(view_layer) -> list:
    return [o for o in view_layer.objects if o.type == "LIGHT" and _shown(o, view_layer)]


# --- Settings and paths --------------------------------------------------------------------


def lightmap_dir(root, name) -> pathlib.Path:
    """<root>/.gamestudio/lightmaps/<name>."""
    from . import project

    return project.gs_dir(root) / DIR / name


def _settings(quality: str, root, density=None, samples=None) -> dict:
    from . import project

    if quality not in QUALITY:
        raise LightmapError(f"quality must be one of {', '.join(QUALITIES)} (not {quality!r})")
    settings = dict(QUALITY[quality])
    section = project.read_config(root).get("lightmap") if root is not None else None
    custom = section.get(quality) if isinstance(section, dict) else None
    for key, value in (custom or {}).items():
        if key in settings and isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            settings[key] = type(settings[key])(value)
    if density is not None:
        settings["density"] = float(density)
    if samples is not None:
        settings["samples"] = int(samples)
    settings["quality"] = quality
    return settings


def _read_meta(folder: pathlib.Path):
    try:
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("version") == VERSION else None


def status(scene=None, root=None, name=None) -> dict | None:
    """What the scene's stored lightmaps hold (lightmap.json without the per-object details), or None."""
    from . import export, project

    scene = scene or bpy.context.scene
    root = root or project.project_root()
    if root is None:
        return None
    meta = _read_meta(lightmap_dir(root, name or export.export_name(scene)))
    if meta is None:
        return None
    return {k: meta.get(k) for k in ("quality", "created", "seconds", "texture_bytes", "baked_lights")} | {
        "objects": len(meta.get("objects", {})), "atlases": len(meta.get("atlases", []))}


def clear(scene=None, root=None, name=None) -> bool:
    """Delete the scene's lightmaps. True if there were any."""
    from . import export, project

    scene = scene or bpy.context.scene
    root = root or project.project_root()
    if root is None:
        return False
    folder = lightmap_dir(root, name or export.export_name(scene))
    if not folder.is_dir():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


# --- Digests -------------------------------------------------------------------------------


def _evaluated(scene, view_layer):
    from .util import any_window

    window = any_window()
    kwargs = {"scene": scene, "view_layer": view_layer}
    if window is not None:
        kwargs["window"] = window
    with bpy.context.temp_override(**kwargs):
        return bpy.context.evaluated_depsgraph_get()


def mesh_digests(mesh) -> tuple:
    """(topology digest, geometry digest) of a mesh: the UVs of one fit the other when topology matches."""
    import numpy as np

    loops = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    topo = hashlib.sha1(loops.tobytes() + starts.tobytes()).hexdigest()[:16]
    geo = hashlib.sha1(topo.encode() + np.round(co, 4).tobytes()).hexdigest()[:16]
    return topo, geo


def _rounded(matrix) -> list:
    return [round(v, 4) for row in matrix for v in row]


def _inputs_digest(scene, receivers, occluders, lights, settings, geometry) -> str:
    from . import bake

    memo = {}
    parts = [VERSION, sorted(settings.items())]
    for obj in sorted([*receivers, *occluders], key=lambda o: o.name_full):
        mats = [bake.material_digest(s.material, memo) if s.material else None for s in obj.material_slots]
        parts.append((obj.name_full, _rounded(obj.matrix_world), geometry.get(obj.name_full), mats,
                      obj.instance_collection.name_full if obj.instance_collection else None))
    for obj in sorted(lights, key=lambda o: o.name_full):
        data = obj.data
        parts.append((obj.name_full, _rounded(obj.matrix_world), light_mode(obj), data.type, tuple(data.color),
                      round(data.energy, 4), round(getattr(data, "shadow_soft_size", 0.0), 4),
                      round(getattr(data, "angle", 0.0), 4), round(getattr(data, "spot_size", 0.0), 4)))
    world = scene.world
    parts.append(bake.material_digest(world, memo) if world is not None and world.node_tree else None)
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:24]


# --- Atlases -------------------------------------------------------------------------------


def _pow2(value: float) -> int:
    return 2 ** max(0, math.ceil(math.log2(max(value, 1.0))))


def _world_area(mesh) -> float:
    return float(sum(p.area for p in mesh.polygons))


def plan_atlases(areas: dict, settings: dict) -> list:
    """Group objects ({name: m²}) into atlases: [{"size": px, "objects": [names], "density": texels/m}].
    Over settings["atlases"] atlases of settings["max_size"], the density drops until everything fits; no
    object takes more than one atlas (a huge ground or sea gets fewer texels per metre)."""
    density, max_size = settings["density"], settings["max_size"]
    capacity = max_size ** 2
    need = {n: min(max(a, 1e-4) * density ** 2 / FILL, capacity) for n, a in areas.items()}
    total = sum(need.values())
    budget = max(1, int(settings.get("atlases", 4))) * capacity
    if total > budget:
        need = {n: v * budget / total for n, v in need.items()}
        total = budget
    count = max(1, math.ceil(total / capacity - 1e-9))
    groups = [{"objects": [], "texels": 0.0} for _ in range(count)]
    for name in sorted(need, key=lambda n: -need[n]):
        group = min(groups, key=lambda g: g["texels"])
        group["objects"].append(name)
        group["texels"] += need[name]
    out = []
    for group in groups:
        if group["objects"]:
            size = min(max(_pow2(math.sqrt(group["texels"])), MIN_SIZE), max_size)
            area = sum(areas[n] for n in group["objects"])
            out.append({"size": size, "objects": group["objects"],
                        "density": round(math.sqrt(size * size * FILL / max(area, 1e-4)), 2)})
    return out


# --- The bake ------------------------------------------------------------------------------


def _uv_array(mesh, name):
    import numpy as np

    uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    mesh.attributes[name].data.foreach_get("vector", uv)
    return uv


def _set_uv(mesh, name, uv) -> int:
    """Add (or fill) UV map `name` with uv; returns its index."""
    layer = mesh.uv_layers.get(name) or mesh.uv_layers.new(name=name)
    mesh.attributes[layer.name].data.foreach_set("vector", uv)
    return list(mesh.uv_layers.keys()).index(layer.name)


def _proxy(obj, depsgraph):
    """A world-space copy of obj's evaluated mesh, as a new object (not linked anywhere)."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
    digests = mesh_digests(mesh)
    mesh.transform(obj.matrix_world)
    if obj.matrix_world.determinant() < 0:
        mesh.flip_normals()
    mesh.name = f"gs_lm_{obj.name}"
    layer = mesh.uv_layers.get(LM_UV) or mesh.uv_layers.new(name=LM_UV)
    mesh.uv_layers.active = layer
    proxy = bpy.data.objects.new(f"gs_lm_{obj.name}", mesh)
    return proxy, digests


def _unwrap(bs, proxies, size: int, pad: int) -> None:
    """Smart UV Project of the proxies together, packed into the 0-1 square with `pad` px margins."""
    active = proxies[0]
    bs.select(active, proxies)
    with bpy.context.temp_override(**bs.override(active, proxies)):
        bpy.ops.object.mode_set(mode="EDIT")
    try:
        with bpy.context.temp_override(**bs.override(active, edit=True)):
            bpy.ops.mesh.reveal(select=False)
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.0, area_weight=0.0,
                                     correct_aspect=True, scale_to_bounds=False)
            bpy.ops.uv.pack_islands(rotate=True, scale=True, margin_method="FRACTION", margin=pad / size,
                                    shape_method="AABB")
    finally:
        with bpy.context.temp_override(**bs.override(active, proxies)):
            bpy.ops.object.mode_set(mode="OBJECT")


def _joined(bs, proxies, meshes):
    """One object made of copies of every proxy. Cycles bakes each selected object on its own, syncing the
    whole scene each time, so one object makes each pass a single bake. The proxies leave the bake scene
    (they keep their UVs for the files)."""
    copies = []
    for proxy in proxies:
        mesh = proxy.data.copy()
        meshes.append(mesh)
        obj = bpy.data.objects.new(f"{proxy.name}_bake", mesh)
        bs.scene.collection.objects.link(obj)
        copies.append(obj)
    for proxy in proxies:
        bs.scene.collection.objects.unlink(proxy)
    active = copies[0]
    bs.temp_objects.append(active)
    if len(copies) > 1:
        bs.select(active, copies)
        with bpy.context.temp_override(**bs.override(active, copies)):
            bpy.ops.object.join()
    bs.view_layer.update()
    return active


def _target_materials(proxy, image, cache, made) -> None:
    """Point every slot of proxy at a copy of its material whose active node is the atlas image."""
    from . import bake

    mesh = proxy.data
    count = max(len(mesh.materials), 1)
    targets = []
    for index in range(count):
        mat = mesh.materials[index] if index < len(mesh.materials) else None
        key = (mat.name_full if mat is not None else None, image.name)
        if key not in cache:
            copy = mat.copy() if mat is not None and mat.node_tree is not None else bpy.data.materials.new("gs_lm_white")
            node = copy.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            copy.node_tree.nodes.active = node
            cache[key] = copy
            made.append(copy)
        targets.append(cache[key])
    bake.set_materials(mesh, targets)


def _read_pixels(image):
    import numpy as np

    width, height = image.size
    px = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    return px.reshape(height, width, 4)


def _run_bake(bs, proxies, kind, pass_filter=None, normal_space=None) -> None:
    from . import bake

    kwargs = {"type": kind, "margin": 0, "use_clear": True, "uv_layer": LM_UV, "target": "IMAGE_TEXTURES",
              "save_mode": "INTERNAL"}
    if pass_filter:
        kwargs["pass_filter"] = pass_filter
    if normal_space:
        kwargs["normal_space"] = normal_space
    active = proxies[0]
    bs.select(active, proxies)
    with bpy.context.temp_override(**bs.override(active, proxies)):
        result = bpy.ops.object.bake(**kwargs)
    if "FINISHED" not in result:
        raise bake.BakeError(f"the Cycles {kind} bake returned {sorted(result)}")


def denoise(rgb, mask, normal=None, radius: int = 2, sigma_range: float = 0.6):
    """Edge-aware smoothing of a lightmap inside its charts: a bilateral filter on relative light,
    guided by the baked normals (texels facing other ways do not mix) and limited to covered texels."""
    import numpy as np

    if radius <= 0:
        return rgb
    height, width = mask.shape
    weight_sum = np.zeros((height, width), dtype=np.float32)
    acc = np.zeros_like(rgb)
    lum = np.log(np.maximum(rgb.mean(axis=2), 1e-4))
    pad = radius
    rgb_p = np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)))
    mask_p = np.pad(mask.astype(np.float32), pad)
    lum_p = np.pad(lum, pad)
    normal_p = np.pad(normal, ((pad, pad), (pad, pad), (0, 0))) if normal is not None else None
    sigma_s = max(radius / 1.5, 0.5)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            ys, xs = slice(pad + dy, pad + dy + height), slice(pad + dx, pad + dx + width)
            w = math.exp(-(dx * dx + dy * dy) / (2 * sigma_s * sigma_s)) * mask_p[ys, xs]
            w = w * np.exp(-((lum_p[ys, xs] - lum) ** 2) / (2 * sigma_range ** 2))
            if normal_p is not None:
                w = w * np.clip((normal_p[ys, xs] * normal).sum(axis=2), 0.0, 1.0) ** 8
            weight_sum += w
            acc += rgb_p[ys, xs] * w[:, :, None]
    out = np.where(weight_sum[:, :, None] > 1e-6, acc / np.maximum(weight_sum, 1e-6)[:, :, None], rgb)
    return np.where(mask[:, :, None], out, rgb).astype(np.float32)


def dilate(rgb, mask, steps: int):
    """Grow the charts by `steps` px into uncovered texels (the average of covered neighbours), so
    bilinear filtering at chart edges does not pick up black."""
    import numpy as np

    rgb, mask = rgb.copy(), mask.copy()
    for _ in range(max(0, steps)):
        if mask.all():
            break
        acc = np.zeros_like(rgb)
        count = np.zeros(mask.shape, dtype=np.float32)
        m = mask.astype(np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            shifted_m = np.roll(np.roll(m, dy, axis=0), dx, axis=1)
            acc += np.roll(np.roll(rgb, dy, axis=0), dx, axis=1) * shifted_m[:, :, None]
            count += shifted_m
        grow = (~mask) & (count > 0)
        rgb[grow] = acc[grow] / count[grow][:, None]
        mask = mask | grow
    return rgb


def encode(rgb, mask) -> tuple:
    """(uint8 sRGB array, scale): light normalised by its 99.5th percentile over the covered texels (Cycles'
    light pass divides by the albedo, so a few dark emissive texels can be very bright: they clip)."""
    import numpy as np

    covered = rgb[mask] if mask.any() else rgb.reshape(-1, 3)
    peak = float(np.percentile(covered.max(axis=1), 99.5)) if covered.size else 1.0
    scale = max(peak, 1e-3)
    x = np.clip(rgb / scale, 0.0, 1.0)
    srgb = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(srgb * 255).astype(np.uint8), round(scale, 6)


def _write_png(path: pathlib.Path, rows) -> None:
    from . import compare

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(compare.encode_png(rows))
    os.replace(tmp, path)


def _file_key(name: str) -> str:
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "object"
    return f"{stem}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"


def bake_lighting(scene=None, quality="preview", objects=None, name=None, root=None, force=False, density=None,
                  samples=None, window=None) -> dict:
    """Bake the lightmaps of the scene's static objects (all, or only `objects`) now. See the module doc."""
    import numpy as np

    from . import bake, export, project
    from .util import any_window

    started = time.perf_counter()
    lap = _laps(started)
    scene = scene or bpy.context.scene
    root = pathlib.Path(root) if root is not None else project.project_root()
    if root is None:
        raise LightmapError("Save the .blend inside a GameStudio project (folder with gamestudio.json) first")
    settings = _settings(quality, root, density, samples)
    name = name or export.export_name(scene)
    folder = lightmap_dir(root, name)
    window = window or any_window()
    if window is None:
        raise LightmapError("Baking needs a Blender window")
    if not bake.cycles_available():
        raise LightmapError("Baking lighting needs the Cycles render engine (enable it in Preferences > Add-ons)")
    view_layer = export._export_view_layer(scene)
    view_layer.update()
    static, skipped = static_objects(scene, view_layer)
    if objects:
        wanted = [o for o in objects if o]
        by_name = {o.name: o for o in static}
        unknown = [n for n in wanted if n not in by_name]
        if unknown:
            why = {o.name: r for o, r in skipped}
            raise LightmapError("; ".join(f"'{n}' is not a static mesh{': ' + why[n] if n in why else ''}"
                                          for n in unknown))
        receivers = [by_name[n] for n in dict.fromkeys(wanted)]
    else:
        receivers = static
    if not receivers:
        raise LightmapError("Nothing to bake: the scene has no static meshes (see API.md, Baked lighting)")
    occluders = _occluders(scene, view_layer, receivers)
    lights = _lights(view_layer)
    depsgraph = _evaluated(scene, view_layer)
    old = _read_meta(folder) or {}
    geometry = {}
    for obj in [*receivers, *occluders]:
        if obj.type == "MESH":
            mesh = obj.evaluated_get(depsgraph).to_mesh()
            try:
                geometry[obj.name_full] = mesh_digests(mesh)[1]
            finally:
                obj.evaluated_get(depsgraph).to_mesh_clear()
    digest = _inputs_digest(scene, receivers, occluders, lights, settings, geometry) if not objects else None
    skipped_out = [{"name": o.name, "reason": r} for o, r in skipped]
    if (digest is not None and not force and old.get("digest") == digest
            and all((folder / a["file"]).is_file() for a in old.get("atlases", []))):
        return _result(scene, old, folder, skipped_out, started, up_to_date=True)

    baked_lights = [o.name for o in lights if light_mode(o) == "baked"]
    run = hashlib.sha1(f"{time.time()}:{name}".encode()).hexdigest()[:6]
    tmp = folder.with_name(folder.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "uv").mkdir(parents=True)
    bs = bake._BakeScene(window, settings["samples"], scene.frame_current)
    made_materials, made_meshes, images, entries, atlases = [], [], [], {}, []
    try:
        cycles = bs.scene.cycles
        bs.scene.world = scene.world
        for attr, value in (("max_bounces", settings["bounces"] + 2), ("diffuse_bounces", settings["bounces"]),
                            ("glossy_bounces", 1), ("transmission_bounces", 2), ("transparent_max_bounces", 4),
                            ("caustics_reflective", False), ("caustics_refractive", False),
                            ("sample_clamp_indirect", 10.0)):
            if hasattr(cycles, attr):
                setattr(cycles, attr, value)
        for obj in [*occluders, *lights]:
            if obj.name_full not in {o.name_full for o in bs.scene.collection.objects}:
                bs.scene.collection.objects.link(obj)
                bs.linked.append(obj)
        proxies, digests, areas = {}, {}, {}
        for obj in receivers:
            proxy, digests[obj.name_full] = _proxy(obj, depsgraph)
            bs.scene.collection.objects.link(proxy)
            bs.temp_objects.append(proxy)
            proxies[obj.name_full] = proxy
            areas[obj.name_full] = _world_area(proxy.data)
        bs.view_layer.update()
        lap(f"{len(receivers)} objects copied ({len(occluders)} occluders, {len(lights)} lights)")
        plan = plan_atlases(areas, settings)
        all_proxies = []
        cache = {}
        for index, group in enumerate(plan):
            size = group["size"]
            group_proxies = [proxies[n] for n in group["objects"]]
            _unwrap(bs, group_proxies, size, settings["pad"])
            image = bpy.data.images.new(f"gs_lm_{index}", size, size, alpha=True, float_buffer=True)
            image.generated_color = (0.0, 0.0, 0.0, 0.0)
            images.append(image)
            for proxy in group_proxies:
                _target_materials(proxy, image, cache, made_materials)
            all_proxies += group_proxies
        lap("lightmap UVs: " + ", ".join(f"{g['size']} px ({len(g['objects'])} objects, {g['density']:g} texels/m)"
                                         for g in plan))
        target = [_joined(bs, all_proxies, made_meshes)]
        # Pass 1: bounce light of everything. Pass 2: direct light without the realtime lamps.
        _run_bake(bs, target, "DIFFUSE", {"INDIRECT"})
        indirect = [_read_pixels(i) for i in images]
        lap(f"indirect light baked ({settings['samples']} samples)")
        hidden = []
        for obj in lights:
            if light_mode(obj) == "realtime" and not obj.hide_render:
                obj.hide_render = True
                hidden.append(obj)
        try:
            bs.view_layer.update()
            _run_bake(bs, target, "DIFFUSE", {"DIRECT"})
        finally:
            for obj in hidden:
                obj.hide_render = False
        direct = [_read_pixels(i) for i in images]
        lap("direct light baked")
        bs.scene.cycles.samples = 1
        _run_bake(bs, target, "NORMAL", normal_space="OBJECT")
        normals = [_read_pixels(i) for i in images]
        lap("normals baked")
        texture_bytes = 0
        for index, group in enumerate(plan):
            a, b, n = indirect[index][::-1], direct[index][::-1], normals[index][::-1]   # top row first
            mask = (a[:, :, 3] > 0.5) & (b[:, :, 3] > 0.5)
            normal = n[:, :, :3] * 2.0 - 1.0
            normal /= np.maximum(np.linalg.norm(normal, axis=2, keepdims=True), 1e-6)
            radius = settings["denoise"]
            light = denoise(a[:, :, :3], mask, normal, radius=radius + 1, sigma_range=0.8) + \
                denoise(b[:, :, :3], mask, normal, radius=radius, sigma_range=0.35)
            light = dilate(light, mask, settings["pad"] + 1)
            rows, scale = encode(light, mask)
            file = f"{name}_{run}_{index}.png"
            _write_png(tmp / file, rows)
            atlases.append({"file": file, "size": group["size"], "scale": scale})
            texture_bytes += group["size"] ** 2 * 4
        lap("denoised and saved")
        for index, group in enumerate(plan):
            for obj_name in group["objects"]:
                obj = bpy.data.objects[obj_name]
                key = _file_key(obj_name)
                np.save(tmp / "uv" / f"{key}.npy", _uv_array(proxies[obj_name].data, LM_UV))
                topo, geo = digests[obj_name]
                entries[obj_name] = {"name": obj.name, "atlas": index, "uv": f"uv/{key}.npy",
                                     "loops": len(proxies[obj_name].data.loops), "topology": topo, "geometry": geo,
                                     "matrix": _rounded(obj.matrix_world)}
    finally:
        bs.close()
        for mesh in made_meshes:
            try:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:  # the joined object's mesh, removed with it
                pass
        for mat in made_materials:
            bpy.data.materials.remove(mat)
        for image in images:
            bpy.data.images.remove(image)
    meta = _merge(old if objects else {}, entries, atlases, tmp, folder)
    meta.update(version=VERSION, scene=name, quality=quality, settings=settings, digest=digest,
                baked_lights=baked_lights,
                created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                seconds=round(time.perf_counter() - started, 2))
    meta["texture_bytes"] = sum(a["size"] ** 2 * 4 for a in meta["atlases"])
    (tmp / META).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(tmp, folder)
    log.info("Baked lighting of '%s' (%s): %d objects, %d atlases, %.1f MB of textures in %.1f s", name, quality,
             len(entries), len(atlases), meta["texture_bytes"] / 2 ** 20, meta["seconds"])
    return _result(scene, meta, folder, skipped_out, started)


def _laps(started: float):
    """A logger of bake phases with the seconds each took (the console shows the progress)."""
    last = [started]

    def lap(label: str) -> None:
        now = time.perf_counter()
        log.info("Bake lighting: %s in %.1f s", label, now - last[0])
        last[0] = now

    return lap


def _merge(old: dict, entries: dict, atlases: list, tmp: pathlib.Path, folder: pathlib.Path) -> dict:
    """The new lightmap.json content: the new entries and atlases, plus the old ones a partial bake keeps
    (their files copied into tmp)."""
    kept = {k: v for k, v in old.get("objects", {}).items() if k not in entries}
    old_atlases = old.get("atlases", [])
    used = sorted({v["atlas"] for v in kept.values() if 0 <= v["atlas"] < len(old_atlases)})
    remap, out_atlases = {}, []
    for index in used:
        source = folder / old_atlases[index]["file"]
        if not source.is_file():
            continue
        shutil.copy2(source, tmp / source.name)
        remap[index] = len(out_atlases)
        out_atlases.append(old_atlases[index])
    objects = {}
    for key, entry in kept.items():
        uv = folder / entry["uv"]
        if entry["atlas"] in remap and uv.is_file():
            shutil.copy2(uv, tmp / entry["uv"])
            objects[key] = dict(entry, atlas=remap[entry["atlas"]])
    offset = len(out_atlases)
    out_atlases += atlases
    for key, entry in entries.items():
        objects[key] = dict(entry, atlas=entry["atlas"] + offset)
    return {"objects": objects, "atlases": out_atlases}


def _result(scene, meta, folder, skipped, started, up_to_date=False) -> dict:
    by_atlas = meta.get("atlases", [])
    return {
        "scene": meta.get("scene"),
        "quality": meta.get("quality"),
        "objects": [{"name": e["name"], "atlas": e["atlas"],
                     "size": by_atlas[e["atlas"]]["size"] if e["atlas"] < len(by_atlas) else None}
                    for e in meta.get("objects", {}).values()],
        "atlases": [dict(a) for a in by_atlas],
        "skipped": skipped,
        "baked_lights": list(meta.get("baked_lights", [])),
        "texture_bytes": meta.get("texture_bytes", 0),
        "seconds": round(time.perf_counter() - started, 2),
        "up_to_date": up_to_date,
        "dir": str(folder),
        "warnings": [],
    }


# --- Background runs ----------------------------------------------------------------------


def background_script(name, quality, objects=None, force=False) -> str:
    """Python for `blender --background <copy of the file> --python-expr`: bakes and prints RESULT_LINE + json."""
    package = pathlib.Path(__file__).resolve().parents[1]
    return "\n".join([
        "import json, sys, importlib",
        "sys.dont_write_bytecode = True",
        f"sys.path.insert(0, {str(package.parent)!r})",
        f"gs = importlib.import_module({package.name!r})",
        "gs.register()",
        f"lightmap = importlib.import_module({package.name!r} + '.core.lightmap')",
        f"registry = importlib.import_module({package.name!r} + '.core.registry')",
        f"project = importlib.import_module({package.name!r} + '.core.project')",
        "registry.reload_registry(project.project_root())",
        "try:",
        f"    out = lightmap.bake_lighting(name={name!r}, quality={quality!r}, objects={list(objects or [])!r} or None,"
        f" force={bool(force)!r})",
        "except Exception as exc:",
        "    out = {'error': f'{type(exc).__name__}: {exc}'}",
        f"print({RESULT_LINE!r} + json.dumps(out), flush=True)",
    ])


# --- Export -------------------------------------------------------------------------------


@contextlib.contextmanager
def export_swap(scene, view_layer, name, gs_folder, report):
    """While exporting: each baked object's mesh is a copy of its evaluated mesh with the stored lightmap
    UVs (modifiers off), so the GLB carries them as an extra TEXCOORD. report gets {"dir", "meta",
    "objects": {name_full: {"atlas", "uv"}}, "stale", "missing", "mesh_names"}."""
    import numpy as np

    from . import bake

    folder = pathlib.Path(gs_folder) / DIR / name
    report.update(dir=str(folder), meta=None, objects={}, stale=[], missing=[], mesh_names={})
    meta = _read_meta(folder)
    undo, meshes = [], []
    try:
        if meta is not None and meta.get("objects"):
            report["meta"] = meta
            layer_names = {o.name_full for o in view_layer.objects}
            depsgraph = _evaluated(scene, view_layer)
            digests = {}   # objects with the same evaluated mesh (bake._shared_key) share its digests
            for key, entry in meta["objects"].items():
                obj = bpy.data.objects.get(key)
                if (obj is None or obj.name_full not in layer_names or obj.type != "MESH"
                        or obj.library is not None or obj.override_library is not None or obj.mode != "OBJECT"):
                    report["missing"].append(entry.get("name", key))
                    continue
                try:
                    uv = np.load(folder / entry["uv"])
                except (OSError, ValueError):
                    report["missing"].append(obj.name)
                    continue
                evaluated = obj.evaluated_get(depsgraph)
                mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
                shared = bake._shared_key(obj, depsgraph)
                if shared is None or shared not in digests:
                    digests[shared] = mesh_digests(mesh)
                topo, geo = digests[shared]
                if len(uv) != len(mesh.loops) * 2 or topo != entry.get("topology"):
                    bpy.data.meshes.remove(mesh)
                    report["missing"].append(obj.name)
                    continue
                if geo != entry.get("geometry") or _rounded(obj.matrix_world) != entry.get("matrix"):
                    report["stale"].append(obj.name)
                if LM_UV in mesh.uv_layers:
                    mesh.uv_layers.remove(mesh.uv_layers[LM_UV])
                if not len(mesh.uv_layers):
                    _set_uv(mesh, "UVMap", uv)  # three.js takes the first UV map as `uv`: keep the lightmap at uv1
                channel = _set_uv(mesh, LM_UV, uv)
                if channel > MAX_CHANNEL:
                    bpy.data.meshes.remove(mesh)
                    report["missing"].append(obj.name)
                    log.warning("'%s' has too many UV maps for a lightmap (the game reads 4)", obj.name)
                    continue
                meshes.append(mesh)
                report["mesh_names"][mesh.name] = obj.data.name
                old_data, states = obj.data, [(m, m.show_viewport) for m in obj.modifiers]
                obj.data = mesh
                for modifier, _shown_state in states:
                    modifier.show_viewport = False
                undo.append((obj, old_data, states))
                report["objects"][obj.name_full] = {"atlas": entry["atlas"], "uv": channel}
        if report["missing"]:
            log.warning("Baked lighting is missing for %d object(s) that changed shape or name since the bake: %s. "
                        "Bake lighting again", len(report["missing"]), ", ".join(report["missing"][:5]))
        if report["stale"]:
            log.warning("Baked lighting may be stale for %d object(s) that moved or changed since the bake: %s",
                        len(report["stale"]), ", ".join(report["stale"][:5]))
        yield report
    finally:
        for obj, old_data, states in reversed(undo):
            try:
                obj.data = old_data
                for modifier, shown in states:
                    modifier.show_viewport = shown
            except (RuntimeError, ReferenceError) as exc:
                log.warning("Could not undo an export change: %s", exc)
        bake.remove_all(meshes)


def manifest_section(report: dict, out_dir, ids_by_name: dict) -> dict | None:
    """The manifest's `lightmaps` (atlases copied to <out_dir>/lightmaps/), or None. ids_by_name: name_full ->
    gs_id of the exported objects."""
    meta = report.get("meta")
    if not meta or not report.get("objects"):
        return None
    folder = pathlib.Path(report["dir"])
    out = pathlib.Path(out_dir) / DIR
    atlases, remap, objects = [], {}, {}
    for name, info in report["objects"].items():
        gs_id = ids_by_name.get(name)
        if gs_id is None:
            continue
        index = info["atlas"]
        if index not in remap:
            atlas = meta["atlases"][index]
            source = folder / atlas["file"]
            try:
                st = source.stat()
            except OSError:
                continue
            dest = out / atlas["file"]
            if not dest.is_file() or dest.stat().st_size != st.st_size or dest.stat().st_mtime_ns != st.st_mtime_ns:
                out.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
            version = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]
            remap[index] = len(atlases)
            atlases.append({"file": f"{DIR}/{atlas['file']}?v={version}", "size": atlas["size"],
                            "scale": atlas["scale"]})
        objects[gs_id] = {"atlas": remap[index], "uv": info["uv"]}
    if not objects:
        return None
    current = {a["file"].split("?", 1)[0].split("/", 1)[1] for a in atlases}
    prefix = f"{meta.get('scene')}_"
    if out.is_dir():
        for path in out.iterdir():
            if path.name.startswith(prefix) and path.suffix == ".png" and path.name not in current:
                try:
                    path.unlink()
                except OSError:
                    pass
    baked = [ids_by_name[n] for n in meta.get("baked_lights", []) if n in ids_by_name]
    return {"version": VERSION, "mode": "indirect", "atlases": atlases, "objects": objects, "bakedLights": baked}


def summary(report: dict) -> dict | None:
    """What the export result says about lightmaps."""
    if not report.get("objects") and not report.get("missing"):
        return None
    meta = report.get("meta") or {}
    used = {v["atlas"] for v in report.get("objects", {}).values()}
    atlases = meta.get("atlases", [])
    return {"objects": len(report.get("objects", {})), "atlases": len(used),
            "texture_bytes": sum(atlases[i]["size"] ** 2 * 4 for i in used if i < len(atlases)),
            "stale": list(report.get("stale", [])), "missing": list(report.get("missing", []))}
