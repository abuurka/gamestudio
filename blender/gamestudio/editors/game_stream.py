# SPDX-License-Identifier: GPL-3.0-or-later
"""The game streamed into Blender: the Game View, its input, and the Scene | Game switch.

Blender's Python API cannot show a web page. With the Embedded game display (prefs.game_display)
Play runs the game in a browser GameStudio controls (core/stream.py) and this module shows its
frames in a Blender area, and sends that area's input back to the game:

  Game View   while the game runs, the largest 3D View of the window becomes an Image Editor that
              shows no image: a draw handler draws the latest frame as a GPU texture, letterboxed
              to the region, and the region is redrawn only when a new frame came. No image
              datablock is made and nothing is saved in the .blend (a save turns the area back
              into the 3D View for the time it takes). Stop turns the area back into the 3D View;
              an area keeps each editor's data, so the view (angle, shading) is the one it left.
  Scene|Game  in the header: the 3D View again while the game keeps running, its stream paused
              (also while no window shows the area), and back to the game.
  warm        Stop keeps the stream host and its page, hidden: the next Play turns the largest 3D
              View into the Game View again and only reloads the scene there, without starting a
              browser (seconds saved). While no Game View shows the game its frames stop; while it
              is also not played (stopped) its page is hidden and renders nothing, so a waiting
              game costs next to nothing. The game ends, and its browser with it, when the user closes the Game View
              while it shows the game (its X button, the area closed or made another editor),
              another project opens, Blender quits, the stream host dies (the next Play starts a
              new one), or after the game_idle_minutes preference without Play. Loading another
              file of the same project keeps it for the next Play.
  input       a click on the game captures the mouse and keyboard: a gizmo covering the frame
              starts the capture operator (gizmos come before the Image Editor's active tool,
              which would take the click). Then everything goes to the game, in frame pixels,
              except the play keys (F5-F8), until Shift+Esc or a click outside the Game View; Esc
              alone goes to the game, which uses it for its pause menu. Moving the mouse out of
              the area, or the window losing focus, lifts the held keys and buttons in the game.
              Pointer lock is not forwarded: a game that locks the pointer gets absolute
              positions only (no relative mouse look).
  resize      the stream follows the region's shape, debounced, at most MAX_PIXELS (decoding a
              bigger frame costs more than the GPU scaling it up to the region).
  Scene view  the same game also feeds the Scene view (core/sceneview.py, editors/scene_view.py: a
              3D View drawn by the game's renderer while it is not played). It registers itself with
              set_viewer(); while it wants frames (frame_region()) and no Game View shows the game,
              the frames go on for its region, at its size and frame rate (max_fps), and its page is
              not hidden while it is on (active()). start() and ensure() then take its region for
              the size, since no Game View is open, and an address that differs only by the Scene
              view's ?gs_view does not reload the page.

Why an Image Editor: it is a plain 2D canvas whose region draws next to nothing, so redraws at the
game's frame rate cost little (a 3D View would draw the scene under the game every frame), its
pan and zoom do not apply to what the handler draws, and its header is replaced with GameStudio's
while the area shows the game. core/play.py decides when the Game View opens and closes.
"""

import pathlib
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, StringProperty

from ..core import link, project
from ..core.log import get_logger
from ..core.util import any_window
from ..prefs import get_prefs

log = get_logger(__name__)

EDITOR = "IMAGE_EDITOR"
SCENE = "VIEW_3D"
MAX_PIXELS = 1280 * 720   # the stream's largest size; the region shows it scaled
MIN_SIZE = 16
RESIZE_DELAY = 0.3        # seconds the region keeps a size before the stream follows it
FRAME_INTERVAL = 1 / 120  # timer while frames come
IDLE_INTERVAL = 0.1
CHECK_INTERVAL = 0.5      # how often the timer checks the project and the idle time
HINT_SECONDS = 4.0        # how long "Shift+Esc releases" shows after a capture
RELEASE_HINT = "Shift+Esc gives the mouse and keyboard back to Blender"
HOVER_HINT = "Click to play. Shift+Esc then gives the mouse and keyboard back to Blender"
STOPPED_HINT = "Stopped. Play runs the game again at once"
SOFTWARE_HINT = "The browser draws the game without the GPU (software rendering): expect a low frame rate"
MOUSE_BUTTONS = {"LEFTMOUSE": 0, "MIDDLEMOUSE": 1, "RIGHTMOUSE": 2, "BUTTON4MOUSE": 3, "BUTTON5MOUSE": 4}
WHEEL_STEP = 100.0        # DOM deltaY of one wheel notch
PLAY_OPERATORS = ("gamestudio.play", "gamestudio.pause", "gamestudio.step", "gamestudio.stop")
PLAY_KEYS = {"F5", "F6", "F7", "F8"}


class GameStreamError(RuntimeError):
    pass


class _State:
    def __init__(self):
        self.area = 0             # as_pointer() of the Game View's area; 0: none
        self.screen = ""          # the name of the screen that has it
        self.shown = "GAME"       # what the area shows: "GAME" (Image Editor) or "SCENE" (3D View)
        self.session = None       # the stream.Session
        self.root = None          # the project it plays (another project ends it)
        self.active_at = 0.0      # when the game was last played (the idle timeout counts from it)
        self.checked_at = 0.0     # when the timer last checked the project and the idle time
        self.closed_peer = None   # the live-link peer of the game that was just ended, while it leaves
        self.peers_before = set()  # game peers on the live link before the session started
        self.paused = False       # the stream is paused (nobody sees it)
        self.suspended = False    # and its page hidden (nobody plays it either)
        self.captured = False
        self.captured_at = 0.0
        self.hover = False        # the mouse is over the frame (the gizmo is highlighted)
        self.texture = None       # (frame_id, GPUTexture, width, height)
        self.floats = None        # numpy scratch for uploads
        self.drawn_id = -1        # the frame id the region was last tagged for
        self.size = (0, 0)        # the size the stream was asked for
        self.region_size = (0, 0)
        self.region_since = 0.0
        self.saving = False       # the area is the 3D View while the file saves
        self.restore = []         # (screen name, area pointer) still to turn back into the 3D View
        self.status_shown = None  # what the header last showed
        self.fps_shown = 0
        self.draw_failed = False
        self.max_fps = 60         # the frame rate cap the session was given (stream.start's default)


