# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI export and live link (docs/GAME-UI.md §3, §4): canvases of the open UI file are
extracted to .gamestudio/export/ui/<Canvas>.ui.json + index.json, and sent live as ui.canvas.

Saving a UI file exports all of its canvases and the index (gameui_export.export_ui_file); entity IDs
are assigned before the save, so the export leaves the saved file unchanged. Opening one (or Revert)
exports it too when its export is stale: older than the file, or written from changes never saved.

The live link works while a UI file is open, a game is connected and the active scene's "Live Link"
toggle is on (the level live link, live.py, stays off in UI files). Sources of change:

* depsgraph_update_post: an updated object marks the canvases it is in, a collection the active canvas,
  and a mesh, curve, material, node tree, image, font or action (keyframe edits) marks, when the export
  runs, the canvases with an object that uses it (it may be shared with canvases the window does not
  show). A scene update (selection, the active object, the frame) marks its canvas only when its timeline
  changed what the export writes (_timeline: frame range, rate, and the frame while it is the rest pose);
  the poll sees design size, camera, excluded collections and timeline markers (they send no update).
* Component field edits (live.mark_components_dirty calls mark_object_dirty) and the gs.gameui
  builder (mark_canvas_dirty), which may edit canvases that are not in the window.
* A cheap signature of every canvas, compared twice a second: scenes added, removed or renamed, and
  edits in canvases the window does not show (they send no depsgraph updates).
* undo/redo marks every canvas.

A marked canvas is exported again 60 ms after the last mark (at most 200 ms after the first): its file
is written and new assets copied (gameui_export.export_canvas), and when its content differs from what
the game has it is sent as `ui.canvas` with the whole canvas. A removed canvas is dropped from the
index and sent as `ui.remove`. What the game has is seeded from the exported files when the link
activates, so a game started on older files gets the canvases that changed since.

send_open() / send_close() send ui.open / ui.close ("Show in Game" and the bridge); open_after_play()
opens the edited canvas once the game plays after Play from a UI file. pump() does the work; a timer
calls it in the UI and tests call it directly.

