# SPDX-License-Identifier: GPL-3.0-or-later
"""Armature clips sampled from their F-curves (docs/SPEC.md §4.1, §4.6).

Blender's glTF exporter samples an action by setting every frame of the union range of all the
scene's actions and evaluating the whole scene, skinning included, once per action. A level with a
dozen rigs spent most of its export there. For armatures whose pose comes from the action alone,
GameStudio samples the clips itself, straight from the F-curves, and splices them into the GLB:

- plan() runs before the exporter. Per GLB (the scene's, the prefabs') it picks the armatures it can
  sample: no enabled bone constraint, no drivers, default bone inheritance, clips that key bone
  transforms only, and no copy kept inside a collection instance. It lists their clips as the
  exporter's ACTIONS mode does (the active action, every NLA track with one unmuted strip) and
  samples each clip once: key values where a key sits on a sampled frame, evaluate() elsewhere.
  It also notes the objects whose nodes the export drops later (contents of prefab instances,
  objects of excluded collections), which the exporter then need not sample at all.
- The package's glTF2ExportUserExtension calls gather_actions_hook() for every object the exporter
  animates. While a plan is armed (armed()), the hook takes the planned clips out of the object's
  actions, so the exporter does not sample them.
- splice() writes the clips the hook took out into the GLB as the exporter would have: an animation
  per action (merged with the exporter's animation of that name), a translation, rotation and scale
  channel per bone, constant channels as two keys, in the exporter's order. Rigs that play the same
  clip share its accessors. A clip the hook did not take out stays with the exporter: without the
  hook an export is slower, never different.

Unkeyed bone channels are the rest pose, as the exporter writes them once it has reset the pose
(export_reset_pose_bones). The exporter skips that reset for the clip that is already active, whose
unkeyed channels then keep whatever the pose held (the NLA's blend, a manual pose); sampled clips
use the rest pose for every clip.

Sampled clips are cached in <project>/.gamestudio/cache/clips/<key>.clip, keyed by a hash of the
slot's F-curves (action_digest()), the frame range and rate, and the skeleton; files unused for 30
days are deleted when new ones are written. The session keeps recent clips in memory too, with the
bytes splice() writes. The keyframes of a curve are read in one go from Blender's keyframe array
(checked once against foreach_get, which is the fallback), so hashing a level's actions on every
export stays cheap.
"""

import collections
import contextlib
import ctypes
import hashlib
import json
import os
import pathlib
import re
import struct
import sys
import time

import bpy
import numpy as np
from bpy_extras import anim_utils
from mathutils import Matrix

from .log import get_logger

log = get_logger(__name__)

CLIPS_VERSION = 1
CACHE_DIR = ("cache", "clips")
SUFFIX = ".clip"
MAGIC = b"GSCLIP01"
MAX_AGE_DAYS = 30
MEMORY_BYTES = 128 * 1024 * 1024  # sampled clips kept in memory for the next export
CONSTANT = 1e-4  # the exporter keeps two keys of a channel whose values vary less than this
FLOAT = 5126
PATHS = ("translation", "rotation", "scale")
WIDTH = {"translation": 3, "rotation": 4, "scale": 3}
TYPES = {1: "SCALAR", 3: "VEC3", 4: "VEC4"}
EULER_MODES = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
ROTATION = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}  # else rotation_euler
BONE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\]\.'
                       r'(location|rotation_quaternion|rotation_euler|rotation_axis_angle|scale)$')
# Blender's Z-up armature space to glTF's Y-up, for root bones (the exporter's axis_basis_change).
Y_UP = np.array(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)), dtype=np.float64)
# What a keyframe holds that evaluation reads, packed (the hashed and cached form), and where those
# fields sit in Blender's keyframe struct (DNA BezTriple; the selection flags in between are skipped).
KEYFRAME = np.dtype([("handle_left", "<f4", 2), ("co", "<f4", 2), ("handle_right", "<f4", 2),
                     ("interpolation", "u1"), ("easing", "u1"), ("back", "<f4"), ("amplitude", "<f4"),
                     ("period", "<f4")])
BEZTRIPLE = np.dtype({"names": list(KEYFRAME.names), "formats": [KEYFRAME.fields[n][0] for n in KEYFRAME.names],
                      "offsets": [0, 12, 24, 48, 55, 56, 60, 64], "itemsize": 72})
# Exporter settings the sampler reproduces (export.GLTF_SETTINGS and the exporter's defaults). With
# any other value the hook leaves every clip to the exporter.
EXPORTER = {
    "gltf_animation_mode": "ACTIONS",
    "gltf_force_sampling": True,
    "gltf_frame_step": 1,
    "gltf_frame_range": False,
    "gltf_optimize_animation": True,
    "gltf_optimize_animation_keep_armature": True,
    "gltf_negative_frames": "SLIDE",
    "gltf_anim_slide_to_zero": True,
    "gltf_merge_animation": "ACTION",
    "gltf_export_reset_pose_bones": True,
    "gltf_sampling_interpolation_fallback": "LINEAR",
    "gltf_bake_animation": False,
    "gltf_export_anim_single_armature": False,
    "gltf_export_extra_animations": False,
    "gltf_def_bones": False,
    "gltf_leaf_bone": False,
    "gltf_flatten_bones_hierarchy": False,
    "gltf_armature_object_remove": False,
    "gltf_meshopt_compression": False,
    "gltf_yup": True,
}

_armed = None                       # the _Run whose clips the hook takes out, while the exporter runs
_logged = set()                     # (object, reason) already logged this session
_memory = collections.OrderedDict()  # clip key -> ClipData, least recently used first
_memory_bytes = 0                   # what _memory holds (ClipData.nbytes)
_paths = {}                         # F-curve data path -> (bone, property) or None
_raw = None                         # keyframes read from Blender's array (None: not checked yet)


def _hash(data: bytes = b""):
    """The hash of digests and cache keys (BLAKE2b: about three times SHA-1's speed in Blender)."""
    return hashlib.blake2b(data, digest_size=20)


def cache_dir(out_dir) -> pathlib.Path:
    """<project>/.gamestudio/cache/clips for <project>/.gamestudio/export (beside out_dir in general)."""
    return pathlib.Path(out_dir).parent.joinpath(*CACHE_DIR)


def forget() -> None:
    """Drop the clips kept in memory (the disk cache stays)."""
    global _memory_bytes
    _memory.clear()
    _memory_bytes = 0


# --- What the exporter writes -------------------------------------------------------------


