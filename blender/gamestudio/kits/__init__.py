# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset kits: parametric generators for game-ready assets (rocks, crates, trees, buildings, ...).

The contract other modules (the agent bridge, operators) rely on:

    catalog() -> list[dict]
        One entry per kit: {"kind": str, "label": str, "description": str,
                            "params": <JSON Schema object for create()'s params>}
    create(kind: str, params: dict | None = None, *, location=(0, 0, 0), collection=None)
        -> list[bpy.types.Object]
        Builds the asset in the current scene and returns its objects. Raises KitError (with a
        message for the user or agent) for an unknown kind or invalid params.

Also available:

    materials.presets() / materials.apply(obj, preset, **overrides)   the shared material palette
    regenerate(obj, params) -> Object     rebuild a kit object's mesh with some params changed
    kit_params(obj) -> (kind, params) | None   what a kit object was built with
    animation.add_clip(obj, kind, params=None, *, name=None) -> Action
                                          a procedural clip: humanoid idle, walk, run, jump,
                                          jump_start, jump_air, land, wave, fall (on a
                                          character_rigged armature); spin, bob, pulse, door_open,
                                          door_close, sway, hover (any object); stored for export
    animation.clips(obj) / remove_clip(obj, name) / preview(obj, name) / catalog()

Every kit builds one mesh object (several material slots) in metres, Z-up, front facing -Y, with
its origin at the bottom centre (collectible: its centre; terrain: centre at ground level), no
rotation or scale, outward normals, a UV map and closed shells. Params are validated against the
schema (unknown keys and out-of-range values raise KitError) and the same seed and params always
build the same mesh. `collection` may be a Collection, a collection name (created if missing) or
None for the active collection. The object stores its kind and params in obj["gs_kit"].

Rigged kits (character_rigged) build two objects instead: create() returns [armature, mesh]. The
armature is the root, at `location` with no rotation or scale: components such as Animator and
CharacterController go on it. The mesh is its child at the origin, skinned with an Armature
modifier and one vertex group per deforming bone. Both store obj["gs_kit"], so kit_params() and
regenerate() accept either; regenerate() rebuilds both and re-bakes the generated clips.

Assembly kits (vehicle, door, switch, weapon) return the root mesh first, then child parts named
<root name>_<suffix> (Car_Wheel_FL, Door_Leaf, Lever_Handle, Blaster_Muzzle): meshes or empties
parented to the root with their origin on their pivot and no rotation or scale, each carrying
obj["gs_kit"] and obj["gs_kit_part"] = suffix. Their clips (door_open, switch_on, spin) live on the
parts. kit_params() and regenerate() accept the root or any part; regenerate() rebuilds them all,
keeps the parts' components, renames them after the root and adds or removes parts and clips the
new params call for. The sky kit builds no mesh: it returns a sun lamp and sets the scene's world.
"""

from . import materials
from .base import catalog, create, kinds, kit_params, regenerate
from .errors import KitError

# Importing the kit modules registers their kits.
from . import (animation, characters, environment, interactive, nature, props,  # noqa: E402,F401  isort: skip
               structures, vehicles, weapons)

__all__ = ["KitError", "animation", "catalog", "create", "kinds", "kit_params", "materials", "regenerate"]


def register():
    pass


def unregister():
    pass
