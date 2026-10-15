# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scene view: a 3D View that shows the level drawn by the game's renderer, live, without playing.

The Game toggle in a 3D View's header (editors/scene_view.py) turns it on for that area; one area
at a time (turning it on elsewhere moves it). The area stays a 3D View: Blender keeps navigation,
selection, transforms, gizmos and its overlays; what it shows under them is the game's frame from
that area's own view. How the frame gets under Blender's overlays is editors/scene_view.py's
docstring; this module is the lifecycle:

  on        reuses Play's game (editors/game_stream.py): the same browser and page, the warm one when
            Play left it, else a new one at the game's address with ?gs_view=scene (the engine
            starts in scene mode: no autoplay, no title). Starts the dev server when the live link
            is down, turns the scene's Live Link on, exports (tiers make it fast); once the page is on
            the live link it gets `scene.load`, `view.mode` {mode: 'scene', animate} and
            `view.camera`, all addressed to that page only (`to`), so a game open in a browser tab is
            left alone. Live edits then reach it as they reach any game (core/live.py).
  camera    the area's region_3d (view_matrix, window_matrix) converted to the game's Y-up space
            (camera_message), sent on change, at most CAMERA_RATE a second, latest wins.
  frames    the stream follows the area's region size (at most game_stream.MAX_PIXELS) at the
            scene_view_fps preference (the host's `quality` max_fps); paused while no window shows
            the area (another workspace), and while the game plays.
  Play      Play (F5) sends the page `view.mode` game before its scene.load and play.start
            (core/play.py send_play -> before_play), then plays as always; the Game View opens by
            the usual rules (it may be this very area). This area says "Playing in the Game View".
            Stop (F8): once the game is stopped this view sends `view.mode` scene and its camera
            again and the frames come back.
  off       restores the area's shading and overlays, stops drawing, and leaves the page (in scene
            mode) warm for the next Play.

In a UI file (docs/GAME-UI.md §11.2) the area shows the active scene's screen as the game draws it
instead of a level: nothing is exported (the UI live link, core/gameui_live.py, keeps the page's
screens current); the page gets scene.load of the start level's last export, when there is one, and
`view.mode` {mode: 'ui', canvas, frame, region, background: 'level' | 'checker', animate} with the
canvas camera's frame in the region's pixels (y down; ui_message), while the view looks through that
camera (camera view, Numpad 0). It takes the camera's place (no view.camera is sent) and is sent the
same way: when it changed, at most CAMERA_RATE a second, latest wins. Out of camera view the area says
how to get there (UI_HINT) and Blender draws it. Play, Stop and off as above; after Stop the start
level is loaded again.

What it changes in the area while on: the shading type (Solid: Material Preview or Rendered would
render underneath for nothing, Wireframe would draw every edge over the game), and while the game's
frame shows, the viewport background (black: Blender adds the background where it drew no object)
and the floor grid and axes. Each is put back when no longer needed, unless the user changed it in
the meantime. Nothing is saved in the .blend: a save puts them back for the time it takes, and
loading a file turns the Scene view off.

pump() does the work; a timer calls it while the Scene view is on, tests call it directly.
"""

import json
import time
import urllib.parse

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

from . import devserver, export, link, project
from .log import get_logger

log = get_logger(__name__)

VIEW_PARAM = "gs_view"          # ?gs_view=scene: the page starts in scene mode
RETRY_PARAM = "gs_retry"        # a page loaded again because it never joined the live link
STUCK_SECONDS = 30.0            # a loaded page not on the live link by then is loaded again (Vite's first start)
MAX_RETRIES = 2
CAMERA_RATE = 60.0              # view.camera messages a second, at most
TIMER_INTERVAL = 1.0 / 60.0
PLAY_HOLD = 15.0                # after Play sent play.start: playing until the game says so, or this long
RESEND_AFTER = 1.0              # a page that reports the wrong view gets view.mode again, at most this often
DEFAULT_FPS = 30

# What the area says (status()).
STARTING_SERVER = "Starting the dev server..."
CONNECTING = "Connecting to the dev server..."
NO_SERVER = "The dev server is not running: Game > Start Dev Server, or Play, starts it"
STARTING = "Starting the game renderer..."
LOADING = "Loading the scene..."
PLAY_STARTING = "Starting the game..."
PLAYING_VIEW = "Playing in the Game View. Stop (F8) returns here"
PLAYING_WINDOW = "Playing in the game window. Stop (F8) returns here"
PLAYING_HERE = "The game is playing. Stop (F8) returns to the scene"
# A UI file out of the canvas camera's view: one line at the bottom of the area (kind "hint").
UI_HINT = "View through the screen's camera (Numpad 0) to see it drawn by the game"

# Area settings: (property path on the 3D View's space, value while on).
BASE_SETTINGS = (("shading.type", "SOLID"),)
FRAME_SETTINGS = (
    ("shading.background_type", "VIEWPORT"),
    ("shading.background_color", (0.0, 0.0, 0.0)),
    ("overlay.show_floor", False),
    ("overlay.show_axis_x", False),
    ("overlay.show_axis_y", False),
)

# Blender's world (Z up) to the game's (Y up): (x, y, z) -> (x, z, -y), docs/SPEC.md §4.3.
TO_GAME = Matrix(((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, -1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)))


# --- Camera ------------------------------------------------------------------------------------


def game_view_matrix(view_matrix) -> Matrix:
    """Blender's world-to-view matrix as the game's: the same view space (OpenGL: -Z forward, +Y
    up) from a Y-up world, view @ TO_GAME^T."""
    return Matrix(view_matrix) @ TO_GAME.transposed()


def column_major(matrix) -> list:
    """The 16 values in column-major order (three.js Matrix4.fromArray)."""
    return [float(matrix[row][col]) for col in range(4) for row in range(4)]


def clip_range(projection) -> tuple:
    """(near, far, ortho) of an OpenGL projection matrix (perspective or orthographic, centred or
    not). An orthographic view of Blender's has a negative near: it sees behind the view point."""
    p = projection
    ortho = abs(p[3][3] - 1.0) < 1e-9 and abs(p[3][2]) < 1e-9
    a, b = p[2][2], p[2][3]
    if ortho:
        return (b + 1.0) / a, (b - 1.0) / a, True
    return b / (a - 1.0), b / (a + 1.0), False