def _filtered_actions() -> set:
    """Pointers of the actions the exporter's action filter leaves out (scene.gltf_action_filter)."""
    scenes = bpy.data.scenes
    items = getattr(scenes[0], "gltf_action_filter", None) if len(scenes) else None
    return {i.action.as_pointer() for i in (items or []) if not i.keep and i.action is not None}


def _entries(anim, filtered) -> list:
    """[[action, slot, NLA track name or None]] the exporter's ACTIONS mode exports from animation
    data: the active action and slot, and each track with one unmuted strip that has a slot. A track
    that holds the active action and slot takes its place (the exporter's ActionData.add_slot)."""
    found = []
    if anim is None:
        return found
    if anim.action is not None and anim.action_slot is not None and anim.action.as_pointer() not in filtered:
        found.append([anim.action, anim.action_slot, None])
    for track in anim.nla_tracks:
        strips = [s for s in track.strips if s.action is not None and not s.mute]
        if len(strips) != 1:
            continue
        strip = strips[0]
        if strip.action_slot is None or strip.action.as_pointer() in filtered:
            continue
        same = next((e for e in found if e[0] == strip.action and e[1].handle == strip.action_slot.handle
                     and e[2] is None), None)
        if same is not None:
            same[2] = track.name
        else:
            found.append([strip.action, strip.action_slot, track.name])
    return found


def _animation_data(obj) -> list:
    """obj's animation data and its shape keys' (both can have clips)."""
    anims = [obj.animation_data]
    keys = getattr(obj.data, "shape_keys", None) if obj.type == "MESH" else None
    if keys is not None:
        anims.append(keys.animation_data)
    return anims


def _frame_range(action, slot) -> tuple:
    """(start, end) frames the exporter samples: the action's frame range, or the slot's keys for a
    one-frame range (the exporter's workaround for Blender bug #107030)."""
    start, end = int(action.frame_range[0]), int(action.frame_range[1])
    if end - start == 1:
        try:
            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            curves = bag.fcurves if bag else []
            start = int(min(c.range()[0] for c in curves))
            end = int(max(c.range()[1] for c in curves))
        except (ValueError, RuntimeError):
            pass
    return start, end


def _default_track(track) -> bool:
    return track is None or track.startswith("NlaTrack") or track.startswith("[Action Stash]")


def _track_slides(objects, filtered) -> dict:
    """{NLA track name: first frame} for named tracks: the exporter slides every clip on a track of a
    given name (across objects) to the earliest start among them; other clips start at their own."""
    slides = {}
    for obj in objects:
        for anim in _animation_data(obj):
            for action, slot, track in _entries(anim, filtered):
                if not _default_track(track):
                    start = _frame_range(action, slot)[0]
                    slides[track] = min(start, slides.get(track, start))
    return slides


def _instance_copies(instancers) -> tuple:
    """Objects that collection instances bring into an export, from [(instancer, kept)]: ({pointer:
    instancer name} of objects with a copy the GLB keeps, {pointers} of those with a copy it drops).
    Instances inside kept contents are kept (only entities are stripped), inside dropped ones dropped."""
    kept, dropped, seen = {}, set(), set()
    stack = list(instancers)
    while stack:
        instancer, keep = stack.pop()
        coll = instancer.instance_collection
        if coll is None or (coll.as_pointer(), keep) in seen:
            continue
        seen.add((coll.as_pointer(), keep))
        for member in coll.all_objects:
            if keep:
                kept.setdefault(member.as_pointer(), instancer.name)
            else:
                dropped.add(member.as_pointer())
            if _instancer(member):
                stack.append((member, keep))
    return kept, dropped


def _instancer(obj) -> bool:
    return obj.instance_type == "COLLECTION" and obj.instance_collection is not None


def _members(instancers) -> list:
    """Every object inside the instances (nested ones too)."""
    found, seen = [], set()
    stack = [inst for inst, _keep in instancers]
    while stack:
        coll = stack.pop().instance_collection
        if coll is None or coll.as_pointer() in seen:
            continue
        seen.add(coll.as_pointer())
        for member in coll.all_objects:
            found.append(member)
            if _instancer(member):
                stack.append(member)
    return found


# --- Eligibility ------------------------------------------------------------------------------


def _shape_key_drivers(objects) -> dict:
    """{armature pointer: mesh name} for meshes with shape-key drivers that an armature parents or
    deforms: the exporter adds their morph weights to the armature's clips."""
    found = {}
    for obj in objects:
        keys = getattr(obj.data, "shape_keys", None) if obj.type == "MESH" else None
        anim = keys.animation_data if keys is not None else None
        if anim is None or not len(anim.drivers):
            continue
        owners = [obj.parent] + [m.object for m in obj.modifiers if m.type == "ARMATURE"]
        for owner in owners:
            if owner is not None and owner.type == "ARMATURE":
                found.setdefault(owner.as_pointer(), obj.name)
    return found


def _rig_problem(obj, sk_drivers) -> str | None:
    """Why the clips of armature obj cannot be sampled from its F-curves alone, or None."""
    if obj.data.pose_position == "REST":
        return "the armature is in Rest Position"
    anim = obj.animation_data
    if anim is not None and anim.use_tweak_mode:
        return "an NLA strip is in tweak mode"
    for data in (anim, obj.data.animation_data):
        if data is not None and len(data.drivers):
            return "it has drivers"
    if obj.as_pointer() in sk_drivers:
        return f"shape keys of '{sk_drivers[obj.as_pointer()]}' have drivers"
    for pb in obj.pose.bones:
        for con in pb.constraints:
            if con.enabled and con.influence > 0:
                return f"bone '{pb.name}' has a {con.type.replace('_', ' ').lower()} constraint"
        bone = pb.bone
        if not bone.use_inherit_rotation or bone.inherit_scale != "FULL" or not bone.use_local_location:
            return f"bone '{pb.name}' does not inherit its parent's rotation and scale fully"
    return None


def _entries_problem(entries) -> str | None:
    """Why a rig's clip list is one the exporter writes in an unusual way (an action with several
    slots, or on several tracks), or None."""
    seen = {}
    for action, slot, _track in entries:
        handles = seen.setdefault(action.as_pointer(), [])
        if slot.target_id_type != "OBJECT":
            return f"action '{action.name}' has a slot for {slot.target_id_type.lower()} data"
        if slot.handle in handles:
            return f"action '{action.name}' is on several NLA tracks"
        if handles:
            return f"action '{action.name}' is used with several slots"
        handles.append(slot.handle)
    return None


# --- Skeletons ----------------------------------------------------------------------------------


def _enum_values(struct_type, prop: str) -> dict:
    return {i.value: i.identifier for i in struct_type.bl_rna.properties[prop].enum_items}


