# SPDX-License-Identifier: GPL-3.0-or-later
"""Play-session orchestration used by the Play operator.

A play request waits (asynchronously, via pump()) until the live link is
connected and a game peer is present, opening the game once if no game is
connected, and then sends `scene.load` followed by `play.start`.

The Play operator comes in two halves so that nothing waits in turn: prepare() starts what takes
long before the scene exports (the dev server when the live link is down, and in the Embedded
display the Game View and the game's browser, which starts without the game's address while the
dev server starts and goes there once it has one), then the operator exports, then request_play()
sends the scene at once if the game is ready, else pump() does as soon as it is. request_play()
alone (the agent bridge's play_in_blender) works the same, without the head start. While a play
waits, status() says for what (the status bar and the Game View show it). A game that is loaded
and stopped drops a play.start that comes while it reloads the scene: send_play() watches for that
and sends it again (_check_started).

Where the game shows is the game_display preference:

  EMBEDDED  streamed into Blender (editors/game_stream.py): the largest 3D View of the window
            becomes the Game View at once, and the scene loads once that game is on the live
            link. Stop (stop()) turns the Game View back into the 3D View and keeps the game
            running hidden (no frames; its page renders nothing), so the next Play only shows it
            and reloads the scene: no browser starts. The game ends (cancel(), or game_stream
            itself) when the user closes the Game View, the project changes, the dev server is
            stopped, Blender quits, or after the game_idle_minutes preference without Play. When
            this Blender cannot stream, or the stream fails while it starts, that one Play opens
            the game in a window instead, and says so.
  WINDOW    in its own window (devserver.open_game_window), if the open_game_window preference
            (the `open_window` argument) allows.

The shelved fork's docked Game View (host.game_view) comes first when its build has one and an
area shows it.

The Scene view (core/sceneview.py) uses the same streamed game while nothing is played, in scene
mode: send_play() first has it switch that page to game mode (view.mode), and stop() tells it, so it
takes the page back once the game has stopped.
"""

import time

from . import devserver, link, live, project
from .export import scene_load_message
from .log import get_logger
from .util import tag_redraw

log = get_logger(__name__)

PLAY_TIMEOUT = 120.0
TIMER_INTERVAL = 0.1
PENDING_INTERVAL = 0.05  # while a play waits: the scene goes out this soon after the game is ready
START_CHECK_TIMEOUT = 15.0  # how long after play.start a game still stopped after its reload gets it again

# What a waiting play waits for (status()).
EXPORTING = "Exporting the scene..."
STARTING_SERVER = "Starting the dev server..."
CONNECTING = "Connecting to the dev server..."
STARTING_VIEW = "Starting the game view..."
LOADING = "Loading the game..."
WAITING = "Waiting for the game..."

# {"result" (None while the scene exports), "start", "open_window", "display", "opened", "docked",
#  "new_view", "deadline"}
_pending = None
_status = ""      # short text for the UI
_starting = None  # after play.start: {"ready": the game.ready before, "deadline"} (_check_started)


def status() -> str:
    return _status


def is_pending() -> bool:
    return _pending is not None


def _set_status(text: str) -> None:
    global _status
    if text != _status:
        _status = text
        tag_redraw()


def cancel() -> None:
    """Forget a pending play, and end the game streamed into Blender (Stop Dev Server, Close Game,
    a failed start): the Game View turns back into the 3D View."""
    global _pending, _status, _starting
    _pending = None
    _status = ""
    _starting = None
    _game_stream().close()


def stop() -> None:
    """Stop: forget a pending play. The Game View turns back into the 3D View; the game streamed
    into Blender keeps running hidden, so the next Play reloads the scene in it instead of starting
    a browser."""
    global _pending, _status, _starting
    _pending = None
    _status = ""
    _starting = None
    _game_stream().stopped()
    _scene_view().on_stop()
    tag_redraw()


def abandon() -> None:
    """The export of a prepared play failed: forget the play. What prepare() started goes on (the
    next Play uses it); a Game View it opened turns back into the 3D View."""
    global _pending, _status
    pending, _pending = _pending, None
    _status = ""
    if pending is not None and pending.get("new_view"):
        _game_stream().close_view()
    tag_redraw()


def _game_stream():
    from ..editors import game_stream

    return game_stream


def _scene_view():
    from . import sceneview

    return sceneview


def embedded() -> bool:
    """Play streams the game into Blender: the Embedded game display, a Blender with windows, and no
    docked fork Game View."""
    from ..prefs import get_prefs

    return (getattr(get_prefs(), "game_display", "EMBEDDED") == "EMBEDDED" and not _game_stream().headless()
            and not can_dock())


def _fall_back(problem: str, open_window: bool) -> None:
    """Say once that this Play shows the game in a window instead of in Blender."""
    then = "it opens in its own window this time" if open_window else "open the game window to play it"
    _game_stream().notify(f"The game cannot stream into Blender ({problem}); {then}")


