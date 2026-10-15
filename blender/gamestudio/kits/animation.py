# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural animation clips: humanoid moves for rigged characters and simple loops for any object.

    add_clip(obj, kind, params=None, *, name=None, activate=True) -> Action
    add_clips(obj, kinds=None, *, activate=True) -> [Action]     (kinds None: every humanoid clip)
    clips(obj) -> [{name, action, kind, params, loop, seconds, frames, ground_speed?}]
    remove_clip(obj, name)
    preview(obj, name, frame=None)   make it the active action and fit the scene's frame range to it
    rebake(obj)                      re-key every generated clip (regenerate() calls it)
    catalog() -> [{kind, clip, target, loop, description, params}]

Humanoid kinds need an armature from the character_rigged kit (or its mesh): idle, walk, run,
jump, jump_start, jump_air, land, wave, fall. Object kinds work on any object: spin, bob, pulse,
door_open, door_close (hinged on the object's local Z, or X for levers and lids), sway, hover.
A list such as ["spin", "bob"] layers object loops into one
clip; its params are then {"spin": {...}, "bob": {...}}. Kinds also accept clip names ("JumpAir").

How clips are stored, so Blender's glTF exporter writes each one as its own named animation: one
action per clip, named after the clip (Walk, spin; Blender adds .001 to a different clip with the
same name), with one slot, on a muted NLA track of its own holding a single strip. The newest clip
is also the active action, so scrubbing the timeline shows it. Keys are sampled every frame at the
scene's frame rate (GameStudio scenes use 30 fps) with linear interpolation; timings are in
seconds. A clip whose keys match an existing clip of the same name reuses its action, so ten coins
with the same spin share one spin action (and one glTF animation).

Humanoid clips stay in place: they key bones only, never the armature object, so the runtime's
character controller moves the character. Feet are placed with two-bone IK, so planted feet do not
slide or sink and the hips bob as the legs allow. Object clips key the object's own transform
around its current placement: place the object first. Gameplay code that moves an object fights a
clip that keys its location; animate a child instead (prefabs: never animate the root).
"""

import hashlib
import json
import math
import re

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import rig as rigging
from .base import choice, number, validate
from .errors import KitError
from .geometry import smoothstep

CLIP_PROP = "gs_clip"  # action[CLIP_PROP]: JSON {kind, params, loop, seconds, signature, ...} of a generated clip
HUMANOID_SET = ("idle", "walk", "run", "jump", "jump_start", "jump_air", "land", "wave", "fall")
LINEAR = 1  # FCurve keyframe interpolation enum value

_CLIPS: dict = {}


class ClipKind:
    def __init__(self, kind, clip, target, loop, description, params, make):
        self.kind = kind
        self.clip = clip                # default clip (action) name
        self.target = target            # "humanoid" or "object"
        self.loop = loop
        self.description = description
        self.params = params            # name -> JSON Schema
        self.make = make                # humanoid: make(rig, p) -> (seconds, pose(t)); object: make(p) -> (seconds, motion(t))

    def entry(self) -> dict:
        return {"kind": self.kind, "clip": self.clip, "target": self.target, "loop": self.loop,
                "description": self.description,
                "params": {"type": "object", "additionalProperties": False,
                           "properties": json.loads(json.dumps(self.params))}}


def clip_kind(kind, clip, target, loop, description, params):
    def register(make):
        _CLIPS[kind] = ClipKind(kind, clip, target, loop, description, params, make)
        return make
    return register


def catalog() -> list[dict]:
    return [_CLIPS[k].entry() for k in sorted(_CLIPS, key=lambda k: (_CLIPS[k].target != "humanoid", k))]


def kinds() -> list[str]:
    return sorted(_CLIPS)


def _kind(kind) -> ClipKind:
    if not isinstance(kind, str):
        raise KitError(f"Clip kind must be a string, got {kind!r}")
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", kind.strip()).lower().replace("-", "_").replace(" ", "_")
    spec = _CLIPS.get(key)
    if spec is None:
        raise KitError(f"Unknown clip kind {kind!r}. Humanoid: {', '.join(HUMANOID_SET)}. "
                       f"Any object: {', '.join(k for k in sorted(_CLIPS) if _CLIPS[k].target == 'object')}")
    return spec


# ------------------------------------------------------------------ rotations

X, Y, Z = Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))


def qx(deg):
    return Quaternion(X, math.radians(deg))


def qy(deg):
    return Quaternion(Y, math.radians(deg))


def qz(deg):
    return Quaternion(Z, math.radians(deg))


def body(pitch=0.0, tilt=0.0, yaw=0.0):
    """Upper-body rotation in armature axes: pitch > 0 leans forward (-Y), tilt > 0 leans to the
    character's left (+X), yaw > 0 turns it to its left."""
    return qz(yaw) @ qy(tilt) @ qx(pitch)


def limb(fwd=0.0, out=0.0):
    """Left arm or leg swing: out > 0 spreads it away from the body, then fwd > 0 swings it forward
    (past 90 it points up: the spread stays outward). mirror() makes the right side's version."""
    return qx(-fwd) @ qy(-out)


def mirror(q: Quaternion) -> Quaternion:
    """The rotation reflected through the YZ plane (left side to right side)."""
    return Quaternion((q.w, q.x, -q.y, -q.z))


def _lerp(a, b, t):
    return a + (b - a) * t


def _ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, t)))


# ------------------------------------------------------------------ humanoid rig and poses