def _pose_signature(obj) -> tuple:
    """(bone names, rotation modes, constraint counts) of obj's pose bones, read in bulk."""
    pose = obj.pose.bones
    modes = np.empty(len(pose), dtype=np.int32)
    pose.foreach_get("rotation_mode", modes)
    return [pb.name for pb in pose], modes, np.array([len(pb.constraints) for pb in pose], dtype=np.int32)


class _Skeleton:
    """An armature object's bones in the exporter's order (depth first), with the correction from a
    bone's pose-space values to its glTF node's values (the exporter's correction_matrix_local)."""

    def __init__(self, obj, pose):
        bones = obj.data.bones
        count = len(bones)
        names = [b.name for b in bones]
        index = {name: i for i, name in enumerate(names)}
        parents = [index[b.parent.name] if b.parent is not None else -1 for b in bones]
        local = np.empty(count * 16, dtype=np.float32)
        bones.foreach_get("matrix_local", local)
        local = local.reshape(count, 4, 4).transpose(0, 2, 1)  # RNA matrices are column-major
        flags = np.empty((4, count), dtype=np.int32)
        for row, name in enumerate(("use_connect", "use_inherit_rotation", "use_local_location", "inherit_scale")):
            bones.foreach_get(name, flags[row])
        pose_names, pose_modes, pose_constraints = pose
        mode_names = _enum_values(bpy.types.PoseBone, "rotation_mode")
        by_pose = {name: i for i, name in enumerate(pose_names)}
        modes = [mode_names[int(pose_modes[by_pose[n]])] for n in names]
        constrained = [int(pose_constraints[by_pose[n]]) > 0 for n in names]
        # Depth first from the roots, children in the armature's order (no recursion: chains can be long).
        children = [[] for _ in range(count)]
        for i, parent in enumerate(parents):
            if parent >= 0:
                children[parent].append(i)
        roots = [i for i, parent in enumerate(parents) if parent < 0]
        order, stack = [], roots[::-1]
        while stack:
            i = stack.pop()
            order.append(i)
            stack.extend(children[i][::-1])
        self.names = [names[i] for i in order]
        self.roots = [names[i] for i in roots]
        self.children = {names[i]: [names[c] for c in children[i]] for i in range(count)}
        self.index = {name: i for i, name in enumerate(self.names)}
        self.connected = np.array([bool(flags[0, i]) for i in order])
        self.modes = {}  # rotation mode -> indices (in self.names) of its bones
        for position, i in enumerate(order):
            self.modes.setdefault(modes[i], []).append(position)
        self.modes = {mode: np.array(found) for mode, found in self.modes.items()}
        self.mode = {names[i]: modes[i] for i in order}
        self.constrained = {names[i]: constrained[i] for i in order}  # makes the exporter sample LINEAR
        # Root bones: Y-up times their armature-space matrix; others: relative to their parent's.
        local64 = local.astype(np.float64)
        corr = np.empty((count, 4, 4))
        inverse = np.linalg.inv(local64)
        for position, i in enumerate(order):
            corr[position] = Y_UP @ local64[i] if parents[i] < 0 else inverse[parents[i]] @ local64[i]
        self.c3, self.ct = corr[:, :3, :3], corr[:, :3, 3]
        self.norms = np.linalg.norm(self.c3, axis=1)  # column norms: (bone, 3)
        self.cq = np.array([Matrix((c / n).tolist()).to_quaternion() for c, n in zip(self.c3, self.norms[:, None, :])],
                           dtype=np.float64).reshape(-1, 4)
        digest = _hash(json.dumps([names, parents, modes, constrained]).encode())
        digest.update(local.tobytes())
        digest.update(flags.tobytes())
        self.digest = digest.digest()


# --- F-curves -------------------------------------------------------------------------------------


def _rna_state(item) -> list:
    """The values of a struct's RNA properties (an F-curve modifier's settings), for hashing."""
    state = []
    for prop in item.bl_rna.properties:
        name = prop.identifier
        if name in ("rna_type", "show_expanded", "active", "is_valid") or prop.type == "POINTER":
            continue
        value = getattr(item, name)
        if prop.type == "COLLECTION":
            value = [_rna_state(member) for member in value]
        elif not isinstance(value, (bool, int, float, str, type(None))):
            try:
                value = list(value)  # arrays; enum flags are sets
            except TypeError:
                value = repr(value)
            value = sorted(value) if isinstance(getattr(item, name), (set, frozenset)) else value
        state.append([name, value])
    return state


def _parse(path: str):
    """(bone, property) of a bone transform F-curve's data path, or None."""
    found = _paths.get(path, False)
    if found is False:
        match = BONE_PATH.match(path)
        if match is not None:
            name = match.group(1)
            found = (re.sub(r"\\(.)", r"\1", name) if "\\" in name else name, match.group(2))  # escape_identifier
        else:
            found = None
        if len(_paths) < 100_000:
            _paths[path] = found
    return found


def _read_keyframes(points, count: int) -> np.ndarray:
    """A curve's keyframes (KEYFRAME records) through foreach_get."""
    keys = np.zeros(count, dtype=KEYFRAME)
    for name in KEYFRAME.names:
        base, shape = KEYFRAME.fields[name][0].base, KEYFRAME.fields[name][0].shape
        values = np.empty(count * (shape[0] if shape else 1), dtype=np.int32 if base.kind == "u" else base)
        points.foreach_get(name, values)
        keys[name] = values.reshape((count,) + shape)
    return keys


def _raw_matches(points, count: int) -> bool:
    """Whether a curve's keyframe array in memory is laid out as BEZTRIPLE (same values as foreach_get)."""
    first = points[0].as_pointer()
    if points[1].as_pointer() - first != BEZTRIPLE.itemsize:
        return False
    raw = np.frombuffer(ctypes.string_at(first, count * BEZTRIPLE.itemsize), dtype=BEZTRIPLE)
    ref = _read_keyframes(points, count)
    return all(np.array_equal(raw[name], ref[name]) for name in KEYFRAME.names)


class _Curve:
    __slots__ = ("fcurve", "bone", "prop", "index", "xs", "ys", "interp", "modified", "applied")