_s = _State()
_draw_handle = None
_header_draw = None  # IMAGE_HT_header's own draw, wrapped while GameStudio is enabled
_viewer = None       # the Scene view (core/sceneview.py) once editors/scene_view.py registered it
GAME_VIEW_FPS = 60   # the Game View's frame rate cap


def set_viewer(viewer) -> None:
    """The Scene view: frame_region() (the region that wants frames now, or None), active() (on and
    shown), max_fps(). None removes it."""
    global _viewer
    _viewer = viewer


def _viewer_call(name: str, default):
    viewer = _viewer
    if viewer is None:
        return default
    try:
        return getattr(viewer, name)()
    except Exception:  # noqa: BLE001 - the Scene view must never break the Game View
        log.exception("The Scene view's %s failed", name)
        return default


def _viewer_region():
    """The Scene view's region while it wants the game's frames, else None."""
    return _viewer_call("frame_region", None)


def _same_game(a, b) -> bool:
    from ..core import sceneview

    return sceneview.same_game(a, b)


def _stream():
    """core/stream.py, or None when this build has no stream client."""
    try:
        from ..core import stream
    except ImportError:
        return None
    return stream


def headless() -> bool:
    """Blender runs without windows (--background): there is nothing to stream into."""
    return bool(bpy.app.background)


def available() -> tuple[bool, str]:
    """(the game can stream into Blender, why not)."""
    stream = _stream()
    if stream is None:
        return False, "this GameStudio has no game stream client"
    try:
        return stream.available()
    except Exception as exc:  # noqa: BLE001 - a broken client must not break Play
        return False, str(exc)


# --- Geometry ----------------------------------------------------------------------------------


def stream_size(width: int, height: int) -> tuple[int, int]:
    """The stream's size for a region of width x height: the same shape, at most MAX_PIXELS."""
    stream = _stream()
    if stream is not None and hasattr(stream, "fit_size"):
        return tuple(stream.fit_size(width, height, max_pixels=MAX_PIXELS))
    width, height = max(int(width), MIN_SIZE), max(int(height), MIN_SIZE)
    factor = min(1.0, (MAX_PIXELS / (width * height)) ** 0.5)
    return max(MIN_SIZE, int(width * factor)), max(MIN_SIZE, int(height * factor))


def fit(frame_w: float, frame_h: float, region_w: float, region_h: float) -> tuple[float, float, float, float]:
    """The frame letterboxed in the region, its aspect kept: (x, y, width, height) in region
    pixels from the bottom left."""
    if frame_w <= 0 or frame_h <= 0 or region_w <= 0 or region_h <= 0:
        return 0.0, 0.0, 0.0, 0.0
    scale = min(region_w / frame_w, region_h / frame_h)
    width, height = frame_w * scale, frame_h * scale
    return (region_w - width) / 2, (region_h - height) / 2, width, height


def to_frame(rect, frame_size, x: float, y: float) -> tuple[float, float, bool]:
    """A region point (pixels from the bottom left) in frame pixels from the top left, clamped to
    the frame, and whether it is on the frame (not on the bars around it)."""
    left, bottom, width, height = rect
    frame_w, frame_h = frame_size
    if width <= 0 or height <= 0:
        return 0.0, 0.0, False
    u, v = (x - left) / width, (bottom + height - y) / height
    inside = 0.0 <= u < 1.0 and 0.0 <= v < 1.0
    u, v = min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
    return u * frame_w, v * frame_h, inside


def frame_size() -> tuple[int, int]:
    """The size of the frame on screen: the last one drawn, else the size asked for."""
    if _s.texture is not None:
        return _s.texture[2], _s.texture[3]
    return _s.size


# --- The Game View's area ----------------------------------------------------------------------


def _main_region(area):
    return next((r for r in area.regions if r.type == "WINDOW"), None) if area is not None else None


def is_game_area(area, screen=None) -> bool:
    """`area` is the Game View, or the same area maximized (Ctrl+Space: a copy in a screen named
    "<screen>-nonnormal")."""
    if area is None or not _s.area:
        return False
    if area.as_pointer() == _s.area:
        return True
    return (screen is not None and area.type == EDITOR and getattr(screen, "show_fullscreen", False)
            and screen.name == f"{_s.screen}-nonnormal" and len(screen.areas) == 1)


def _screen(name: str):
    return bpy.data.screens.get(name)


def _windows() -> list:
    wm = getattr(bpy.context, "window_manager", None)
    return list(wm.windows) if wm is not None else []


def _override(window, screen, area):
    return bpy.context.temp_override(window=window, screen=screen, area=area)


def _find(screen_name: str, pointer: int):
    """The area with that pointer in that screen, or None (areas are looked up, never kept: a
    Python reference to a closed area is not safe to touch)."""
    screen = _screen(screen_name)
    area = next((a for a in screen.areas if a.as_pointer() == pointer), None) if screen is not None else None
    return screen, area


def find_area():
    """(window or None, screen, area) of the Game View, or None when it is gone."""
    if not _s.area:
        return None
    screen, area = _find(_s.screen, _s.area)
    if area is None:
        return None
    return _window_showing(screen), screen, area


def _window_showing(screen):
    return next((w for w in _windows() if w.screen == screen), None)


def set_area_type(window, screen, area, ui_type: str) -> bool:
    """Change an area's editor. Blender sets the area up again through the context window, so this
    is done only while `window` shows `screen`, with it as the context (as workspaces.set_region_shown
    does); returns False until then."""
    if area.ui_type == ui_type:
        return True
    if window is None or window.screen != screen:
        return False
    try:
        with _override(window, screen, area):
            area.ui_type = ui_type
    except (TypeError, RuntimeError) as exc:
        log.warning("Could not switch the area to %s: %s", ui_type, exc)
        return False
    return area.ui_type == ui_type


def _choose_area(window):
    """The largest 3D View of the window's screen."""
    screen = window.screen
    areas = [a for a in screen.areas if a.type == SCENE]
    return max(areas, key=lambda a: a.width * a.height) if areas else None


def view_open() -> bool:
    return find_area() is not None


