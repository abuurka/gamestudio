# SPDX-License-Identifier: GPL-3.0-or-later
"""Armatures for rigged kits: bone layouts, building bones from code, skinning a mesh to them.

Bones are built in edit mode on a temporary object linked to the scene's master collection, so
this works in background mode, from timers and when the real armature is hidden or excluded.
Every bone's local X axis points along +X (the character's left side), so a rotation about local
X swings it forward or back in the side view.
"""

import contextlib
import json
from typing import NamedTuple

import bpy
from mathutils import Vector

from .errors import KitError

RIG_PROP = "gs_rig"  # armature data: JSON with the layout the animation library needs (see humanoid())

# The humanoid skeleton of character_rigged; the animation library drives these names.
HUMANOID_PARENTS = {
    "root": None,
    "hips": "root",
    "spine": "hips",
    "chest": "spine",
    "neck": "chest",
    "head": "neck",
    "upper_arm_L": "chest",
    "lower_arm_L": "upper_arm_L",
    "upper_arm_R": "chest",
    "lower_arm_R": "upper_arm_R",
    "upper_leg_L": "hips",
    "lower_leg_L": "upper_leg_L",
    "foot_L": "lower_leg_L",
    "upper_leg_R": "hips",
    "lower_leg_R": "upper_leg_R",
    "foot_R": "lower_leg_R",
}
HUMANOID_BONES = tuple(HUMANOID_PARENTS)


class Bone(NamedTuple):
    name: str
    head: tuple
    tail: tuple
    parent: str | None = None
    deform: bool = True


def build(armature: bpy.types.Armature, bones: list[Bone], layout: dict | None = None) -> None:
    """Replace the armature's bones with `bones` (parents listed before children) and store
    `layout` (sizes the animation library needs) in armature[RIG_PROP]."""
    armature[RIG_PROP] = json.dumps(layout or {}, sort_keys=True)
    with _edit_mode(armature):
        edit = armature.edit_bones
        for old in list(edit):
            edit.remove(old)
        made = {}
        for spec in bones:
            head, tail = Vector(spec.head), Vector(spec.tail)
            bone = edit.new(spec.name)
            bone.head, bone.tail = head, tail
            bone.align_roll(Vector((1.0, 0.0, 0.0)).cross(tail - head))  # local X = +X
            bone.use_deform = spec.deform
            if spec.parent:
                bone.parent = made[spec.parent]
                bone.use_connect = (made[spec.parent].tail - head).length < 1e-6
            made[spec.name] = bone


def skin(body: bpy.types.Object, armature_obj: bpy.types.Object, groups: list[str]) -> None:
    """Parent `body` to the armature (keeping its place), add the Armature modifier and name the
    vertex groups MeshBuilder.weigh() filled in, in index order (the weights are already stored)."""
    existing = body.vertex_groups
    for i, group in enumerate(groups):
        if i < len(existing):
            existing[i].name = group
        else:
            existing.new(name=group)
    body.parent = armature_obj
    body.matrix_parent_inverse.identity()
    mod = next((m for m in body.modifiers if m.type == "ARMATURE"), None)
    if mod is None:
        mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = armature_obj


def layout(armature: bpy.types.Armature) -> dict:
    """The JSON layout stored with the bones (rig type, sizes); {} if there is none."""
    try:
        return json.loads(armature.get(RIG_PROP, "{}"))
    except (TypeError, ValueError):
        return {}


@contextlib.contextmanager
def _edit_mode(armature):
    context = bpy.context
    scene = context.scene
    view_layer = context.view_layer or scene.view_layers[0]
    if getattr(context, "mode", "OBJECT") != "OBJECT":
        active = view_layer.objects.active
        try:
            with context.temp_override(active_object=active, object=active):
                bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError as exc:
            raise KitError(f"Switch to Object Mode first (could not leave {context.mode}: {exc})") from None
    tmp = bpy.data.objects.new("gs_rig_edit", armature)
    scene.collection.objects.link(tmp)
    previous = view_layer.objects.active
    selected = [o for o in view_layer.objects.selected]
    try:
        for obj in selected:
            obj.select_set(False)  # mode_set would take every selected armature into edit mode
        view_layer.objects.active = tmp
        tmp.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        if not armature.is_editmode:
            raise KitError("Could not enter edit mode to build the armature's bones")
        yield
    finally:
        if armature.is_editmode:
            bpy.ops.object.mode_set(mode="OBJECT")
        tmp.select_set(False)  # else the view layer lists the removed object as a selected None
        bpy.data.objects.remove(tmp)
        for obj in selected:
            try:
                obj.select_set(True)
            except (ReferenceError, RuntimeError):
                pass
        view_layer.objects.active = previous
