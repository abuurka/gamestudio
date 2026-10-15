# SPDX-License-Identifier: GPL-3.0-or-later
"""The game streamed into Blender (editors/game_stream.py and core/play.py): the game display
preference, Play in Embedded with a fake stream client (the area becomes the Game View and Stop
turns it back, the fallback to a window), the warm game (Play starts the dev server and the
browser before the export; Stop keeps the game hidden, frames paused and page suspended, and the
next Play reuses it; the idle timeout, another project, a dead host, closing the view and
unregistering end it), Scene | Game, resizing, redraws only for new frames, the letterbox mapping,
the input capture with synthetic events, the headers, and nothing in the .blend.

Background Blender has no windows, so the screens, areas and windows here are stand-ins; the
real area switch (and the 3D View keeping its view) is checked by tools/screenshots.py
--only embedded-play in a UI Blender.
"""

import contextlib
import itertools
import pathlib
import shutil
import time
import types
import unittest
from unittest import mock

import bpy

from gamestudio import prefs
from gamestudio.core import devserver, export, link, play, project, stream
from gamestudio.editors import game_stream

import helpers
from test_ui import MockLayout

GAME = {"peer": "g2", "role": "game", "client": "GameStudio engine (HeadlessChrome)"}
TAB = {"peer": "g1", "role": "game", "client": "GameStudio engine (Edge)"}
GAME3 = {"peer": "g3", "role": "game", "client": "GameStudio engine (HeadlessChrome)"}
_pointers = itertools.count(0x1000, 0x100)


class FakeRegion:
    def __init__(self, kind="WINDOW", x=0, y=0, width=1600, height=900):
        self.type, self.x, self.y, self.width, self.height = kind, x, y, width, height
        self.redraws = 0

    def tag_redraw(self):
        self.redraws += 1


class FakeArea:
    """An area that records its editor switches; `view` stands for the 3D View's own data."""

    def __init__(self, kind="VIEW_3D", width=1600, height=926, x=0, y=0):
        self.type, self.width, self.height, self.x, self.y = kind, width, height, x, y
        self.regions = [FakeRegion("HEADER", x, y + height - 26, width, 26), FakeRegion("WINDOW", x, y, width, height - 26)]
        self.spaces = types.SimpleNamespace(active=types.SimpleNamespace(ui_mode="VIEW"))
        self.view = {"distance": 23.0, "shading": "WIREFRAME"}
        self.switches = []
        self._pointer = next(_pointers)

    @property
    def ui_type(self):
        return self.type

    @ui_type.setter
    def ui_type(self, value):
        self.switches.append(value)
        self.type = value

    def as_pointer(self):
        return self._pointer

    def region(self):
        return self.regions[1]


class FakeSession:
    def __init__(self, url, width, height, on_status):
        self.url, self.size, self.on_status = url, (width, height), on_status
        self.status, self.message, self.fps, self.frame_id = "starting", "Starting the game stream", 0.0, 0
        self.calls = []
        self.stopped = False
        self.float_frames = False
        self.keep_on_load = False

    def poll(self):
        pass

    def navigate(self, url):
        self.calls.append(("navigate", url))
        self.url = url

    def latest_frame(self):
        return None

    def resize(self, width, height):
        self.calls.append(("resize", width, height))
        self.size = (width, height)

    def send_mouse(self, kind, x, y, button=0, dx=0.0, dy=0.0, shift=False, ctrl=False, alt=False):
        self.calls.append(("mouse", kind, round(x), round(y), button, dx, dy))

    def send_key(self, kind, code, key, shift=False, ctrl=False, alt=False, repeat=False):
        self.calls.append(("key", kind, code, key))

    def release_input(self):
        self.calls.append(("release",))

    def set_paused(self, paused, suspend=False):
        self.calls.append(("paused", paused, suspend))

    def stop(self):
        self.stopped = True
        self.status = "stopped"


class FakeStream:
    """core/stream.py's contract."""

    class StreamError(Exception):
        pass

    def __init__(self):
        self.ok = (True, "")
        self.fail = ""
        self.sessions = []
        self.events = None  # a list: records ("browser", url) when a session starts

    def available(self):
        return self.ok

    def start(self, url, width, height, *, project_root, on_status=None):
        if self.fail:
            raise self.StreamError(self.fail)
        if self.events is not None:
            self.events.append(("browser", url))
        session = FakeSession(url, width, height, on_status)
        self.sessions.append(session)
        return session

    key_code = staticmethod(stream.key_code)
    fit_size = staticmethod(stream.fit_size)


class Event(types.SimpleNamespace):
    """A Blender event: type, value, modifiers, the typed character and the mouse in window pixels."""

    def __init__(self, kind, value="PRESS", x=0, y=0, shift=False, ctrl=False, alt=False, unicode="", repeat=False):
        super().__init__(type=kind, value=value, mouse_x=x, mouse_y=y, mouse_prev_x=x, mouse_prev_y=y,
                         shift=shift, ctrl=ctrl, alt=alt, unicode=unicode, is_repeat=repeat)