def camera_message(view_matrix, projection) -> dict:
    """The view.camera message (docs/SPEC.md §5.4) for a Blender view and projection matrix
    (region_3d.view_matrix and window_matrix): `view` in the game's space, `projection` as is."""
    near, far, ortho = clip_range(projection)
    return {"t": "view.camera", "view": column_major(game_view_matrix(view_matrix)),
            "projection": column_major(projection), "ortho": ortho, "near": near, "far": far}


def _camera_key(message: dict) -> tuple:
    return tuple(round(v, 6) for v in message["view"] + message["projection"])


# --- A UI file's screen (docs/GAME-UI.md §11.2) --------------------------------------------------


def camera_frame(corners, perspective, width: float, height: float) -> list | None:
    """The rectangle [x, y, width, height] that world points (the camera frame's corners) cover in a
    region of width x height pixels seen through `perspective` (window_matrix @ view_matrix), from
    the region's top left, y down; None when a corner is behind the view or the rectangle is empty."""
    xs, ys = [], []
    for corner in corners:
        clip = perspective @ Vector((corner[0], corner[1], corner[2], 1.0))
        if clip.w <= 1e-9:
            return None
        xs.append((clip.x / clip.w + 1.0) * width / 2.0)
        ys.append((1.0 - clip.y / clip.w) * height / 2.0)
    left, top = min(xs), min(ys)
    w, h = max(xs) - left, max(ys) - top
    if not (w >= 1.0 and h >= 1.0):
        return None
    return [round(left, 2), round(top, 2), round(w, 2), round(h, 2)]


def viewed_camera(space, scene):
    """The camera a 3D View's camera view looks through: its local camera, else the scene's."""
    local = getattr(space, "camera", None) if getattr(space, "use_local_camera", False) else None
    return local if local is not None else scene.camera


def ui_message(scene, space, region, region_data=None, background="checker") -> dict | None:
    """The view.mode ui message (docs/GAME-UI.md §11.2) for a UI file's screen `scene` seen in a 3D View
    (`space`, its main `region`; `region_data` defaults to the space's): the canvas camera's frame in
    the region's pixels. None while the view does not look through that camera (camera view, Numpad 0)."""
    r3d = region_data if region_data is not None else getattr(space, "region_3d", None)
    if scene is None or region is None or r3d is None or getattr(r3d, "view_perspective", "") != "CAMERA":
        return None
    camera = scene.camera
    if camera is None or camera.type != "CAMERA" or viewed_camera(space, scene) != camera:
        return None
    corners = [camera.matrix_world @ Vector(corner) for corner in camera.data.view_frame(scene=scene)]
    frame = camera_frame(corners, Matrix(r3d.window_matrix) @ Matrix(r3d.view_matrix), region.width, region.height)
    if frame is None:
        return None
    return {"t": "view.mode", "mode": "ui", "canvas": scene.name, "frame": frame,
            "region": [int(region.width), int(region.height)], "background": background, "animate": animate()}


def _ui_key(message: dict) -> tuple:
    return (message["canvas"], *message["frame"], *message["region"], message["background"], message["animate"])