def _choose_display(open_window: bool) -> str:
    """"embedded" with the Game View open, or "window" (the preference, or the fallback). A game
    streamed into Blender that this Play does not use ends: it would play along, sound and all."""
    game_stream = _game_stream()
    if not embedded():
        if game_stream.session() is not None:
            game_stream.close()
        return "window"
    ok, problem = game_stream.available()
    if ok:
        problem = game_stream.open_view()
    if problem:
        if game_stream.session() is not None:
            game_stream.close()
        _fall_back(problem, open_window)
        return "window"
    return "embedded"


def _needs_streamed_game(client) -> bool:
    """The game streamed into Blender is not on the live link (not started, or still loading)."""
    return _game_stream().stream_peer(client) is None


def _games(client) -> list:
    """The game peers on the live link, less a streamed game that was just ended (it leaves the
    link in a moment)."""
    gone = _game_stream().closed_peer(client)
    peers = client.game_peers()
    return [p for p in peers if p.get("peer") != gone] if gone else peers


def send_play(client, result: dict, start: bool = True) -> None:
    """Send scene.load (and play.start) to the connected games now. A page the Scene view put in
    scene mode gets view.mode game first."""
    global _starting
    if start:
        _scene_view().before_play(client)
    client.send(scene_load_message(result))
    if start:
        client.send({"t": "play.start"})
        _starting = {"ready": client.game_ready, "deadline": time.monotonic() + START_CHECK_TIMEOUT}
    live.reseed()


def _check_started(client) -> None:
    """A game that is loaded and stopped (the one kept from the last Play) drops a play.start that
    comes while it reloads the scene, and keeps its state from before the reload: stopped. Once its
    game.ready says the reload is done, a game still stopped gets play.start again."""
    global _starting
    if (time.monotonic() > _starting["deadline"] or not client.connected
            or client.game_state in ("playing", "paused")):
        _starting = None
    elif client.game_ready is not _starting["ready"] and client.game_state == "stopped":
        log.info("Play: the game was still loading the scene when play.start came; sending it again")
        client.send({"t": "play.start"})
        _starting = None


def _new_pending(result, start: bool, open_window: bool, display: str, new_view: bool = False) -> dict:
    return {"result": result, "start": start, "open_window": open_window, "display": display, "opened": False,
            "docked": False, "new_view": new_view, "deadline": time.monotonic() + PLAY_TIMEOUT}


def prepare(root, open_window: bool = True) -> str:
    """The Play operator's first half, before the export: start what Play waits for, so that it
    starts while the scene exports. The dev server when the live link is down (devserver.start()
    does not wait for it), and in the Embedded display the Game View and the game's browser (with
    the game's address when it is known, else it goes there once the dev server has one; a game
    already running there is kept). request_play() finishes the play; abandon() forgets it.

    Returns devserver.start()'s answer ("started", "starting", "running"), or "" when the live link
    is up. Raises devserver.DevServerError."""
    global _pending
    client = link.get_link()
    game_stream = _game_stream()
    was_open = game_stream.view_open()
    display = _choose_display(open_window)
    _pending = _new_pending(None, True, open_window, display,
                            new_view=display == "embedded" and not was_open)
    game_stream.touch()
    server = ""
    if not client.connected:
        server = devserver.start(root, on_ready=on_server_ready)
    if display == "embedded":
        _pump_stream(client)
    _set_status(EXPORTING)
    return server


def request_play(result: dict, start: bool = True, open_window: bool = True) -> bool:
    """Load the exported scene in the game (and start it) as soon as possible.

    With a visible Game View the game plays there: a game connected elsewhere (a browser
    tab) does not count until the docked one is on the live link too.
    Returns True if the messages went out immediately, False if queued.
    """
    global _pending
    client = link.get_link()
    prepared = _pending if _pending is not None and _pending.get("result") is None else None
    display = prepared["display"] if prepared is not None else _choose_display(open_window)
    _game_stream().touch()
    if (client.connected and _games(client) and not _needs_docked_game(client)
            and not (display == "embedded" and _needs_streamed_game(client))):
        send_play(client, result, start)
        _pending = None
        _set_status("")
        return True
    if prepared is not None:
        prepared.update(result=result, start=start, open_window=open_window,
                        deadline=time.monotonic() + PLAY_TIMEOUT)
    else:
        _pending = _new_pending(result, start, open_window, display)
    _set_status(_progress(client))
    return False


def on_server_ready(ok: bool, message: str) -> None:
    """devserver.start() callback: give up on a pending play if the server failed."""
    global _pending
    if not ok and _pending is not None:
        if _pending.get("display") == "embedded":
            _game_stream().close()
        _pending = None
        _set_status("Dev server failed to start (see the log)")


def _game_url(client) -> str | None:
    session = client.session or {}
    return session.get("url")


def _game_address(client) -> str | None:
    """The game's URL: from the live link, else from the dev server's session file once the server
    is up (the browser can go there before the link connects)."""
    if client.connected:
        return _game_url(client)
    if devserver.is_starting():
        return None
    root = project.project_root()
    session = devserver.running_session(root, check_port=False) if root is not None else None
    return (session or {}).get("url")