class _SlotCurves:
    """The F-curves of an action slot, with their keyframes read once (KEYFRAME records) and hashed."""

    def __init__(self, action, slot):
        global _raw
        self.problem = None
        bag = anim_utils.action_get_channelbag_for_slot(action, slot)
        fcurves = bag.fcurves if bag is not None else []
        flags = np.zeros((3, len(fcurves)), dtype=np.int32)  # per-curve settings in bulk
        for row, name in enumerate(("array_index", "mute", "extrapolation")):
            if len(fcurves):
                fcurves.foreach_get(name, flags[row])
        indices, mutes = flags[0].tolist(), flags[1].tolist()
        muted = {(c.data_path, c.array_index) for g in (bag.groups if bag is not None else []) if g.mute
                 for c in g.channels}
        if _raw is None:  # check Blender's keyframe layout once, on the first curve with two keys
            sample = next((fc.keyframe_points for fc in fcurves if len(fc.keyframe_points) > 1), None)
            if sample is not None:
                _raw = _raw_matches(sample, len(sample))
                if not _raw:
                    log.debug("Keyframes are read through foreach_get (unexpected keyframe layout)")
        raw = bool(_raw)
        self.entries = []  # (fcurve, (bone, property) or None, array index, keys, modified, applied)
        paths, counts, modifiers, chunks = [], [], [], []
        for i, fc in enumerate(fcurves):
            path = fc.data_path
            points = fc.keyframe_points
            count = len(points)
            modified = len(fc.modifiers) > 0
            parsed = _parse(path)
            if parsed is None and self.problem is None and (count or modified):
                self.problem = f"action '{action.name}' animates {path or 'a property'}"
            paths.append(path)
            counts.append(count)
            if modified:
                modifiers.append([i, [_rna_state(m) for m in fc.modifiers]])
            if count:
                if raw:
                    chunks.append(ctypes.string_at(points[0].as_pointer(), count * BEZTRIPLE.itemsize))
                else:
                    chunks.append(_read_keyframes(points, count).tobytes())
            group_mute = bool(muted) and (path, indices[i]) in muted
            # Blender skips muted curves, curves of muted groups and empty curves.
            applied = not mutes[i] and not group_mute and (count > 0 or modified)
            self.entries.append((fc, parsed, indices[i], count, modified, applied))
        joined = b"".join(chunks)
        if raw:
            stored = np.frombuffer(joined, dtype=BEZTRIPLE)
            self.keys = np.empty(len(stored), dtype=KEYFRAME)
            for name in KEYFRAME.names:
                self.keys[name] = stored[name]
        else:
            self.keys = np.frombuffer(joined, dtype=KEYFRAME)
        digest = _hash(json.dumps([list(action.frame_range), paths, modifiers, sorted(muted)],
                                         default=repr).encode())
        digest.update(flags.tobytes())
        digest.update(np.array(counts, dtype=np.int64).tobytes())
        digest.update(self.keys.tobytes())
        self.digest = digest.digest()
        self._curves = None

    @property
    def curves(self) -> list:
        """The bone transform curves (_Curve), made on first use (sampling a clip)."""
        if self._curves is None:
            self._curves, offset = [], 0
            for fc, parsed, index, count, modified, applied in self.entries:
                keys = self.keys[offset:offset + count]
                offset += count
                if parsed is None:
                    continue
                curve = _Curve()
                curve.fcurve = fc
                curve.bone, curve.prop = parsed
                curve.index = index
                curve.xs, curve.ys = keys["co"][:, 0], keys["co"][:, 1]
                curve.interp = keys["interpolation"]
                curve.modified, curve.applied = modified, applied
                self._curves.append(curve)
        return self._curves


def action_digest(action, slot) -> str:
    """A hash of what an action slot animates: its F-curves (paths, keyframes with their handles and
    interpolation, mute, extrapolation, modifiers) and the action's frame range. Changes with every edit
    that changes the clip, never with selection. Cheap (one read per curve)."""
    return _SlotCurves(action, slot).digest.hex()


def clip_digests(objects) -> dict:
    """{(action name_full, slot identifier): action_digest()} for the clips the export writes for
    objects: active actions, NLA tracks with one unmuted strip, and their shape keys' clips."""
    filtered = _filtered_actions()
    found = {}
    for obj in objects:
        for anim in _animation_data(obj):
            for action, slot, _track in _entries(anim, filtered):
                key = (action.name_full, slot.identifier)
                if key not in found:
                    found[key] = action_digest(action, slot)
    return found


# --- Sampling ------------------------------------------------------------------------------------


class _Unsupported(Exception):
    pass


class ClipData:
    """A sampled clip: key times (seconds, float64) and per (bone, path) the glTF values of its keys
    (float32 rows) and their interpolation."""

    def __init__(self, times, channels):
        self.times = times
        self.channels = channels  # {(bone, path): (values, interpolation)}
        self._encoded = None
        # Its memory with the encoded bytes (for the session's memory budget).
        self.nbytes = times.nbytes + 2 * sum(values.nbytes for values, _interp in channels.values())

    def encoded(self) -> dict:
        """{(bone, path): (input bytes, (first, last time), output bytes, width, count, interpolation)}:
        the accessor data splice() writes, encoded once."""
        if self._encoded is None:
            self._encoded, inputs = {}, {}
            for (bone, path), (values, interp) in self.channels.items():
                count = len(values)
                if count not in inputs:
                    times = self.times if count == len(self.times) else self.times[[0, -1][:count]]
                    inputs[count] = (times.astype("<f4").tobytes(), (float(times.min()), float(times.max())))
                self._encoded[(bone, path)] = (*inputs[count], values.astype("<f4").tobytes(), values.shape[1],
                                               count, interp)
        return self._encoded

    def to_bytes(self) -> bytes:
        header = {"version": CLIPS_VERSION, "frames": len(self.times),
                  "channels": [[bone, path, len(values), interp]
                               for (bone, path), (values, interp) in self.channels.items()]}
        body = json.dumps(header, separators=(",", ":")).encode("utf-8")
        parts = [MAGIC, struct.pack("<I", len(body)), body, self.times.astype("<f8").tobytes()]
        parts += [values.astype("<f4").tobytes() for values, _interp in self.channels.values()]
        return b"".join(parts)

    @classmethod
    def from_bytes(cls, data: bytes):
        if data[:len(MAGIC)] != MAGIC:
            raise ValueError("not a clip file")
        offset = len(MAGIC)
        (size,) = struct.unpack_from("<I", data, offset)
        offset += 4
        header = json.loads(data[offset:offset + size].decode("utf-8"))
        if header.get("version") != CLIPS_VERSION:
            raise ValueError("another clip version")
        offset += size
        frames = header["frames"]
        times = np.frombuffer(data, dtype="<f8", count=frames, offset=offset).astype(np.float64)
        offset += 8 * frames
        channels = {}
        for bone, path, count, interp in header["channels"]:
            width = WIDTH[path]
            values = np.frombuffer(data, dtype="<f4", count=count * width, offset=offset).reshape(count, width)
            offset += 4 * count * width
            channels[(bone, path)] = (values, interp)
        if offset != len(data):
            raise ValueError("clip file has the wrong size")
        return cls(times, channels)