def _ui_file() -> bool:
    """The open file is a UI file: the Scene view shows its active screen, not a level."""
    from . import gameui

    return gameui.is_open_ui_file()


def start_level_export(root) -> dict | None:
    """The start level's last export (gamestudio.json startScene) as a scene.load message's {"scene",
    "url", "rev"}, or None when it has none: what a UI file's screen is drawn over."""
    from . import gameui_export

    name = export._sanitize(gameui_export.start_level(root))
    try:
        data = json.loads((project.export_dir(root) / f"{name}.scene.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("scene"):
        return None
    rev = data.get("rev")
    return {"scene": data["scene"], "url": export.manifest_url(name), "rev": rev if isinstance(rev, int) else 0}


# --- URLs --------------------------------------------------------------------------------------


def _with_query(url: str, pairs: list) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(pairs)))


def game_url(url: str | None) -> str | None:
    """The game's address without the Scene view's ?gs_view parameter."""
    if not url:
        return url
    pairs = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    return _with_query(url, [(k, v) for k, v in pairs if k not in (VIEW_PARAM, RETRY_PARAM)])


def scene_url(url: str | None, retry: int = 0) -> str | None:
    """The game's address that starts the page in scene mode (?gs_view=scene)."""
    if not url:
        return url
    pairs = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    pairs = [(k, v) for k, v in pairs if k not in (VIEW_PARAM, RETRY_PARAM)] + [(VIEW_PARAM, "scene")]
    return _with_query(url, pairs + ([(RETRY_PARAM, str(retry))] if retry else []))


def same_game(a: str | None, b: str | None) -> bool:
    """Two addresses of the same game page, whatever ?gs_view (and ?gs_retry) says: the page at
    one need not reload for the other (the mode is switched with view.mode)."""
    return bool(a) and bool(b) and game_url(a) == game_url(b)


# --- State -------------------------------------------------------------------------------------


class _State:
    def __init__(self):
        self.area = 0              # as_pointer() of the Scene view's area; 0: off
        self.screen = ""           # the name of the screen that has it
        self.saved = {}            # setting path -> (the user's value, ours) for what was changed
        self.frame_on = False      # FRAME_SETTINGS are applied
        self.status = ""           # what the area says ("" while the frame shows)
        self.kind = ""             # "info" | "playing" | "error"
        self.error = ""
        self.session = None        # the stream session this view used: Play sends it view.mode game
        self.peer = None           # the page's live-link peer the state below is for
        self.loaded = False        # scene.load sent to it
        self.scene_uid = None      # the scene it shows
        self.mode_sent = None      # (mode, animate) last sent
        self.mode_at = 0.0
        self.camera_key = None     # the last camera sent
        self.camera_at = 0.0
        self.camera_pending = None  # a camera to send once the rate allows
        self.background = None     # a UI file: what the page draws under the screen, "level" or "checker"
        self.ui_key = None         # a UI file: the view.mode ui last sent (_ui_key)
        self.ui_at = 0.0
        self.ui_pending = None     # a view.mode ui to send once the rate allows
        self.ui_canvas = None      # the screen whose Live Link this view turned on
        self.wants = False         # frames wanted now (game_stream follows frame_region())
        self.frame_mark = -1       # the stream's frame id when the view was last (re)set: older frames are stale
        self.showing = False       # the game's frame is drawn
        self.playing = False
        self.play_until = 0.0      # Play sent play.start: playing until the game says so (PLAY_HOLD)
        self.peer_state = {}       # game peer id -> (state, view) from its game.status
        self.restart_at = 0.0
        self.stuck_since = None    # the loaded page is not on the live link since then
        self.retries = 0
        self.saving = False


_s = _State()


def _game_stream():
    from ..editors import game_stream

    return game_stream


def _play():
    from . import play

    return play


def is_on() -> bool:
    return bool(_s.area)


def status() -> tuple:
    """(text, kind) for the area: kind "info" (starting), "playing", "error", or "hint" (a UI file out
    of its screen's camera view: one line at the bottom); ("", "") while the game's frame shows."""
    return _s.status, _s.kind


def showing() -> bool:
    """The game's frame is drawn in the Scene view now."""
    return bool(_s.area) and _s.showing


def fps() -> int:
    from ..prefs import get_prefs

    try:
        return max(1, int(getattr(get_prefs(), "scene_view_fps", DEFAULT_FPS)))
    except (TypeError, ValueError):
        return DEFAULT_FPS


def animate() -> bool:
    from ..prefs import get_prefs

    return bool(getattr(get_prefs(), "scene_view_animate", True))


def prefs_changed() -> None:
    """The preferences changed: view.mode again (animate), the frame rate on the next stream update."""
    _s.mode_sent = None


# --- The area ----------------------------------------------------------------------------------