class Rig:
    """The rest layout of a humanoid armature, read from its bones."""

    def __init__(self, arm):
        bones = arm.data.bones
        missing = [n for n in rigging.HUMANOID_BONES if n not in bones]
        if missing:
            raise KitError(f"{arm.name} is not a GameStudio humanoid rig (no bone {missing[0]}); humanoid clips "
                           "need an armature from the character_rigged kit")
        self.name = arm.name
        self.rest = {n: bones[n].matrix_local.to_quaternion() for n in rigging.HUMANOID_BONES}
        self.head = {n: bones[n].head_local.copy() for n in rigging.HUMANOID_BONES}
        layout = rigging.layout(arm.data)
        foot = (bones["foot_L"].tail_local - bones["foot_L"].head_local).length
        self.ankle_h = layout.get("ankle", self.head["foot_L"].z)
        self.heel = layout.get("heel", 0.35 * foot)
        self.toe = layout.get("toe", 1.1 * foot)
        self.height = layout.get("height", bones["head"].tail_local.z)
        ah, heel, toe = self.ankle_h, self.heel, self.toe
        self.sole = (Vector((0.0, heel, -ah)), Vector((0.0, 0.4 * ah - toe, -ah)), Vector((0.0, -toe, -0.65 * ah)))
        self.l1 = (self.head["lower_leg_L"] - self.head["upper_leg_L"]).length
        self.l2 = (self.head["foot_L"] - self.head["lower_leg_L"]).length
        self.leg = self.l1 + self.l2

    def ankle(self, side) -> Vector:
        return self.head[f"foot_{side}"].copy()

    def hip_joint(self, pose, side) -> Vector:
        hips = self.head["hips"]
        return hips + pose.hips_offset + pose.hips @ (self.head[f"upper_leg_{side}"] - hips)

    def planted(self, side, y=0.0, pitch=0.0, x=None) -> Vector:
        """Ankle of a foot on the ground at depth y, rolled `pitch` degrees (toe up > 0) about its
        heel, or toe down < 0 about its toe."""
        rest = self.ankle(side)
        flat = Vector((rest.x if x is None else x, rest.y + y, self.ankle_h))
        if abs(pitch) < 1e-9:
            return flat
        pivot = Vector((flat.x, flat.y + self.heel, 0.0)) if pitch > 0 else Vector((flat.x, flat.y - self.toe, 0.0))
        return pivot + qx(-pitch) @ (flat - pivot)

    def reach_z(self, pose, side, target, k=0.985):
        """The highest hips z offset (with the pose's other hips settings) at which the leg still
        reaches `target` while bent a little."""
        hip = self.hip_joint(pose, side) - Vector((0.0, 0.0, pose.hips_offset.z))
        horizontal = (target.x - hip.x) ** 2 + (target.y - hip.y) ** 2
        r = k * self.leg
        if horizontal >= r * r:
            return -self.leg
        return target.z + math.sqrt(r * r - horizontal) - hip.z


class Pose:
    """One frame of a humanoid clip. Rotations are quaternions in armature axes relative to the
    parent bone; legs are IK targets (ankle position, foot orientation) in armature space."""

    def __init__(self):
        self.hips_offset = Vector()
        self.hips = Quaternion()
        self.spine = Quaternion()
        self.chest = Quaternion()
        self.neck = Quaternion()
        self.head = Quaternion()
        self.arms = {"L": (Quaternion(), Quaternion()), "R": (Quaternion(), Quaternion())}
        self.legs = {"L": None, "R": None}   # (ankle target, foot rotation)

    def arm(self, side, shoulder, elbow_bend=0.0):
        """Set an arm from left-side values: shoulder rotation, elbow bend in degrees (forward)."""
        elbow = qx(-elbow_bend)
        self.arms[side] = (shoulder, elbow) if side == "L" else (mirror(shoulder), mirror(elbow))

    def leg(self, side, ankle, pitch=0.0):
        self.legs[side] = (ankle, qx(-pitch))


def _aim(rest: Quaternion, direction: Vector, x_axis: Vector) -> Quaternion:
    """Armature-space rotation that turns a bone from rest to point along `direction`, its X axis
    towards `x_axis` (the hinge axis)."""
    y0, x0 = rest @ Y, rest @ X
    y1 = direction.normalized()
    x1 = (x_axis - y1 * x_axis.dot(y1)).normalized()
    m0 = Matrix((x0, y0, x0.cross(y0))).transposed()
    m1 = Matrix((x1, y1, x1.cross(y1))).transposed()
    return (m1 @ m0.transposed()).to_quaternion()


def solve(r: Rig, pose: Pose):
    """Bone-local rotations {bone: Quaternion} and the hips location for a pose."""
    w = {"root": Quaternion(), "hips": pose.hips}
    for name, rel in (("spine", pose.spine), ("chest", pose.chest), ("neck", pose.neck), ("head", pose.head)):
        w[name] = w[rigging.HUMANOID_PARENTS[name]] @ rel
    for side in "LR":
        shoulder, elbow = pose.arms[side]
        w[f"upper_arm_{side}"] = w["chest"] @ shoulder
        w[f"lower_arm_{side}"] = w[f"upper_arm_{side}"] @ elbow
        thigh, shin, foot = f"upper_leg_{side}", f"lower_leg_{side}", f"foot_{side}"
        target, foot_rot = pose.legs[side] or (r.ankle(side), Quaternion())
        floor = -min((foot_rot @ p).z for p in r.sole)  # the sole never goes below the origin
        if target.z < floor:
            target = Vector((target.x, target.y, floor))
        hip = r.hip_joint(pose, side)
        to = target - hip
        d = min(max(to.length, abs(r.l1 - r.l2) + 1e-4), 0.9999 * r.leg)
        u = to.normalized()
        pole = pose.hips @ Vector((0.0, -1.0, 0.0))          # knees point forward
        pole = (pole - u * pole.dot(u)).normalized()
        cos_a = (r.l1 ** 2 + d * d - r.l2 ** 2) / (2 * r.l1 * d)
        a = math.acos(min(1.0, max(-1.0, cos_a)))
        knee = hip + (u * math.cos(a) + pole * math.sin(a)) * r.l1
        hinge = pole.cross(u)
        w[thigh] = _aim(r.rest[thigh], knee - hip, hinge)
        w[shin] = _aim(r.rest[shin], hip + u * d - knee, hinge)
        w[foot] = foot_rot
    local = {}
    for name, parent in rigging.HUMANOID_PARENTS.items():
        if parent is None:
            continue
        rest = r.rest[name]
        local[name] = rest.inverted() @ (w[parent].inverted() @ w[name]) @ rest
    return local, r.rest["hips"].inverted() @ pose.hips_offset