def open_view(window=None) -> str:
    """Show the Game View: the largest 3D View of `window` (the context window, else the first
    one) becomes it, or the open one shows the game again. Returns "" or why it could not."""
    found = find_area()
    if found is not None:
        if found[0] is not None:
            show("GAME")
            return ""
        # No window shows it (another workspace): it goes back to the 3D View once one does, and
        # the running game moves here.
        if found[2].type == EDITOR:
            _s.restore.append((found[1].name, found[2].as_pointer()))
        _s.area = 0
    window = window or any_window()
    if window is not None and getattr(window.screen, "is_temporary", False):
        window = next((w for w in _windows() if not w.screen.is_temporary), None)
    if window is None:
        return "Blender has no window to show it in"
    area = _choose_area(window)
    if area is None:
        return "the window has no 3D Viewport to show it in"
    screen = window.screen
    if not set_area_type(window, screen, area, EDITOR):
        return "the 3D Viewport could not show it"
    _s.area, _s.screen, _s.shown = area.as_pointer(), screen.name, "GAME"
    _s.status_shown = None
    space = area.spaces.active
    if getattr(space, "ui_mode", "VIEW") != "VIEW":
        with _override(window, screen, area):
            space.ui_mode = "VIEW"  # Paint or Mask mode would act on clicks around the frame
    _set_stream(paused=False)  # a game kept from the last Play: its frames again
    _ensure_timer()
    return ""


def show(view: str) -> bool:
    """Show "GAME" or "SCENE" in the Game View's area. The game keeps running either way."""
    found = find_area()
    if found is None:
        return False
    window, screen, area = found
    if not set_area_type(window, screen, area, EDITOR if view == "GAME" else SCENE):
        return False
    _s.shown = view
    if view != "GAME":
        _s.captured = False
    _set_stream(paused=view != "GAME", suspended=view != "GAME" and not in_use())
    _tag_area(area)
    _ensure_timer()
    return True


def _set_stream(paused: bool, suspended: bool = False) -> None:
    """Frames only while a Game View shows the game; a hidden game nobody plays is also suspended
    (the host hides its page, which then renders nothing, until frames restart), so a waiting game
    costs next to nothing; its live link keeps running."""
    current = _s.session
    if paused and _viewer_region() is not None:
        paused = False  # the Scene view shows the game's frames
    if suspended and _viewer_call("active", False):
        suspended = False  # the Scene view is on: its page renders again in a moment
    suspended = paused and suspended
    if (current is None or getattr(current, "status", "") not in ("starting", "running")
            or (paused, suspended) == (_s.paused, _s.suspended)):
        return
    _s.paused, _s.suspended = paused, suspended
    current.set_paused(paused, suspend=suspended)


def _restore_pending() -> None:
    """Turn the areas the Game View left back into 3D Views, as soon as a window shows them."""
    for item in list(_s.restore):
        screen, area = _find(*item)
        if area is None or area.type != EDITOR:
            _s.restore.remove(item)  # gone, or the user made it something else
        elif set_area_type(_window_showing(screen), screen, area, SCENE):
            _s.restore.remove(item)


# --- The session -------------------------------------------------------------------------------


def session():
    return _s.session


def active() -> bool:
    """A stream session is starting or running."""
    return _s.session is not None and getattr(_s.session, "status", "") in ("starting", "running")


def failure() -> str:
    """The session's error, or ""."""
    current = _s.session
    if current is not None and getattr(current, "status", "") == "error":
        return getattr(current, "message", "") or "the game stream failed"
    return ""


def stream_peer(client=None):
    """The live-link peer of the game streamed into Blender, or None (not started, still loading, or
    ended). The host marks the page's user agent, which the engine sends when it joins the link; a
    host without the mark: the game peer that joined since the session started."""
    current = _s.session
    if current is None or getattr(current, "status", "") not in ("starting", "running"):
        return None
    client = client if client is not None else link.get_link()
    if client is None or not client.connected:
        return None
    agent = getattr(current, "agent", "")
    if agent:
        return next((p for p in client.game_peers() if agent in str(p.get("client", ""))), None)
    return next((p for p in client.game_peers() if p.get("peer") not in _s.peers_before), None)


def closed_peer(client=None):
    """The live-link peer id of the streamed game that was just ended, while it is still on the link
    (it leaves in a moment: until then Play must not count it as a game), else None."""
    gone = _s.closed_peer
    if gone is None:
        return None
    client = client if client is not None else link.get_link()
    if client is None or not client.connected or all(p.get("peer") != gone for p in client.game_peers()):
        _s.closed_peer = None
        return None
    return gone


def start(url: str | None, *, project_root=None, region=None) -> None:
    """Start streaming the game at `url` into the open Game View, or for `region` (the Scene view's)
    when given (a running session is replaced). Without a `url` the browser starts and navigate()
    gives it the address later. Raises GameStreamError (or stream.StreamError) when it cannot start."""
    stream = _stream()
    if stream is None:
        raise GameStreamError("this GameStudio has no game stream client")
    if region is None:
        found = find_area()
        if found is None:
            raise GameStreamError("no Game View is open")
        region = _main_region(found[2])
    _stop_session()
    width, height = stream_size(region.width if region else 1280, region.height if region else 720)
    client = link.get_link()
    _s.peers_before = {p.get("peer") for p in client.game_peers()} if client is not None and client.connected else set()
    _s.session = stream.start(url, width, height, project_root=project_root, on_status=_on_status)
    _s.session.float_frames = True  # the reader thread makes the floats the texture upload takes
    _s.session.keep_on_load = True  # a file of the same project keeps it (_on_load_post)
    _s.root = pathlib.Path(project_root) if project_root is not None else None
    _s.active_at = time.monotonic()
    _s.size = (width, height)
    _s.region_size = (region.width, region.height) if region else (0, 0)
    _s.paused = _s.suspended = False
    _s.drawn_id = -1
    _s.max_fps = GAME_VIEW_FPS
    _ensure_timer()


def ensure(url: str | None, *, project_root=None, region=None) -> bool:
    """The game in the Game View (or for the Scene view's `region`): the running one when it plays
    this project (a new `url` sends it there: the dev server moved), else a new session for `url`
    (see start()). Returns True when a new session started. Raises like start()."""
    root = pathlib.Path(project_root) if project_root is not None else None
    if active() and _s.root == root:
        navigate(url)
        return False
    start(url, project_root=project_root, region=region)
    return True