def _windows() -> list:
    wm = getattr(bpy.context, "window_manager", None)
    return list(wm.windows) if wm is not None else []


def _screen(name: str):
    return bpy.data.screens.get(name)


def _space(area):
    """The area's 3D View space (also while another editor is active in the area)."""
    if area is None:
        return None
    return next((s for s in area.spaces if s.type == "VIEW_3D"), None)


def main_region(area):
    """The region that draws the area's main view (with Quad View, the one of space.region_3d)."""
    if area is None:
        return None
    regions = [r for r in area.regions if r.type == "WINDOW"]
    space = _space(area)
    main = getattr(space, "region_3d", None) if space is not None else None
    if main is not None and len(regions) > 1:
        for region in regions:
            data = getattr(region, "data", None)
            if data is not None and data.as_pointer() == main.as_pointer():
                return region
    return regions[-1] if regions else None


def is_area(area, screen=None) -> bool:
    """`area` is the Scene view, or the same area maximized (Ctrl+Space: a copy in a screen named
    "<screen>-nonnormal")."""
    if area is None or not _s.area:
        return False
    if area.as_pointer() == _s.area:
        return True
    return (screen is not None and area.type == "VIEW_3D" and getattr(screen, "show_fullscreen", False)
            and screen.name == f"{_s.screen}-nonnormal" and len(screen.areas) == 1)


def find_area():
    """(window showing it or None, screen, area) of the Scene view, or None when it is gone. Areas
    are looked up, never kept: a reference to a closed area is not safe to touch."""
    if not _s.area:
        return None
    screen = _screen(_s.screen)
    area = next((a for a in screen.areas if a.as_pointer() == _s.area), None) if screen is not None else None
    if area is None:
        return None
    return next((w for w in _windows() if w.screen == screen), None), screen, area


def visible_area():
    """(window, screen, area) a window shows the Scene view in (the area, or its maximized copy),
    or None."""
    found = find_area()
    if found is None:
        return None
    if found[0] is not None:
        return found
    for window in _windows():
        screen = window.screen
        if screen is not None and len(screen.areas) == 1 and is_area(screen.areas[0], screen):
            return window, screen, screen.areas[0]
    return None


def frame_region():
    """The region that wants the game's frames now (editors/game_stream.py streams for it), or None."""
    if not _s.area or not _s.wants:
        return None
    shown = visible_area()
    if shown is None or shown[2].type != "VIEW_3D":
        return None
    return main_region(shown[2])


def active() -> bool:
    """The Scene view is on and a window shows it: its page is not to be hidden."""
    return bool(_s.area) and visible_area() is not None


def max_fps() -> int:
    return fps()


# --- Area settings -----------------------------------------------------------------------------


def _get(space, path: str):
    owner, _, name = path.rpartition(".")
    value = getattr(getattr(space, owner), name)
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def _set(space, path: str, value) -> None:
    owner, _, name = path.rpartition(".")
    setattr(getattr(space, owner), name, value)


def _same(a, b) -> bool:
    if isinstance(a, tuple) or isinstance(b, tuple):
        return len(a) == len(b) and all(abs(x - y) < 1e-6 for x, y in zip(a, b))
    return a == b


def _apply(space, settings) -> None:
    for path, value in settings:
        try:
            current = _get(space, path)
            if _same(current, value):
                continue
            _set(space, path, value)
            _s.saved[path] = (current, value)
        except (AttributeError, TypeError, ValueError) as exc:
            log.debug("Scene view: could not set %s: %s", path, exc)


def _restore(space, paths) -> None:
    """Put back what the Scene view changed, unless the user changed it since."""
    for path in paths:
        entry = _s.saved.pop(path, None)
        if entry is None or space is None:
            continue
        original, ours = entry
        try:
            if _same(_get(space, path), ours):
                _set(space, path, original)
        except (AttributeError, TypeError, ValueError) as exc:
            log.debug("Scene view: could not restore %s: %s", path, exc)


def _set_frame_settings(space, on: bool) -> None:
    """FRAME_SETTINGS while the game's frame shows. The background is kept (the frame's colours depend
    on it); the grid and axes are set once, so the user can turn them back on."""
    if on:
        _apply(space, FRAME_SETTINGS if not _s.frame_on else FRAME_SETTINGS[:2])
        _s.frame_on = True
    elif _s.frame_on:
        _restore(space, [path for path, _value in FRAME_SETTINGS])
        _s.frame_on = False


def _settings_space(found):
    """The 3D View space whose settings the Scene view changes: its maximized copy's while a window
    shows that (Blender copies it back when the area is restored), else the area's own."""
    shown = visible_area()
    if shown is not None and shown[2].as_pointer() != _s.area:
        return _space(shown[2])
    return _space(found[2]) if found is not None else None