def _humanoid_channels(r: Rig, pose_at, seconds, n):
    """{(data_path, index, group): [values]} sampled at n + 1 evenly spaced times over `seconds`."""
    channels = {}
    previous = {}
    for i in range(n + 1):
        local, loc = solve(r, pose_at(seconds * i / n))
        for name, q in local.items():
            q = q.normalized()
            if name in previous and previous[name].dot(q) < 0:
                q = -q  # stay on one hemisphere so keys interpolate the short way
            previous[name] = q
            path = f'pose.bones["{name}"].rotation_quaternion'
            for index in range(4):
                channels.setdefault((path, index, name), []).append(q[index])
        for index in range(3):
            channels.setdefault(('pose.bones["hips"].location', index, "hips"), []).append(loc[index])
    return channels


# ------------------------------------------------------------------ humanoid clips


def _params(speed, stride, bounce, **extra):
    return {"speed": speed, "stride": stride, "bounce": bounce, **extra}


def _tempo(description="Tempo (1 = natural, 2 = twice as fast)"):
    return number(1.0, 0.25, 3.0, description)


@clip_kind("idle", "Idle", "humanoid", True,
           "Standing: breathing, a slow weight shift and a glance around. The feet stay planted. 4 s loop.",
           _params(_tempo(), number(1.0, 0.0, 2.0, "Size of the weight shift and glance"),
                   number(1.0, 0.0, 2.0, "Depth of the breathing")))
def _idle(r, p):
    seconds = 4.0 / p["speed"]
    s, b, L = p["stride"], p["bounce"], r.leg

    def pose(t):
        a = 2 * math.pi * t / seconds
        breath = 0.5 - 0.5 * math.cos(2 * a)
        P = Pose()
        P.hips_offset = Vector((0.012 * r.height * s * math.sin(a), 0.0, -(0.015 + 0.012 * b * breath) * L))
        P.hips = body(pitch=1.0, tilt=-2.0 * s * math.sin(a))
        P.spine = body(pitch=0.5, tilt=2.5 * s * math.sin(a))
        P.chest = body(pitch=-1.5 * b * breath)
        P.neck = body(yaw=4.0 * s * math.sin(a + 0.6))
        P.head = body(pitch=1.5 * math.sin(2 * a) + 1.5 * b * breath, yaw=5.0 * s * math.sin(a + 0.6))
        for side, phase in (("L", 0.5), ("R", 2.1)):
            P.arm(side, limb(fwd=2.0 + 2.5 * s * math.sin(a + phase), out=3.0 + 1.5 * b * breath),
                  10.0 + 4.0 * breath)
            P.leg(side, r.ankle(side))
        return P
    return seconds, pose, {}


def _gait(r, p, run):
    """Walk and run: feet on two-bone IK following a stance/swing cycle, the hips as high as the legs
    allow (that gives the natural bob), counter-rotating shoulders and swinging arms."""
    v, L, s = p["speed"], r.leg, p["stride"]
    # Stride length grows with speed (Alexander: stride / leg = 2.3 (v^2 / g L)^0.3).
    rel = 2.3 * (v * v / (9.81 * L)) ** 0.3 * s
    cycle = min(rel, 3.0 if run else 1.9) * L
    seconds = cycle / v
    duty = 0.36 if run else 0.6                     # share of the cycle a foot is on the ground
    travel = duty * cycle
    lift = (0.4 if run else 0.12) * L * min(1.4, 0.6 + 0.4 * s)
    strike, toe_off = (8.0, 35.0) if run else (14.0, 22.0)
    tangent = travel * (1 - duty) / duty

    def foot(side, ph):
        ph %= 1.0
        if ph < duty:
            u = ph / duty
            pitch = strike * (1 - smoothstep(0.0, 0.15, u)) - toe_off * smoothstep(0.65, 1.0, u)
            return r.planted(side, -travel / 2 + travel * u, pitch), pitch
        u = (ph - duty) / (1 - duty)
        a0, a1 = r.planted(side, travel / 2, -toe_off), r.planted(side, -travel / 2, strike)
        h00, h10, h01, h11 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u, -2 * u**3 + 3 * u**2, u**3 - u**2
        yc = h00 * travel / 2 + h10 * tangent - h01 * travel / 2 + h11 * tangent
        e = _ease(u)
        y = yc + _lerp(a0.y - travel / 2, a1.y + travel / 2, e)
        bump = math.sin(math.pi * u ** 0.7) if run else math.sin(math.pi * u)
        z = _lerp(a0.z, a1.z, e) + lift * bump
        pitch = _lerp(-toe_off, strike, e) - (25.0 if run else 8.0) * math.sin(math.pi * u)
        return Vector((a0.x, y, z)), pitch

    sway = (0.006 if run else 0.012) * r.height * s
    mid = duty / 2  # left foot mid-stance

    def base(t):
        ph = t / seconds
        a = 2 * math.pi * ph
        c, m = math.cos(a), math.cos(a - 2 * math.pi * mid)
        P = Pose()
        P.hips_offset = Vector((sway * m, 0.0, 0.0))
        if run:
            P.hips = body(pitch=8.0, tilt=-4.0 * m, yaw=-8.0 * s * c)
            P.spine = body(pitch=4.0, tilt=2.0 * m, yaw=7.0 * s * c)
            P.chest = body(pitch=3.0, yaw=8.0 * s * c)
            P.neck = body(pitch=-7.0, yaw=-3.0 * s * c)
            P.head = body(pitch=-6.0, yaw=-3.0 * s * c)
            swing, bend = 38.0 * s, 88.0
        else:
            P.hips = body(pitch=3.0, tilt=-3.0 * m, yaw=-5.0 * s * c)
            P.spine = body(pitch=1.0, tilt=2.5 * m, yaw=4.0 * s * c)
            P.chest = body(pitch=-1.0, yaw=5.0 * s * c)
            P.neck = body(yaw=-2.0 * s * c)
            P.head = body(pitch=-2.0, yaw=-2.0 * s * c)
            swing, bend = 20.0 * s, 15.0
        for side, sign in (("L", -1.0), ("R", 1.0)):
            fwd = sign * swing * c          # each arm swings with the opposite leg
            extra = (12.0 if run else 15.0) * max(0.0, fwd) / max(swing, 1e-6)
            P.arm(side, limb(fwd=fwd + (8.0 if run else 0.0), out=9.0 if run else 6.0), bend + extra)
            P.leg(side, *foot(side, ph + (0.0 if side == "L" else 0.5)))
        return P

    def highest(P):
        return min(r.reach_z(P, side, P.legs[side][0]) for side in "LR") - 0.004 * L

    samples = [highest(base(seconds * i / 96)) for i in range(96)]
    lo, hi = min(samples), max(samples)
    b = p["bounce"]

    def pose(t):
        P = base(t)
        z = highest(P)
        if run:
            ph = t / seconds
            z = min(z, hi - 0.06 * L * b * (0.5 + 0.5 * math.cos(4 * math.pi * (ph - mid))))
        elif b <= 1.0:
            z = lo + b * (z - lo)
        else:
            z = hi - b * (hi - z)
        P.hips_offset.z = z
        return P
    return seconds, pose, {"ground_speed": round(v, 4), "stride_length": round(cycle, 4)}