class StubbedBlender(helpers.ProjectTestCase):
    """A window showing a screen with two 3D Views and a Text Editor, and a fake stream client."""

    def setUp(self):
        super().setUp()
        self.obj = helpers.new_object("Hero", helpers.new_mesh())
        bpy.context.view_layer.objects.active = self.obj
        self.small, self.big = FakeArea(width=400, height=300, x=1200, y=600), FakeArea()
        self.console = FakeArea("TEXT_EDITOR", width=1600, height=200)
        self.screen = types.SimpleNamespace(name="Game", areas=[self.small, self.big, self.console],
                                            is_temporary=False, show_fullscreen=False)
        self.window = types.SimpleNamespace(screen=self.screen)
        self.fake = FakeStream()
        self.notes, self.windows_opened = [], []
        self.hub = None
        settings = prefs._defaults
        self.saved = (settings.game_display, settings.open_game_window, settings.game_idle_minutes)
        settings.game_display, settings.open_game_window, settings.game_idle_minutes = "EMBEDDED", True, 15
        patches = (
            mock.patch.object(game_stream, "_screen", lambda name: self.screen if name == self.screen.name else None),
            mock.patch.object(game_stream, "_windows", lambda: [self.window]),
            mock.patch.object(game_stream, "_override", lambda *_args: contextlib.nullcontext()),
            mock.patch.object(game_stream, "any_window", lambda: self.window),
            mock.patch.object(game_stream, "headless", lambda: False),
            mock.patch.object(game_stream, "_stream", lambda: self.fake),
            mock.patch.object(game_stream, "_ensure_timer", lambda: None),
            mock.patch.object(game_stream, "notify", lambda message, level="WARNING": self.notes.append(message)),
            mock.patch.object(devserver, "open_game_window", lambda url: self.windows_opened.append(url) or "app"),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        play.cancel()
        game_stream._s.__init__()
        prefs._defaults.game_display, prefs._defaults.open_game_window, prefs._defaults.game_idle_minutes = self.saved
        helpers.restore_global_link()
        if self.hub is not None:
            self.hub.close()
        super().tearDown()

    def op(self, name, **kwargs):
        with bpy.context.temp_override(active_object=self.obj, object=self.obj):
            return getattr(bpy.ops.gamestudio, name)("EXEC_DEFAULT", **kwargs)

    def connect(self, peers):
        self.hub = helpers.FakeHub(peers=peers)
        return helpers.connect_global_link(self.hub)

    def pump(self, until, client):
        return helpers.pump_until(until, [client.pump, play.pump])

    def stream_now(self, peers=(TAB,)):
        """Play, and the streamed game connects: returns (client, session)."""
        client = self.connect(list(peers))
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(self.pump(lambda: self.fake.sessions, client))
        session = self.fake.sessions[-1]
        session.status = "running"
        self.hub.broadcast({"t": "peers", "peers": [*peers, GAME]})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        return client, session


class EmbeddedPlayTest(StubbedBlender):
    def test_play_streams_into_the_largest_3d_view_and_stop_restores_it(self):
        client = self.connect([TAB])  # a browser tab is open: it is not the game in Blender
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual((self.big.type, self.small.type, self.console.type), ("IMAGE_EDITOR", "VIEW_3D", "TEXT_EDITOR"))
        self.assertTrue(play.is_pending())
        self.assertTrue(self.pump(lambda: self.fake.sessions, client))
        session = self.fake.sessions[0]
        self.assertEqual(session.url, "http://localhost:5173/")
        self.assertEqual(session.size, (1280, 720), "the region's shape, at most 1280 x 720 pixels")
        self.assertTrue(session.float_frames, "the reader thread makes the floats for the texture")
        for _ in range(3):
            client.pump()
            play.pump()
        self.assertEqual(self.hub.messages("scene.load"), [], "waits for the streamed game, not the open tab")
        self.hub.broadcast({"t": "peers", "peers": [TAB, GAME]})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        self.assertFalse(play.is_pending())
        self.assertEqual(game_stream.stream_peer(client)["peer"], "g2")
        self.assertEqual(self.notes, [])

        # Play again: the streamed game is on the link and reloads at once, in the same session.
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertFalse(play.is_pending())
        self.assertTrue(self.pump(lambda: self.hub.messages("scene.load"), client))
        self.assertEqual(len(self.fake.sessions), 1)

        # Stop: the 3D View again, with the view it left; the game keeps running hidden.
        self.assertEqual(self.op("stop"), {"FINISHED"})
        self.assertFalse(game_stream.view_open())
        self.assertEqual(self.big.type, "VIEW_3D")
        self.assertEqual(self.big.switches, ["IMAGE_EDITOR", "VIEW_3D"], "switched there and back, nothing else")
        self.assertEqual(self.big.view, {"distance": 23.0, "shading": "WIREFRAME"})
        self.assertFalse(session.stopped, "Stop keeps the game for the next Play")
        self.assertIs(game_stream.session(), session)

        # Its X button closes the game: the browser ends and the area is the 3D View again.
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(self.big.type, "IMAGE_EDITOR")
        self.assertEqual(len(self.fake.sessions), 1)
        self.assertEqual(bpy.ops.gamestudio.game_stream_close(), {"FINISHED"})
        self.assertTrue(session.stopped)
        self.assertFalse(game_stream.view_open())
        self.assertIsNone(game_stream.session())
        self.assertEqual(self.big.switches, ["IMAGE_EDITOR", "VIEW_3D", "IMAGE_EDITOR", "VIEW_3D"])
        self.assertEqual(self.big.view, {"distance": 23.0, "shading": "WIREFRAME"})
        self.assertEqual(self.small.switches, [])
        self.assertEqual(self.windows_opened, [])

    def test_window_display_opens_a_window(self):
        prefs._defaults.game_display = "WINDOW"
        client = self.connect([])
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(self.pump(lambda: self.windows_opened, client))
        self.assertEqual(self.big.switches, [])
        self.assertEqual(self.fake.sessions, [])

    def test_falls_back_to_a_window_when_it_cannot_stream(self):
        self.fake.ok = (False, "No Edge, Chrome or Chromium found")
        client = self.connect([])
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(self.big.switches, [], "the 3D View stays")
        self.assertTrue(self.pump(lambda: self.windows_opened, client))
        self.assertEqual(len(self.notes), 1)
        self.assertIn("No Edge, Chrome or Chromium found", self.notes[0])
        self.assertIn("own window", self.notes[0])
        self.hub.broadcast({"t": "peers", "peers": [TAB]})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        self.assertEqual(self.windows_opened, ["http://localhost:5173/"], "once")

    def test_falls_back_when_the_stream_fails_while_it_starts(self):
        client = self.connect([])
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(self.pump(lambda: self.fake.sessions, client))
        session = self.fake.sessions[0]
        session.status, session.message = "error", "The page crashed"
        self.assertTrue(self.pump(lambda: self.windows_opened, client))
        self.assertTrue(session.stopped)
        self.assertEqual(self.big.type, "VIEW_3D", "the Game View closed")
        self.assertEqual(len(self.notes), 1)
        self.assertIn("The page crashed", self.notes[0])

    def test_falls_back_when_the_stream_does_not_start(self):
        self.fake.fail = "Node.js was not found"
        client = self.connect([])
        prefs._defaults.open_game_window = False
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(self.pump(lambda: self.notes, client))
        self.assertIn("Node.js was not found", self.notes[0])
        self.assertIn("open the game window", self.notes[0], "the window preference still applies")
        self.assertEqual(self.big.type, "VIEW_3D")
        for _ in range(3):
            client.pump()
            play.pump()
        self.assertEqual(self.windows_opened, [])
        self.assertEqual(len(self.notes), 1)

    def test_a_dev_server_failure_closes_the_game_view(self):
        self.assertTrue(game_stream.open_view(self.window) == "")
        play._pending = {"display": "embedded"}
        play.on_server_ready(False, "npm failed")
        self.assertEqual(self.big.type, "VIEW_3D")
        self.assertFalse(game_stream.view_open())

    def test_scene_and_game_switch_pauses_the_stream(self):
        client, session = self.stream_now()
        client.game_state = "playing"
        game_stream._update()
        self.assertEqual([c for c in session.calls if c[0] == "paused"], [])
        self.assertEqual(bpy.ops.gamestudio.game_stream_show(view="SCENE"), {"FINISHED"})
        self.assertEqual(self.big.type, "VIEW_3D")
        game_stream._update()
        self.assertEqual(session.calls[-1], ("paused", True, False), "nobody sees it: no frames, the game runs")
        self.assertFalse(session.stopped, "the game keeps running")
        self.assertEqual(bpy.ops.gamestudio.game_stream_show(view="GAME"), {"FINISHED"})
        game_stream._update()
        self.assertEqual(session.calls[-1], ("paused", False, False))
        # Nobody plays it either (stopped, e.g. by the agent): the page is suspended too.
        game_stream.show("SCENE")
        client.game_state = "stopped"
        game_stream._update()
        self.assertEqual(session.calls[-1], ("paused", True, True))
        game_stream.show("GAME")
        self.assertEqual(session.calls[-1], ("paused", False, False), "resumed at once")
        # The editor menu works too; Play shows the game again.
        self.big.ui_type = "VIEW_3D"
        game_stream._update()
        self.assertEqual(game_stream._s.shown, "SCENE")
        self.op("play")
        self.assertEqual(self.big.type, "IMAGE_EDITOR")

    def test_a_maximized_game_view_keeps_streaming(self):
        _client, session = self.stream_now()
        copy = FakeArea("IMAGE_EDITOR")  # Ctrl+Space: Blender shows a copy of the area in a temporary screen
        full = types.SimpleNamespace(name="Game-nonnormal", areas=[copy], is_temporary=True, show_fullscreen=True)
        self.window.screen = full
        self.assertTrue(game_stream.is_game_area(copy, full))
        self.assertFalse(game_stream.is_game_area(FakeArea("IMAGE_EDITOR"), self.screen))
        game_stream._update()
        self.assertEqual([c for c in session.calls if c[0] == "paused"], [], "it is still seen")
        redraws = copy.region().redraws
        session.frame_id += 1
        game_stream._update()
        self.assertGreater(copy.region().redraws, redraws)
        self.window.screen = self.screen

    def test_redraws_only_for_new_frames_and_follows_the_size(self):
        _client, session = self.stream_now()
        region = self.big.region()
        game_stream._update()
        game_stream._update()
        redraws = region.redraws
        game_stream._update()
        self.assertEqual(region.redraws, redraws, "no new frame, no redraw")
        session.frame_id += 1
        game_stream._update()
        self.assertEqual(region.redraws, redraws + 1)

        region.width, region.height = 800, 600
        game_stream._update()
        game_stream._update()
        self.assertNotIn(("resize", 800, 600), session.calls, "not before the size settles")
        game_stream._s.region_since -= game_stream.RESIZE_DELAY
        game_stream._update()
        self.assertEqual([c for c in session.calls if c[0] == "resize"], [("resize", 800, 600)])
        region.width, region.height = 3200, 1800
        game_stream._update()
        game_stream._s.region_since -= game_stream.RESIZE_DELAY
        game_stream._update()
        self.assertEqual(session.calls[-1], ("resize", 1280, 720), "capped")

    def test_saving_keeps_the_3d_view_in_the_file(self):
        self.stream_now()
        game_stream._on_save_pre()
        self.assertEqual(self.big.type, "VIEW_3D")
        game_stream._on_save_post()
        self.assertEqual(self.big.type, "IMAGE_EDITOR")

    def test_nothing_is_added_to_the_blend(self):
        def ids():
            return {name: len(getattr(bpy.data, name)) for name in dir(bpy.data)
                    if isinstance(getattr(bpy.data, name, None), bpy.types.bpy_prop_collection)}

        before = ids()
        _client, session = self.stream_now()
        game_stream._update()
        self.op("stop")
        play.cancel()
        after = ids()
        after.pop("window_managers", None), before.pop("window_managers", None)
        self.assertEqual(after, before)
        self.assertEqual(len(bpy.data.images), before["images"], "no image datablock for the frames")


class WarmGameTest(StubbedBlender):
    """Play starts what takes long first; Stop keeps the game; the next Play reuses it."""

    def test_play_starts_the_dev_server_and_the_browser_before_exporting(self):
        events = []
        real_export = export.export_scene

        def exporting(scene):
            events.append("export")
            self.assertEqual(play.status(), play.EXPORTING)
            return real_export(scene)

        self.fake.events = events
        with mock.patch.object(devserver, "start", side_effect=lambda root, on_ready=None: events.append(
                "dev server") or "started"), \
                mock.patch.object(devserver, "is_starting", return_value=True), \
                mock.patch.object(export, "export_scene", side_effect=exporting):
            self.assertEqual(self.op("play"), {"FINISHED"})
            self.assertEqual(events, ["dev server", ("browser", None), "export"],
                             "the dev server and the browser start while the scene exports")
            self.assertTrue(play.is_pending())
            self.assertEqual(play.status(), play.STARTING_SERVER)
            self.assertEqual(game_stream.status_text(), (play.STARTING_SERVER, "SORTTIME"))
        session = self.fake.sessions[0]
        self.assertIsNone(session.url, "no address yet: the browser waits for it")
        self.assertEqual(self.big.type, "IMAGE_EDITOR")

        # The dev server is up: the browser goes to the game, then the scene loads once it joins.
        client = self.connect([])
        self.assertTrue(self.pump(lambda: session.url, client))
        self.assertEqual(session.calls[-1], ("navigate", "http://localhost:5173/"))
        self.assertEqual(play.status(), play.STARTING_VIEW)
        session.status = "running"
        self.assertTrue(self.pump(lambda: play.status() == play.LOADING, client))
        self.hub.broadcast({"t": "peers", "peers": [GAME]})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        self.assertFalse(play.is_pending())
        self.assertEqual(play.status(), "")
        self.assertEqual(len(self.fake.sessions), 1)

    def test_stop_keeps_the_game_hidden_and_the_next_play_reuses_it(self):
        client, session = self.stream_now()
        game_stream._s.captured = True
        game_stream._s.texture = (1, None, 1280, 720)  # a frame came
        self.assertEqual(self.op("stop"), {"FINISHED"})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.stop"), client))
        self.assertEqual(client.game_state, "stopped")
        # The 3D View again, with the view it left; the mouse and keyboard are Blender's.
        self.assertEqual(self.big.type, "VIEW_3D")
        self.assertEqual(self.big.view, {"distance": 23.0, "shading": "WIREFRAME"})
        self.assertFalse(game_stream.view_open())
        self.assertFalse(game_stream._s.captured)
        self.assertIsNone(game_stream._s.texture, "the next Play shows new frames, not the last one")
        # The game keeps running hidden: no frames, its page suspended (nobody plays it).
        self.assertFalse(session.stopped, "the browser keeps running")
        self.assertEqual(session.calls[-1], ("paused", True, True))
        for _ in range(3):
            game_stream._update()
        self.assertFalse(session.stopped, "turning the area back is not closing the Game View")
        self.assertEqual(session.calls[-1], ("paused", True, True))
        # The 3D View is the user's again: whatever becomes of it does not end the game.
        self.big.ui_type = "NODE_EDITOR"
        game_stream._update()
        self.big.ui_type = "VIEW_3D"
        self.assertFalse(session.stopped)

        # The next Play: the largest 3D View is the Game View again, the page resumes and the scene
        # goes out at once, to the same browser.
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(self.big.type, "IMAGE_EDITOR")
        self.assertEqual(session.calls[-1], ("paused", False, False), "frames again, before the scene")
        self.assertFalse(play.is_pending())
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        sent = [m["t"] for m in self.hub.messages() if m["t"].startswith(("scene.", "play."))]
        self.assertEqual(sent, ["scene.load", "play.start"])
        self.assertEqual(len(self.fake.sessions), 1, "no new browser")
        self.assertFalse(session.stopped)

    def test_a_play_start_lost_in_the_reload_is_sent_again(self):
        # The kept game is loaded and stopped: it drops a play.start that comes while it reloads the
        # scene, and keeps its state from before (stopped). Its game.ready says the reload is done.
        client, _session = self.stream_now()
        self.op("stop")
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        for message in ({"t": "game.status", "state": "loading"}, {"t": "game.ready", "scene": "Scene", "rev": 9},
                        {"t": "game.status", "state": "stopped"}):
            self.hub.broadcast(message)
        self.assertTrue(self.pump(lambda: len(self.hub.messages("play.start")) == 2, client))
        self.hub.broadcast({"t": "game.status", "state": "playing"})
        self.assertTrue(self.pump(lambda: client.game_state == "playing", client))
        self.assertIsNone(play._starting)

        # A game that plays after the reload gets nothing more; neither does one that Stop stopped.
        self.hub.clear()
        self.op("play")
        self.hub.broadcast({"t": "game.ready", "scene": "Scene", "rev": 10})
        self.assertTrue(self.pump(lambda: client.game_ready["rev"] == 10, client))
        self.assertIsNone(play._starting, "it was playing")
        self.op("play")
        self.op("stop")
        self.hub.broadcast({"t": "game.ready", "scene": "Scene", "rev": 11})
        self.hub.broadcast({"t": "game.status", "state": "stopped"})
        self.assertTrue(self.pump(lambda: client.game_ready["rev"] == 11, client))
        for _ in range(3):
            client.pump()
            play.pump()
        self.assertEqual(len(self.hub.messages("play.start")), 2, "Play twice, and nothing after Stop")

    def test_the_host_marks_its_game(self):
        # A host that marks the page's user agent: its game is that peer, whatever joined since.
        client, session = self.stream_now()
        session.agent = "GameStudioStream/4242"
        self.assertIsNone(game_stream.stream_peer(client), "GAME is not marked")
        marked = dict(GAME3, client="GameStudio engine (Mozilla/5.0 HeadlessChrome/150 GameStudioStream/4242)")
        self.hub.broadcast({"t": "peers", "peers": [TAB, GAME, marked]})
        self.assertTrue(self.pump(lambda: game_stream.stream_peer(client), client))
        self.assertEqual(game_stream.stream_peer(client)["peer"], "g3")

    def test_an_idle_game_closes(self):
        client, session = self.stream_now()

        def check():
            game_stream._s.checked_at = 0.0
            game_stream._update()

        self.op("stop")
        check()
        self.assertFalse(session.stopped, "just stopped")
        # Playing is not idle, however long ago Play was.
        client.game_state = "playing"
        game_stream._s.active_at -= 3600
        check()
        self.assertFalse(session.stopped)
        self.assertGreater(game_stream._s.active_at, time.monotonic() - 60)
        # 0: never.
        client.game_state = "stopped"
        prefs._defaults.game_idle_minutes = 0
        game_stream._s.active_at -= 3600
        check()
        self.assertFalse(session.stopped)
        prefs._defaults.game_idle_minutes = 15
        game_stream._s.active_at = time.monotonic() - 14 * 60
        check()
        self.assertFalse(session.stopped)
        game_stream._s.active_at = time.monotonic() - 15 * 60 - 1
        check()
        self.assertTrue(session.stopped)
        self.assertFalse(game_stream.view_open())
        self.assertEqual(self.big.type, "VIEW_3D")
        self.assertIn("15 minutes", self.notes[-1])

    def test_a_file_of_the_same_project_keeps_the_game_and_another_project_ends_it(self):
        client, session = self.stream_now()
        self.op("stop")
        # Another file of this project: its screens replace these; the game waits for the next Play.
        game_stream._on_load_pre()
        game_stream._on_load_post()
        self.assertFalse(session.stopped)
        self.assertFalse(game_stream.view_open())
        self.big.type = "VIEW_3D"  # the new file's 3D View
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(self.big.type, "IMAGE_EDITOR")
        self.assertFalse(play.is_pending(), "the game was on the link: the scene went out at once")
        self.assertEqual(len(self.fake.sessions), 1)

        # Another project (Save As elsewhere, or a file of another game): the game ends.
        other = helpers.make_project()
        self.addCleanup(shutil.rmtree, other, True)
        project.set_project_root_override(other)
        game_stream._s.checked_at = 0.0
        game_stream._update()
        self.assertTrue(session.stopped)
        self.assertEqual(self.big.type, "VIEW_3D")
        # The same at once when the file is loaded.
        project.set_project_root_override(self.root)
        self.big.type = "VIEW_3D"
        self.op("play")
        session = self.fake.sessions[-1]
        game_stream._on_load_pre()
        project.set_project_root_override(other)
        game_stream._on_load_post()
        self.assertTrue(session.stopped)
        self.assertIsNone(game_stream.session())
        self.assertFalse(play.is_pending(), "the play was for the other project")

    def test_a_dead_host_is_replaced_by_the_next_play(self):
        client, session = self.stream_now()
        self.op("stop")
        session.status, session.message = "error", "The stream host exited (code 1)"
        self.hub.broadcast({"t": "peers", "peers": [TAB]})  # its game left the live link
        self.assertTrue(self.pump(lambda: len(client.game_peers()) == 1, client))
        game_stream._update()
        self.assertEqual(game_stream.status_text(), ("The stream host exited (code 1)", "ERROR"))
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(len(self.fake.sessions), 2, "a new browser")
        self.assertTrue(play.is_pending())
        self.assertEqual(self.notes, [], "not a fallback to a window")
        replacement = self.fake.sessions[1]
        replacement.status = "running"
        self.hub.broadcast({"t": "peers", "peers": [TAB, GAME3]})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        self.assertEqual(game_stream.stream_peer(client)["peer"], "g3")

    def test_closing_the_game_view_ends_the_game(self):
        _client, session = self.stream_now()
        self.screen.areas.remove(self.big)  # joined into its neighbor
        game_stream._update()
        self.assertTrue(session.stopped)
        self.assertIsNone(game_stream.session())
        self.assertFalse(game_stream.view_open())

    def test_making_the_game_view_another_editor_ends_the_game(self):
        _client, session = self.stream_now()
        self.big.ui_type = "NODE_EDITOR"
        game_stream._update()
        self.assertTrue(session.stopped)
        self.assertFalse(game_stream.view_open())
        self.assertEqual(self.big.type, "NODE_EDITOR", "left as the user made it")

    def test_the_window_display_ends_the_streamed_game(self):
        client, session = self.stream_now(peers=())
        self.op("stop")
        prefs._defaults.game_display = "WINDOW"
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(session.stopped, "it would play along")
        self.assertEqual(self.big.type, "VIEW_3D")
        self.assertTrue(play.is_pending(), "the ended game is still on the link, but does not count")
        self.assertTrue(self.pump(lambda: self.windows_opened, client))
        self.hub.broadcast({"t": "peers", "peers": [TAB]})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))

    def test_a_failed_export_forgets_the_play_and_keeps_the_browser(self):
        self.connect([TAB])
        with mock.patch.object(export, "export_scene", side_effect=export.ExportError("Nothing to export")):
            with self.assertRaises(RuntimeError):
                self.op("play")
        self.assertFalse(play.is_pending())
        self.assertEqual(play.status(), "")
        self.assertEqual(self.big.type, "VIEW_3D", "the Game View it opened is the 3D View again")
        session = self.fake.sessions[0]
        self.assertFalse(session.stopped, "the browser goes on starting for the next Play")
        self.assertEqual(session.calls[-1], ("paused", True, True), "hidden and not played")
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(len(self.fake.sessions), 1)
        self.assertEqual(self.big.type, "IMAGE_EDITOR")
        self.assertEqual(session.calls[-1], ("paused", False, False))

    def test_unregister_ends_the_game(self):
        _client, session = self.stream_now()
        self.op("stop")
        import gamestudio

        gamestudio.unregister()
        try:
            self.assertTrue(session.stopped)
            self.assertIsNone(game_stream.session())
            self.assertFalse(play.is_pending())
            self.assertNotIn(game_stream._on_load_post, bpy.app.handlers.load_post)
        finally:
            gamestudio.register()
        self.assertIn(game_stream._on_load_post, bpy.app.handlers.load_post)

    def test_quitting_blender_ends_the_game(self):
        _client, session = self.stream_now()
        self.op("stop")
        game_stream._on_exit()
        self.assertTrue(session.stopped)