def _remember(key: str, data: ClipData) -> None:
    global _memory_bytes
    if key in _memory:
        _memory.move_to_end(key)
        return
    _memory[key] = data
    _memory_bytes += data.nbytes
    while _memory_bytes > MEMORY_BYTES and len(_memory) > 1:
        _old, dropped = _memory.popitem(last=False)
        _memory_bytes -= dropped.nbytes


def _quat_mul(a, b):
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def _canonical(q):
    """Quaternions with w >= 0, as Blender's matrix-to-quaternion conversion returns them."""
    return np.where(q[..., :1] < 0, -q, q)


def _euler_quat(euler, order: str):
    """Euler angles (..., 3) in a rotation mode ('XYZ': X applied first) -> quaternions (w, x, y, z)."""
    q = np.zeros(euler.shape[:-1] + (4,))
    q[..., 0] = 1.0
    for letter in order:
        axis = "XYZ".index(letter)
        step = np.zeros_like(q)
        step[..., 0] = np.cos(euler[..., axis] / 2)
        step[..., 1 + axis] = np.sin(euler[..., axis] / 2)
        q = _quat_mul(step, q)
    return q


def _curve_values(curve, frames):
    """The curve at each frame: the key's value where a key sits on the frame (no modifiers),
    evaluate() elsewhere."""
    xs = curve.xs
    out = np.empty(len(frames))
    miss = range(len(frames))
    if not curve.modified and len(xs):
        if np.array_equal(xs, frames):  # a key on every frame (baked and imported clips)
            return curve.ys.astype(np.float64)
        if len(xs) < 2 or (np.diff(xs) > 0).all():
            index = np.minimum(np.searchsorted(xs, frames), len(xs) - 1)
            hit = xs[index] == frames
            out[hit] = curve.ys[index[hit]]
            miss = np.nonzero(~hit)[0]
    evaluate = curve.fcurve.evaluate
    for i in miss:
        out[i] = evaluate(frames[i])
    return out


def _animated(skeleton, curves) -> dict:
    """{(bone, path): glTF interpolation} for the channels the exporter treats as animated (it has
    F-curves for them), with the interpolation it gives them (get_channel_groups, needs_baking)."""
    names = _enum_values(bpy.types.Keyframe, "interpolation")  # value -> identifier
    groups = {}
    for curve in curves.curves:
        mode = skeleton.mode.get(curve.bone)
        if mode is None or not len(curve.xs):
            continue
        prop = curve.prop
        if prop.startswith("rotation_") and prop != ROTATION.get(mode, "rotation_euler"):
            continue  # keys of another rotation mode: Blender and the exporter ignore them
        groups.setdefault((curve.bone, prop), []).append(curve)
    found = {}
    for (bone, prop), group in groups.items():
        first = names.get(int(group[0].interp[0]))
        bake = (first not in ("BEZIER", "LINEAR", "CONSTANT")
                or any((c.interp != group[0].interp[0]).any() for c in group)
                or len({len(c.xs) for c in group}) > 1
                or len(group[0].xs) <= 1
                or any(not np.array_equal(c.xs, group[0].xs) for c in group)
                or skeleton.constrained[bone])
        interp = "LINEAR" if bake else {"BEZIER": "CUBICSPLINE", "LINEAR": "LINEAR", "CONSTANT": "STEP"}[first]
        path = "translation" if prop == "location" else ("scale" if prop == "scale" else "rotation")
        found[(bone, path)] = interp
    return found


def _sampler_interpolation(count: int, animated, same: bool) -> str:
    """The exporter's interpolation for a sampled channel of count keys (__gather_interpolation)."""
    if count > 2:
        return "STEP" if animated == "STEP" else "LINEAR"
    if animated is None:
        return "STEP"
    if count == 1:
        return "LINEAR" if animated == "CUBICSPLINE" else animated
    return "STEP" if same else "LINEAR"


def sample(skeleton, curves, start: int, end: int, slide: int, fps: float) -> ClipData:
    """Sample a clip at every frame from start to end as the exporter does: a bone's pose-space
    transform from its F-curves (rest values for unkeyed channels), corrected into its glTF node's
    space; times slide by `slide` frames. Raises _Unsupported for a zero or negative scale."""
    frames = np.arange(start, end + 1, dtype=np.float64)
    n, count = len(frames), len(skeleton.names)
    times = (frames - slide) / fps
    animated = _animated(skeleton, curves)
    # Every bone at once, (frame, bone, component), from the rest values: rotation holds the mode's
    # values (a quaternion, an axis-angle as angle and axis, or Euler angles and a 0).
    loc = np.zeros((n, count, 3))
    rot = np.zeros((n, count, 4))
    rot[:, skeleton.modes.get("QUATERNION", []), 0] = 1.0
    rot[:, skeleton.modes.get("AXIS_ANGLE", []), 2] = 1.0  # angle 0 about Y, as Blender's reset leaves it
    scale = np.ones((n, count, 3))
    for curve in curves.curves:  # a later curve of the same channel overrides, as in Blender
        b = skeleton.index.get(curve.bone)
        if b is None or not curve.applied:
            continue
        if curve.prop == "location":
            target, width = (None, 0) if skeleton.connected[b] else (loc, 3)  # Blender ignores a connected bone's
        elif curve.prop == "scale":
            target, width = scale, 3
        elif curve.prop == ROTATION.get(skeleton.mode[curve.bone], "rotation_euler"):
            target, width = rot, (3 if curve.prop == "rotation_euler" else 4)
        else:
            continue
        if target is not None and curve.index < width:
            target[:, b, curve.index] = _curve_values(curve, frames)
    q = rot.copy()
    index = skeleton.modes.get("QUATERNION", [])
    if len(index):
        length = np.linalg.norm(rot[:, index], axis=2, keepdims=True)
        q[:, index] = np.where(length > 0, rot[:, index] / np.where(length > 0, length, 1), (0.0, 1.0, 0.0, 0.0))
    index = skeleton.modes.get("AXIS_ANGLE", [])
    if len(index):
        aa = rot[:, index]
        length = np.linalg.norm(aa[..., 1:], axis=2, keepdims=True)
        axis = aa[..., 1:] / np.where(length > 0, length, 1)
        turned = np.concatenate([np.cos(aa[..., :1] / 2), axis * np.sin(aa[..., :1] / 2)], axis=2)
        q[:, index] = np.where(length > 0, turned, (1.0, 0.0, 0.0, 0.0))
    for order in EULER_MODES:
        index = skeleton.modes.get(order, [])
        if len(index):
            q[:, index] = _euler_quat(rot[:, index, :3], order)
    q = _canonical(q)
    bad = np.nonzero((scale <= 0).any(axis=(0, 2)))[0]
    if len(bad):
        raise _Unsupported(f"bone '{skeleton.names[bad[0]]}' has a zero or negative scale")
    rotation = _canonical(_quat_mul(np.broadcast_to(skeleton.cq, q.shape), q))[..., [1, 2, 3, 0]]
    values = {"translation": (loc, np.einsum("bij,nbj->nbi", skeleton.c3, loc) + skeleton.ct),
              "rotation": (q, rotation),
              "scale": (scale, scale * skeleton.norms)}
    out = {}
    for path, (pose, gltf) in values.items():
        constant = (np.ptp(pose, axis=0) < CONSTANT).all(axis=1) if n >= 2 else np.zeros(count, dtype=bool)
        same = (pose[0].astype(np.float32) == pose[-1].astype(np.float32)).all(axis=1)
        out[path] = (gltf.astype(np.float32), constant, same)
    channels = {}
    for b, name in enumerate(skeleton.names):
        for path in PATHS:
            gltf, constant, same = out[path]
            keys = gltf[[0, -1], b] if constant[b] else gltf[:, b]
            interp = _sampler_interpolation(len(keys), animated.get((name, path)), constant[b] and same[b])
            channels[(name, path)] = (np.ascontiguousarray(keys), interp)
    return ClipData(times, channels)