def navigate(url: str | None) -> None:
    """Send the game's browser to `url` (a session started without the address, or a new one). An
    address that differs only by the Scene view's ?gs_view keeps the page: view.mode switches it."""
    current = _s.session
    if current is not None and url and not _same_game(getattr(current, "url", url), url):
        current.navigate(url)


def touch() -> None:
    """Play: the idle timeout starts again, and a suspended page shows again at once."""
    _s.active_at = time.monotonic()
    if _s.suspended:
        _set_stream(paused=_s.paused)


def stopped() -> None:
    """Stop: the Game View turns back into the 3D View, with the view it left, and the mouse and
    keyboard are Blender's again. The stopped game keeps running hidden, its frames stopped and its
    page hidden, for the next Play; the idle timeout counts from now."""
    _s.active_at = time.monotonic()
    close_view()
    _set_stream(paused=True, suspended=True)


def _on_status(*_args) -> None:
    # Possibly on the client's thread: only flag a redraw, the timer does the rest.
    _s.status_shown = None


def _stop_session() -> None:
    if _s.session is not None:
        peer = stream_peer()
        _s.closed_peer = peer.get("peer") if peer else None
    current, _s.session = _s.session, None
    _s.root = None
    _s.captured = False
    _s.texture = None
    _s.peers_before = set()
    if current is not None:
        try:
            current.stop()
        except Exception:  # noqa: BLE001 - stopping must never fail Stop
            log.exception("Could not stop the game stream")


def close_view() -> None:
    """Turn the Game View back into the 3D View (now, or once a window shows it). The game keeps
    running, unseen (no frames), for the next Play. This is not the user closing the Game View: the
    area is no longer the game's, so nothing it becomes ends the game."""
    _s.captured = False  # the capture operator ends on its next event
    found = find_area()
    if found is not None:
        window, screen, area = found
        if area.type == EDITOR and not set_area_type(window, screen, area, SCENE):
            _s.restore.append((screen.name, area.as_pointer()))
        _tag_area(area)
    _s.area, _s.screen, _s.shown = 0, "", "GAME"
    _s.status_shown = None
    _s.texture, _s.drawn_id, _s.floats = None, -1, None  # the next Play shows new frames, not this one
    _set_stream(paused=True, suspended=not in_use())
    if _s.restore:
        _ensure_timer()


def close() -> None:
    """End the game (its browser too) and turn the Game View back into the 3D View (now, or once a
    window shows it)."""
    _stop_session()
    close_view()
    _s.size = (0, 0)


def _end_game(why: str) -> None:
    """End the game and forget a Play that waits for it (the Game View closed, another project)."""
    from ..core import play

    log.info(why)
    play.cancel()  # close(): the session, and the area if it is still the Game View


def status_text() -> tuple[str, str]:
    """(text, icon) for the Game View's header and overlay: while Play is under way, what it waits
    for (play.status()); then how to play, or that the game is stopped."""
    from ..core import play

    progress = play.status() if play.is_pending() else ""
    current = _s.session
    if current is None:
        return (play.status() or "Starting the game..."), "SORTTIME"
    status = getattr(current, "status", "")
    if status == "error":
        return (getattr(current, "message", "") or "The game stream failed"), "ERROR"
    if status == "stopped":
        return "Stopped", "SNAP_FACE"
    if status != "running" or _s.texture is None:
        return (progress or getattr(current, "message", "") or "Starting the game..."), "SORTTIME"
    if progress:
        return progress, "SORTTIME"
    if _s.captured:
        return "Playing: Shift+Esc releases", "MOUSE_LMB"
    if game_stopped():
        return "Stopped: Play runs it again", "SNAP_FACE"
    return "Click the game to play", "MOUSE_LMB"


def detail_text() -> str:
    """The stream's own message, under the overlay's status while the game starts ("Loading ...")."""
    current = _s.session
    message = getattr(current, "message", "") if current is not None else ""
    return message if message and message != status_text()[0] else ""


def game_stopped() -> bool:
    """The streamed game is on the live link and stopped (Stop: it waits for the next Play)."""
    client = link.get_link()
    return bool(client is not None and client.connected and client.game_state == "stopped"
                and stream_peer(client) is not None)


def in_use() -> bool:
    """The game is being played: Play is under way, the game runs or is paused, or it has the
    mouse and keyboard. The idle timeout does not count that time."""
    from ..core import play

    if play.is_pending() or _s.captured:
        return True
    client = link.get_link()
    return bool(client is not None and client.connected and client.game_state in ("playing", "paused", "loading")
                and stream_peer(client) is not None)


def idle_minutes() -> int:
    """The game_idle_minutes preference: minutes without Play before the game closes (0: never)."""
    try:
        return max(0, int(getattr(get_prefs(), "game_idle_minutes", 15)))
    except (TypeError, ValueError):
        return 15


def _housekeeping(now: float) -> None:
    """End the game when another project is open (Save As elsewhere, a file of another project),
    or when it was not played for the idle time."""
    if _s.root is not None and project.project_root() != _s.root:
        _end_game("Another project is open: the game streamed into Blender ends")
        return
    if in_use() or _viewer_call("is_on", False):
        _s.active_at = now  # played, or drawn by the Scene view: not idle
        return
    minutes = idle_minutes()
    if minutes and now - _s.active_at >= minutes * 60:
        log.info("The game streamed into Blender was not played for %d minutes: it ends", minutes)
        close()
        notify(f"Closed the game after {minutes} minutes without Play; the next Play starts it again "
               "(Preferences > Add-ons > GameStudio > Play)", "INFO")


# --- Timer: poll, redraw, pause, resize --------------------------------------------------------


def _ensure_timer() -> None:
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.0, persistent=True)


def _timer():
    try:
        return _update()
    except Exception:  # noqa: BLE001 - never let the timer die
        log.exception("Game stream update failed")
        return 0.5


def _maximized():
    """The Game View's maximized copy (Ctrl+Space) a window shows, or None."""
    for window in _windows():
        screen = window.screen
        if screen is not None and len(screen.areas) == 1 and is_game_area(screen.areas[0], screen):
            return screen.areas[0]
    return None