Phase 3: a canvas with models (§11.1) exports its GLB again only when model content changed (the
export's signature); the GLB's hash is in the canvas, so such a change sends ui.canvas too. The poll
also watches the translation tables (ui/locales/*.json) and gamestudio.json: when they change they are
exported (gameui_export.export_locales) and, when that differs from what the game has, sent as
`ui.locales` {sourceLocale, locales, tables} (§11.3).
"""

import hashlib
import json
import time

import bpy
from bpy.app.handlers import persistent

from . import export, gameui, gameui_export, ids, link, project
from .log import get_logger

log = get_logger(__name__)

DEBOUNCE = 0.06
MAX_WAIT = 0.2
POLL_INTERVAL = 0.5
OPEN_TIMEOUT = 60.0
TIMER_INTERVAL = 0.02
IDLE_INTERVAL = 0.2
DATA_TYPES = (bpy.types.Material, bpy.types.NodeTree, bpy.types.Image, bpy.types.VectorFont, bpy.types.Mesh,
              bpy.types.Curve, bpy.types.Action)


class _State:
    def __init__(self):
        self.active = False
        self.dirty = set()          # canvas (scene) names to export again
        self.changed = set()        # as_pointer() of changed DATA_TYPES: their canvases export again
        self.first = 0.0
        self.last = 0.0
        self.sent = {}              # canvas name -> digest of what the game has
        self.names = set()          # canvas names at the last poll
        self.signatures = {}        # canvas name -> cheap signature at the last poll
        self.timelines = {}         # canvas name -> _timeline() at the last scene update
        self.polled = 0.0
        self.warned = set()         # skipped-canvas messages already logged
        self.pending_open = None    # after Play from a UI file: {"canvas", "ready", "deadline"}
        self.locale_files = None    # _locale_files() at the last poll (None: not looked at yet)
        self.locales = None         # digest of the translations the game has (ui.locales payload)
        self.stats = {"canvases": 0, "removes": 0, "opens": 0, "closes": 0, "locales": 0}


_s = _State()


# --- Status --------------------------------------------------------------------------------------


def _scene_live(scene) -> bool:
    props = getattr(scene, "gs", None)
    return props is not None and props.live_enabled


def _game_client():
    client = link.get_link()
    if client is None or not client.connected or not client.game_peers():
        return None
    return client


def is_active() -> bool:
    return _s.active


def stats() -> dict:
    return dict(_s.stats)


# --- Marking ---------------------------------------------------------------------------------------


def _canvas_names() -> list:
    return [s.name for s in gameui.canvas_scenes()]


def _mark(names, data=()) -> None:
    """Export these canvases, and the canvases using these datablocks (as_pointer()), soon."""
    now = time.monotonic()
    names = [n for n in names if not n.startswith("gs_")]
    if not names and not data:
        return
    if not _s.dirty and not _s.changed:
        _s.first = now
    _s.dirty.update(names)
    _s.changed.update(data)
    _s.last = now


def _material_uses(mat, memo: dict) -> set:
    """as_pointer() of a material, its node trees (groups too) and their images."""
    key = mat.as_pointer()
    if key not in memo:
        found, trees = {key}, [mat.node_tree] if mat.node_tree is not None else []
        while trees:
            tree = trees.pop()
            if tree.as_pointer() in found:
                continue
            found.add(tree.as_pointer())
            for node in tree.nodes:
                image = getattr(node, "image", None)
                if image is not None:
                    found.add(image.as_pointer())
                group = getattr(node, "node_tree", None)
                if group is not None:
                    trees.append(group)
        memo[key] = found
    return memo[key]


def canvases_using(pointers) -> set:
    """The canvases with an object that uses one of these datablocks (as_pointer()): its mesh, curve or
    image, its text's fonts, its materials and their node trees and images, its action. Walks the canvases
    once."""
    pointers, memo, found = set(pointers), {}, set()
    for scene in gameui.canvas_scenes():
        for obj in scene.objects:
            data = obj.data
            uses = {data.as_pointer()} if data is not None else set()
            if obj.type == "FONT":
                uses.update(f.as_pointer() for f in (data.font, data.font_bold, data.font_italic,
                                                     data.font_bold_italic) if f is not None)
            for slot in obj.material_slots:
                if slot.material is not None:
                    uses |= _material_uses(slot.material, memo)
            anim = obj.animation_data
            if anim is not None and anim.action is not None:
                uses.add(anim.action.as_pointer())
            if not pointers.isdisjoint(uses):
                found.add(scene.name)
                break
    return found


def mark_canvas_dirty(canvas) -> None:
    """Export a canvas (a Scene or its name) again soon and send it if it changed. The gs.gameui builder
    calls this after editing a canvas the window may not show."""
    if _s.active:
        _mark([canvas.name if isinstance(canvas, bpy.types.Scene) else str(canvas)])


def mark_object_dirty(obj) -> None:
    """An element's components changed (live.mark_components_dirty forwards every component edit)."""
    if not _s.active or obj is None:
        return
    try:
        _mark([s.name for s in obj.users_scene])
    except ReferenceError:
        pass


def _signature(scene):
    """What the twice-a-second check compares: cheap, without components values or geometry. Local
    matrices: the world ones of a canvas the window does not show are only updated when it is evaluated."""
    camera = scene.camera
    items = [scene.render.resolution_x, scene.render.resolution_y, camera.name if camera else None]
    if camera is not None:
        items.append(tuple(round(v, 5) for row in camera.matrix_basis for v in row))
        items.append(getattr(camera.data, "ortho_scale", None))
    for obj in scene.objects:
        data = obj.data
        entry = (obj.name, obj.type, obj.parent.name if obj.parent else None, obj.hide_render,
                 tuple(round(v, 5) for row in obj.matrix_basis for v in row), data.name if data else None,
                 tuple(slot.material.name if slot.material else None for slot in obj.material_slots),
                 tuple((ref.name, ref.enabled) for ref in obj.gs.components), obj.color[3])
        if obj.type == "FONT":
            entry += (data.body, data.font.name if data.font else None, data.size, data.align_x, data.align_y)
        elif obj.type == "EMPTY":
            entry += (obj.empty_display_type, obj.empty_display_size, tuple(obj.empty_image_offset),
                      obj.use_empty_image_alpha)
        items.append(entry)
    items.append(tuple(_excluded(export._export_view_layer(scene).layer_collection)))
    items.append(_timeline(scene))
    return hash(tuple(items))


def _timeline(scene) -> tuple:
    """What a canvas's animations and rest pose take from its timeline (§10.5), keyframes aside: the markers,
    the scene's end and frame rate, and the current frame while it is the rest pose (no open marker). Nothing
    without markers: then the export reads the scene as it is."""
    markers = tuple(sorted((m.frame, m.name) for m in scene.timeline_markers))
    if not markers:
        return ()
    has_open = any(name.strip().lower() == "open" for _frame, name in markers)
    return (markers, scene.frame_end, scene.render.fps, scene.render.fps_base,
            None if has_open else scene.frame_current)


def _excluded(layer_collection, out=None) -> list:
    """Names of the collections excluded from a view layer (their objects are not exported). A new scene's
    view layer has no layer collection until it is first evaluated: nothing excluded."""
    out = [] if out is None else out
    for child in layer_collection.children if layer_collection is not None else ():
        if child.exclude:
            out.append(child.name)
        else:
            _excluded(child, out)
    return out


# --- Activation ------------------------------------------------------------------------------------


def _seed() -> None:
    """What the game has: the exported files of this UI file."""
    _s.sent.clear()
    _s.signatures.clear()
    _s.timelines.clear()
    _s.names.clear()
    _s.locale_files = None
    _s.locales = None
    root = project.project_root()
    if root is None:
        return
    _s.locales = _locales_digest(gameui_export.exported_locales(root))
    source = gameui.source_path(root)
    for name, entry in gameui_export.read_index(root)["canvases"].items():
        if entry.get("source") != source:
            continue
        _s.names.add(name)
        data = gameui_export._read_json(gameui.export_dir(root) / entry["file"])
        if isinstance(data, dict):
            _s.sent[name] = gameui_export.digest(data)


def _activate() -> None:
    _s.active = True
    _s.warned.clear()
    _seed()
    _s.polled = 0.0
    _mark(_canvas_names())
    log.info("Game UI live link active")


def _deactivate() -> None:
    _s.active = False
    _s.dirty.clear()
    _s.changed.clear()
    _s.sent.clear()
    _s.signatures.clear()
    _s.timelines.clear()
    _s.names.clear()
    _s.locale_files = None
    _s.locales = None


# --- Translations (§11.3) ----------------------------------------------------------------------------


def _locale_files(root) -> tuple:
    """What the poll compares to see table edits: gamestudio.json's and every ui/locales/*.json's stamp."""
    def stamp(path):
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    folder = gameui.locales_dir(root)
    tables = tuple(sorted((p.name, stamp(p)) for p in folder.glob("*.json"))) if folder.is_dir() else ()
    return stamp(root / project.PROJECT_FILE), tables


def _locales_digest(payload: dict) -> str:
    body = {key: payload.get(key) for key in ("sourceLocale", "locales", "tables")}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def send_locales(client=None, force: bool = False) -> bool:
    """Export the translation tables and send them as ui.locales when they differ from what the game has
    (force: always). Returns True when ui.locales went out."""
    client = client or _game_client()
    root = project.project_root()
    if client is None or root is None:
        return False
    result = gameui_export.export_locales(root)
    payload = {"sourceLocale": result["sourceLocale"], "locales": result["locales"], "tables": result["tables"]}
    digest = _locales_digest(payload)
    if not force and digest == _s.locales:
        return False
    if client.send({"t": "ui.locales", **payload}):
        _s.locales = digest
        _s.stats["locales"] += 1
        return True
    return False


def _poll_locales(client) -> None:
    root = project.project_root()
    if root is None:
        return
    files = _locale_files(root)
    if files != _s.locale_files:
        _s.locale_files = files
        try:
            send_locales(client)
        except Exception:  # noqa: BLE001 - a broken table must not stop the live link
            log.exception("Sending the UI translations failed")


# --- Sending ---------------------------------------------------------------------------------------


def _send_canvas(client, scene, force: bool = False) -> bool:
    """Export a canvas and send it when its content differs from what the game has (force: always)."""
    try:
        result = gameui_export.export_canvas(scene)
    except Exception:
        log.exception("Exporting UI screen '%s' failed", scene.name)
        return False
    if result.get("skipped"):
        if result["skipped"] not in _s.warned:
            _s.warned.add(result["skipped"])
            log.warning("%s", result["skipped"])
        return False
    if not force and _s.sent.get(scene.name) == result["digest"]:
        return False
    if client.send({"t": "ui.canvas", "canvas": scene.name, "data": result["data"]}):
        _s.sent[scene.name] = result["digest"]
        _s.stats["canvases"] += 1
        return True
    return False


def _remove(client, name: str) -> None:
    _s.sent.pop(name, None)
    _s.signatures.pop(name, None)
    _s.timelines.pop(name, None)
    _s.dirty.discard(name)
    try:
        gameui_export.remove_canvas(name)
    except OSError as exc:
        log.warning("Could not drop UI screen '%s' from the export: %s", name, exc)
    if client.send({"t": "ui.remove", "canvas": name}):
        _s.stats["removes"] += 1


def _poll(client, now: float) -> None:
    _s.polled = now
    current = {s.name: s for s in gameui.canvas_scenes()}
    for name in sorted(_s.names - set(current)):
        _remove(client, name)
    changed = []
    for name, scene in current.items():
        try:
            signature = _signature(scene)
        except (ReferenceError, AttributeError):
            continue
        if name not in _s.names or _s.signatures.get(name) != signature:
            changed.append(name)
        _s.signatures[name] = signature
        _s.timelines[name] = _timeline(scene)  # what scene updates compare with (_collect)
    _s.names = set(current)
    _mark(changed)
    _poll_locales(client)


def _flush(client) -> None:
    names = set(_s.dirty)
    if _s.changed:
        try:
            names |= canvases_using(_s.changed)
        except Exception as exc:  # noqa: BLE001 - never stuck in a failing timer: export them all instead
            log.debug("Finding the canvases of changed data failed: %s", exc)
            names |= set(_canvas_names())
    _s.dirty.clear()
    _s.changed.clear()
    for name in sorted(names):
        scene = bpy.data.scenes.get(name)
        if scene is not None and not name.startswith("gs_"):
            _send_canvas(client, scene)


def send_open(canvas, client=None) -> bool:
    """Open a canvas (a Scene or its name) in the connected games: ui.open, after its current content
    as ui.canvas when it is a canvas of the open UI file that the game may not have. Needs a connected
    game, not the Live Link toggle. Returns True when ui.open went out."""
    name = canvas.name if isinstance(canvas, bpy.types.Scene) else str(canvas)
    client = client or _game_client()
    if client is None:
        return False
    scene = bpy.data.scenes.get(name)
    if scene is not None and not name.startswith("gs_") and gameui.is_open_ui_file():
        _send_canvas(client, scene, force=name not in _s.sent)
    if client.send({"t": "ui.open", "canvas": name}):
        _s.stats["opens"] += 1
        return True
    return False


def send_close(canvas=None, client=None) -> bool:
    """Close a canvas (or, without one, the open menu) in the connected games: ui.close."""
    client = client or _game_client()
    if client is None:
        return False
    msg = {"t": "ui.close"}
    if canvas is not None:
        msg["canvas"] = canvas.name if isinstance(canvas, bpy.types.Scene) else str(canvas)
    if client.send(msg):
        _s.stats["closes"] += 1
        return True
    return False


def open_after_play(canvas) -> None:
    """Play from a UI file: open this canvas once the game has loaded the level and plays (a ui.open
    sent before would be lost in the reload)."""
    client = link.get_link()
    name = canvas.name if isinstance(canvas, bpy.types.Scene) else str(canvas)
    _s.pending_open = {"canvas": name, "ready": client.game_ready if client is not None else None,
                       "deadline": time.monotonic() + OPEN_TIMEOUT}


def _check_open(client) -> None:
    pending = _s.pending_open
    if time.monotonic() > pending["deadline"]:
        log.warning("Play: the game did not start within %d s; UI screen '%s' was not opened",
                    int(OPEN_TIMEOUT), pending["canvas"])
        _s.pending_open = None
    elif (client.connected and client.game_ready is not None and client.game_ready is not pending["ready"]
          and client.game_state == "playing"):
        _s.pending_open = None
        send_open(pending["canvas"], client)


def pump() -> None:
    """Send pending UI changes. Main thread only; tests call this directly."""
    if not gameui.is_open_ui_file():
        if _s.active:
            _deactivate()
        _s.pending_open = None
        return
    client = link.get_link()
    if _s.pending_open is not None and client is not None:
        _check_open(client)
    scene = bpy.context.scene
    if scene is None or not _scene_live(scene) or _game_client() is None:
        if _s.active:
            _deactivate()
        return
    if not _s.active:
        _activate()
    now = time.monotonic()
    if now - _s.polled >= POLL_INTERVAL:
        _poll(client, now)
    if (_s.dirty or _s.changed) and (now - _s.last >= DEBOUNCE or now - _s.first >= MAX_WAIT):
        _flush(client)


def _timer():
    try:
        pump()
    except Exception:
        log.exception("Game UI live pump failed")
    return TIMER_INTERVAL if _s.active or _s.pending_open is not None else IDLE_INTERVAL


# --- Handlers --------------------------------------------------------------------------------------


def _collect(scene, depsgraph) -> None:
    names, changed = set(), set()
    for update in depsgraph.updates:
        data = update.id
        if isinstance(data, bpy.types.Object):
            names.update(s.name for s in data.original.users_scene)
        elif isinstance(data, bpy.types.Collection):  # objects linked or unlinked
            names.add(scene.name)
        elif isinstance(data, DATA_TYPES):  # its canvases are looked up once per export (canvases_using)
            changed.add(data.original.as_pointer())
        elif isinstance(data, bpy.types.Scene):
            # Selection, the active object or the frame: only a timeline change matters (animations).
            original = data.original
            timeline = _timeline(original)
            if _s.timelines.get(original.name, timeline) != timeline:
                names.add(original.name)
            _s.timelines[original.name] = timeline
    _mark(names, changed)


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if not _s.active or export.is_exporting() or gameui_export.is_extracting():
        return
    try:
        _collect(scene, depsgraph)
    except Exception:
        log.exception("Game UI update collection failed")


@persistent
def _on_undo_redo(*_args):
    if _s.active:
        _mark(_canvas_names())


@persistent
def _on_load_post(*_args):
    _deactivate()
    _s.pending_open = None
    gameui_export.clear_caches()
    try:
        # Opened or reverted: when its export is older than the file, or was made from changes that were
        # not saved, export the file as it is now (it does not make the file dirty).
        if gameui.is_ui_file():
            root = project.project_root()
            if root is not None and any(gameui_export._is_open(p) for p in gameui_export.stale_ui_files(root)):
                gameui_export.export_ui_file(root, unsaved=False)
    except export.ExportError as exc:
        log.warning("Exporting the opened UI file failed: %s", exc)
    except Exception:
        log.exception("Exporting the opened UI file failed")


def _saved_copy(args) -> bool:
    """save_post of "Save Copy" (a checkpoint): the open file is not what was saved."""
    path = next((a for a in args if isinstance(a, str) and a), "")
    return bool(path) and gameui._real(path) != gameui._real(bpy.data.filepath or path)


@persistent
def _on_save_pre(*_args):
    """Entity IDs go into the saved file, and fonts Blender's Python cannot read are measured now (with
    temporary curves), so exporting after the save changes nothing in the file."""
    try:
        if gameui.is_ui_file():
            for scene in gameui.canvas_scenes():
                ids.repair_duplicate_ids(scene)  # as the export after the save does (copies get new IDs)
                for obj in scene.objects:
                    if obj.library is None and ids.get_id(obj) is None:
                        ids.ensure_id(obj)
                    if obj.type == "FONT":
                        gameui_export.font_metrics(obj.data.font)
    except Exception:
        log.exception("Preparing the UI file's export before saving failed")


@persistent
def _on_save_post(*args):
    try:
        if not gameui.is_ui_file():
            return
        # The file as saved (Blender clears is_dirty only after save_post); a copy leaves it unsaved.
        result = gameui_export.export_ui_file(unsaved=None if _saved_copy(args) else False)
    except Exception as exc:
        log.error("Exporting the UI file failed: %s", exc)
        return
    written = [c["canvas"] for c in result["canvases"] if c.get("written")]
    log.info("Exported UI screens (%s changed)", ", ".join(written) or "none")


_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("undo_post", _on_undo_redo),
    ("redo_post", _on_undo_redo),
    ("load_post", _on_load_post),
    ("save_pre", _on_save_pre),
    ("save_post", _on_save_post),
)


def register():
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _deactivate()
    _s.pending_open = None
