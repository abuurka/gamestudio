# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender half of tools/bench_play.py: builds a heavy level in a throw-away GameStudio project and
times the Play export path on it (export_scene_to, then export_scene() after typical edits).

    blender --background --factory-startup --python tools/bench/play_blender.py -- \\
        --root DIR --out result.json [--rigs 12] [--shared 9] [--clips 7] [--props 200] [--profile]

Run it through tools/bench_play.py, which gives it a scratch profile and deletes everything after.
The project is created under --root the way the add-on tests create theirs (helpers.make_project).
Features other work may not have landed yet (export tiers, clip sampling, manifest file hashes) are
reported as missing ("n/a"), never required. Prints "BENCH ..." progress lines and writes every
measurement to --out as JSON.
"""

import argparse
import contextlib
import cProfile
import functools
import json
import math
import os
import pathlib
import pstats
import random
import sys
import time
import traceback

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "blender"))
sys.path.insert(0, str(REPO / "blender" / "tests" / "addon"))
sys.dont_write_bytecode = True

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras import anim_utils  # noqa: E402
from mathutils import Vector  # noqa: E402

import gamestudio  # noqa: E402
from gamestudio.core import components, export, project, registry, trust  # noqa: E402

import helpers  # noqa: E402  (blender/tests/addon/helpers.py: make_project, fixture registry)


def optional(name):
    """gamestudio.core.<name>, or None while that module does not exist (yet)."""
    try:
        return __import__(f"gamestudio.core.{name}", fromlist=[name])
    except Exception:
        return None


journal = optional("journal")
clips_mod = optional("clips")
bake_mod = optional("bake")
fidelity = optional("fidelity")
lightmap = optional("lightmap")
ids_mod = optional("ids")
kits = None
try:
    from gamestudio import kits
except Exception:
    pass

# The demo's Animator component (the fixture registry has none): the rigs carry one, as in real levels.
ANIMATOR = {"name": "Animator", "description": "Plays an animation clip.", "category": "Animation", "builtin": True,
            "fields": [{"name": "clip", "type": "string", "default": ""},
                       {"name": "loop", "type": "bool", "default": True},
                       {"name": "speed", "type": "float", "default": 1},
                       {"name": "playOnStart", "type": "bool", "default": True},
                       {"name": "fade", "type": "float", "default": 0.25, "min": 0}]}
FRAMES = 60
BONES = 65
UNIQUE_RIGS = ("Hero", "NPC", "Boss", "Guard", "Merchant", "Wolf", "Knight", "Archer", "Mage", "Golem")
# (kit kind, extra params): the props the level scatters.
PROP_KITS = (("crate", {}), ("barrel", {}), ("rock", {}), ("bush", {}), ("tree", {}),
             ("tree", {"style": "pine"}), ("fence", {}), ("platform", {}), ("stairs", {}),
             ("collectible", {"style": "gem"}))


def say(msg) -> None:
    print(f"BENCH {msg}", flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="play_blender.py")
    p.add_argument("--root", required=True, help="folder to create the throw-away project in")
    p.add_argument("--out", required=True, help="result JSON")
    p.add_argument("--rigs", type=int, default=12, help="synthetic Mixamo-like rigs")
    p.add_argument("--shared", type=int, default=None, help="rigs sharing one skeleton and its clips (default 3/4)")
    p.add_argument("--clips", type=int, default=7, help="clips per rig")
    p.add_argument("--props", type=int, default=200, help="kit props")
    p.add_argument("--procedural", type=int, default=4, help="props with procedural (baked) materials")
    p.add_argument("--profile", action="store_true", help="cProfile a warm full and a mesh-edit export")
    return p.parse_args(argv)


# --- Phase timers --------------------------------------------------------------------------------------


class Phases:
    """Inclusive seconds and calls per wrapped function, for the measurement in progress."""

    def __init__(self):
        self.data = {}
        self.active = set()
        self.patched = []  # (owner, attr, original)

    def add(self, label, seconds) -> None:
        item = self.data.setdefault(label, [0.0, 0])
        item[0] += seconds
        item[1] += 1

    def wrap(self, owner, attr, label=None, labeller=None) -> None:
        fn = getattr(owner, attr, None) if owner is not None else None
        if fn is None or not callable(fn):
            return
        label = label or attr
        phases = self

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            name = labeller(args) if labeller else label
            if name in phases.active:  # recursion: time the outermost call only
                return fn(*args, **kwargs)
            phases.active.add(name)
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                phases.active.discard(name)
                phases.add(name, time.perf_counter() - start)

        self.patched.append((owner, attr, fn))
        setattr(owner, attr, wrapper)

    def wrap_cm(self, owner, attr, label=None) -> None:
        """A context manager factory: its __enter__ and __exit__ are timed apart (setup, restore)."""
        fn = getattr(owner, attr, None) if owner is not None else None
        if fn is None or not callable(fn):
            return
        label = label or attr
        phases = self

        class Timed:
            def __init__(self, cm):
                self.cm = cm

            def __enter__(self):
                start = time.perf_counter()
                try:
                    return self.cm.__enter__()
                finally:
                    phases.add(f"{label} enter", time.perf_counter() - start)

            def __exit__(self, *exc):
                start = time.perf_counter()
                try:
                    return self.cm.__exit__(*exc)
                finally:
                    phases.add(f"{label} exit", time.perf_counter() - start)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return Timed(fn(*args, **kwargs))

        self.patched.append((owner, attr, fn))
        setattr(owner, attr, wrapper)

    def unpatch(self) -> None:
        for owner, attr, fn in reversed(self.patched):
            setattr(owner, attr, fn)
        self.patched.clear()

    def patch(self) -> None:
        if self.patched:
            return

        def gltf_label(args):
            scene = args[1] if len(args) > 1 else None
            prefab = getattr(export, "PREFAB_SCENE", "gs_prefab_export")
            return "gltf exporter: prefabs" if getattr(scene, "name", "") == prefab else "gltf exporter: scene"

        w, cm = self.wrap, self.wrap_cm
        w(export, "_gltf", labeller=gltf_label)
        w(export, "_export_prefab_glb", "prefab GLB run (total)")
        for attr in ("read_glb", "inject_ids", "_build_templates", "_slim_glb", "strip_instance_contents",
                     "_drop_meshes", "_drop_excluded", "gather_animations", "build_manifest", "copy_assets",
                     "_finish_glb", "build_glb", "_write_atomic", "refreshed_manifest", "instance_counts",
                     "display_only", "_depsgraph", "missing_libraries", "check_nla", "file_hash",
                     "_restore_node_names", "_size_summary", "_measure_compressed", "prefab_names",
                     "_export_from_journal"):
            w(export, attr, f"export.{attr}")
        cm(export, "_preserved_state", "export._preserved_state")
        cm(export, "_distinct_node_names", "export._distinct_node_names")
        if ids_mod is not None:
            w(ids_mod, "repair_duplicate_ids", "ids.repair_duplicate_ids")
        if bake_mod is not None:
            cm(bake_mod, "export_swap", "bake.export_swap")
            for attr in ("collect_objects", "plan", "run", "_bake_unit", "_prepare", "_swap_in"):
                w(bake_mod, attr, f"bake.{attr}")
        if lightmap is not None:
            cm(lightmap, "export_swap", "lightmap.export_swap")
        if fidelity is not None:
            for attr in ("instance_meshes", "process_textures", "prune", "collision_meshes", "size_report", "rename"):
                w(fidelity, attr, f"fidelity.{attr}")
            glb_class = getattr(fidelity, "Glb", None)
            if glb_class is not None:
                w(glb_class, "finish", "fidelity.Glb.finish")
                w(glb_class, "__init__", "fidelity.Glb()")
        if clips_mod is not None:
            for attr in ("plan", "splice", "sample", "prune"):
                w(clips_mod, attr, f"clips.{attr}")
            cm(clips_mod, "armed", "clips.armed")
        if journal is not None:
            for attr in ("plan", "_capture", "_snapshot", "_compare", "_files_changed"):
                w(journal, attr, f"journal.{attr}")
        w(components, "export_components", "components.export_components")

    def report(self) -> dict:
        return {k: {"s": round(v[0], 4), "calls": v[1]} for k, v in sorted(self.data.items(), key=lambda kv: -kv[1][0])}


PH = Phases()


# --- Profiles -----------------------------------------------------------------------------------------------


MARKERS = ("/gamestudio/", "/io_scene_gltf2/", "/bpy_extras/", "/bpy/", "/bl_operators/", "/tools/bench/",
           "/numpy/", "/json/", "/Lib/")


def short_name(file: str, line: int, func: str) -> str:
    if file == "~":
        return func
    path = file.replace("\\", "/")
    for marker in MARKERS:
        i = path.rfind(marker)
        if i >= 0:
            path = path[i + 1:]
            if marker == "/Lib/":
                path = path[4:]
            break
    else:
        path = path.rsplit("/", 1)[-1]
    return f"{path}:{line}({func})"


def top_stats(prof, n=25) -> dict:
    stats = pstats.Stats(prof).stats
    rows = [(ct, tt, nc, short_name(*key)) for key, (cc, nc, tt, ct, _callers) in stats.items()]
    total = max((r[0] for r in rows), default=0.0)

    def table(key):
        return [{"cum": round(ct, 3), "tot": round(tt, 3), "calls": nc, "func": name}
                for ct, tt, nc, name in sorted(rows, key=key)[:n]]

    return {"total": round(total, 3), "cumulative": table(lambda r: -r[0]), "tottime": table(lambda r: -r[1])}


# --- The level ------------------------------------------------------------------------------------------------


def make_project(root: pathlib.Path) -> pathlib.Path:
    os.environ["GS_TEST_TMP"] = str(root)  # helpers.make_project creates it under GS_TEST_TMP
    data = helpers.fixture_registry()
    if not any(c["name"] == "Animator" for c in data["components"]):
        data["components"].append(ANIMATOR)
    proj = helpers.make_project(data)
    # Ephemeral live-link ports: a browser run starts its own hub and must never meet the user's.
    config = {"name": "bench-game", "formatVersion": 1, "startScene": "main", "link": {"editorPort": 0, "gamePort": 0}}
    (proj / "gamestudio.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return proj


def principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def procedural_materials() -> list:
    """Three node setups the exporter cannot translate: they go through the bake path."""
    mats = []
    lava = bpy.data.materials.new("Proc_Lava")
    tree = lava.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.05, 0.0, 0.0, 1.0)
    ramp.color_ramp.elements[1].color = (1.0, 0.6, 0.0, 1.0)
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], principled(lava).inputs["Base Color"])
    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Distance"].default_value = 0.2
    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], principled(lava).inputs["Normal"])
    mats.append(lava)

    moss = bpy.data.materials.new("Proc_Moss")
    tree = moss.node_tree
    voronoi = tree.nodes.new("ShaderNodeTexVoronoi")
    voronoi.inputs["Scale"].default_value = 9.0
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (0.1, 0.3, 0.05, 1)
    mix.inputs["B"].default_value = (0.4, 0.35, 0.3, 1)
    tree.links.new(voronoi.outputs["Distance"], mix.inputs["Factor"])
    tree.links.new(mix.outputs["Result"], principled(moss).inputs["Base Color"])
    math_node = tree.nodes.new("ShaderNodeMath")
    math_node.operation = "MULTIPLY_ADD"
    math_node.inputs[1].default_value = 0.5
    math_node.inputs[2].default_value = 0.4
    tree.links.new(voronoi.outputs["Distance"], math_node.inputs[0])
    tree.links.new(math_node.outputs[0], principled(moss).inputs["Roughness"])
    mats.append(moss)

    rust = bpy.data.materials.new("Proc_Rust")
    tree = rust.node_tree
    wave = tree.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = 4.0
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.25, 0.08, 0.02, 1.0)
    ramp.color_ramp.elements[1].color = (0.6, 0.6, 0.62, 1.0)
    tree.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], principled(rust).inputs["Base Color"])
    tree.links.new(wave.outputs["Fac"], principled(rust).inputs["Metallic"])
    mats.append(rust)
    return mats


def texture_image(proj: pathlib.Path, name: str, size: int, kind: str, seed: int):
    """A size² image with a smooth-ish pattern (compresses like a real texture), saved into the project."""
    gen = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / size
    base = 0.5 + 0.25 * np.sin(x * 37 + seed) * np.cos(y * 23) + 0.15 * np.sin((x + y) * 91)
    grain = gen.normal(0, 0.03, (size, size)).astype(np.float32)
    v = np.clip(base + grain, 0, 1)
    rgba = np.empty((size, size, 4), dtype=np.float32)
    if kind == "normal":
        rgba[..., 0] = 0.5 + 0.2 * (v - 0.5)
        rgba[..., 1] = 0.5 + 0.2 * np.roll(v - 0.5, 7, axis=0)
        rgba[..., 2] = 1.0
    else:
        tint = gen.uniform(0.3, 1.0, 3)
        for c in range(3):
            rgba[..., c] = v * tint[c]
    rgba[..., 3] = 1.0
    img = bpy.data.images.new(name, size, size, alpha=False, is_data=(kind == "normal"))
    img.pixels.foreach_set(rgba.ravel())
    folder = proj / "textures"
    folder.mkdir(exist_ok=True)
    img.filepath_raw = str(folder / f"{name}.png")
    img.file_format = "PNG"
    img.save()
    if kind == "normal":
        img.colorspace_settings.name = "Non-Color"
    return img


def image_material(name, color_img, normal_img=None):
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    bsdf = principled(mat)
    uv = tree.nodes.new("ShaderNodeUVMap")
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = color_img
    tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])
    tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if normal_img is not None:
        ntex = tree.nodes.new("ShaderNodeTexImage")
        ntex.image = normal_img
        nmap = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(uv.outputs["UV"], ntex.inputs["Vector"])
        tree.links.new(ntex.outputs["Color"], nmap.inputs["Color"])
        tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def add_component(obj, name, values=None) -> bool:
    try:
        components.add_component(obj, name, values or {})
        return True
    except Exception as exc:  # a component the registry lacks: the level just goes without it
        say(f"note: component {name} on {obj.name} skipped ({exc})")
        return False


# The prototype's Mixamo-like rigs (clipproto/proto.py), with the kit convention: clip tracks muted.


def make_skeleton(scene, name, rng):
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bones = []
    for i in range(BONES):
        b = arm.edit_bones.new(f"mixamorig:B{i:02d}")
        parent = bones[rng.randrange(max(0, len(bones) - 6), len(bones))] if bones else None
        head = Vector(parent.tail) if parent else Vector((0, 0, 1))
        b.head = head
        b.tail = head + Vector((rng.uniform(-.1, .1), rng.uniform(-.1, .1), rng.uniform(.05, .15)))
        b.roll = rng.uniform(-1, 1)
        if parent:
            b.parent = parent
            b.use_connect = rng.random() < 0.4
        bones.append(b)
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in obj.pose.bones:  # mixed rotation modes, as imported and hand-made rigs have
        r = rng.random()
        pb.rotation_mode = "QUATERNION" if r < .8 else ("XYZ" if r < .9 else "ZXY")
    return obj


def make_body(arm_obj):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=0.5, location=(0, 0, 1.4))
    body = bpy.context.active_object
    body.name = arm_obj.name + "_Body"
    heads = np.array([b.head_local for b in arm_obj.data.bones], dtype=np.float32)
    names = [b.name for b in arm_obj.data.bones]
    groups = [body.vertex_groups.new(name=n) for n in names]
    co = np.empty(len(body.data.vertices) * 3, dtype=np.float32)
    body.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    nearest = np.argmin(((co[:, None, :] - heads[None, :, :]) ** 2).sum(axis=2), axis=1)
    for gi, group in enumerate(groups):
        members = np.nonzero(nearest == gi)[0].tolist()
        if members:
            group.add(members, 1.0, "REPLACE")
    body.parent = arm_obj
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj
    return body


def make_actions(arm_obj, prefix, count, rng):
    actions = []
    frames = np.arange(1, FRAMES + 1, dtype=np.float32)
    for k in range(count):
        act = bpy.data.actions.new(f"{prefix}_Clip{k}")
        slot = act.slots.new(id_type="OBJECT", name=arm_obj.name)
        bag = anim_utils.action_ensure_channelbag_for_slot(act, slot)
        for pb in arm_obj.pose.bones:
            base = f'pose.bones["{pb.name}"]'
            phase, amp = rng.uniform(0, 6), rng.uniform(.1, .8)
            if pb.rotation_mode == "QUATERNION":
                ax = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))).normalized()
                ang = amp * np.sin(frames / FRAMES * 2 * math.pi + phase)
                vals = [np.cos(ang / 2), ax.x * np.sin(ang / 2), ax.y * np.sin(ang / 2), ax.z * np.sin(ang / 2)]
                channels = [("rotation_quaternion", i, vals[i]) for i in range(4)]
            else:
                channels = [("rotation_euler", i, amp * np.sin(frames / FRAMES * 2 * math.pi + phase + i))
                            for i in range(3)]
            if pb.parent is None or rng.random() < .15:  # root motion and some loose bones
                channels += [("location", i, .05 * np.cos(frames / 10 + i + phase)) for i in range(3)]
            if rng.random() < .05:
                channels += [("scale", i, 1 + .1 * np.sin(frames / 7 + i)) for i in range(3)]
            for prop, index, values in channels:
                fc = bag.fcurves.new(f"{base}.{prop}", index=index, group_name=pb.name)
                fc.keyframe_points.add(FRAMES)
                co = np.empty(FRAMES * 2, dtype=np.float32)
                co[0::2], co[1::2] = frames, values
                fc.keyframe_points.foreach_set("co", co)
                fc.update()
        actions.append((act, slot))
    return actions


def assign(arm_obj, actions):
    ad = arm_obj.animation_data_create()
    for act, slot in actions:
        track = ad.nla_tracks.new()
        track.name = act.name
        strip = track.strips.new(act.name, 1, act)
        strip.action_slot = slot
        track.mute = True  # the kit convention: the exporter still writes it, the viewport does not blend it
    ad.action = actions[0][0]
    ad.action_slot = actions[0][1]


def build_level(args, proj: pathlib.Path) -> dict:
    rng = random.Random(7)
    started = time.perf_counter()
    helpers.reset_scene()
    scene = bpy.context.scene
    scene.name = "main"
    scene.render.fps = 30
    notes = {"kits": [], "procedural_objects": [], "image_materials": [], "prefab_instances": 0}

    # Sky (sun + world) and a camera.
    if kits is not None:
        with contextlib.suppress(Exception):
            kits.create("sky")
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.location = (0, -30, 20)
    cam.rotation_euler = (math.radians(60), 0, 0)
    scene.camera = cam

    # Image-textured materials: 2048² PNGs saved into the project.
    ground_img = texture_image(proj, "ground_albedo", 2048, "color", 1)
    wall_img = texture_image(proj, "wall_albedo", 2048, "color", 2)
    wall_nrm = texture_image(proj, "wall_normal", 2048, "normal", 3)
    ground_mat = image_material("Img_Ground", ground_img)
    wall_mat = image_material("Img_Wall", wall_img, wall_nrm)
    notes["image_materials"] = [ground_mat.name, wall_mat.name]
    bpy.ops.mesh.primitive_plane_add(size=100, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    ground.data.materials.append(ground_mat)
    add_component(ground, "RigidBody", {"type": "fixed"})
    add_component(ground, "Collider", {"shape": "box"})

    # Kit props: a quarter built (distinct seeds), the rest linked duplicates (shared meshes).
    procs = procedural_materials()
    props_coll = bpy.data.collections.new("Props")
    scene.collection.children.link(props_coll)
    unique = []
    n_unique = max(1, args.props // 4) if kits is not None else 0
    broken = set()
    i = 0
    while len(unique) < n_unique and kits is not None and i < n_unique * 3:
        kind, extra = PROP_KITS[i % len(PROP_KITS)]
        i += 1
        if (kind, tuple(extra.items())) in broken:
            continue
        loc = (rng.uniform(-40, 40), rng.uniform(-40, 40), 0)
        try:
            made = kits.create(kind, {**extra, "seed": i, "name": f"{kind.title()}{i:03d}"}, location=loc,
                               collection=props_coll)
        except Exception as exc:
            say(f"note: kit {kind} {extra} skipped ({exc})")
            broken.add((kind, tuple(extra.items())))
            continue
        root = made[0]
        root.rotation_euler.z = rng.uniform(0, math.tau)
        unique.append(root)
        if kind not in notes["kits"]:
            notes["kits"].append(kind)
    # Some with procedural materials (the bake path), some with the image materials.
    for k, obj in enumerate([o for o in unique if o.type == "MESH" and o.data.materials][:args.procedural]):
        obj.data.materials[0] = procs[k % len(procs)]
        notes["procedural_objects"].append(obj.name)
    platforms = [o for o in unique if "Platform" in o.name or "Stairs" in o.name]
    for obj in platforms[:4]:
        if obj.type == "MESH" and obj.data.materials:
            obj.data.materials[0] = wall_mat
    # Components: colliders everywhere, bodies on crates and barrels, spins on gems.
    for obj in unique:
        add_component(obj, "Collider", {"shape": "box"})
        if obj.name.startswith(("Crate", "Barrel")):
            add_component(obj, "RigidBody", {"type": "dynamic", "mass": 2})
        if obj.name.startswith("Collectible"):
            add_component(obj, "Spin", {"speed": 2})
    # Duplicates: every unique prop but each third one (those stay single-user: the mesh edit uses one).
    sources = [o for k, o in enumerate(unique) if k % 3 != 0] or unique
    copies = 0
    while len(unique) + copies < args.props and sources:
        src = sources[copies % len(sources)]
        dup = src.copy()  # shares the mesh: what Alt+D and copy-paste of kit props give
        dup.location = (rng.uniform(-45, 45), rng.uniform(-45, 45), 0)
        dup.rotation_euler.z = rng.uniform(0, math.tau)
        props_coll.objects.link(dup)
        for child in src.children:  # assemblies: their parts come along
            part = child.copy()
            part.parent = dup
            props_coll.objects.link(part)
        copies += 1
    notes["props_unique"] = len(unique)
    notes["props_copies"] = copies

    # Prefabs: a spinning coin and a lamp post, instanced around the level.
    prefabs = []
    if kits is not None:
        coin = bpy.data.collections.new("CoinPrefab")
        coin_root = bpy.data.objects.new("CoinRoot", None)
        coin.objects.link(coin_root)
        with contextlib.suppress(Exception):
            body = kits.create("collectible", {"name": "CoinBody", "style": "coin"}, collection=coin)[0]
            body.parent = coin_root
            body.location = (0, 0, 0.6)
            add_component(body, "Spin", {"speed": 3})
            with contextlib.suppress(Exception):
                kits.animation.add_clip(body, "bob")
        lamp = bpy.data.collections.new("LampPrefab")
        with contextlib.suppress(Exception):
            post = kits.create("barrel", {"name": "LampPost", "seed": 3}, collection=lamp)[0]
            light = kits.create("collectible", {"name": "LampGlow", "style": "gem"}, collection=lamp)[0]
            light.parent = post
            light.location = (0, 0, 1.4)
            add_component(post, "Collider", {"shape": "box"})
        for coll in (coin, lamp):
            if len(coll.all_objects):
                export.set_prefab(coll)
                prefabs.append(coll)
        for k in range(30):
            coll = prefabs[k % len(prefabs)] if prefabs else None
            if coll is None:
                break
            inst = bpy.data.objects.new(f"{coll.name}_{k:02d}", None)
            inst.instance_type = "COLLECTION"
            inst.instance_collection = coll
            inst.location = (rng.uniform(-40, 40), rng.uniform(-40, 40), 0)
            scene.collection.objects.link(inst)
            notes["prefab_instances"] += 1
    notes["prefabs"] = [c.name for c in prefabs]

    # The kit's rigged character (the player) and the synthetic rigs.
    player = None
    if kits is not None:
        try:
            player = kits.create("character_rigged", {"name": "Player"}, location=(0, -5, 0))[0]
            add_component(player, "CharacterController")
            add_component(player, "Animator", {"clip": "Idle"})
            add_component(player, "RigidBody", {"type": "kinematic"})
            add_component(player, "Collider", {"shape": "capsule"})
        except Exception as exc:
            say(f"note: character_rigged skipped ({exc})")
    if player is not None:
        add_component(cam, "FollowCamera", {"target": player})

    rigs = []
    shared = args.shared if args.shared is not None else (args.rigs * 3) // 4
    shared = max(0, min(shared, args.rigs))
    lead_actions = None
    if shared:
        lead = make_skeleton(scene, "Soldier", rng)
        lead_body = make_body(lead)
        lead_actions = make_actions(lead, "Soldier", args.clips, rng)
        assign(lead, lead_actions)
        rigs.append(lead)
        for k in range(shared - 1):  # sharing the armature, mesh and clips
            arm = lead.copy()
            arm.name = f"Soldier.{k + 1:03d}"
            arm.location.x += 2 * (k + 1)
            scene.collection.objects.link(arm)
            body = lead_body.copy()
            body.name = f"Soldier_Body.{k + 1:03d}"
            body.parent = arm
            body.modifiers["Armature"].object = arm
            scene.collection.objects.link(body)
            rigs.append(arm)
    for k in range(args.rigs - shared):
        name = UNIQUE_RIGS[k % len(UNIQUE_RIGS)] + ("" if k < len(UNIQUE_RIGS) else str(k))
        arm = make_skeleton(scene, name, rng)
        arm.location = (-10 - 3 * k, 10, 0)
        make_body(arm)
        assign(arm, make_actions(arm, name, args.clips, rng))
        rigs.append(arm)
    for k, arm in enumerate(rigs):
        if k % 2 == 0:
            add_component(arm, "Animator", {"clip": arm.animation_data.action.name})
    bpy.context.view_layer.update()
    notes["rigs"] = [r.name for r in rigs]
    notes["shared_rigs"] = shared
    notes["build_seconds"] = round(time.perf_counter() - started, 2)
    return notes


def level_stats(scene) -> dict:
    objects = list(scene.objects)
    meshes = {o.data for o in objects if o.type == "MESH"}
    return {
        "objects": len(objects),
        "meshes_unique": len(meshes),
        "mesh_objects": sum(1 for o in objects if o.type == "MESH"),
        "vertices_unique": sum(len(m.vertices) for m in meshes),
        "armatures": sum(1 for o in objects if o.type == "ARMATURE"),
        "bones": sum(len(o.data.bones) for o in objects if o.type == "ARMATURE"),
        "actions": len(bpy.data.actions),
        "materials": len(bpy.data.materials),
        "images": len([i for i in bpy.data.images if i.source == "FILE"]),
        "collection_instances": sum(1 for o in objects if o.instance_type == "COLLECTION" and o.instance_collection),
    }


# --- Measuring ----------------------------------------------------------------------------------------------


def summarize(result) -> dict:
    if not isinstance(result, dict):
        return {}
    out = {"tier": result.get("tier"), "reason": result.get("reason"), "rev": result.get("rev"),
           "entities": result.get("entities"), "animations": result.get("animations"),
           "warnings": len(result.get("warnings") or [])}
    bake = result.get("bake")
    if isinstance(bake, dict):
        out["bake"] = {k: bake.get(k) for k in ("mode", "baked", "cached", "units", "fixed", "seconds")}
    clips = result.get("clips")
    if isinstance(clips, dict):
        out["clips"] = {k: (len(v) if isinstance(v, list) else v) for k, v in clips.items()}
    size = result.get("size")
    if isinstance(size, dict):
        out["size"] = {k: size.get(k) for k in ("bytes", "glb", "prefabs", "files")}
        out["size"]["per_file"] = size.get("per_file")
    textures = result.get("textures")
    if isinstance(textures, list):
        out["textures"] = len(textures)
    if isinstance(result.get("instancing"), list):
        out["instancing"] = len(result["instancing"])
    pruned = result.get("pruned")
    if isinstance(pruned, dict):
        out["pruned"] = pruned
    with contextlib.suppress(Exception):
        out["export_seconds"] = export.last_export().get("seconds")
    out["warning_samples"] = list(result.get("warnings") or [])[:4]
    return out


def measure(key, label, fn, profile=False) -> dict:
    say(f"{key}: {label} ...")
    PH.data.clear()
    if profile:
        PH.unpatch()  # the timers would show in the profile
    else:
        PH.patch()
    prof = cProfile.Profile() if profile else None
    result, error = None, None
    start = time.perf_counter()
    try:
        if prof is not None:
            prof.enable()
        try:
            result = fn()
        finally:
            if prof is not None:
                prof.disable()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    wall = time.perf_counter() - start
    row = {"key": key, "label": label, "wall": round(wall, 3), "error": error, **summarize(result)}
    if prof is not None:
        row["profile"] = top_stats(prof)
    else:
        row["phases"] = PH.report()
    say(f"{key}: {wall:.2f} s  tier={row.get('tier')}  {('ERROR ' + error) if error else ''}")
    return row


def first(objs, pred):
    return next((o for o in objs if pred(o)), None)


def main() -> int:
    args = parse_args()
    out_path = pathlib.Path(args.out)
    if not os.environ.get("GS_BENCH_VERBOSE"):
        import logging
        logging.getLogger("gamestudio").setLevel(logging.WARNING)
    gamestudio.register()
    report = {"blender": bpy.app.version_string, "args": vars(args), "features": {}, "rows": [], "notes": {}}

    def save():
        out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    root = pathlib.Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    proj = make_project(root)
    trust.trust(proj, "bench", save=False)  # its dev server runs (core/trust.py), for this run only
    report["project"] = str(proj)
    project.set_project_root_override(proj)
    registry.reload_registry(proj)
    say(f"project {proj}")
    notes = build_level(args, proj)
    blend = proj / "scenes" / "main.blend"
    blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    # Open it again, as a user would before pressing Play; the project is found from the file's folder.
    project.set_project_root_override(None)
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    if project.project_root() is None or pathlib.Path(project.project_root()).resolve() != proj.resolve():
        project.set_project_root_override(proj)
        notes["root_override"] = True
    registry.reload_registry(proj)
    scene = bpy.context.scene
    report["notes"] = notes
    report["level"] = level_stats(scene)
    report["blend_bytes"] = blend.stat().st_size
    say(f"level built in {notes['build_seconds']} s: {json.dumps(report['level'])}")
    save()

    out = project.export_dir(proj)
    report["features"] = {
        "tiers (journal.py)": journal is not None,
        "clip sampler (clips.py)": clips_mod is not None and hasattr(clips_mod, "plan"),
        "bake (bake.py)": bake_mod is not None,
    }
    if journal is not None:
        journal.register()  # export_scene() registers it too; the first full export must be snapshotted
    rows = report["rows"]

    def full():
        return export.export_scene_to(out, scene)

    def incremental():
        return export.export_scene(scene)

    rows.append(measure("a", "cold full export (empty caches)", full))
    save()
    rows.append(measure("b", "warm full export", full))
    save()
    if args.profile:
        report["profile_b"] = measure("b'", "warm full export (cProfile)", full, profile=True)
        save()
    rows.append(measure("c", "export_scene(), nothing changed", incremental))
    save()

    # The edits. Each is evaluated before the clock starts, as the UI evaluates it before Play.
    layer = bpy.context.view_layer
    objs = list(scene.objects)
    props = [o for o in objs if o.users_collection and o.users_collection[0].name == "Props"]
    mover = first(props, lambda o: o.parent is None and o.type == "MESH") or first(objs, lambda o: o.type == "MESH")
    spinner = first(objs, lambda o: "Spin" in components.list_components(o))
    collider = first(objs, lambda o: "Collider" in components.list_components(o))
    procedural = set(notes.get("procedural_objects", []))
    editable = [o for o in props if o.type == "MESH" and o.data.users == 1 and o.name not in procedural
                and len(o.data.vertices) > 8]
    palettes = [m for m in bpy.data.materials if m.get("gs_material_key") and m.node_tree is not None]
    palette = max(palettes, key=lambda m: m.users, default=None)
    rig_actions = [a for a in bpy.data.actions if a.name.startswith(("Soldier_Clip", "Hero_Clip"))]
    muted_action = first(rig_actions, lambda a: a.name.endswith("Clip1"))
    active_action = first(rig_actions, lambda a: a.name.endswith("Clip0"))

    def edit_move():
        mover.location.x += 1.5
        return f"moved '{mover.name}'"

    def edit_component():
        if spinner is not None:
            components.set_field(spinner, "Spin", "speed", 7.5)
            return f"Spin.speed of '{spinner.name}'"
        components.set_field(collider, "Collider", "friction", 0.9)
        return f"Collider.friction of '{collider.name}'"

    def edit_mesh(k):
        obj = editable[k % len(editable)]
        mesh = obj.data
        v = mesh.vertices[k % len(mesh.vertices)]
        v.co.z += 0.05
        mesh.update()
        return f"moved a vertex of '{mesh.name}' ({len(mesh.vertices)} verts)"

    def edit_colour():
        bsdf = principled(palette)
        bsdf.inputs["Base Color"].default_value = (0.8, 0.2, 0.2, 1.0)
        return f"Base Color of '{palette.name}' ({palette.users} users)"

    def edit_keyframe(action, where):
        bag = None
        for slot in action.slots:
            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            if bag is not None:
                break
        fc = bag.fcurves[3]
        kp = fc.keyframe_points[10]
        kp.co.y += 0.05
        kp.handle_left.y += 0.05
        kp.handle_right.y += 0.05
        fc.update()
        users = sum(1 for o in objs if o.type == "ARMATURE" and o.animation_data
                    and any(s.action == action for t in o.animation_data.nla_tracks for s in t.strips))
        return f"a key of '{action.name}' ({where}; {fc.data_path}[{fc.array_index}]), played by {users} rigs"

    edits = [
        ("d", "after moving one object", edit_move if mover is not None else None),
        ("e", "after a component edit", edit_component if (spinner or collider) is not None else None),
        ("f", "after editing one mesh", (lambda: edit_mesh(0)) if editable else None),
        ("g", "after changing one material colour", edit_colour if palette is not None else None),
        ("h", "after editing one action's keyframe",
         (lambda: edit_keyframe(muted_action, "a clip on a muted NLA track, the kit convention"))
         if muted_action is not None else None),
        ("h2", "... keyframe of the active action",
         (lambda: edit_keyframe(active_action, "the rig's active action")) if active_action is not None else None),
    ]
    for key, label, edit in edits:
        if edit is None:
            rows.append({"key": key, "label": label, "error": "n/a: nothing in the level to edit"})
            continue
        try:
            what = edit()
            layer.update()
        except Exception as exc:
            traceback.print_exc()
            rows.append({"key": key, "label": label, "error": f"edit failed: {exc}"})
            continue
        row = measure(key, label, incremental)
        row["edit"] = what
        rows.append(row)
        save()
        if key == "f" and args.profile:
            what = edit_mesh(1)
            layer.update()
            report["profile_f"] = measure("f'", "mesh-edit export (cProfile)", incremental, profile=True)
            report["profile_f"]["edit"] = what
            save()

    # Extras: what the journal's snapshot costs per full export, and what the manifest carries.
    PH.unpatch()
    extras = {}
    try:
        manifest = json.loads((out / "main.scene.json").read_text(encoding="utf-8"))
        extras["manifest_files_hashes"] = bool(manifest.get("files"))
        extras["manifest_bytes"] = (out / "main.scene.json").stat().st_size
        extras["manifest_entities"] = len(manifest.get("entities", []))
        extras["manifest_animations"] = len(manifest.get("animations", []))
    except Exception as exc:
        extras["manifest_error"] = str(exc)
    if journal is not None and hasattr(journal, "_capture"):
        try:
            settings = export.export_settings(proj)
            start = time.perf_counter()
            journal._capture(scene, export._export_view_layer(scene), settings, out, export.export_name(scene))
            extras["journal_capture_s"] = round(time.perf_counter() - start, 3)
        except Exception as exc:
            extras["journal_capture_s"] = f"n/a ({exc})"
    for name, folder in (("bake_cache", out.parent / "bake"), ("clip_cache", out.parent / "cache" / "clips")):
        if folder.is_dir():
            files = [p for p in folder.rglob("*") if p.is_file()]
            extras[name] = {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}
    report["extras"] = extras
    save()
    with contextlib.suppress(Exception):
        gamestudio.unregister()
    say("done")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.exit(code)