def _tag_area(area) -> None:
    for region in area.regions:
        if region.type in ("WINDOW", "HEADER"):
            region.tag_redraw()


def _update():
    if _s.restore:
        _restore_pending()
    now = time.monotonic()
    if _s.session is not None and now - _s.checked_at >= CHECK_INTERVAL:
        _s.checked_at = now
        _housekeeping(now)
    current = _s.session
    found = find_area()
    if _s.area and found is None:
        # The area was closed (joined, or its window or workspace closed): the game goes with it.
        _end_game("The Game View was closed: the game ends")
        current = None
    elif found is not None and found[0] is not None and found[2].type not in (SCENE, EDITOR):
        _end_game("The Game View became another editor: the game ends")  # left as the user made it
        current, found = None, None
    if current is None and found is None:
        return IDLE_INTERVAL if _s.restore else None
    if current is not None:
        try:
            current.poll()
        except Exception:  # noqa: BLE001
            log.exception("The game stream's poll failed")
    window, _screen, area = found if found is not None else (None, None, None)
    if window is not None:
        _s.shown = "GAME" if area.type == EDITOR else "SCENE"  # also when picked from the editor menu
    area = area if window is not None else _maximized()
    visible = area is not None and area.type == EDITOR
    scene_region = None if visible or current is None else _viewer_region()
    if current is not None:
        # Frames while seen (the Game View, else the Scene view); hidden and not played (stopped, or
        # after Stop): suspended too.
        _set_stream(paused=not visible, suspended=not visible and not in_use())
        _set_fps(current, GAME_VIEW_FPS if visible else _viewer_call("max_fps", None) if scene_region else None)
    if scene_region is not None:
        return _update_scene_view(current, scene_region)
    if not visible:
        return IDLE_INTERVAL
    region = _main_region(area)
    shown = status_text()
    if shown != _s.status_shown:
        _s.status_shown = shown
        _tag_area(area)
    fps = round(getattr(current, "fps", 0.0)) if current is not None else 0
    if fps != _s.fps_shown:  # the header's frame rate
        _s.fps_shown = fps
        header = next((r for r in area.regions if r.type == "HEADER"), None)
        if header is not None:
            header.tag_redraw()
    if current is not None and region is not None:
        frame_id = getattr(current, "frame_id", -1)
        if frame_id != _s.drawn_id:
            _s.drawn_id = frame_id
            region.tag_redraw()
        _follow_size(current, region)
    running = current is not None and getattr(current, "status", "") == "running"
    return FRAME_INTERVAL if running else IDLE_INTERVAL


def _update_scene_view(current, region):
    """Frames for the Scene view: its region redraws for each new one and the stream follows its size."""
    frame_id = getattr(current, "frame_id", -1)
    if frame_id != _s.drawn_id:
        _s.drawn_id = frame_id
        region.tag_redraw()
    _follow_size(current, region)
    return FRAME_INTERVAL if getattr(current, "status", "") == "running" else IDLE_INTERVAL


def _set_fps(current, fps) -> None:
    """The stream's frame rate cap: the Game View's, or the Scene view's preference."""
    if not fps or fps == _s.max_fps or not hasattr(current, "set_quality"):
        return
    _s.max_fps = int(fps)
    try:
        current.set_quality(max_fps=_s.max_fps)
    except Exception:  # noqa: BLE001
        log.exception("Could not set the game stream's frame rate")


def _follow_size(current, region) -> None:
    """Resize the stream once the region kept a new size for RESIZE_DELAY."""
    size = (region.width, region.height)
    now = time.monotonic()
    if size != _s.region_size:
        _s.region_size, _s.region_since = size, now
        return
    wanted = stream_size(*size)
    if wanted != _s.size and now - _s.region_since >= RESIZE_DELAY:
        _s.size = wanted
        try:
            current.resize(*wanted)
        except Exception:  # noqa: BLE001
            log.exception("Could not resize the game stream")


# --- Drawing -----------------------------------------------------------------------------------


def _upload(width: int, height: int, pixels=None, floats=None):
    """A GPU texture of a frame (rows top to bottom). Blender's Python texture upload takes floats
    only: the stream's reader thread makes them (floats), else they are made here from RGBA8."""
    import gpu
    import numpy as np

    count = width * height * 4
    if floats is None:
        source = np.frombuffer(pixels, dtype=np.uint8, count=count)
        if _s.floats is None or _s.floats.size != count:
            _s.floats = np.empty(count, dtype=np.float32)
        np.multiply(source, np.float32(1.0 / 255.0), out=_s.floats, casting="unsafe")
        floats = _s.floats
    buffer = gpu.types.Buffer("FLOAT", count, floats)
    return gpu.types.GPUTexture((width, height), format="RGBA8", data=buffer)


def _texture():
    """The texture of the latest frame, uploaded when a new one came."""
    current = _s.session
    if current is None:
        return None
    frame_id = getattr(current, "frame_id", -1)
    if _s.texture is not None and _s.texture[0] == frame_id:
        return _s.texture
    floats = getattr(current, "latest_frame_float", lambda: None)()
    frame = floats if floats is not None else current.latest_frame()
    if frame is None:
        return _s.texture
    width, height, data = frame
    if width > 0 and height > 0:
        texture = _upload(width, height, floats=data) if floats is not None else _upload(width, height, pixels=data)
        _s.texture = (frame_id, texture, width, height)
    return _s.texture


def _rect(shader, x, y, width, height, color) -> None:
    from gpu_extras.batch import batch_for_shader

    batch = batch_for_shader(shader, "TRI_FAN", {"pos": ((x, y), (x + width, y), (x + width, y + height),
                                                         (x, y + height))})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _text(text: str, x: float, y: float, size: float, color=(1.0, 1.0, 1.0, 0.9), center=False) -> None:
    import blf

    font = 0
    blf.size(font, size)
    if center:
        x -= blf.dimensions(font, text)[0] / 2
    blf.enable(font, blf.SHADOW)
    blf.shadow(font, 5, 0.0, 0.0, 0.0, 0.8)
    blf.shadow_offset(font, 1, -1)
    blf.color(font, *color)
    blf.position(font, x, y, 0)
    blf.draw(font, text)
    blf.disable(font, blf.SHADOW)