def _tag(area) -> None:
    if area is not None:
        for region in area.regions:
            if region.type in ("WINDOW", "HEADER"):
                region.tag_redraw()


# --- On and off --------------------------------------------------------------------------------


def enable(window, screen, area, scene=None) -> str:
    """Turn the Scene view on in `area` (off wherever else it was). Returns "" or why it cannot."""
    gs = _game_stream()
    root = project.project_root()
    if root is None:
        return "Save the file in a GameStudio project first: the Scene view draws the project's game"
    if gs.headless():
        return "Blender runs without windows"
    if area is None or area.type != "VIEW_3D":
        return "The Scene view is a 3D Viewport"
    # Node.js, the game packages, the project's links and a browser: GameStudio sets up what is
    # missing first (asking once before it downloads), then turns the Scene view on here.
    from .. import environment

    state = environment.ensure_ready(root, "sceneview", then=_enable_when_ready(screen, area))
    if state in (environment.STARTED, environment.ASKING):
        _gs_note("Setting up GameStudio first (see the status bar); the Scene view turns on when it is ready")
        return ""
    if state != environment.READY:
        return state
    ok, problem = gs.available()
    if not ok:
        return f"The game cannot be drawn in Blender: {problem}"
    if _s.area and _s.area != area.as_pointer():
        disable()
    scene = scene or getattr(window, "scene", None) or bpy.context.scene
    props = getattr(scene, "gs", None)
    if props is not None and not props.live_enabled:
        props.live_enabled = True  # edits reach the game without Play
    client = link.get_link()
    if not client.connected:
        try:
            devserver.start(root)
        except devserver.DevServerError as exc:
            return str(exc)
    _s.area, _s.screen = area.as_pointer(), screen.name
    _reset_page()
    _s.error, _s.restart_at, _s.retries, _s.stuck_since = "", 0.0, 0, None
    _s.ui_canvas = scene.name if _ui_file() else None
    _apply(_space(area), BASE_SETTINGS)
    try:
        _start_stream(client, area)
    except Exception as exc:  # noqa: BLE001 - StreamError, or whatever the client raised
        disable()
        return f"The game renderer did not start: {exc}"
    if not _ui_file():  # a UI file's screens reach the page through the UI live link
        try:
            export.export_scene(scene)  # while the browser starts; the page loads it once on the link
        except (export.ExportError, OSError, RuntimeError) as exc:
            disable()
            return f"Could not export the scene: {exc}"
    _set_status(STARTING, "info")
    _ensure_timer()
    _tag(area)
    return ""


def _gs_note(message: str, level: str = "INFO") -> None:
    _game_stream().notify(message, level)


def _enable_when_ready(screen, area):
    """enable() again for this area once GameStudio's setup is done (areas are looked up again, never
    kept: the screen may have changed meanwhile)."""
    pointer, screen_name = area.as_pointer(), screen.name

    def done(ok, message):
        if not ok:
            _gs_note(f"The Scene view did not turn on: {message}" if message else
                     "The Scene view did not turn on: GameStudio's setup did not finish", "ERROR")
            return
        found = _screen(screen_name)
        target = next((a for a in found.areas if a.as_pointer() == pointer), None) if found is not None else None
        window = next((w for w in _windows() if w.screen == found), None)
        if target is None or window is None:
            return  # the area is gone, or not shown: the user turns it on again
        problem = enable(window, found, target, window.scene)
        if problem:
            _gs_note(problem, "ERROR")
    return done


def disable() -> None:
    """Turn the Scene view off: the area's settings back, nothing drawn. The page stays, in scene
    mode, warm for Play (which sends it view.mode game)."""
    found = find_area()
    if found is not None:
        space = _settings_space(found)
        _set_frame_settings(space, False)
        _restore(space, list(_s.saved))
        _tag(found[2])
    _forget()


def _forget() -> None:
    _s.area, _s.screen = 0, ""
    _s.saved.clear()
    _s.frame_on = _s.showing = _s.wants = False
    _s.status = _s.kind = _s.error = ""
    _reset_page()


def _reset_page() -> None:
    """The page needs the scene, the mode and the camera again (a new page, or the view again)."""
    _s.peer = None
    _s.loaded = False
    _s.scene_uid = None
    _s.mode_sent = None
    _s.camera_key = _s.camera_pending = None
    _s.background = _s.ui_key = _s.ui_pending = None
    _s.frame_mark = -1


def toggle(window, screen, area, scene=None) -> str:
    if is_area(area, screen):
        disable()
        return ""
    return enable(window, screen, area, scene)