# --- Plan ---------------------------------------------------------------------------------------


class _Clip:
    __slots__ = ("ident", "name", "key")

    def __init__(self, ident, name, key):
        self.ident, self.name, self.key = ident, name, key


class _Rig:
    def __init__(self, obj, skeleton):
        self.obj = obj
        self.skeleton = skeleton
        self.clips = {}  # (action pointer, slot handle, track) -> _Clip

    def match(self, action_data):
        """The planned clip an exporter ActionData stands for, or None."""
        if action_data.name != action_data.action.name or len(action_data.slots) != 1:
            return None
        slot = action_data.slots[0]
        if slot.target_id_type != "OBJECT":
            return None
        return self.clips.get((action_data.action.as_pointer(), slot.slot.handle, slot.track))


class _Run:
    """What one exporter run (the scene GLB, the prefab GLB) leaves to the sampler."""

    def __init__(self, label):
        self.label = label
        self.rigs = {}        # object pointer -> _Rig
        self.drop = set()     # pointers of objects whose nodes the export drops: no clips needed
        self.calls = []       # (object pointer, its animation names) in the exporter's object order
        self.removed = {}     # object pointer -> {clip ident: _Clip} the hook took out
        self.hooked = False
        self.disabled = None  # why the hook left every clip to the exporter
        self.spliced = 0      # rig-clip pairs written into the GLB


class Plan:
    """The clips of one export: what each exporter run leaves to the sampler, and their data."""

    def __init__(self, cache):
        self.cache = cache
        self.scene = None
        self.prefabs = None
        self.stock = {}       # object name -> why the exporter samples its clips
        self.data = {}        # clip key -> ClipData
        self.sampled = 0
        self.cached = 0
        self.written = 0
        self.seconds = 0.0
        self._curves = {}
        self._skeletons = {}

    def note_stock(self, obj, reason: str) -> None:
        self.stock.setdefault(obj.name, reason)
        if (obj.name, reason) not in _logged:
            _logged.add((obj.name, reason))
            log.info("Clips of '%s' are sampled by the glTF exporter (slower): %s", obj.name, reason)

    def curves(self, action, slot) -> _SlotCurves:
        key = (action.as_pointer(), slot.handle)
        if key not in self._curves:
            self._curves[key] = _SlotCurves(action, slot)
        return self._curves[key]

    def skeleton(self, obj) -> _Skeleton:
        """obj's skeleton; objects that share an armature and its pose settings share one."""
        pose = _pose_signature(obj)
        key = (obj.data.as_pointer(), tuple(pose[0]), pose[1].tobytes(), pose[2].tobytes())
        if key not in self._skeletons:
            self._skeletons[key] = _Skeleton(obj, pose)
        return self._skeletons[key]

    def clip_data(self, key: str, rig, curves, start, end, slide, fps) -> ClipData:
        """The clip with this key: from this export, memory, the disk cache, or sampled now."""
        data = self.data.get(key)
        if data is not None:
            return data
        path = self.cache / f"{key}{SUFFIX}" if self.cache is not None else None
        data = _memory.get(key)
        if data is not None:
            self.cached += 1
            if path is not None:
                with contextlib.suppress(OSError):
                    os.utime(path, None)  # last used: prune() keeps it
        elif path is not None and path.is_file():
            try:
                data = ClipData.from_bytes(path.read_bytes())
                os.utime(path, None)
                self.cached += 1
            except (OSError, ValueError, KeyError, struct.error) as exc:
                log.debug("Clip cache %s unreadable (%s); sampling again", path.name, exc)
                data = None
        if data is None:
            data = sample(rig.skeleton, curves, start, end, slide, fps)
            self.sampled += 1
            if path is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_name(path.name + ".tmp")
                    tmp.write_bytes(data.to_bytes())
                    os.replace(tmp, path)
                    self.written += 1
                except OSError as exc:
                    log.debug("Could not cache clip %s: %s", path.name, exc)
        _remember(key, data)
        self.data[key] = data
        return data

    def summary(self) -> dict:
        """The export result's "clips" entry."""
        runs = [r for r in (self.scene, self.prefabs) if r is not None]
        for run in runs:
            if run.rigs and (not run.hooked or run.disabled):
                for rig in run.rigs.values():
                    self.note_stock(rig.obj, run.disabled or "the glTF exporter did not call GameStudio's hook")
        return {"fast": sum(r.spliced for r in runs), "sampled": self.sampled, "cached": self.cached,
                "stock": [{"object": name, "reason": reason} for name, reason in self.stock.items()],
                "seconds": round(self.seconds, 3)}


def _key(curves, skeleton, start, end, slide, fps) -> str:
    digest = _hash(f"{CLIPS_VERSION}|{fps!r}|{start}|{end}|{slide}|".encode())
    digest.update(curves.digest)
    digest.update(skeleton.digest)
    return digest.hexdigest()