def _draw_game(context) -> None:
    import gpu
    from gpu_extras.batch import batch_for_shader

    region = context.region
    scale = context.preferences.system.ui_scale or 1.0
    color = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    color.bind()
    back = context.preferences.themes[0].image_editor.space.back
    _rect(color, 0, 0, region.width, region.height, (*back[:3], 1.0))  # over any image the editor shows

    texture = _texture()
    if texture is not None:
        _frame_id, tex, width, height = texture
        x, y, w, h = fit(width, height, region.width, region.height)
        image = gpu.shader.from_builtin("IMAGE")
        batch = batch_for_shader(image, "TRI_FAN", {
            "pos": ((x, y), (x + w, y), (x + w, y + h), (x, y + h)),
            "texCoord": ((0, 1), (1, 1), (1, 0), (0, 0)),  # rows come top to bottom
        })
        image.bind()
        image.uniform_sampler("image", tex)
        batch.draw(image)

    text, icon = status_text()
    small, large = 11 * scale, 15 * scale
    current = _s.session
    running = texture is not None and current is not None and getattr(current, "status", "") == "running"
    if not running:
        alert = icon == "ERROR"
        _text(text, region.width / 2, region.height / 2, large, (1.0, 0.45, 0.4, 1.0) if alert else (1, 1, 1, 0.85),
              center=True)
        if alert:
            _text("Press Play to try again, or switch the game display to Window in the top bar",
                  region.width / 2, region.height / 2 - 2 * large, small, center=True)
        else:
            detail = detail_text()  # what the stream does meanwhile ("Loading http://...")
            if detail:
                _text(detail, region.width / 2, region.height / 2 - 2 * large, small, (1, 1, 1, 0.6), center=True)
    elif _s.captured:
        if time.monotonic() - _s.captured_at < HINT_SECONDS:
            _text(RELEASE_HINT, region.width / 2, 12 * scale, small, center=True)
    elif game_stopped():
        _text(STOPPED_HINT, region.width / 2, 12 * scale, small, center=True)
    elif _s.hover:
        _text(HOVER_HINT, region.width / 2, 12 * scale, small, center=True)
    if running and getattr(current, "gpu", None) is False:
        _text(SOFTWARE_HINT, 10 * scale, region.height - 20 * scale, small, (1.0, 0.8, 0.4, 0.9))
    if running and (_s.hover or bpy.app.debug_value or bpy.app.debug):
        fps = f"{getattr(current, 'fps', 0.0):.0f} fps  {texture[2]} x {texture[3]}"
        import blf

        blf.size(0, small)
        _text(fps, region.width - blf.dimensions(0, fps)[0] - 10 * scale, region.height - 20 * scale, small,
              (1, 1, 1, 0.7))
    gpu.state.blend_set("NONE")


def _draw() -> None:
    context = bpy.context
    if not is_game_area(context.area, context.screen) or context.region is None:
        return
    try:
        _draw_game(context)
    except Exception:  # noqa: BLE001 - say it once, not on every frame
        if not _s.draw_failed:
            _s.draw_failed = True
            log.exception("Could not draw the game")


# --- Headers -----------------------------------------------------------------------------------


def draw_switch(layout, current: str) -> None:
    """Scene | Game: the 3D View or the running game, in the same area."""
    row = layout.row(align=True)
    for view, text, icon in (("SCENE", "Scene", "SCENE_DATA"), ("GAME", "Game", "PLAY")):
        op = row.operator(GAMESTUDIO_OT_game_stream_show.bl_idname, text=text, icon=icon, depress=current == view)
        op.view = view


def draw_game_header(layout, context) -> None:
    """The Game View's header, in place of the Image Editor's."""
    layout.template_header()
    draw_switch(layout, "GAME")
    text, icon = status_text()
    row = layout.row()
    row.alert = icon == "ERROR"
    row.active = icon == "ERROR" or _s.captured
    row.label(text=text, icon=icon)
    layout.separator_spacer()
    current = _s.session
    if current is not None and getattr(current, "status", "") == "running":
        row = layout.row()
        row.active = False
        row.label(text=f"{getattr(current, 'fps', 0.0):.0f} fps")
    layout.operator(GAMESTUDIO_OT_game_stream_close.bl_idname, text="", icon="X")


def _image_header(self, context):
    if is_game_area(context.area, context.screen):
        draw_game_header(self.layout, context)
    elif _header_draw is not None:
        _header_draw(self, context)


def draw_view3d_header(self, context) -> None:
    """Prepended to the 3D View's header: Scene | Game in the Game View's area while it shows the scene."""
    if _s.area and is_game_area(context.area, context.screen):
        draw_switch(self.layout, "SCENE")


# --- Input -------------------------------------------------------------------------------------


def play_keys() -> set:
    """GameStudio's play shortcuts as (type, shift, ctrl, alt): F5-F8, or what the user made them."""
    wm = getattr(bpy.context, "window_manager", None)
    keyconfig = wm.keyconfigs.user if wm is not None else None
    keymap = keyconfig.keymaps.get("Window") if keyconfig is not None else None
    items = [i for i in keymap.keymap_items if i.idname in PLAY_OPERATORS and i.active] if keymap else []
    if not items:
        return {(key, False, False, False) for key in PLAY_KEYS}
    return {(i.type, bool(i.shift), bool(i.ctrl), bool(i.alt)) for i in items}


def _hit(context, location) -> bool:
    """A region point is on the game's frame (where a click captures)."""
    if _s.session is None or _s.captured or context.region is None:
        return False
    size = frame_size()
    rect = fit(size[0], size[1], context.region.width, context.region.height)
    return to_frame(rect, size, location[0], location[1])[2]


class GAMESTUDIO_GT_game_stream(bpy.types.Gizmo):
    """Covers the game's frame: a click on it runs the capture operator."""

    bl_idname = "GAMESTUDIO_GT_game_stream"

    def draw(self, _context):
        if self.is_highlight != _s.hover:
            _s.hover = self.is_highlight

    def test_select(self, context, location):
        return 0 if _hit(context, location) else -1