def _start_stream(client, area) -> None:
    """The game's browser for the Scene view: the running one (Play's warm page), else a new one
    at the game's address in scene mode (or without an address until the dev server has one)."""
    gs = _game_stream()
    url = scene_url(_play()._game_address(client))
    region = main_region(area)
    if gs.ensure(url, project_root=project.project_root(), region=region):
        log.info("Scene view: started the game renderer at %s", url or "(the address follows)")
    _s.session = gs.session()


# --- Play and Stop -----------------------------------------------------------------------------


def before_play(client) -> None:
    """Play is about to send scene.load and play.start (core/play.py): the page this view put in
    scene mode gets view.mode game first. Called whether the Scene view is on or not."""
    _s.play_until = time.monotonic() + PLAY_HOLD
    gs = _game_stream()
    session = gs.session()
    if session is None or session is not _s.session:
        return
    peer = gs.stream_peer(client)
    if peer is not None and client.send({"t": "view.mode", "mode": "game", "to": peer["peer"]}):
        _s.mode_sent = ("game",)
        _s.ui_key = _s.ui_pending = None
        _s.peer_state[peer["peer"]] = (_s.peer_state.get(peer["peer"], (None, None))[0], "game")


def on_stop() -> None:
    """Stop (core/play.py stop()): the game stops; this view takes the page back once it says so."""
    _s.play_until = 0.0
    client = link.get_link()
    peer = _game_stream().stream_peer(client) if client is not None else None
    if peer is not None and peer["peer"] in _s.peer_state:
        _s.peer_state[peer["peer"]] = ("stopped", _s.peer_state[peer["peer"]][1])


def _on_game_status(msg: dict) -> None:
    peer = msg.get("from")
    if peer:
        _s.peer_state[peer] = (msg.get("state"), msg.get("view"))


def _playing(client, peer) -> tuple:
    """(the game is being played, where: "view" or "window"). The page this view uses counts by its
    own game.status; without it, any game on the live link (Play in a window)."""
    play = _play()
    if play.is_pending():
        return True, "starting"
    state = _s.peer_state.get(peer, (client.game_state, None))[0] if peer is not None else None
    if _s.play_until:
        # Play sent play.start: the game loads, then plays. Until it says so (or for PLAY_HOLD), it is played.
        if state in ("playing", "paused") or time.monotonic() >= _s.play_until and state != "loading":
            _s.play_until = 0.0
        else:
            return True, "view"
    if not client.connected:
        return False, ""
    if peer is not None:
        return state in ("playing", "paused"), "view"
    games = {p.get("peer") for p in client.game_peers()}
    if any(state in ("playing", "paused") for gid, (state, _view) in _s.peer_state.items() if gid in games):
        return True, "window"
    return False, ""


# --- Pump --------------------------------------------------------------------------------------


def _set_status(text: str, kind: str = "info") -> None:
    if (text, kind if text else "") != (_s.status, _s.kind):
        _s.status, _s.kind = text, (kind if text else "")
        found = find_area()
        if found is not None:
            _tag(found[2])


def pump() -> None:
    """Do the Scene view's work: follow the page, send the scene, the mode and the camera. Main
    thread only; tests call it directly."""
    if not _s.area:
        return
    found = find_area()
    if found is None:
        log.info("The Scene view's area was closed: the Scene view is off")
        _forget()
        return
    window, screen, area = found
    gs = _game_stream()
    if area.type != "VIEW_3D" and not gs.is_game_area(area, screen):
        log.info("The Scene view's area became another editor: the Scene view is off")
        disable()
        return
    space = _settings_space(found)
    if area.type == "VIEW_3D" and space is not None and not _s.saving and space.shading.type != "SOLID":
        # Another shading picked next to the Game toggle: Blender draws this view again (Material
        # Preview and Rendered would also colour-manage the game's frame).
        log.info("The Scene view's area was given another shading: the Scene view is off")
        disable()
        return
    shown = visible_area()
    _update(shown, area)
    _set_frame_settings(space, _s.showing and not _s.saving)