def _plan_run(plan, label, real, universe, kept, dropped, fps, filtered, sk_drivers):
    """The _Run for one exporter run. real: the objects the GLB has nodes for; universe: every object
    the exporter visits; kept/dropped: objects with copies in kept/dropped instance contents."""
    run = _Run(label)
    real_ptrs = {o.as_pointer() for o in real}
    run.drop = {o.as_pointer() for o in universe} - real_ptrs - set(kept)
    run.drop &= dropped
    slides = _track_slides(universe, filtered)
    for obj in real:
        if obj.type != "ARMATURE":
            continue
        entries = _entries(obj.animation_data, filtered)
        if not entries:
            continue
        ptr = obj.as_pointer()
        reason = (f"a copy is kept in collection instance '{kept[ptr]}'" if ptr in kept else
                  _rig_problem(obj, sk_drivers) or _entries_problem(entries))
        rig = None
        if reason is None:
            rig = _Rig(obj, plan.skeleton(obj))
            try:
                for action, slot, track in entries:
                    curves = plan.curves(action, slot)
                    if curves.problem:
                        raise _Unsupported(curves.problem)
                    start, end = _frame_range(action, slot)
                    slide = start if _default_track(track) else slides.get(track, start)
                    key = _key(curves, rig.skeleton, start, end, slide, fps)
                    plan.clip_data(key, rig, curves, start, end, slide, fps)
                    ident = (action.as_pointer(), slot.handle, track)
                    rig.clips[ident] = _Clip(ident, action.name, key)
            except _Unsupported as exc:
                reason = str(exc)
        if reason is not None:
            plan.note_stock(obj, reason)
            continue
        run.rigs[ptr] = rig
    return run


def plan(scene, layer_objects, prefabs=(), prefab_objects=(), cache=None) -> Plan:
    """What the exporter can leave to the sampler in this export, with the clips sampled or read
    from the cache. layer_objects: the view layer's objects (the scene GLB); prefabs and
    prefab_objects: the prefab collections and all their objects (the prefab GLB); cache: the cache
    folder (cache_dir(); None: none). Never raises: after an unexpected error the plan is empty and
    the exporter samples every clip."""
    started = time.perf_counter()
    result = Plan(pathlib.Path(cache) if cache is not None else None)
    render = scene.render
    try:
        if render.frame_map_old != render.frame_map_new:
            log.info("Clips are sampled by the glTF exporter (slower): the scene remaps time (Time Stretching)")
            return result
        fps = render.fps * render.fps_base  # the exporter's frame rate (sic: fps times fps_base)
        filtered = _filtered_actions()
        prefab_ptrs = {c.as_pointer() for c in prefabs}
        layer = {o.as_pointer() for o in layer_objects}

        def outside(obj):  # obj and all its parents are outside the view layer: export drops its node
            while obj is not None:
                if obj.as_pointer() in layer:
                    return False
                obj = obj.parent
            return True

        everything = list(scene.objects)
        instancers = []
        for obj in everything:
            if _instancer(obj):
                if obj.as_pointer() in layer:  # an entity: the contents of a prefab instance are stripped
                    keep = obj.instance_collection.as_pointer() not in prefab_ptrs
                else:
                    keep = not outside(obj)
                instancers.append((obj, keep))
        kept, dropped = _instance_copies(instancers)
        dropped |= {o.as_pointer() for o in everything if o.as_pointer() not in layer and outside(o)}
        universe = list({o.as_pointer(): o for o in everything + _members(instancers)}.values())
        sk_drivers = _shape_key_drivers(universe)
        result.scene = _plan_run(result, "scene", list(layer_objects), universe, kept, dropped, fps, filtered,
                                 sk_drivers)
        if prefab_objects:
            real = list(prefab_objects)
            instancers = [(o, o.instance_collection.as_pointer() not in prefab_ptrs) for o in real if _instancer(o)]
            kept, dropped = _instance_copies(instancers)
            universe = list({o.as_pointer(): o for o in real + _members(instancers)}.values())
            result.prefabs = _plan_run(result, "prefabs", real, universe, kept, dropped, fps, filtered,
                                       _shape_key_drivers(universe))
        if result.written:
            prune(result.cache)
    except Exception:
        log.exception("Sampling clips failed; the glTF exporter samples them")
        result.scene = result.prefabs = None
    finally:
        result.seconds += time.perf_counter() - started
    return result