class GAMESTUDIO_GGT_game_stream(bpy.types.GizmoGroup):
    bl_idname = "GAMESTUDIO_GGT_game_stream"
    bl_label = "Game View Input"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT"}

    @classmethod
    def poll(cls, context):
        return _s.session is not None and is_game_area(context.area, context.screen)

    def setup(self, _context):
        gizmo = self.gizmos.new(GAMESTUDIO_GT_game_stream.bl_idname)
        gizmo.target_set_operator(GAMESTUDIO_OT_game_stream_capture.bl_idname)


class Capture:
    """One capture of the mouse and keyboard: which events go to the game, and in what form. The
    operator below feeds it Blender's events (tests feed it plain ones). `region` is the Game View's
    main region; handle() returns what the modal operator returns."""

    def __init__(self, region, keys=None):
        self.region = region
        self.play_keys = keys if keys is not None else play_keys()
        self.keys = {}          # DOM code -> key, held in the game
        self.buttons = set()    # DOM buttons held in the game
        self.last = (0.0, 0.0)  # the mouse in frame pixels
        self.inside = True      # the mouse is over the Game View

    def begin(self, event) -> None:
        _s.captured, _s.captured_at = True, time.monotonic()
        self.forward(event)  # the click that captured reaches the game too
        _tag_game()

    def end(self, pass_through=False) -> set:
        self.release()
        _s.captured = False
        _tag_game()
        return {"FINISHED", "PASS_THROUGH"} if pass_through else {"FINISHED"}

    def position(self, event) -> tuple[float, float, bool]:
        """(x, y) in frame pixels, and whether the mouse is over the Game View's region."""
        region = self.region
        x, y = event.mouse_x - region.x, event.mouse_y - region.y
        over = 0 <= x < region.width and 0 <= y < region.height
        size = frame_size()
        fx, fy, _on_frame = to_frame(fit(size[0], size[1], region.width, region.height), size, x, y)
        return fx, fy, over

    def release(self) -> None:
        """Lift every key and button held in the game, so none sticks."""
        current = _s.session
        keys, buttons = self.keys, self.buttons
        self.keys, self.buttons = {}, set()
        if current is None:
            return
        try:
            for code, key in keys.items():
                current.send_key("up", code, key)
            for button in buttons:
                current.send_mouse("up", *self.last, button=button)
            release = getattr(current, "release_input", None)
            if release is not None:
                release()  # the host lifts whatever else the page still sees held
        except Exception:  # noqa: BLE001
            log.exception("Could not release the game's keys")

    def handle(self, event) -> set:
        kind = event.type
        if kind in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
            x, y, over = self.position(event)
            if over:
                self.inside = True
                self.last = (x, y)
                _s.session.send_mouse("move", x, y, **_modifiers(event))
                return {"RUNNING_MODAL"}
            if self.inside:  # the mouse left the Game View: Blender's again until it comes back
                self.inside = False
                self.release()
            return {"PASS_THROUGH"}
        if kind == "WINDOW_DEACTIVATE":
            return self.end(pass_through=True)
        if not self.inside:
            if kind in MOUSE_BUTTONS and event.value == "PRESS":
                return self.end(pass_through=True)  # a click elsewhere gives Blender the input back
            return {"PASS_THROUGH"}
        if kind == "ESC" and event.shift and event.value == "PRESS":
            return self.end()
        if (kind, event.shift, event.ctrl, event.alt) in self.play_keys:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"} if self.forward(event) else {"PASS_THROUGH"}

    def forward(self, event) -> bool:
        """Send one event to the game; False when it is not for the game."""
        current = _s.session
        kind, value = event.type, event.value
        if current is None:
            return False
        modifiers = _modifiers(event)
        if kind in MOUSE_BUTTONS:
            x, y, _over = self.position(event)
            self.last = (x, y)
            button = MOUSE_BUTTONS[kind]
            if value in ("PRESS", "DOUBLE_CLICK"):
                self.buttons.add(button)
                current.send_mouse("down", x, y, button=button, **modifiers)
            elif value == "RELEASE":
                self.buttons.discard(button)
                current.send_mouse("up", x, y, button=button, **modifiers)
            return True
        if kind in ("WHEELUPMOUSE", "WHEELDOWNMOUSE"):
            x, y, _over = self.position(event)
            current.send_mouse("wheel", x, y, dy=-WHEEL_STEP if kind == "WHEELUPMOUSE" else WHEEL_STEP, **modifiers)
            return True
        if kind == "TRACKPADPAN":
            x, y, _over = self.position(event)
            current.send_mouse("wheel", x, y, dx=float(event.mouse_prev_x - event.mouse_x),
                               dy=float(event.mouse_y - event.mouse_prev_y), **modifiers)
            return True
        stream = _stream()
        mapped = stream.key_code(kind, event.shift) if stream is not None else None
        if mapped is None:
            return False
        code, key = mapped
        if value == "PRESS":
            typed = event.unicode
            if typed and len(typed) == 1 and typed.isprintable():
                key = typed  # the keyboard layout's character
            self.keys[code] = key
            current.send_key("down", code, key, repeat=event.is_repeat, **modifiers)
        elif value == "RELEASE":
            current.send_key("up", code, self.keys.pop(code, key), **modifiers)
        return True


class GAMESTUDIO_OT_game_stream_capture(bpy.types.Operator):
    bl_idname = "gamestudio.game_stream_capture"
    bl_label = "Play in the Game View"
    bl_description = ("Send the mouse and keyboard to the game. Shift+Esc, or a click outside the Game View, "
                      "gives them back to Blender; F5-F8 still control play")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return _s.session is not None and not _s.captured and is_game_area(context.area, context.screen)

    def invoke(self, context, event):
        self._capture = Capture(context.region)
        context.window_manager.modal_handler_add(self)
        self._capture.begin(event)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        capture = self._capture
        # Blender checks the handler's area and region still exist before it calls this (None if not).
        if (not _s.captured or _s.session is None or _s.shown != "GAME" or context.region is None
                or not is_game_area(context.area, context.screen)):
            return capture.end()  # released elsewhere (Stop, Scene, the area closed or changed)
        capture.region = context.region
        return capture.handle(event)

    def cancel(self, _context):
        _s.captured = False


def _modifiers(event) -> dict:
    return {"shift": event.shift, "ctrl": event.ctrl, "alt": event.alt}


def _tag_game() -> None:
    found = find_area()
    if found is not None:
        _tag_area(found[2])


# --- Operators ---------------------------------------------------------------------------------