def _update(shown, area) -> None:
    gs = _game_stream()
    client = link.get_link()
    now = time.monotonic()
    current = gs.session()
    peer = gs.stream_peer(client) if current is not None else None
    peer_id = peer["peer"] if peer is not None else None
    playing, where = _playing(client, peer_id)
    if playing != _s.playing:
        _s.playing = playing
        if not playing:  # Stop: the scene again, from this view
            _s.mode_sent = _s.camera_key = _s.ui_key = None
            _s.frame_mark = getattr(current, "frame_id", -1) if current is not None else -1
            if _ui_file():
                _s.loaded = False  # the start level again: the game may have gone to another
    _s.wants = False
    _s.showing = False
    if playing:
        if where == "view" and not gs.view_open():
            where = "here"  # Step (F7) in the Scene view: the page plays (paused) without a Game View
        _set_status({"starting": PLAY_STARTING, "window": PLAYING_WINDOW, "here": PLAYING_HERE}.get(where, PLAYING_VIEW),
                    "playing")
        return
    if not client.connected:
        root = project.project_root()
        running = root is not None and devserver.running_session(root, check_port=False) is not None
        _set_status(STARTING_SERVER if devserver.is_starting() else CONNECTING if running else NO_SERVER, "info")
        return
    if _s.error:
        _set_status(_s.error, "error")
        return
    if not gs.active():
        problem = gs.failure()
        if problem:
            _s.error = f"The game renderer failed: {problem}"
            _set_status(_s.error, "error")
            return
        if now >= _s.restart_at:  # ended (closed, or Play in a window closed it): a new one
            _s.restart_at = now + 5.0
            try:
                _start_stream(client, area)
            except Exception as exc:  # noqa: BLE001
                _s.error = f"The game renderer did not start: {exc}"
                _set_status(_s.error, "error")
                return
        _set_status(STARTING, "info")
        return
    address = _play()._game_address(client)
    gs.navigate(scene_url(address))  # a browser started before the address was known
    if peer_id is None:
        _check_stuck(current, address, now)
        _set_status(STARTING, "info")
        return
    _s.stuck_since = None
    if peer_id != _s.peer:
        _reset_page()
        _s.peer = peer_id
        _s.session = current
        _s.frame_mark = getattr(current, "frame_id", -1)
    window = shown[0] if shown is not None else None
    scene = getattr(window, "scene", None) or bpy.context.scene
    if _ui_file():
        _update_ui(client, shown, area, scene, peer_id, current, now)
        return
    if not _s.loaded or scene.session_uid != _s.scene_uid:
        if not _load(client, scene):
            return
    mode = ("scene", animate())
    view = _s.peer_state.get(peer_id, (None, None))[1]
    if _s.mode_sent != mode or (view == "game" and now - _s.mode_at > RESEND_AFTER):
        if client.send({"t": "view.mode", "mode": "scene", "animate": mode[1], "to": peer_id}):
            _s.mode_sent, _s.mode_at = mode, now
            _s.peer_state[peer_id] = (_s.peer_state.get(peer_id, (None, None))[0], "scene")
            _s.camera_key = None
    if _s.camera_pending is None:  # drawing the region gives it first (note_view); else the view now
        _s.camera_pending = view_camera(shown[2] if shown is not None else area)
    flush_camera()
    if _s.camera_key is None or shown is None:
        _set_status(LOADING, "info")
        return
    _s.wants = True
    frame_id = getattr(current, "frame_id", -1)
    _s.showing = frame_id > _s.frame_mark
    _set_status("" if _s.showing else LOADING, "info")


def _update_ui(client, shown, area, scene, peer_id, current, now) -> None:
    """A UI file: the page draws the active screen as the game does (view.mode ui), over the start
    level, while the view looks through the screen's camera; else the area says how to get there."""
    if not _s.loaded and not _load_level(client):
        return
    if scene.name != _s.ui_canvas:  # another screen: its edits reach the page too (the UI live link)
        _s.ui_canvas = scene.name
        props = getattr(scene, "gs", None)
        if props is not None and not props.live_enabled:
            props.live_enabled = True
    target = shown[2] if shown is not None else area
    message = ui_message(scene, _space(target), main_region(target), background=_s.background)
    if message is None:
        _s.ui_pending = None
        _set_status(UI_HINT, "hint")
        return
    view = _s.peer_state.get(peer_id, (None, None))[1]
    if view not in (None, "ui") and now - _s.mode_at > RESEND_AFTER:
        _s.ui_key = None  # the page left the ui view (a tool's scene shot): again
    _s.ui_pending = message
    flush_ui()
    if _s.ui_key is None or shown is None:
        _set_status(LOADING, "info")
        return
    _s.wants = True
    _s.showing = getattr(current, "frame_id", -1) > _s.frame_mark
    _set_status("" if _s.showing else LOADING, "info")


def _load_level(client) -> bool:
    """A UI file's page: the start level's last export to draw the screen over (scene.load), or a
    checkerboard when it has none."""
    root = project.project_root()
    level = start_level_export(root) if root is not None else None
    if level is not None and not client.send({"t": "scene.load", **level, "to": _s.peer}):
        return False
    _s.loaded, _s.background, _s.ui_key = True, "level" if level is not None else "checker", None
    return True