class RealAreaTest(unittest.TestCase):
    """The real area switch. Background Blender has one window without a GHOST window, enough for
    Blender to set an area up again; changing an area's editor needs a window showing its screen,
    and without one nothing is touched."""

    def setUp(self):
        self.window = bpy.context.window_manager.windows[0]
        self.area = max((a for a in self.window.screen.areas if a.type == "VIEW_3D"),
                        key=lambda a: a.width * a.height)

    def tearDown(self):
        game_stream.close()
        game_stream._s.__init__()

    def test_no_window_no_switch(self):
        screen = self.window.screen
        self.assertFalse(game_stream.set_area_type(None, screen, self.area, "IMAGE_EDITOR"))
        hidden = next(s for s in bpy.data.screens if s != screen and any(a.type == "VIEW_3D" for a in s.areas))
        area = next(a for a in hidden.areas if a.type == "VIEW_3D")
        self.assertFalse(game_stream.set_area_type(self.window, hidden, area, "IMAGE_EDITOR"), "not shown")
        self.assertEqual((self.area.type, area.type), ("VIEW_3D", "VIEW_3D"))
        self.assertTrue(game_stream.set_area_type(None, screen, self.area, "VIEW_3D"), "already there")

    def test_the_game_view_comes_and_goes_and_the_3d_view_keeps_its_view(self):
        space = self.area.spaces.active
        space.region_3d.view_distance = 23.0
        space.shading.type = "WIREFRAME"
        view = [tuple(row) for row in space.region_3d.view_matrix]
        images = len(bpy.data.images)
        self.assertEqual(game_stream.open_view(self.window), "")
        self.assertEqual(self.area.type, "IMAGE_EDITOR")
        self.assertTrue(game_stream.is_game_area(self.area, self.window.screen))
        self.assertTrue(game_stream.show("SCENE"))
        self.assertEqual(self.area.type, "VIEW_3D")
        self.assertTrue(game_stream.show("GAME"))

        # A save writes the 3D View: the area is one while the file is written, then the game again.
        seen = []
        record = lambda *_args: seen.append(self.area.type)
        bpy.app.handlers.save_pre.append(record)
        try:
            path = pathlib.Path(helpers.tmp_base()) / "gs_game_view_save.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
            path.unlink(missing_ok=True)
        finally:
            bpy.app.handlers.save_pre.remove(record)
        self.assertEqual(seen, ["VIEW_3D"])
        self.assertEqual(self.area.type, "IMAGE_EDITOR")

        game_stream.close()
        self.assertEqual(self.area.type, "VIEW_3D")
        space = self.area.spaces.active
        self.assertEqual(space.region_3d.view_distance, 23.0)
        self.assertEqual(space.shading.type, "WIREFRAME")
        self.assertEqual([tuple(row) for row in space.region_3d.view_matrix], view)
        self.assertEqual(len(bpy.data.images), images, "no image datablock")

    def test_background_blender_plays_in_a_window_quietly(self):
        self.assertTrue(game_stream.headless())
        self.assertFalse(play.embedded())