@clip_kind("walk", "Walk", "humanoid", True,
           "Walk cycle in place. The feet move back at `speed` m/s while planted: give CharacterController.speed "
           "the same value, or play it at Animator.speed = controller speed / clip speed.",
           _params(number(1.5, 0.3, 3.5, "Ground speed in m/s that the planted feet match"),
                   number(1.0, 0.5, 1.5, "Stride length (1 = natural for the speed; longer strides, slower steps)"),
                   number(1.0, 0.0, 2.0, "Hip bob (1 = what the legs give, 0 = level)")))
def _walk(r, p):
    return _gait(r, p, run=False)


@clip_kind("run", "Run", "humanoid", True,
           "Run cycle in place with flight phases, forward lean and pumping arms. Feet match `speed` m/s.",
           _params(number(4.0, 1.5, 9.0, "Ground speed in m/s that the planted feet match"),
                   number(1.0, 0.5, 1.4, "Stride length (1 = natural for the speed)"),
                   number(1.0, 0.0, 2.0, "Vertical bounce")))
def _run(r, p):
    return _gait(r, p, run=True)


# Key poses for jumps: crouch (hips down), rise (hips up, in leg lengths), air (0 = feet planted,
# 1 = feet hang under the hips), tuck (knees up), lean (degrees), arms (forward swing, out, elbow
# bend), toes (heel off the ground / toes pointed, degrees), head (pitch).
STAND = dict(crouch=0.0, rise=0.0, air=0.0, tuck=0.0, lean=0.0, fwd=0.0, out=6.0, elbow=12.0, toes=0.0, head=0.0)
CROUCH = dict(STAND, crouch=1.0, lean=30.0, fwd=-45.0, out=12.0, elbow=25.0, head=-10.0)
PUSH = dict(STAND, rise=0.05, lean=6.0, fwd=150.0, out=22.0, elbow=15.0, toes=40.0, head=-4.0)
AIR = dict(STAND, air=1.0, tuck=0.45, lean=4.0, fwd=120.0, out=35.0, elbow=30.0, toes=25.0)
LAND = dict(STAND, crouch=0.85, lean=26.0, fwd=35.0, out=30.0, elbow=28.0, head=-8.0)
SETTLE = dict(STAND, crouch=0.35, lean=10.0, fwd=10.0, out=14.0, elbow=18.0, head=-3.0)


def _jump_pose(r, p, k):
    L = r.leg
    crouch = k["crouch"] * p["bounce"]
    swing = p["stride"]
    P = Pose()
    P.hips_offset = Vector((0.0, 0.1 * L * crouch, (-0.3 * crouch + k["rise"]) * L))
    lean = k["lean"] * min(1.5, 0.4 + 0.6 * p["bounce"])
    P.hips = body(pitch=0.5 * lean)
    P.spine = body(pitch=0.3 * lean)
    P.chest = body(pitch=0.2 * lean)
    P.neck = body(pitch=-0.4 * lean + k["head"])
    P.head = body(pitch=-0.3 * lean)
    for side in "LR":
        P.arm(side, limb(fwd=k["fwd"] * swing, out=k["out"]), k["elbow"])
        tuck = k["tuck"] + (0.12 * k["air"] if side == "R" else 0.0)
        ground = r.planted(side, 0.0, -k["toes"])
        hanging = r.hip_joint(P, side) + Vector((0.0, (-0.32 * tuck + 0.04 * (1 - tuck)) * L, -(0.96 - 0.5 * tuck) * L))
        P.leg(side, ground.lerp(hanging, k["air"]), -k["toes"])
    return P


def _keyed(r, p, keys, seconds):
    """A one-shot clip through key poses [(time fraction, pose dict)], eased between them."""
    def pose(t):
        f = t / seconds
        for (t0, k0), (t1, k1) in zip(keys, keys[1:]):
            if f <= t1:
                e = smoothstep(t0, t1, f)
                return _jump_pose(r, p, {key: _lerp(k0[key], k1[key], e) for key in k0})
        return _jump_pose(r, p, keys[-1][1])
    return pose


_JUMP_PARAMS = _params(_tempo(), number(1.0, 0.0, 2.0, "Arm swing"), number(1.0, 0.0, 1.4, "Crouch depth"))


@clip_kind("jump", "Jump", "humanoid", False,
           "A whole jump in place: crouch, push off, tuck in the air, land and stand up (1.25 s, play once).",
           _JUMP_PARAMS)