class GAMESTUDIO_OT_game_stream_show(bpy.types.Operator):
    bl_idname = "gamestudio.game_stream_show"
    bl_label = "Show"
    bl_options = {"INTERNAL"}

    view: EnumProperty(items=(
        ("SCENE", "Scene", "The 3D Viewport; the game keeps running"),
        ("GAME", "Game", "The running game"),
    ), default="GAME")

    @classmethod
    def description(cls, _context, properties):
        if properties.view == "SCENE":
            return "Show the 3D Viewport here. The game keeps running; Game shows it again"
        return "Show the running game here"

    @classmethod
    def poll(cls, _context):
        return view_open()

    def execute(self, _context):
        if not show(self.view):
            self.report({"WARNING"}, "Could not switch the Game View")
            return {"CANCELLED"}
        return {"FINISHED"}


class GAMESTUDIO_OT_game_stream_close(bpy.types.Operator):
    bl_idname = "gamestudio.game_stream_close"
    bl_label = "Close Game"
    bl_description = ("End the game in the Game View, and its browser, and show the 3D Viewport here. Stop "
                      "keeps the game ready instead, so the next Play starts at once")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return view_open() or _s.session is not None

    def execute(self, _context):
        from ..core import play

        play.cancel()
        return {"FINISHED"}


DISPLAY_TIPS = {
    "EMBEDDED": "Game display: Embedded. Play streams the game into the 3D Viewport. Click to play it in its own window",
    "WINDOW": "Game display: Window. Play opens the game in its own window. Click to stream it into the 3D Viewport",
}
DISPLAY_ICONS = {"EMBEDDED": "IMAGE_BACKGROUND", "WINDOW": "WINDOW"}


def display() -> str:
    """The game_display preference: "EMBEDDED" or "WINDOW"."""
    return getattr(get_prefs(), "game_display", "EMBEDDED")


class GAMESTUDIO_OT_toggle_game_display(bpy.types.Operator):
    bl_idname = "gamestudio.toggle_game_display"
    bl_label = "Game Display"
    bl_description = "Where Play shows the game: streamed into the 3D Viewport, or in its own window"

    @classmethod
    def description(cls, _context, _properties):
        return DISPLAY_TIPS.get(display(), cls.bl_description)

    def execute(self, context):
        prefs = get_prefs()
        prefs.game_display = "WINDOW" if display() == "EMBEDDED" else "EMBEDDED"
        try:
            context.preferences.is_dirty = True  # saved with the preferences (Auto-Save Preferences)
        except AttributeError:
            pass
        self.report({"INFO"}, "Play streams the game into the 3D Viewport" if prefs.game_display == "EMBEDDED"
                    else "Play opens the game in its own window")
        return {"FINISHED"}


class GAMESTUDIO_OT_game_stream_note(bpy.types.Operator):
    """Shows a message from a timer in the status bar (timers cannot report)."""

    bl_idname = "gamestudio.game_stream_note"
    bl_label = "Game View"
    bl_options = {"INTERNAL"}

    message: StringProperty(options={"SKIP_SAVE"})
    level: StringProperty(default="WARNING", options={"SKIP_SAVE"})

    def execute(self, _context):
        self.report({self.level if self.level in ("INFO", "WARNING", "ERROR") else "INFO"}, self.message)
        return {"FINISHED"}


def notify(message: str, level: str = "WARNING") -> None:
    """Report `message` in the status bar and the Info log, from anywhere."""
    (log.warning if level != "INFO" else log.info)(message)
    if bpy.app.background:
        return

    def report():
        window = any_window()
        if window is not None:
            with bpy.context.temp_override(window=window):
                bpy.ops.gamestudio.game_stream_note(message=message, level=level)

    bpy.app.timers.register(report, first_interval=0.05)


# --- File handlers -----------------------------------------------------------------------------


@persistent
def _on_save_pre(*_args):
    """The .blend never keeps the Game View: the area is the 3D View while the file is written."""
    found = find_area()
    if found is not None and found[2].type == EDITOR:
        window, screen, area = found
        _s.saving = set_area_type(window, screen, area, SCENE)


@persistent
def _on_save_post(*_args):
    if _s.saving:
        _s.saving = False
        found = find_area()
        if found is not None and _s.shown == "GAME":
            set_area_type(found[0], found[1], found[2], EDITOR)


@persistent
def _on_load_pre(*_args):
    """Another file: its screens replace this one's, so the Game View is gone. The game keeps
    running, unseen, until load_post knows whether the file is in the same project."""
    _s.captured = False
    _s.area, _s.screen, _s.shown, _s.restore = 0, "", "GAME", []


@persistent
def _on_load_post(*_args):
    """A file of the same project keeps the game for the next Play; another project ends it."""
    if _s.session is not None and project.project_root() != _s.root:
        _end_game("A file of another project was opened: the game streamed into Blender ends")


@persistent
def _on_exit(*_args):
    _stop_session()


_HANDLERS = (("save_pre", _on_save_pre), ("save_post", _on_save_post), ("load_pre", _on_load_pre),
             ("load_post", _on_load_post), ("exit_pre", _on_exit))
_CLASSES = (
    GAMESTUDIO_OT_game_stream_capture,
    GAMESTUDIO_GT_game_stream,
    GAMESTUDIO_GGT_game_stream,
    GAMESTUDIO_OT_game_stream_show,
    GAMESTUDIO_OT_game_stream_close,
    GAMESTUDIO_OT_toggle_game_display,
    GAMESTUDIO_OT_game_stream_note,
)


def register():
    global _draw_handle, _header_draw
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
    header = bpy.types.IMAGE_HT_header
    if header.draw is not _image_header:
        _header_draw, header.draw = header.draw, _image_header
    bpy.types.VIEW3D_HT_header.prepend(draw_view3d_header)
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)


def unregister():
    global _draw_handle, _header_draw
    close()
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.types.VIEW3D_HT_header.remove(draw_view3d_header)
    header = bpy.types.IMAGE_HT_header
    if header.draw is _image_header and _header_draw is not None:
        header.draw, _header_draw = _header_draw, None
    # Else something wrapped it after GameStudio: _image_header stays in the chain and keeps
    # calling Blender's draw (no Game View is open any more).
    if _draw_handle is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