def can_dock() -> bool:
    """True when Play would run the game docked: a Game View is showing on a build that has one."""
    from .. import host

    return host.game_view.available() and host.game_view.visible()


def docked_peer(client=None) -> dict | None:
    """The live-link peer of the game running in the Game View (docked or detached), or None.

    The runtime host puts its pid in the page's user agent: "GameStudioHost/<v> (pid <n>)".
    """
    from .. import host

    client = client if client is not None else link.get_link()
    if client is None or not host.game_view.available():
        return None
    pid = host.game_view.status().get("host_pid")
    if not pid:
        return None
    tag = f"(pid {pid})"
    return next((p for p in client.game_peers() if tag in str(p.get("client", ""))), None)


def _needs_docked_game(client) -> bool:
    """Play should run the game in the visible Game View, and that game is not on the live link yet."""
    return can_dock() and docked_peer(client) is None


def _waiting_for_game(client) -> bool:
    from .. import host

    if _pending.get("docked") and host.game_view.is_running():
        return docked_peer(client) is None
    return not _games(client)


def open_game(url: str, window_fallback: bool = True) -> str:
    """Show the game at `url`: docked in a visible Game View if possible, else in its own window.

    A docked game that is already starting or running is kept (it reloads by
    itself). Returns "docked", "app", "browser", or "" when nothing was opened.
    """
    from .. import host

    if can_dock():
        if host.game_view.is_running():
            return "docked"
        try:
            host.game_view.start(url, project_root=project.project_root())
            return "docked"
        except host.game_view.GameViewError as exc:
            log.warning("Could not run the game in the Game View (%s); opening a window instead", exc)
    if not window_fallback:
        return ""
    return devserver.open_game_window(url)


def _progress(client) -> str:
    """What the pending play waits for, for status()."""
    if _pending["result"] is None:
        return EXPORTING
    if not client.connected:
        return STARTING_SERVER if devserver.is_starting() else CONNECTING
    if _pending.get("display") == "embedded":
        current = _game_stream().session()
        return LOADING if current is not None and getattr(current, "status", "") == "running" else STARTING_VIEW
    return WAITING


def pump() -> None:
    global _pending
    if _starting is not None:
        _check_started(link.get_link())
    if _pending is None:
        return
    client = link.get_link()
    if time.monotonic() > _pending["deadline"]:
        log.error("Play: no game connected within %d s", int(PLAY_TIMEOUT))
        if _pending.get("display") == "embedded":
            _game_stream().close()
        _pending = None
        _set_status("No game connected (timed out)")
        return
    if _pending.get("display") == "embedded":
        ready = _pump_stream(client)
        if _pending.get("display") == "embedded":  # still: a failed stream makes it a window play
            if ready and _pending["result"] is not None:
                _send_pending(client)
            else:
                _set_status(_progress(client))
            return
    if _pending["result"] is None or not client.connected:
        _set_status(_progress(client))
        return
    # The "open window" preference is about external windows; a visible Game View always docks.
    if not _pending["opened"] and (
            (not _games(client) and (_pending["open_window"] or can_dock())) or _needs_docked_game(client)):
        url = _game_url(client)
        if url:
            _pending["opened"] = True
            how = open_game(url, window_fallback=_pending["open_window"])
            _pending["docked"] = how == "docked"
            log.info("Opened the game (%s): %s", how or "nothing", url)
    if _waiting_for_game(client) or (not _pending["opened"] and _needs_docked_game(client)):
        _set_status(WAITING)
        return
    _send_pending(client)


def _send_pending(client) -> None:
    global _pending
    send_play(client, _pending["result"], _pending["start"])
    _pending = None
    _set_status("")


def _pump_stream(client) -> bool:
    """Embedded play: start the game's browser, or keep the running one, and send it to the game's
    address once that is known; True once its game is on the live link. A stream that cannot start
    or fails while it starts makes this Play a window one."""
    game_stream = _game_stream()
    url = _game_address(client)
    if not _pending["opened"]:
        try:
            if game_stream.ensure(url, project_root=project.project_root()):
                log.info("Streaming the game into Blender: %s", url or "the address follows once the dev server is up")
        except Exception as exc:  # noqa: BLE001 - StreamError, or whatever the client raised
            _stream_failed(str(exc) or type(exc).__name__)
            return False
        _pending["opened"] = True
    elif url:
        game_stream.navigate(url)
    problem = game_stream.failure()
    if problem:
        _stream_failed(problem)
        return False
    return client.connected and game_stream.stream_peer(client) is not None


def _stream_failed(problem: str) -> None:
    log.warning("The game stream failed: %s", problem)
    _game_stream().close()
    _pending["display"] = "window"
    _pending["opened"] = False
    _fall_back(problem, _pending["open_window"])


def _timer():
    try:
        pump()
    except Exception:
        log.exception("Play pump failed")
    return PENDING_INTERVAL if _pending is not None or _starting is not None else TIMER_INTERVAL