def flush_ui() -> None:
    """Send the pending view.mode ui when it differs from the last, at most CAMERA_RATE a second."""
    message = _s.ui_pending
    if message is None or _s.peer is None:
        return
    key = _ui_key(message)
    if key == _s.ui_key:
        _s.ui_pending = None
        return
    now = time.monotonic()
    if now - _s.ui_at < 1.0 / CAMERA_RATE:
        return
    client = link.get_link()
    if client is not None and client.send({**message, "to": _s.peer}):
        _s.ui_key, _s.ui_at, _s.ui_pending = key, now, None
        _s.mode_sent, _s.mode_at = ("ui",), now
        _s.peer_state[_s.peer] = (_s.peer_state.get(_s.peer, (None, None))[0], "ui")


def _check_stuck(current, address, now) -> None:
    """A page the host loaded that never joins the live link is loaded again, at most MAX_RETRIES
    times (Vite's very first start can leave it broken: it optimizes its dependencies meanwhile)."""
    if getattr(current, "status", "") != "running" or not address:
        _s.stuck_since = None
        return
    if _s.stuck_since is None:
        _s.stuck_since = now
    elif now - _s.stuck_since >= STUCK_SECONDS and _s.retries < MAX_RETRIES:
        _s.retries += 1
        _s.stuck_since = now
        log.warning("The game page did not join the live link in %d s: loading it again", STUCK_SECONDS)
        current.navigate(scene_url(address, retry=_s.retries))


def _load(client, scene) -> bool:
    """Export the scene (only what changed) and load it in the page."""
    try:
        result = export.export_scene(scene)
    except (export.ExportError, OSError, RuntimeError) as exc:
        _s.error = f"Could not export the scene: {exc}"
        _set_status(_s.error, "error")
        return False
    if not client.send({**export.scene_load_message(result), "to": _s.peer}):
        return False
    _s.loaded, _s.scene_uid = True, scene.session_uid
    return True


def view_camera(area):
    """The view.camera message for an area's main view, or None (no 3D View data yet)."""
    space = _space(area)
    r3d = getattr(space, "region_3d", None) if space is not None else None
    if r3d is None:
        return None
    return camera_message(r3d.view_matrix, r3d.window_matrix)


def note_view(region_data, region=None, space=None, scene=None) -> None:
    """The Scene view's region is drawing with this view (editors/scene_view.py PRE_VIEW): the
    freshest camera, or in a UI file the screen's frame in it; sent now if the rate allows, else by
    the next pump."""
    if not _s.area or _s.playing or _s.peer is None:
        return
    if _ui_file():
        if not _s.loaded or region is None or scene is None:
            return
        try:
            message = ui_message(scene, space, region, region_data, background=_s.background)
        except (AttributeError, ValueError, ZeroDivisionError):
            return
        if message is not None:  # out of camera view: the next pump says so
            _s.ui_pending = message
            flush_ui()
        return
    try:
        _s.camera_pending = camera_message(region_data.view_matrix, region_data.window_matrix)
    except (AttributeError, ValueError, ZeroDivisionError):
        return
    flush_camera()


def flush_camera() -> None:
    message = _s.camera_pending
    if message is None or _s.peer is None or _s.mode_sent is None or _s.mode_sent[0] != "scene":
        return
    key = _camera_key(message)
    if key == _s.camera_key:
        _s.camera_pending = None
        return
    now = time.monotonic()
    if now - _s.camera_at < 1.0 / CAMERA_RATE:
        return
    client = link.get_link()
    if client is not None and client.send({**message, "to": _s.peer}):
        _s.camera_key, _s.camera_at, _s.camera_pending = key, now, None


# --- Timer and handlers ------------------------------------------------------------------------


def _ensure_timer() -> None:
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.0, persistent=True)


def _timer():
    try:
        pump()
    except Exception:  # noqa: BLE001 - never let the timer die
        log.exception("Scene view update failed")
    return TIMER_INTERVAL if _s.area else None


@persistent
def _on_save_pre(*_args):
    """The .blend never keeps the Scene view's settings: they are put back while the file is written."""
    found = find_area()
    if found is None:
        return
    space = _settings_space(found)
    _s.saving = True
    _set_frame_settings(space, False)
    _restore(space, [path for path, _value in BASE_SETTINGS])


@persistent
def _on_save_post(*_args):
    if not _s.saving:
        return
    _s.saving = False
    found = find_area()
    if found is not None:
        space = _settings_space(found)
        _apply(space, BASE_SETTINGS)
        _set_frame_settings(space, _s.showing)


@persistent
def _on_load_pre(*_args):
    """Another file: its screens replace these, and the Scene view is off."""
    _forget()


_HANDLERS = (("save_pre", _on_save_pre), ("save_post", _on_save_post), ("load_pre", _on_load_pre))


def register():
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)
    link.get_link().on("game.status", _on_game_status)


def unregister():
    disable()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    link.get_link().off("game.status", _on_game_status)
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    _s.session = None
    _s.peer_state.clear()