def _jump(r, p):
    seconds = 1.25 / p["speed"]
    air2 = dict(AIR, tuck=0.55, fwd=100.0, out=40.0)
    keys = [(0.0, STAND), (0.2, CROUCH), (0.3, PUSH), (0.42, AIR), (0.62, air2), (0.69, LAND), (0.82, SETTLE),
            (1.0, STAND)]
    return seconds, _keyed(r, p, keys, seconds), {}


@clip_kind("jump_start", "JumpStart", "humanoid", False,
           "Take-off: crouch and push off into the air pose (0.42 s, play once, then JumpAir).", _JUMP_PARAMS)
def _jump_start(r, p):
    seconds = 0.42 / p["speed"]
    keys = [(0.0, STAND), (0.48, CROUCH), (0.76, PUSH), (1.0, AIR)]
    return seconds, _keyed(r, p, keys, seconds), {}


@clip_kind("jump_air", "JumpAir", "humanoid", True,
           "Airborne loop: knees tucked, arms up and drifting (1 s loop; play while in the air).", _JUMP_PARAMS)
def _jump_air(r, p):
    seconds = 1.0 / p["speed"]

    def pose(t):
        a = 2 * math.pi * t / seconds
        k = dict(AIR, tuck=AIR["tuck"] + 0.08 * math.sin(a), fwd=AIR["fwd"] + 8.0 * math.sin(a + 1.0),
                 out=AIR["out"] + 5.0 * math.sin(a), lean=AIR["lean"] + 2.0 * math.sin(a))
        return _jump_pose(r, p, k)
    return seconds, pose, {}


@clip_kind("land", "Land", "humanoid", False,
           "Landing from the air pose: impact crouch, then stand up (0.5 s, play once, then Idle).", _JUMP_PARAMS)
def _land(r, p):
    seconds = 0.5 / p["speed"]
    keys = [(0.0, AIR), (0.16, LAND), (0.5, SETTLE), (1.0, STAND)]
    return seconds, _keyed(r, p, keys, seconds), {}


@clip_kind("wave", "Wave", "humanoid", True,
           "Standing and waving one hand above the shoulder (1.2 s loop: two waves).",
           _params(_tempo(), number(1.0, 0.0, 2.0, "How far the hand waves"), number(1.0, 0.0, 2.0, "Body bob"),
                   arm=choice("right", ("right", "left"), "Which arm waves")))
def _wave(r, p):
    seconds = 1.2 / p["speed"]
    waving = "R" if p["arm"] == "right" else "L"
    other = "L" if waving == "R" else "R"
    lean = -1.0 if waving == "L" else 1.0  # lean away from the raised arm (tilt > 0 leans left)

    def pose(t):
        a = 2 * math.pi * t / seconds
        P = Pose()
        P.hips_offset = Vector((0.0, 0.0, -(0.015 + 0.006 * p["bounce"] * (0.5 - 0.5 * math.cos(2 * a))) * r.leg))
        P.spine = body(tilt=-1.5 * lean)
        P.chest = body(tilt=-2.0 * lean, pitch=-1.0)
        P.head = body(tilt=4.0 * lean * 0.5, yaw=0.0, pitch=-2.0 + 1.5 * math.sin(2 * a))
        # Twist the upper arm so the elbow hinges in the frontal plane, raise it to the side, and
        # swing the forearm across above the shoulder.
        P.arm(waving, qy(-(100.0 + 4.0 * math.sin(2 * a))) @ qz(90.0), 88.0 + 26.0 * p["stride"] * math.sin(2 * a))
        P.arm(other, limb(fwd=2.0, out=4.0), 12.0)
        for side in "LR":
            P.leg(side, r.ankle(side))
        return P
    return seconds, pose, {}


@clip_kind("fall", "Fall", "humanoid", True,
           "Falling loop in place: arms flailing overhead, legs kicking, looking down (0.8 s loop).",
           _params(_tempo("Flailing tempo"), number(1.0, 0.0, 2.0, "Flailing size"),
                   number(1.0, 0.0, 2.0, "Back arch")))
def _fall(r, p):
    seconds = 0.8 / p["speed"]
    s, b, L = p["stride"], p["bounce"], r.leg

    def pose(t):
        a = 2 * math.pi * t / seconds
        P = Pose()
        P.hips = body(pitch=-8.0 * b + 3.0 * math.sin(a))
        P.spine = body(pitch=-6.0 * b)
        P.chest = body(pitch=-4.0 * b + 3.0 * math.sin(a + 0.5))
        P.neck = body(pitch=10.0 + 4.0 * b)
        P.head = body(pitch=12.0, yaw=5.0 * math.sin(a))
        for side, phase in (("L", 0.0), ("R", math.pi)):
            P.arm(side, limb(fwd=140.0 + 25.0 * s * math.sin(a + phase), out=55.0 + 18.0 * s * math.sin(2 * a + phase)),
                  25.0 + 15.0 * math.sin(a + phase + 1.0))
            hip = r.hip_joint(P, side)
            P.leg(side, hip + Vector((0.0, -0.18 * L * s * math.sin(a + phase), -L * (0.8 - 0.12 * s * math.cos(a + phase)))),
                  -20.0)
        return P
    return seconds, pose, {}


# ------------------------------------------------------------------ object clips


class Motion:
    """One frame of an object clip, around the object's own placement: loc is an offset in the
    parent's axes, rot turns about the origin in the parent's axes, local turns in the object's
    own axes (hinges), scale multiplies."""

    def __init__(self, loc=None, rot=None, local=None, scale=None):
        self.loc, self.rot, self.local, self.scale = loc, rot, local, scale


AXIS = {"X": X, "Y": Y, "Z": Z}