def prune(cache, max_age_days: float = MAX_AGE_DAYS) -> int:
    """Delete cached clips not used for max_age_days. Returns how many were deleted."""
    root = pathlib.Path(cache) if cache is not None else None
    if root is None or not root.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in root.glob(f"*{SUFFIX}"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --- The exporter hook --------------------------------------------------------------------------


def _package():
    """The add-on package module (it exposes glTF2ExportUserExtension)."""
    return sys.modules.get(__package__.rpartition(".")[0])


@contextlib.contextmanager
def armed(run):
    """Let the hook take run's clips out while the exporter runs. The exporter only calls the hooks
    of enabled add-ons: when the package is not one (the test runner, a background export imports
    it), it is listed as an add-on for the duration."""
    global _armed
    if run is None or not (run.rigs or run.drop):
        yield
        return
    prefs = bpy.context.preferences
    package = _package()
    entry = None
    dirty = prefs.is_dirty
    if package is not None and not any(sys.modules.get(name) is package for name in prefs.addons.keys()):
        entry = prefs.addons.new()
        entry.module = package.__name__
    _armed = run
    try:
        yield
    finally:
        _armed = None
        if entry is not None:
            prefs.addons.remove(entry)
            prefs.is_dirty = dirty


def _settings_problem(export_settings) -> str | None:
    for name, value in EXPORTER.items():
        if export_settings.get(name, None) != value:
            return f"the glTF exporter runs with {name} = {export_settings.get(name)!r}"
    return None


def gather_actions_hook(blender_object, actions, export_settings) -> None:
    """The exporter's gather_actions_hook: called once per exported object with the actions the
    exporter will write for it (an ActionsData). Takes the armed run's clips out."""
    run = _armed
    if run is None or blender_object is None:
        return
    if not run.hooked:
        run.hooked = True
        run.disabled = _settings_problem(export_settings)
        if run.disabled:
            log.info("Clips are sampled by the glTF exporter (slower): %s", run.disabled)
    if run.disabled:
        return
    ptr = blender_object.as_pointer()
    values = [data for items in actions.actions.values() for data in items]
    values.sort(key=lambda a: a.action.name.lower())  # the order the exporter writes them in
    run.calls.append((ptr, [data.name for data in values]))
    if ptr in run.drop:
        actions.actions.clear()
        return
    rig = run.rigs.get(ptr)
    if rig is None:
        return
    for key, items in list(actions.actions.items()):
        keep = []
        for data in items:
            clip = rig.match(data)
            if clip is None:
                keep.append(data)
            else:
                run.removed.setdefault(ptr, {})[clip.ident] = clip
        if keep:
            items[:] = keep
        else:
            del actions.actions[key]


# --- Splicing into the GLB -------------------------------------------------------------------------


def _bone_nodes(nodes, rig_node, skeleton, entity_nodes) -> dict:
    """{bone name: node index} of a rig's bones: the root bones are children of its node, a bone's
    children are children of the bone's node."""
    found = {}
    stack = [(rig_node, skeleton.roots)]
    while stack:
        index, wanted = stack.pop()
        wanted = set(wanted)
        for child in nodes[index].get("children", []):
            name = nodes[child].get("name")
            if child in entity_nodes or name not in wanted or name in found:
                continue
            found[name] = child
            stack.append((child, skeleton.children[name]))
    return found


def _add_accessor(glb, data: bytes, width: int, count: int, bounds=None) -> int:
    """A float accessor over a new buffer view holding data (fidelity.Glb.add_accessor, pre-encoded)."""
    gltf = glb.gltf
    views = gltf.setdefault("bufferViews", [])
    if not gltf.get("buffers"):
        gltf["buffers"] = [{"byteLength": 0}]
    views.append({"buffer": 0, "byteOffset": 0, "byteLength": len(data)})
    glb.replace(len(views) - 1, data)
    accessor = {"bufferView": len(views) - 1, "componentType": FLOAT, "count": count, "type": TYPES[width]}
    if bounds is not None:  # a sampler input: min and max, as the exporter writes them (float64)
        accessor["min"], accessor["max"] = [bounds[0]], [bounds[1]]
    accessors = gltf.setdefault("accessors", [])
    accessors.append(accessor)
    return len(accessors) - 1


def splice(glb, run, plan, matched: dict, objects: dict) -> int:
    """Write the clips the hook took out of run into glb (fidelity.Glb), as the exporter would.
    matched: {object key: node index} (export.inject_ids); objects: {object key: (object, gs_id)}.
    Returns the rig-clip pairs written."""
    if run is None or not run.removed:
        return 0
    started = time.perf_counter()
    gltf = glb.gltf
    nodes = gltf.get("nodes", [])
    entity_nodes = set(matched.values())
    node_of = {objects[k][0].as_pointer(): i for k, i in matched.items()}
    rank = {}
    for i, (ptr, _names) in enumerate(run.calls):
        rank.setdefault(ptr, i)
    animations = gltf.setdefault("animations", [])
    by_name = {}
    for anim in animations:
        by_name.setdefault(anim.get("name"), anim)
    accessors, by_clip = {}, {}

    def accessor(data, width, count, bounds=None):  # identical data (rest channels too) is stored once
        key = (width, data)
        if key not in accessors:
            accessors[key] = _add_accessor(glb, data, width, count, bounds)
        return accessors[key]

    touched = set()
    for ptr in sorted(run.removed, key=lambda p: rank.get(p, len(rank))):
        rig = run.rigs[ptr]
        rig_node = node_of.get(ptr)
        if rig_node is None:
            log.warning("Clips of '%s' are missing from the export: its node is not in the GLB", rig.obj.name)
            plan.note_stock(rig.obj, "its node is not in the GLB")
            continue
        found = _bone_nodes(nodes, rig_node, rig.skeleton, entity_nodes)
        bones = [(name, found[name]) for name in rig.skeleton.names if name in found]  # the exporter's order
        for clip in sorted(run.removed[ptr].values(), key=lambda c: c.name.lower()):
            encoded = plan.data[clip.key].encoded()
            anim = by_name.get(clip.name)
            if anim is None:
                anim = by_name[clip.name] = {"name": clip.name, "channels": [], "samplers": []}
                animations.append(anim)
            touched.add(id(anim))
            samplers, channels = anim["samplers"], anim["channels"]
            for name, node in bones:
                for path in PATHS:
                    shared = (clip.key, name, path)
                    sampler = by_clip.get(shared)
                    if sampler is None:  # rigs that play the same clip share its accessors
                        times, bounds, values, width, count, interp = encoded[(name, path)]
                        sampler = by_clip[shared] = {"input": accessor(times, 1, count, bounds),
                                                     "interpolation": interp,
                                                     "output": accessor(values, width, count)}
                    samplers.append(dict(sampler))
                    channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
            run.spliced += 1
    if touched:
        _order(gltf, run, rank, matched, objects, touched)
    elif not animations:
        gltf.pop("animations", None)
    plan.seconds += time.perf_counter() - started
    return run.spliced


def _order(gltf, run, rank, matched, objects, touched) -> None:
    """Put the animations in the exporter's order (by first use, objects in the exporter's order,
    each object's actions by name) and the channels of the animations splice() touched in object
    order, so the manifest lists clips and their entities as without the sampler."""
    first = {}
    for _ptr, names in run.calls:
        for name in names:
            first.setdefault(name, len(first))
    animations = gltf["animations"]
    order = sorted(range(len(animations)),
                   key=lambda i: (0, first[animations[i].get("name")], i) if animations[i].get("name") in first
                   else (1, 0, i))
    gltf["animations"] = [animations[i] for i in order]
    parent = {}
    for index, node in enumerate(gltf.get("nodes", [])):
        for child in node.get("children", []):
            parent[child] = index
    owner_rank = {i: rank.get(objects[k][0].as_pointer(), len(rank)) for k, i in matched.items()}

    def owner(index):  # the rank of the nearest entity above a node (memoised along the way)
        path = []
        while index is not None and index not in owner_rank and len(path) <= len(parent):
            path.append(index)
            index = parent.get(index)
        found = owner_rank.get(index, len(rank))
        for visited in path:
            owner_rank[visited] = found
        return found

    for anim in gltf["animations"]:
        if id(anim) not in touched:
            continue
        channels = anim["channels"]
        ranks = [owner(c.get("target", {}).get("node")) for c in channels]
        if ranks == sorted(ranks):
            continue  # already in object order (each sampler next to its channel)
        channels = [channels[i] for i in sorted(range(len(channels)), key=lambda i: (ranks[i], i))]
        samplers, remap = [], {}
        for channel in channels:
            old = channel["sampler"]
            if old not in remap:
                remap[old] = len(samplers)
                samplers.append(anim["samplers"][old])
            channel["sampler"] = remap[old]
        anim["channels"], anim["samplers"] = channels, samplers
