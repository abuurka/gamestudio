# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender Z-up -> runtime Y-up transform conversion (docs/SPEC.md §4.3).

This mirrors what the stock glTF exporter does for node TRS (see
io_scene_gltf2/blender/exp/tree.py and nodes.py in Blender 5.2):

* a node's local matrix is parent_node_world^-1 @ node_world, so Blender's
  matrix_parent_inverse never leaks into the runtime;
* camera and light nodes get a -90 degree rotation about local X appended to
  their world matrix, which also changes the local matrix of their children;
* the local matrix is decomposed in Blender space and each part is swizzled:
  position (x, y, z) -> (x, z, -y), rotation (w, x, y, z) -> (w, x, z, -y),
  scale (x, y, z) -> (x, z, y);
* a collection-instance empty bakes -instance_offset into its translation.

A test exports a scene with the real exporter and checks that these values
match every node within 1e-4.
"""

import math

from mathutils import Matrix, Quaternion, Vector

# Rotation of -90 degrees about X, applied in local space to cameras and lights.
CORRECTION = Quaternion((math.sqrt(0.5), -math.sqrt(0.5), 0.0, 0.0)).to_matrix().to_4x4()

_CORRECTED_TYPES = {"CAMERA", "LIGHT"}


def vector_to_runtime(v) -> list:
    """A Blender-space direction or position as a Y-up [x, y, z] list."""
    return [float(v[0]), float(v[2]), -float(v[1])]


def _node_world(obj) -> Matrix:
    world = obj.matrix_world.copy()
    if obj.type in _CORRECTED_TYPES:
        world = world @ CORRECTION
    return world


def local_matrix(obj) -> Matrix:
    """The object's glTF-node matrix relative to its parent node, in Blender axes."""
    world = _node_world(obj)
    if obj.parent is None:
        return world
    return _node_world(obj.parent).inverted_safe() @ world


def to_runtime_trs(obj) -> tuple:
    """(p, r, s) for an entity.transform item: Y-up, local to the parent.

    p = [x, y, z], r = [x, y, z, w] (Three.js order), s = [x, y, z].
    """
    loc, rot, sca = local_matrix(obj).decompose()
    rot.normalize()
    p = Vector((loc.x, loc.z, -loc.y))
    q = Quaternion((rot.w, rot.x, rot.z, -rot.y))
    s = Vector((sca.x, sca.z, sca.y))

    coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
    if coll is not None:
        offset = -Vector(vector_to_runtime(coll.instance_offset))
        m = Matrix.Translation(p) @ q.to_matrix().to_4x4() @ Matrix.Diagonal(s).to_4x4() @ Matrix.Translation(offset)
        p = m.translation

    return [p.x, p.y, p.z], [q.x, q.y, q.z, q.w], [s.x, s.y, s.z]


def transform_item(obj, gs_id: str, digits: int = 6) -> dict:
    """An entity.transform item ({id, p, r, s}) with values rounded for the wire."""
    p, r, s = to_runtime_trs(obj)
    return {
        "id": gs_id,
        "p": [round(v, digits) for v in p],
        "r": [round(v, digits) for v in r],
        "s": [round(v, digits) for v in s],
    }