@clip_kind("spin", "spin", "object", True,
           "Turns around an axis through the origin, one full turn per loop (coins, pickups, fans).",
           {"speed": number(0.5, 0.05, 5.0, "Turns per second"),
            "axis": choice("Z", ("X", "Y", "Z"), "Axis (in the parent's axes)"),
            "reverse": {"type": "boolean", "default": False, "description": "Turn clockwise seen from above"}})
def _spin(p):
    seconds = 1.0 / p["speed"]
    sign = -1.0 if p["reverse"] else 1.0
    axis = AXIS[p["axis"]]
    return seconds, lambda t: Motion(rot=Quaternion(axis, sign * 2 * math.pi * t / seconds)), {}


@clip_kind("bob", "bob", "object", True, "Floats up and down around its place (pickups, buoys).",
           {"height": number(0.15, 0.01, 5.0, "How far it moves up and down, metres"),
            "speed": number(0.6, 0.05, 5.0, "Bobs per second")})
def _bob(p):
    seconds = 1.0 / p["speed"]
    return seconds, lambda t: Motion(loc=Vector((0.0, 0.0, p["height"] * math.sin(2 * math.pi * t / seconds)))), {}


@clip_kind("pulse", "pulse", "object", True, "Grows and shrinks around its origin (glowing pickups, hearts, buttons).",
           {"amount": number(0.12, 0.01, 1.0, "Scale change (0.12 = 12% bigger and smaller)"),
            "speed": number(1.0, 0.05, 6.0, "Pulses per second")})
def _pulse(p):
    seconds = 1.0 / p["speed"]

    def motion(t):
        f = 1.0 + p["amount"] * math.sin(2 * math.pi * t / seconds)
        return Motion(scale=Vector((f, f, f)))
    return seconds, motion, {}


_DOOR = {"angle": number(100.0, -180.0, 180.0, "Opening angle in degrees around the door's own Z axis "
                                               "(its origin must be on the hinge); negative opens the other way"),
         "seconds": number(1.0, 0.1, 10.0, "Duration"),
         "axis": choice("Z", ("X", "Y", "Z"), "Hinge axis in the object's own axes: Z for doors, X for levers, "
                                              "lids and trapdoors")}


def _hinge(p, amount):
    return Quaternion(AXIS[p["axis"]], math.radians(p["angle"] * amount))


@clip_kind("door_open", "door_open", "object", False,
           "Swings open around the object's local Z axis (or `axis`) through its origin, easing to a stop "
           "(play once).", _DOOR)
def _door_open(p):
    def motion(t):
        u = t / p["seconds"]
        return Motion(local=_hinge(p, 1 - (1 - u) ** 3))
    return p["seconds"], motion, {}


@clip_kind("door_close", "door_close", "object", False,
           "Swings shut from `angle` back to its placed rotation (play once).", _DOOR)
def _door_close(p):
    def motion(t):
        u = t / p["seconds"]
        return Motion(local=_hinge(p, 1 - u * u * (3 - 2 * u)))
    return p["seconds"], motion, {}


@clip_kind("sway", "sway", "object", True,
           "Sways gently around its origin, as in wind (trees and plants: their origin is at the base).",
           {"angle": number(3.0, 0.1, 30.0, "Largest tilt in degrees"),
            "speed": number(0.25, 0.02, 3.0, "Sways per second")})
def _sway(p):
    seconds = 1.0 / p["speed"]

    def motion(t):
        a = 2 * math.pi * t / seconds
        return Motion(rot=qy(0.6 * p["angle"] * math.sin(2 * a + 1.0)) @ qx(p["angle"] * math.sin(a)))
    return seconds, motion, {}


@clip_kind("hover", "hover", "object", True,
           "Hovers: drifts up and down and rocks a little (drones, floating platforms, spirits).",
           {"height": number(0.08, 0.0, 5.0, "How far it drifts up and down, metres"),
            "tilt": number(4.0, 0.0, 30.0, "How far it rocks, degrees"),
            "speed": number(0.35, 0.02, 3.0, "Cycles per second")})
def _hover(p):
    seconds = 1.0 / p["speed"]

    def motion(t):
        a = 2 * math.pi * t / seconds
        rock = qy(0.7 * p["tilt"] * math.sin(a + math.pi)) @ qx(p["tilt"] * math.sin(a + math.pi / 2))
        return Motion(loc=Vector((0.0, 0.0, p["height"] * math.sin(a))), rot=rock)
    return seconds, motion, {}


def _object_base(obj, skip_track=None):
    """The transform object clips animate around: the current one, except that channels another
    generated clip animates come from that clip's recorded base (the current value is animated)."""
    base = {"location": list(obj.location), "rotation": list(obj.matrix_basis.to_quaternion()),
            "scale": list(obj.scale)}
    ad = obj.animation_data
    if ad is not None:
        for track in ad.nla_tracks:
            if track.name == skip_track or len(track.strips) != 1 or track.strips[0].action is None:
                continue
            data = _clip_data(track.strips[0].action)
            for channel, value in (data or {}).get("base", {}).items():
                base[channel] = value
    return base


