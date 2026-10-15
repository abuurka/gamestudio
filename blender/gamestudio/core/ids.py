# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable entity IDs (docs/SPEC.md §2).

Every exported object carries obj["gs_id"] = "gs_" + 12 hex chars, plus
obj["gs_id_owner"] = the object name at assignment time. Blender's duplicate
operators copy both properties, so repair_duplicate_ids() decides which copy
keeps the ID: a linked object (it cannot take another), then the object tracked
by session_uid in this session, then the object whose gs_id_owner matches its
current name. Linked objects without a stored ID get one derived from their
library path (separators normalised, so every OS derives the same) and name.

Python references die on undo, so lookups go through a dict cache that is
validated on every hit and rebuilt on load, undo, redo, or a miss.
"""

import hashlib
import posixpath
import re
import secrets

import bpy
from bpy.app.handlers import persistent

from .log import get_logger

log = get_logger(__name__)

ID_PROP = "gs_id"
OWNER_PROP = "gs_id_owner"
ID_RE = re.compile(r"^gs_[0-9a-f]{12}$")

# gs_id -> Object. Entries are validated on access because undo invalidates them.
_cache: dict = {}
_cache_valid = False

# gs_id -> session_uid of the object that owns the ID in this session.
# session_uid is stable across undo but not saved, so this is session-only.
_owner_uid: dict = {}


def is_valid_id(value) -> bool:
    return isinstance(value, str) and ID_RE.match(value) is not None


def get_id(obj) -> str | None:
    """The object's gs_id, or None if it has none (or an invalid one)."""
    if obj is None:
        return None
    value = obj.get(ID_PROP)
    return value if is_valid_id(value) else None


def _id_in_use(gs_id: str) -> bool:
    if not _cache_valid:
        _rebuild_cache()
    return gs_id in _cache


def new_id() -> str:
    """A fresh random ID that no object in the file uses."""
    while True:
        gs_id = "gs_" + secrets.token_hex(6)
        if not _id_in_use(gs_id):
            return gs_id


def library_key(library) -> str:
    """A library's path as stored, with "/" separators and no "." or ".." detours: the same on every OS."""
    path = library.filepath.replace("\\", "/")
    prefix = "//" if path.startswith("//") else ""
    rest = path[len(prefix):]
    return prefix + (posixpath.normpath(rest) if rest else "")


def _linked_id(obj) -> str:
    # Linked objects cannot store a new ID (writes to linked data are lost on
    # reload), so derive a stable one from the library path and object name.
    key = f"{library_key(obj.library)}|{obj.name}".encode("utf-8")
    return "gs_" + hashlib.sha1(key).hexdigest()[:12]


def _can_write(obj) -> bool:
    return obj.library is None


def _assign(obj, gs_id: str) -> None:
    obj[ID_PROP] = gs_id
    obj[OWNER_PROP] = obj.name
    _owner_uid[gs_id] = obj.session_uid
    _cache[gs_id] = obj


def ensure_id(obj) -> str:
    """Return the object's gs_id, assigning a new one if needed."""
    gs_id = get_id(obj)
    if gs_id is not None:
        _owner_uid.setdefault(gs_id, obj.session_uid)
        return gs_id
    if not _can_write(obj):
        return _linked_id(obj)
    gs_id = new_id()
    _assign(obj, gs_id)
    return gs_id


def _pick_owner(gs_id: str, objs: list):
    """Choose which of several objects sharing gs_id keeps it."""
    # Linked objects cannot be changed, so they keep their ID; a local copy gets a new one.
    linked = sorted((o for o in objs if not _can_write(o)), key=lambda o: (library_key(o.library), o.name))
    if linked:
        return linked[0]
    tracked = _owner_uid.get(gs_id)
    if tracked is not None:
        for obj in objs:
            if obj.session_uid == tracked:
                return obj
    for obj in objs:
        if obj.get(OWNER_PROP) == obj.name:
            return obj
    return sorted(objs, key=lambda o: o.name)[0]


def _rebuild_cache() -> None:
    global _cache_valid
    groups: dict = {}
    for obj in bpy.data.objects:
        gs_id = get_id(obj)
        if gs_id is not None:
            groups.setdefault(gs_id, []).append(obj)
    _cache.clear()
    for gs_id, objs in groups.items():
        owner = objs[0] if len(objs) == 1 else _pick_owner(gs_id, objs)
        _cache[gs_id] = owner
        if len(objs) == 1:
            _owner_uid.setdefault(gs_id, owner.session_uid)
    _cache_valid = True


def invalidate_cache() -> None:
    global _cache_valid
    _cache_valid = False


def find_by_id(gs_id: str):
    """The object with this gs_id, or None. Never returns a stale reference."""
    if not is_valid_id(gs_id):
        return None
    obj = _cache.get(gs_id)
    if obj is not None and _cache_valid:
        try:
            if obj.get(ID_PROP) == gs_id:
                return obj
        except ReferenceError:
            pass
    _rebuild_cache()
    return _cache.get(gs_id)


def repair_duplicate_ids(scene=None) -> int:
    """Give every object that shares another object's gs_id a new one.

    Considers the objects of `scene`, or every object in the file when scene
    is None. Returns the number of IDs reassigned.
    """
    objs = scene.objects if scene is not None else bpy.data.objects
    groups: dict = {}
    for obj in objs:
        gs_id = get_id(obj)
        if gs_id is not None:
            groups.setdefault(gs_id, []).append(obj)

    reassigned = 0
    for gs_id, members in groups.items():
        if len(members) < 2:
            continue
        owner = _pick_owner(gs_id, members)
        for obj in members:
            if obj == owner:
                continue
            if not _can_write(obj):
                log.warning("Linked object '%s' shares ID %s and cannot be changed", obj.name, gs_id)
                continue
            fresh = new_id()
            _assign(obj, fresh)
            reassigned += 1
            log.info("Object '%s' was a copy of '%s'; assigned new ID %s", obj.name, owner.name, fresh)
        _owner_uid[gs_id] = owner.session_uid
        if _can_write(owner) and owner.get(OWNER_PROP) != owner.name:
            # The owner was renamed; record its current name for the fallback rule.
            owner[OWNER_PROP] = owner.name
        _cache[gs_id] = owner
    return reassigned


# --- Handlers ------------------------------------------------------------------


@persistent
def _on_load_post(*_args):
    _owner_uid.clear()
    _rebuild_cache()


@persistent
def _on_undo_redo(*_args):
    invalidate_cache()


_HANDLERS = (
    ("load_post", _on_load_post),
    ("undo_post", _on_undo_redo),
    ("redo_post", _on_undo_redo),
)


def register():
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)
    invalidate_cache()


def unregister():
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _cache.clear()
    _owner_uid.clear()
    invalidate_cache()