class GeometryTest(unittest.TestCase):
    def test_letterbox_and_pillarbox(self):
        self.assertEqual(game_stream.fit(1280, 720, 1000, 1000), (0.0, 218.75, 1000.0, 562.5))
        self.assertEqual(game_stream.fit(640, 480, 1600, 900), (200.0, 0.0, 1200.0, 900.0))
        self.assertEqual(game_stream.fit(0, 0, 100, 100), (0.0, 0.0, 0.0, 0.0))

    def test_region_points_in_frame_pixels(self):
        rect = game_stream.fit(1280, 720, 1000, 1000)
        to_frame = lambda x, y: game_stream.to_frame(rect, (1280, 720), x, y)
        self.assertEqual(to_frame(500, 500), (640.0, 360.0, True))
        x, y, inside = to_frame(0, 781.25)
        self.assertEqual((x, y, inside), (0.0, 0.0, True), "the frame's top left corner")
        x, y, inside = to_frame(250, 100)
        self.assertFalse(inside, "the bar below the frame")
        self.assertEqual((x, y), (320.0, 720.0), "clamped to the frame's bottom edge")
        self.assertEqual(to_frame(999.99, 500)[0] // 1, 1279.0)

    def test_stream_size(self):
        self.assertEqual(game_stream.stream_size(1600, 900), (1280, 720))
        self.assertEqual(game_stream.stream_size(800, 600), (800, 600))
        self.assertEqual(game_stream.stream_size(3000, 2000), (1175, 783), "the shape, at most 1280 x 720 pixels")
        self.assertEqual(game_stream.stream_size(10, 10), (16, 16))
        with mock.patch.object(game_stream, "_stream", lambda: None):  # no client: the same sizes
            self.assertEqual(game_stream.stream_size(3000, 2000), (1175, 783))
            self.assertEqual(game_stream.stream_size(1600, 900), (1280, 720))


class CaptureTest(unittest.TestCase):
    """The capture with synthetic events: a 1000 x 1000 region at (100, 50) showing a 1280 x 720 frame."""

    def setUp(self):
        self.session = FakeSession("http://localhost:5173/", 1280, 720, None)
        self.session.status = "running"
        game_stream._s.session, game_stream._s.size, game_stream._s.shown = self.session, (1280, 720), "GAME"
        self.region = FakeRegion(x=100, y=50, width=1000, height=1000)
        patch = mock.patch.object(game_stream, "_stream", lambda: stream)
        patch.start()
        self.addCleanup(patch.stop)
        self.capture = game_stream.Capture(self.region, keys={("F5", False, False, False), ("F8", False, False, False)})

    def tearDown(self):
        game_stream._s.__init__()

    def at(self, x, y):
        """Window pixels of a region point."""
        return x + self.region.x, y + self.region.y

    def test_click_captures_and_reaches_the_game_in_frame_pixels(self):
        self.capture.begin(Event("LEFTMOUSE", x=600, y=550))
        self.assertTrue(game_stream._s.captured)
        self.assertEqual(self.session.calls, [("mouse", "down", 640, 360, 0, 0.0, 0.0)])
        self.assertEqual(self.capture.handle(Event("LEFTMOUSE", "RELEASE", *self.at(500, 500))), {"RUNNING_MODAL"})
        self.capture.handle(Event("MOUSEMOVE", "NOTHING", *self.at(0, 781.25)))
        self.assertEqual(self.session.calls[-1][:4], ("mouse", "move", 0, 0), "the frame's top left")
        self.capture.handle(Event("RIGHTMOUSE", "PRESS", *self.at(1000 - 1, 218.75)))
        self.assertEqual(self.session.calls[-1][:5], ("mouse", "down", 1279, 720, 2))
        self.capture.handle(Event("WHEELDOWNMOUSE", x=600, y=550))
        self.assertEqual(self.session.calls[-1], ("mouse", "wheel", 640, 360, 0, 0.0, 100.0))

    def test_keys_go_to_the_game_except_the_play_keys(self):
        handle = self.capture.handle
        self.capture.begin(Event("LEFTMOUSE", x=600, y=550))
        self.session.calls.clear()
        self.assertEqual(handle(Event("W", x=600, y=550)), {"RUNNING_MODAL"})
        self.assertEqual(handle(Event("W", "RELEASE", x=600, y=550)), {"RUNNING_MODAL"})
        handle(Event("A", x=600, y=550, shift=True, unicode="A"))
        handle(Event("SPACE", x=600, y=550, unicode=" "))
        self.assertEqual(handle(Event("ESC", x=600, y=550)), {"RUNNING_MODAL"}, "Esc is the game's (pause menus)")
        self.assertEqual(self.session.calls, [("key", "down", "KeyW", "w"), ("key", "up", "KeyW", "w"),
                                              ("key", "down", "KeyA", "A"), ("key", "down", "Space", " "),
                                              ("key", "down", "Escape", "Escape")])
        self.assertEqual(handle(Event("F5", x=600, y=550)), {"PASS_THROUGH"}, "Play still works")
        self.assertEqual(handle(Event("F8", x=600, y=550)), {"PASS_THROUGH"})
        self.assertEqual(handle(Event("TIMER", "NOTHING")), {"PASS_THROUGH"})
        self.assertEqual(len(self.session.calls), 5)

    def test_shift_esc_releases_and_lifts_held_keys(self):
        handle = self.capture.handle
        self.capture.begin(Event("LEFTMOUSE", x=600, y=550))
        handle(Event("D", x=600, y=550))
        self.session.calls.clear()
        self.assertEqual(handle(Event("ESC", x=600, y=550, shift=True)), {"FINISHED"})
        self.assertFalse(game_stream._s.captured)
        self.assertEqual(self.session.calls, [("key", "up", "KeyD", "d"), ("mouse", "up", 640, 360, 0, 0.0, 0.0),
                                              ("release",)])

    def test_leaving_the_area_lifts_keys_and_a_click_outside_releases(self):
        handle = self.capture.handle
        self.capture.begin(Event("LEFTMOUSE", x=600, y=550))
        handle(Event("LEFTMOUSE", "RELEASE", x=600, y=550))
        handle(Event("W", x=600, y=550))
        self.session.calls.clear()
        self.assertEqual(handle(Event("MOUSEMOVE", "NOTHING", x=50, y=20)), {"PASS_THROUGH"})
        self.assertEqual(self.session.calls, [("key", "up", "KeyW", "w"), ("release",)])
        self.assertEqual(handle(Event("S", x=50, y=20)), {"PASS_THROUGH"}, "Blender's keys outside the Game View")
        self.assertTrue(game_stream._s.captured)
        handle(Event("MOUSEMOVE", "NOTHING", x=600, y=550))
        self.assertEqual(handle(Event("S", x=600, y=550)), {"RUNNING_MODAL"}, "back over the game")
        handle(Event("MOUSEMOVE", "NOTHING", x=50, y=20))
        self.assertEqual(handle(Event("LEFTMOUSE", x=50, y=20)), {"FINISHED", "PASS_THROUGH"})
        self.assertFalse(game_stream._s.captured)

    def test_window_losing_focus_releases(self):
        self.capture.begin(Event("LEFTMOUSE", x=600, y=550))
        self.capture.handle(Event("LEFT_SHIFT", x=600, y=550))
        self.session.calls.clear()
        self.assertEqual(self.capture.handle(Event("WINDOW_DEACTIVATE", "NOTHING")), {"FINISHED", "PASS_THROUGH"})
        self.assertEqual(self.session.calls[0], ("key", "up", "ShiftLeft", "Shift"))
        self.assertFalse(game_stream._s.captured)

    def test_the_gizmo_hits_the_frame_only(self):
        context = types.SimpleNamespace(region=self.region)
        self.assertTrue(game_stream._hit(context, (500, 500)))
        self.assertFalse(game_stream._hit(context, (500, 100)), "the bar below the frame")
        game_stream._s.captured = True
        self.assertFalse(game_stream._hit(context, (500, 500)), "already captured")

    def test_play_keys_follow_the_keymap(self):
        keys = game_stream.play_keys()
        self.assertTrue({("F5", False, False, False), ("F8", False, False, False)} <= keys)


class HeaderLayout(MockLayout):
    def template_header(self):
        self.texts.append("<editor type>")

    def separator_spacer(self):
        pass

    def operator(self, idname, text="", icon="NONE", **kwargs):
        if kwargs.get("depress"):
            self.texts.append(f"<pressed {text}>")
        return super().operator(idname, text=text, icon=icon, **kwargs)

    def _child(self, *_args, **_kwargs):
        return HeaderLayout(self._test, self.texts)

    row = column = _child


class HeaderTest(StubbedBlender):
    def draw(self, func, area):
        layout = HeaderLayout(self)
        context = types.SimpleNamespace(area=area, screen=self.screen, region=area.regions[0])
        func(types.SimpleNamespace(layout=layout), context)
        return layout.texts

    def test_game_header_and_scene_switch(self):
        self.stream_now()
        texts = self.draw(game_stream._image_header, self.big)
        self.assertEqual(texts[:4], ["<editor type>", "Scene", "<pressed Game>", "Game"])
        self.assertIn("Starting the game stream", texts)
        self.assertIn("0 fps", texts)
        with mock.patch.object(game_stream, "_header_draw", lambda _self, _context: None):
            self.assertEqual(self.draw(game_stream._image_header, self.small), [], "other Image Editors: Blender's")
        game_stream.show("SCENE")
        self.assertEqual(self.draw(game_stream.draw_view3d_header, self.big)[:2], ["<pressed Scene>", "Scene"])
        self.assertEqual(self.draw(game_stream.draw_view3d_header, self.small), [])

    def test_registered_once(self):
        draw = bpy.types.IMAGE_HT_header.draw
        self.assertTrue(draw is game_stream._image_header or game_stream._image_header in draw._draw_funcs)
        self.assertIn(game_stream.draw_view3d_header, bpy.types.VIEW3D_HT_header.draw._draw_funcs)
        self.assertIn("bl_rna", game_stream.GAMESTUDIO_GGT_game_stream.__dict__, "the gizmo group is registered")
        import gamestudio

        self.assertIn("editors.game_stream", gamestudio._MODULE_NAMES)

    def test_status_while_waiting_and_failing(self):
        self.assertEqual(game_stream.status_text()[1], "SORTTIME")
        _client, session = self.stream_now()
        session.status, session.message = "error", "The browser closed"
        self.assertEqual(game_stream.status_text(), ("The browser closed", "ERROR"))


class PreferenceTest(unittest.TestCase):
    def test_game_display(self):
        prop = prefs.GSPreferences.bl_rna.properties["game_display"]
        self.assertEqual(prop.default, "EMBEDDED")
        self.assertEqual([item.identifier for item in prop.enum_items], ["EMBEDDED", "WINDOW"])
        self.assertEqual(prefs._DefaultPrefs.game_display, "EMBEDDED")
        self.assertEqual(set(game_stream.DISPLAY_ICONS), {"EMBEDDED", "WINDOW"})

    def test_idle_minutes(self):
        prop = prefs.GSPreferences.bl_rna.properties["game_idle_minutes"]
        self.assertEqual((prop.default, prop.hard_min), (15, 0))
        self.assertEqual(prefs._DefaultPrefs.game_idle_minutes, 15)
        self.assertEqual(game_stream.idle_minutes(), 15)


if __name__ == "__main__":
    unittest.main()