def _object_channels(obj, motions, base, seconds, n):
    loc0, rot0, scale0 = Vector(base["location"]), Quaternion(base["rotation"]), Vector(base["scale"])
    frames = [[m(seconds * i / n) for m in motions] for i in range(n + 1)]
    moves, sizes = (any(getattr(m, attr) is not None for row in frames for m in row) for attr in ("loc", "scale"))
    turns = any(m.rot is not None or m.local is not None for row in frames for m in row)
    mode = obj.rotation_mode
    if turns and mode == "AXIS_ANGLE":
        raise KitError(f"{obj.name} uses axis-angle rotation; set its rotation mode to XYZ Euler or Quaternion")
    channels, recorded = {}, {}
    previous = None
    for row in frames:
        loc, rot, local, scale = Vector(), Quaternion(), Quaternion(), Vector((1.0, 1.0, 1.0))
        for m in row:
            if m.loc is not None:
                loc += m.loc
            if m.rot is not None:
                rot = m.rot @ rot
            if m.local is not None:
                local = local @ m.local
            if m.scale is not None:
                scale = Vector((scale.x * m.scale.x, scale.y * m.scale.y, scale.z * m.scale.z))
        if moves:
            for i, value in enumerate(loc0 + loc):
                channels.setdefault(("location", i, "Object Transforms"), []).append(value)
            recorded["location"] = list(loc0)
        if turns:
            q = rot @ rot0 @ local
            if mode == "QUATERNION":
                if previous is not None and previous.dot(q) < 0:
                    q = -q
                previous = q
                values, path = list(q), "rotation_quaternion"
            else:
                e = q.to_euler(mode, previous) if previous is not None else q.to_euler(mode)
                previous = e
                values, path = list(e), "rotation_euler"
            for i, value in enumerate(values):
                channels.setdefault((path, i, "Object Transforms"), []).append(value)
            recorded["rotation"] = list(rot0)
        if sizes:
            for i, value in enumerate(Vector((scale0.x * scale.x, scale0.y * scale.y, scale0.z * scale.z))):
                channels.setdefault(("scale", i, "Object Transforms"), []).append(value)
            recorded["scale"] = list(scale0)
    return channels, recorded


# ------------------------------------------------------------------ storing clips


def _clip_data(action):
    try:
        return json.loads(action.get(CLIP_PROP, ""))
    except (TypeError, ValueError):
        return None


def _signature(channels, n, loop):
    rows = [[path, index, [round(v, 5) for v in values]] for (path, index, _), values in sorted(channels.items())]
    return hashlib.sha1(json.dumps([n, loop, rows]).encode()).hexdigest()


def _base_name(name):
    return re.sub(r"\.\d{3}$", "", name)


def _users(action):
    """Objects that use `action` as their active action or on an NLA strip."""
    found = []
    for obj in bpy.data.objects:
        ad = obj.animation_data
        if ad is None:
            continue
        if ad.action == action or any(s.action == action for t in ad.nla_tracks for s in t.strips):
            found.append(obj)
    return found


def _channelbag(action, slot):
    layer = action.layers[0] if action.layers else action.layers.new("Layer")
    strip = layer.strips[0] if layer.strips else layer.strips.new(type="KEYFRAME")
    return strip.channelbag(slot, ensure=True)


def _write(action, slot, channels, n):
    bag = _channelbag(action, slot)
    bag.fcurves.clear()
    for (path, index, group), values in channels.items():
        fc = bag.fcurves.new(path, index=index, group_name=group)
        fc.keyframe_points.add(len(values))
        fc.keyframe_points.foreach_set("co", [c for i, v in enumerate(values) for c in (1.0 + i, v)])
        fc.keyframe_points.foreach_set("interpolation", [LINEAR] * len(values))
        fc.update()
    action.use_frame_range = True
    action.frame_start, action.frame_end = 1.0, 1.0 + n


def _store(obj, clip, meta, channels, n, activate):
    """Put the clip on obj: reuse an identical action, re-key obj's own one, or make a new one."""
    meta["signature"] = _signature(channels, n, meta["loop"])
    ad = obj.animation_data or obj.animation_data_create()
    track = ad.nla_tracks.get(clip)
    old = track.strips[0].action if track is not None and len(track.strips) == 1 else None
    if track is not None and (old is None or _clip_data(old) is None):
        raise KitError(f"{obj.name} already has a clip {clip!r} that the animation library did not make; "
                       "pass another name, or remove_clip() it first")
    action = None
    for candidate in ([old] if old is not None else []) + list(bpy.data.actions):
        data = _clip_data(candidate)
        if data and data.get("signature") == meta["signature"] and _base_name(candidate.name) == clip:
            action = candidate
            break
    if action is None:
        if old is not None and _users(old) == [obj]:
            action = old
        else:
            action = bpy.data.actions.new(clip)
        slot = action.slots[0] if len(action.slots) else action.slots.new(id_type="OBJECT", name=obj.name)
        _write(action, slot, channels, n)
        action.use_cyclic = meta["loop"]
        action[CLIP_PROP] = json.dumps(meta, sort_keys=True)
    slot = action.slots[0]
    if track is None:
        track = ad.nla_tracks.new()
        track.name = clip
    for strip in list(track.strips):
        track.strips.remove(strip)
    strip = track.strips.new(clip, int(action.frame_range[0]), action)
    strip.action_slot = slot
    track.mute = True  # the exporter still writes it; muted, it does not blend into the viewport
    if activate or (old is not None and ad.action == old and old != action):
        ad.action = action
        ad.action_slot = slot
    if old is not None and old != action and old.users == 0:
        bpy.data.actions.remove(old)
    return action


def _target(obj, spec):
    if spec.target == "humanoid":
        arm = obj
        if obj.type == "MESH" and obj.parent is not None and obj.parent.type == "ARMATURE":
            arm = obj.parent
        if arm.type != "ARMATURE":
            raise KitError(f"{spec.clip} is a humanoid clip: give it the armature of a character_rigged kit, "
                           f"not {obj.name} ({obj.type}). Object clips: spin, bob, pulse, door_open, sway, hover")
        return arm
    return obj


def add_clip(obj, kind, params=None, *, name=None, activate=True):
    """Generate a clip on `obj` and return its action (see the module docstring)."""
    if obj is None or not isinstance(obj, bpy.types.Object):
        raise KitError(f"add_clip needs an object, got {obj!r}")
    specs = [_kind(k) for k in ([kind] if isinstance(kind, str) else list(kind or []))]
    if not specs:
        raise KitError("add_clip needs a clip kind")
    if len(specs) > 1 and any(s.target != "object" or not s.loop for s in specs):
        raise KitError("Only looping object clips (spin, bob, pulse, sway, hover) can be layered into one clip")
    if len({s.kind for s in specs}) != len(specs):
        raise KitError("Each kind can appear once in a layered clip")
    clip = name if name is not None else "_".join(s.clip for s in specs)
    if not isinstance(clip, str) or not 1 <= len(clip) <= 60:
        raise KitError(f"Clip name must be 1 to 60 characters, got {clip!r}")
    if len(specs) == 1:
        resolved = [validate(specs[0].kind, specs[0].params, params)]
        stored = resolved[0]
    else:
        params = params or {}
        if not isinstance(params, dict) or set(params) - {s.kind for s in specs}:
            raise KitError(f"Params for a layered clip are {{kind: params}} for {', '.join(s.kind for s in specs)}")
        resolved = [validate(s.kind, s.params, params.get(s.kind)) for s in specs]
        stored = {s.kind: r for s, r in zip(specs, resolved)}
    obj = _target(obj, specs[0])
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base

    meta = {"kind": specs[0].kind if len(specs) == 1 else [s.kind for s in specs], "params": stored,
            "loop": specs[0].loop}
    if specs[0].target == "humanoid":
        r = Rig(obj)
        seconds, pose_at, extra = specs[0].make(r, resolved[0])
        n = max(1, round(seconds * fps))
        for bone in rigging.HUMANOID_BONES:
            obj.pose.bones[bone].rotation_mode = "QUATERNION"
        channels = _humanoid_channels(r, pose_at, seconds, n)
    else:
        made = [s.make(rp) for s, rp in zip(specs, resolved)]
        seconds = max(period for period, _, _ in made)
        motions = []
        for period, motion, _ in made:
            # Layers are stretched a little so each fits whole cycles into the longest: it loops.
            stretch = seconds / (max(1, round(seconds / period)) * period)
            motions.append(lambda t, motion=motion, stretch=stretch: motion(t / stretch))
        extra = {}
        n = max(1, round(seconds * fps))
        base = _object_base(obj, skip_track=clip)
        channels, meta["base"] = _object_channels(obj, motions, base, seconds, n)
    meta.update(extra, seconds=round(n / fps, 6), frames=n, fps=fps)
    return _store(obj, clip, meta, channels, n, activate)


def add_clips(obj, kinds=None, *, activate=True):
    """Add several clips with default params (kinds None: the standard humanoid set). The first one
    becomes the active action."""
    kinds = list(HUMANOID_SET if kinds is None else kinds)
    actions = [add_clip(obj, kind, activate=False) for kind in kinds]
    if actions and activate:
        preview(obj, actions[0], fit=False)
    return actions


def clips(obj) -> list[dict]:
    """Every clip on the object: its single-strip NLA tracks, then an active action on no track."""
    if obj.type == "MESH" and obj.parent is not None and obj.parent.type == "ARMATURE" and obj.animation_data is None:
        obj = obj.parent
    ad = obj.animation_data
    out, seen = [], set()
    if ad is None:
        return out
    entries = [(t.name, t.strips[0].action) for t in ad.nla_tracks if len(t.strips) == 1 and t.strips[0].action]
    if ad.action is not None and all(a != ad.action for _, a in entries):
        entries.append((ad.action.name, ad.action))
    for clip, action in entries:
        if action in seen:
            continue
        seen.add(action)
        data = _clip_data(action) or {}
        start, end = action.frame_range
        entry = {"name": clip, "action": action.name, "kind": data.get("kind"), "params": data.get("params"),
                 "loop": data.get("loop", bool(action.use_cyclic)), "frames": int(round(end - start)),
                 "seconds": data.get("seconds"), "active": ad.action == action}
        for key in ("ground_speed", "stride_length"):
            if key in data:
                entry[key] = data[key]
        out.append(entry)
    return out


def _find(obj, name):
    ad = obj.animation_data
    if ad is not None:
        track = ad.nla_tracks.get(name)
        if track is not None and len(track.strips) == 1:
            return track, track.strips[0].action
        for track in ad.nla_tracks:
            if len(track.strips) == 1 and track.strips[0].action and track.strips[0].action.name == name:
                return track, track.strips[0].action
        if ad.action is not None and ad.action.name == name:
            return None, ad.action
    raise KitError(f"{obj.name} has no clip {name!r}. Its clips: {', '.join(c['name'] for c in clips(obj)) or 'none'}")


def remove_clip(obj, name) -> None:
    if obj.type == "MESH" and obj.parent is not None and obj.parent.type == "ARMATURE" and obj.animation_data is None:
        obj = obj.parent
    track, action = _find(obj, name)
    ad = obj.animation_data
    if track is not None:
        ad.nla_tracks.remove(track)
    if ad.action == action:
        ad.action = None
    if action.users == 0 and _clip_data(action) is not None:
        bpy.data.actions.remove(action)


def preview(obj, clip, frame=None, fit=True):
    """Make a clip (name or action) the active action; with `fit`, set the scene's frame range to it
    and go to `frame` (default: its first frame)."""
    if obj.type == "MESH" and obj.parent is not None and obj.parent.type == "ARMATURE" and obj.animation_data is None:
        obj = obj.parent
    action = clip if isinstance(clip, bpy.types.Action) else _find(obj, clip)[1]
    ad = obj.animation_data or obj.animation_data_create()
    ad.action = action
    if len(action.slots):
        ad.action_slot = action.slots[0]
    if fit:
        scene = bpy.context.scene
        start, end = (int(round(f)) for f in action.frame_range)
        scene.frame_start, scene.frame_end = start, end
        scene.frame_set(start if frame is None else int(frame))
    return action


def rebake(obj) -> list:
    """Re-key every clip the library made on obj with its stored params (for a changed rig or base)."""
    ad = obj.animation_data
    if ad is None:
        return []
    active = ad.action
    redone = []
    for entry in clips(obj):
        action = bpy.data.actions.get(entry["action"])
        data = _clip_data(action) if action else None
        if not data:
            continue
        new = add_clip(obj, data["kind"], data["params"], name=entry["name"], activate=False)
        if active == action:
            active = new
        redone.append(new)
    if active is not None and ad.action != active:
        ad.action = active
        ad.action_slot = active.slots[0] if len(active.slots) else None
    return redone
