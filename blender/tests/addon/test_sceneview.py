# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scene view's lifecycle (core/sceneview.py) with a fake stream client and a fake hub: the camera
conversion (a world point lands at the same view-space point in Blender's and the game's conventions:
perspective, orthographic, an off-centre camera view), the game's address with ?gs_view, turning it on
(the browser in scene mode, the scene, view.mode and view.camera to that page only), the frames it
wants, the area settings and their restore, one Scene view at a time, Play and Stop, the warm page,
nothing in the .blend.

Background Blender has no windows: the areas, screens and windows are stand-ins (test_game_stream's);
the drawing itself is checked in a UI Blender (docs/SPEC.md §7.3).
"""

import math
import time
import types
import unittest
from unittest import mock

import bpy
from mathutils import Matrix, Vector

from gamestudio import prefs
from gamestudio.core import convert, link, play, sceneview
from gamestudio.editors import game_stream

from test_game_stream import GAME, TAB, FakeArea, StubbedBlender


def unflatten(values) -> Matrix:
    """A column-major list of 16 (three.js order) back into a Matrix."""
    return Matrix([[values[col * 4 + row] for col in range(4)] for row in range(4)])


def frustum(left, right, bottom, top, near, far) -> Matrix:
    """glFrustum: an off-centre perspective projection."""
    return Matrix((
        (2 * near / (right - left), 0, (right + left) / (right - left), 0),
        (0, 2 * near / (top - bottom), (top + bottom) / (top - bottom), 0),
        (0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)),
        (0, 0, -1, 0),
    ))


def ortho(left, right, bottom, top, near, far) -> Matrix:
    return Matrix((
        (2 / (right - left), 0, 0, -(right + left) / (right - left)),
        (0, 2 / (top - bottom), 0, -(top + bottom) / (top - bottom)),
        (0, 0, -2 / (far - near), -(far + near) / (far - near)),
        (0, 0, 0, 1),
    ))


def look_at(eye, target) -> Matrix:
    """Blender's world-to-view matrix of a view at `eye` looking at `target`, Z up."""
    eye, target = Vector(eye), Vector(target)
    rotation = (target - eye).to_track_quat("-Z", "Y").to_matrix().to_4x4()
    return (Matrix.Translation(eye) @ rotation).inverted()


POINTS = ((0, 0, 0), (1.5, -2.0, 0.75), (-3.0, 4.0, 2.0), (10.0, 7.5, -1.25))


class CameraConversionTest(unittest.TestCase):
    def check(self, view, projection):
        message = sceneview.camera_message(view, projection)
        game_view, game_projection = unflatten(message["view"]), unflatten(message["projection"])
        for point in POINTS:
            blender = view @ Vector((*point, 1.0))
            game = game_view @ Vector((*convert.vector_to_runtime(Vector(point)), 1.0))
            for a, b in zip(blender, game):
                self.assertAlmostEqual(a, b, places=5, msg=f"{point}: {blender} vs {game}")
            clip_b, clip_g = projection @ blender, game_projection @ game
            for a, b in zip(clip_b, clip_g):
                self.assertAlmostEqual(a, b, places=5)
        return message

    def test_perspective(self):
        view = look_at((7.0, -6.0, 5.0), (0.0, 0.0, 0.5))
        message = self.check(view, frustum(-0.08, 0.08, -0.045, 0.045, 0.1, 1000.0))
        self.assertFalse(message["ortho"])
        self.assertAlmostEqual(message["near"], 0.1, places=5)
        self.assertAlmostEqual(message["far"] / 1000.0, 1.0, places=3, msg="float32 matrices: far is approximate")
        self.assertEqual(message["t"], "view.camera")
        self.assertEqual(len(message["view"]), 16)

    def test_orthographic(self):
        view = look_at((0.0, -20.0, 0.0), (0.0, 0.0, 0.0))  # Numpad 1
        message = self.check(view, ortho(-8.0, 8.0, -4.5, 4.5, -500.0, 500.0))
        self.assertTrue(message["ortho"])
        self.assertAlmostEqual(message["near"], -500.0, places=3, msg="Blender's ortho views see behind the eye")
        self.assertAlmostEqual(message["far"], 500.0, places=3)

    def test_camera_view_with_zoom_and_offset(self):
        """Numpad 0: the region shows the camera's frame zoomed and moved, an off-centre frustum."""
        data = bpy.data.cameras.new("SceneViewCam")
        cam = bpy.data.objects.new("SceneViewCam", data)
        bpy.context.scene.collection.objects.link(cam)

        def remove():
            bpy.data.objects.remove(bpy.data.objects["SceneViewCam"])
            bpy.data.cameras.remove(bpy.data.cameras["SceneViewCam"])

        self.addCleanup(remove)
        cam.location = (4.0, -9.0, 3.0)
        cam.rotation_euler = (math.radians(75), 0.0, math.radians(25))
        data.clip_start, data.clip_end = 0.25, 300.0
        data.shift_x, data.shift_y = 0.15, -0.05
        bpy.context.view_layer.update()
        projection = cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=1600, y=900,
                                            scale_x=1.0, scale_y=1.0)
        view = cam.matrix_world.inverted()
        message = self.check(view, projection)
        self.assertAlmostEqual(message["near"], 0.25, places=4)
        self.assertAlmostEqual(message["far"] / 300.0, 1.0, places=3)
        # The region around the frame (zoomed out, panned): a wider, shifted frustum.
        zoomed = frustum(-0.14, 0.11, -0.09, 0.05, 0.25, 300.0)
        self.check(view, zoomed)
        data.type = "ORTHO"
        bpy.context.view_layer.update()
        message = self.check(view, cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=1600, y=900))
        self.assertTrue(message["ortho"])

    def test_the_game_view_is_y_up(self):
        """Blender's +Z is the game's +Y: a view looking down Blender's -Z looks down the game's -Y."""
        view = look_at((0.0, 0.0, 10.0), (0.0, 0.0, 0.0))
        game = unflatten(sceneview.camera_message(view, frustum(-1, 1, -1, 1, 1, 100))["view"])
        forward = game.inverted().to_3x3() @ Vector((0.0, 0.0, -1.0))
        self.assertAlmostEqual(forward.y, -1.0, places=5)
        eye = game.inverted().translation
        self.assertAlmostEqual(eye.y, 10.0, places=5)

    def test_column_major(self):
        matrix = Matrix.Translation((1.0, 2.0, 3.0))
        self.assertEqual(sceneview.column_major(matrix)[12:15], [1.0, 2.0, 3.0], "three.js keeps translation last")


class AddressTest(unittest.TestCase):
    def test_scene_url(self):
        self.assertEqual(sceneview.scene_url("http://localhost:5173/"), "http://localhost:5173/?gs_view=scene")
        self.assertEqual(sceneview.scene_url("http://localhost:5173/?a=1&gs_view=game"),
                         "http://localhost:5173/?a=1&gs_view=scene")
        self.assertIsNone(sceneview.scene_url(None))
        self.assertEqual(sceneview.game_url("http://localhost:5173/?gs_view=scene"), "http://localhost:5173/")

    def test_same_game(self):
        self.assertTrue(sceneview.same_game("http://localhost:5173/?gs_view=scene", "http://localhost:5173/"))
        self.assertFalse(sceneview.same_game("http://localhost:5174/", "http://localhost:5173/"))
        self.assertFalse(sceneview.same_game("", "http://localhost:5173/"))


# --- The lifecycle -----------------------------------------------------------------------------


class FakeRegion3D:
    def __init__(self):
        self.view_matrix = look_at((7.0, -6.0, 5.0), (0.0, 0.0, 0.0))
        self.window_matrix = frustum(-0.08, 0.08, -0.045, 0.045, 0.1, 1000.0)

    def as_pointer(self):
        return id(self)


class FakeSpace3D:
    type = "VIEW_3D"
    ui_mode = "VIEW"

    def __init__(self):
        self.shading = types.SimpleNamespace(type="MATERIAL", background_type="THEME", background_color=(0.2, 0.2, 0.2))
        self.overlay = types.SimpleNamespace(show_floor=True, show_axis_x=True, show_axis_y=True)
        self.region_3d = FakeRegion3D()


class FakeSpaces(list):
    @property
    def active(self):
        return self[0]


class SceneArea(FakeArea):
    """A 3D View with its space's shading, overlays and view."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.spaces = FakeSpaces([FakeSpace3D()])

    @property
    def space(self):
        return self.spaces[0]


class SceneViewCase(StubbedBlender):
    def setUp(self):
        super().setUp()
        self.small, self.big = SceneArea(width=400, height=300, x=1200, y=600), SceneArea()
        self.screen.areas = [self.small, self.big, self.console]
        self.window.scene = bpy.context.scene
        self.saved_prefs = (prefs._defaults.scene_view_fps, prefs._defaults.scene_view_animate)
        patches = (
            mock.patch.object(sceneview, "_screen", lambda name: self.screen if name == self.screen.name else None),
            mock.patch.object(sceneview, "_windows", lambda: [self.window]),
            mock.patch.object(sceneview, "_ensure_timer", lambda: None),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        sceneview.disable()
        sceneview._s.__init__()
        prefs._defaults.scene_view_fps, prefs._defaults.scene_view_animate = self.saved_prefs
        super().tearDown()

    def enable(self, area=None):
        return sceneview.enable(self.window, self.screen, area or self.big)

    def tick(self, client, times=3):
        for _ in range(times):
            client.pump()
            play.pump()
            sceneview._s.camera_at -= 1.0  # the camera rate never holds a test back
            sceneview.pump()
            game_stream._update()
        self.sync(client)

    def sync(self, client):
        """Wait until the hub has read everything sent so far (it reads on its own thread)."""
        if self.hub is None or not client.connected:
            return
        self._syncs = getattr(self, "_syncs", 0) + 1
        client.send({"t": "test.sync", "n": self._syncs})
        self.assertTrue(self.pump(lambda: any(m.get("n") == self._syncs for m in self.hub.messages("test.sync")),
                                  client))

    def sent(self):
        return [m for m in self.hub.messages() if m["t"] != "test.sync"]

    def page_joins(self, client, session, peers=(GAME,)):
        session.status = "running"
        session.set_quality = lambda **kw: session.calls.append(("quality", kw))
        self.hub.broadcast({"t": "peers", "peers": list(peers)})
        self.assertTrue(self.pump(lambda: game_stream.stream_peer(client) is not None, client))

    def on_and_showing(self, peers=(TAB,)):
        """The Scene view on in the big area, its page on the link, a first frame: (client, session)."""
        client = self.connect(list(peers))
        self.assertEqual(self.enable(), "")
        session = self.fake.sessions[-1]
        self.page_joins(client, session, (*peers, GAME))
        self.tick(client)
        session.frame_id += 1
        self.tick(client)
        self.assertTrue(sceneview.showing())
        return client, session

    def to_page(self, t):
        return [m for m in self.hub.messages(t) if m.get("to") == "g2"]


class LifecycleTest(SceneViewCase):
    def test_on_starts_the_page_in_scene_mode_and_sends_it_the_scene_and_the_view(self):
        client = self.connect([TAB])  # a browser tab is open too: it gets none of this
        self.assertEqual(self.enable(), "")
        self.assertTrue(sceneview.is_on())
        self.assertTrue(sceneview.is_area(self.big, self.screen))
        self.assertEqual(len(self.fake.sessions), 1)
        session = self.fake.sessions[0]
        self.assertEqual(session.url, "http://localhost:5173/?gs_view=scene")
        self.assertEqual(session.size, (1280, 720), "the region's shape, at most game_stream.MAX_PIXELS")
        self.assertEqual(self.big.space.shading.type, "SOLID")
        self.assertEqual(self.big.switches, [], "the area stays a 3D View")
        self.assertFalse(game_stream.view_open(), "no Game View")
        self.assertEqual(sceneview.status(), (sceneview.STARTING, "info"))
        self.tick(client)
        self.assertEqual(self.hub.messages("scene.load"), [], "not before the page is on the link")

        self.page_joins(client, session, (TAB, GAME))
        self.tick(client)
        loads, modes, cameras = self.hub.messages("scene.load"), self.hub.messages("view.mode"), self.hub.messages("view.camera")
        self.assertEqual([m["to"] for m in loads + modes + cameras], ["g2"] * 3, "the page only, once each")
        self.assertEqual((modes[0]["mode"], modes[0]["animate"]), ("scene", True))
        received = [m["t"] for m in self.hub.messages() if m.get("to") == "g2"]
        self.assertEqual(received, ["scene.load", "view.mode", "view.camera"])
        expected = sceneview.camera_message(self.big.space.region_3d.view_matrix,
                                            self.big.space.region_3d.window_matrix)
        self.assertEqual({k: cameras[0][k] for k in expected}, expected)
        self.assertEqual(sceneview.status(), (sceneview.LOADING, "info"), "until a frame comes")
        self.assertIs(sceneview.frame_region(), self.big.region())
        self.assertEqual([c for c in session.calls if c[0] == "paused"][-1:], [("paused", False, False)],
                         "frames for the Scene view (paused while the page loaded)")
        self.assertIn(("quality", {"max_fps": 30}), session.calls, "the Scene view's frame rate")
        self.assertEqual(self.big.space.shading.background_type, "THEME", "Blender's own drawing until a frame")

        redraws = self.big.region().redraws
        session.frame_id += 1
        self.tick(client)
        self.assertTrue(sceneview.showing())
        self.assertEqual(sceneview.status(), ("", ""))
        self.assertGreater(self.big.region().redraws, redraws, "a new frame redraws the area")
        space = self.big.space
        self.assertEqual((space.shading.background_type, tuple(space.shading.background_color)),
                         ("VIEWPORT", (0.0, 0.0, 0.0)))
        self.assertEqual((space.overlay.show_floor, space.overlay.show_axis_x, space.overlay.show_axis_y),
                         (False, False, False))
        self.assertEqual(len(self.hub.messages("view.camera")), 1, "an unchanged view is not sent again")

    def test_the_camera_follows_the_view(self):
        client, _session = self.on_and_showing()
        r3d = self.big.space.region_3d
        r3d.view_matrix = look_at((2.0, -9.0, 4.0), (0.0, 0.0, 1.0))
        self.tick(client, 1)
        cameras = self.to_page("view.camera")
        self.assertEqual(len(cameras), 2)
        self.assertEqual(cameras[-1]["view"], sceneview.camera_message(r3d.view_matrix, r3d.window_matrix)["view"])
        # Drawing the region gives the freshest view; within 1/60 s it waits, then the latest goes.
        r3d.view_matrix = look_at((3.0, -9.0, 4.0), (0.0, 0.0, 1.0))
        sceneview._s.camera_at = time.monotonic()
        sceneview.note_view(r3d)
        self.sync(client)
        self.assertEqual(len(self.to_page("view.camera")), 2, "rate limited")
        r3d.view_matrix = look_at((4.0, -9.0, 4.0), (0.0, 0.0, 1.0))
        sceneview.note_view(r3d)
        self.tick(client, 1)
        cameras = self.to_page("view.camera")
        self.assertEqual(len(cameras), 3, "one message, the latest view")
        self.assertEqual(cameras[-1]["view"], sceneview.camera_message(r3d.view_matrix, r3d.window_matrix)["view"])

    def test_off_restores_the_area_and_keeps_the_page(self):
        client, session = self.on_and_showing()
        space = self.big.space
        space.overlay.show_floor = True  # the user turned the grid back on meanwhile: kept
        sceneview.disable()
        self.assertFalse(sceneview.is_on())
        self.assertEqual(space.shading.type, "MATERIAL")
        self.assertEqual((space.shading.background_type, tuple(space.shading.background_color)),
                         ("THEME", (0.2, 0.2, 0.2)))
        self.assertEqual((space.overlay.show_floor, space.overlay.show_axis_x), (True, True))
        self.assertFalse(session.stopped, "the page stays warm for Play")
        self.assertIsNone(sceneview.frame_region())
        game_stream._update()
        self.assertEqual(session.calls[-1], ("paused", True, True), "no frames, the page hidden")
        sent = len(self.sent())
        self.tick(client)
        self.assertEqual(len(self.sent()), sent, "nothing more is sent")

    def test_one_scene_view_at_a_time(self):
        client, session = self.on_and_showing()
        self.assertEqual(self.enable(self.small), "")
        self.assertTrue(sceneview.is_area(self.small, self.screen))
        self.assertFalse(sceneview.is_area(self.big, self.screen))
        self.assertEqual(self.big.space.shading.type, "MATERIAL", "the first area is Blender's again")
        self.assertEqual(self.small.space.shading.type, "SOLID")
        self.assertEqual(len(self.fake.sessions), 1, "the same page")
        self.tick(client)
        self.assertEqual(len(self.to_page("scene.load")), 2, "the scene again for the new view")
        self.assertEqual(self.to_page("view.camera")[-1]["projection"],
                         sceneview.camera_message(self.small.space.region_3d.view_matrix,
                                                  self.small.space.region_3d.window_matrix)["projection"])

    def test_frames_pause_while_no_window_shows_the_area(self):
        client, session = self.on_and_showing()
        other = types.SimpleNamespace(name="Layout", areas=[SceneArea()], is_temporary=False, show_fullscreen=False)
        self.window.screen = other  # another workspace
        self.tick(client, 1)
        self.assertIsNone(sceneview.frame_region())
        self.assertEqual(session.calls[-1], ("paused", True, True))
        self.assertTrue(sceneview.is_on(), "still on, for when it shows again")
        self.window.screen = self.screen
        self.tick(client, 1)
        self.assertEqual(session.calls[-1], ("paused", False, False))
        self.assertIs(sceneview.frame_region(), self.big.region())

    def test_preferences(self):
        client, session = self.on_and_showing()
        prefs._defaults.scene_view_fps, prefs._defaults.scene_view_animate = 45, False
        sceneview.prefs_changed()
        self.tick(client, 1)
        self.assertEqual([c for c in session.calls if c[0] == "quality"][-1], ("quality", {"max_fps": 45}))
        modes = self.to_page("view.mode")
        self.assertEqual((modes[-1]["mode"], modes[-1]["animate"]), ("scene", False))
        prop = prefs.GSPreferences.bl_rna.properties
        self.assertEqual((prop["scene_view_fps"].default, prop["scene_view_animate"].default), (30, True))

    def test_the_area_closed_or_made_another_editor(self):
        client, _session = self.on_and_showing()
        self.big.ui_type = "OUTLINER"
        self.tick(client, 1)
        self.assertFalse(sceneview.is_on())
        self.assertEqual(self.big.space.shading.type, "MATERIAL", "its 3D View settings are put back")
        self.big.ui_type = "VIEW_3D"
        self.assertEqual(self.enable(), "")
        self.screen.areas.remove(self.big)  # joined into another area
        self.tick(client, 1)
        self.assertFalse(sceneview.is_on())

    def test_another_shading_turns_it_off(self):
        client, _session = self.on_and_showing()
        space = self.big.space
        space.shading.type = "MATERIAL"  # picked next to the Game toggle
        self.tick(client, 1)
        self.assertFalse(sceneview.is_on())
        self.assertEqual(space.shading.type, "MATERIAL", "the user's choice stays")
        self.assertEqual((space.shading.background_type, space.overlay.show_floor), ("THEME", True))

    def test_the_grid_can_come_back_but_not_the_background(self):
        client, _session = self.on_and_showing()
        space = self.big.space
        space.overlay.show_floor = True
        space.shading.background_type = "THEME"
        self.tick(client, 1)
        self.assertTrue(space.overlay.show_floor, "the user's grid stays")
        self.assertEqual(space.shading.background_type, "VIEWPORT", "the frame's colours need the black background")

    def test_saving_keeps_the_settings_out_of_the_file(self):
        client, _session = self.on_and_showing()
        space = self.big.space
        sceneview._on_save_pre()
        self.assertEqual((space.shading.type, space.shading.background_type, space.overlay.show_floor),
                         ("MATERIAL", "THEME", True))
        self.tick(client, 1)  # a timer during the save changes nothing
        self.assertEqual(space.shading.background_type, "THEME")
        sceneview._on_save_post()
        self.tick(client, 1)
        self.assertEqual((space.shading.type, space.shading.background_type, space.overlay.show_floor),
                         ("SOLID", "VIEWPORT", False))

    def test_loading_a_file_turns_it_off(self):
        self.on_and_showing()
        sceneview._on_load_pre()
        self.assertFalse(sceneview.is_on())
        self.assertIsNone(sceneview.frame_region())

    def test_the_warm_page_of_play_is_reused(self):
        client, session = self.stream_now(peers=(TAB,))  # Play, then Stop: the page stays
        self.assertEqual(self.op("stop"), {"FINISHED"})
        self.big.ui_type = "VIEW_3D"
        self.hub.clear()
        self.assertEqual(self.enable(), "")
        self.assertEqual(len(self.fake.sessions), 1, "no new browser")
        self.assertNotIn("navigate", [c[0] for c in session.calls], "the page is not reloaded for ?gs_view")
        self.tick(client)
        received = [m["t"] for m in self.hub.messages() if m.get("to") == "g2"]
        self.assertEqual(received, ["scene.load", "view.mode", "view.camera"])

    def test_errors(self):
        self.fake.ok = (False, "No Edge, Chrome or Chromium found")
        self.connect([])
        self.assertIn("No Edge, Chrome or Chromium found", self.enable())
        self.assertFalse(sceneview.is_on())
        self.assertEqual(self.big.space.shading.type, "MATERIAL")
        self.fake.ok = (True, "")
        self.fake.fail = "Node.js was not found"
        self.assertIn("Node.js was not found", self.enable())
        self.assertFalse(sceneview.is_on())
        self.assertEqual(self.big.space.shading.type, "MATERIAL")

    def test_a_failed_renderer_says_so(self):
        client = self.connect([])
        self.assertEqual(self.enable(), "")
        session = self.fake.sessions[0]
        session.status, session.message = "error", "The page crashed"
        self.tick(client, 1)
        text, kind = sceneview.status()
        self.assertEqual(kind, "error")
        self.assertIn("The page crashed", text)

    def test_without_the_dev_server(self):
        started = []
        with mock.patch.object(sceneview.devserver, "start", lambda root, on_ready=None: started.append(root) or "started"):
            self.assertEqual(self.enable(), "")
        self.assertEqual(len(started), 1, "the dev server starts")
        self.assertFalse(self.fake.sessions[0].url, "the browser starts before the address is known")
        sceneview.pump()
        self.assertIn(sceneview.status()[0], (sceneview.STARTING_SERVER, sceneview.CONNECTING, sceneview.NO_SERVER))
        client = self.connect([])
        self.tick(client, 1)
        self.assertIn(("navigate", "http://localhost:5173/?gs_view=scene"), self.fake.sessions[0].calls)


class StuckPageTest(SceneViewCase):
    def test_a_page_that_never_joins_is_loaded_again(self):
        client = self.connect([])
        self.assertEqual(self.enable(), "")
        session = self.fake.sessions[0]
        session.status = "running"  # the host loaded the page, but the game never joins the live link
        self.tick(client, 1)
        self.assertEqual([c for c in session.calls if c[0] == "navigate"], [])
        sceneview._s.stuck_since -= sceneview.STUCK_SECONDS
        self.tick(client, 1)
        self.assertEqual([c for c in session.calls if c[0] == "navigate"],
                         [("navigate", "http://localhost:5173/?gs_view=scene&gs_retry=1")])
        self.assertTrue(sceneview.same_game(session.url, "http://localhost:5173/"), "Play keeps this page")
        for _ in range(3):
            sceneview._s.stuck_since -= sceneview.STUCK_SECONDS
            self.tick(client, 1)
        self.assertEqual(len([c for c in session.calls if c[0] == "navigate"]), sceneview.MAX_RETRIES)


class PlayStopTest(SceneViewCase):
    def test_play_switches_the_page_to_game_mode_and_stop_brings_the_scene_back(self):
        client, session = self.on_and_showing()
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual(self.big.type, "IMAGE_EDITOR", "the Game View opens by the usual rules (here, this area)")
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        order = [(m["t"], m.get("mode")) for m in self.hub.messages()
                 if m["t"] in ("view.mode", "scene.load", "play.start")]
        self.assertEqual(order, [("view.mode", "game"), ("scene.load", None), ("play.start", None)])
        self.assertEqual(self.hub.messages("view.mode")[0]["to"], "g2")
        self.assertEqual(len(self.fake.sessions), 1, "Play uses the Scene view's page")
        self.assertNotIn("navigate", [c[0] for c in session.calls])
        self.tick(client)
        self.assertEqual(sceneview.status()[1], "playing")
        self.assertIsNone(sceneview.frame_region())
        self.hub.broadcast({"t": "game.status", "state": "playing", "view": "game", "from": "g2"})
        self.tick(client)
        self.assertEqual(sceneview.status(), (sceneview.PLAYING_VIEW, "playing"))
        self.assertEqual(self.hub.messages("view.camera"), [], "no camera while the game plays")

        self.assertEqual(self.op("stop"), {"FINISHED"})
        self.assertEqual(self.big.type, "VIEW_3D")
        self.tick(client)
        order = [(m["t"], m.get("mode")) for m in self.hub.messages()
                 if m["t"] in ("play.stop", "view.mode", "view.camera")]
        self.assertEqual(order[-3:], [("play.stop", None), ("view.mode", "scene"), ("view.camera", None)])
        self.assertIs(sceneview.frame_region(), self.big.region())
        self.assertFalse(sceneview.showing(), "not before a frame of the scene again")
        session.frame_id += 1
        self.tick(client, 1)
        self.assertTrue(sceneview.showing())
        self.assertEqual(self.big.space.shading.type, "SOLID", "the area kept its Scene view settings")

    def test_play_in_another_area_says_where_the_game_plays(self):
        client, session = self.on_and_showing()
        self.small.width, self.small.height = 1600, 926  # the other half is the larger: the Game View
        self.big.width, self.big.height = 800, 926
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertEqual((self.small.type, self.big.type), ("IMAGE_EDITOR", "VIEW_3D"))
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        self.hub.broadcast({"t": "game.status", "state": "playing", "from": "g2"})
        self.tick(client)
        self.assertEqual(sceneview.status(), (sceneview.PLAYING_VIEW, "playing"))
        self.assertFalse(sceneview.showing())
        self.assertEqual(self.big.space.shading.background_type, "THEME", "Blender's drawing meanwhile")
        self.assertEqual([c for c in session.calls if c[0] == "quality"][-1], ("quality", {"max_fps": 60}),
                         "the Game View's frame rate")

    def test_play_after_the_scene_view_was_turned_off(self):
        client, _session = self.on_and_showing()
        sceneview.disable()
        self.hub.clear()
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(self.pump(lambda: self.hub.messages("play.start"), client))
        self.assertEqual([m["mode"] for m in self.hub.messages("view.mode")], ["game"],
                         "the page it left in scene mode plays")

    def test_step_in_the_scene_view_and_a_stop_from_elsewhere(self):
        """Step (F7) leaves scene mode in the page itself (the engine); the game then stops."""
        client, session = self.on_and_showing()
        self.hub.broadcast({"t": "game.status", "state": "paused", "view": "game", "from": "g2"})
        self.tick(client)
        self.assertEqual(sceneview.status(), (sceneview.PLAYING_HERE, "playing"))
        self.assertIsNone(sceneview.frame_region())
        modes = len(self.to_page("view.mode"))
        self.hub.broadcast({"t": "game.status", "state": "stopped", "view": "game", "from": "g2"})
        self.tick(client)
        self.assertEqual([m["mode"] for m in self.to_page("view.mode")[modes:]], ["scene"])
        session.frame_id += 1
        self.tick(client, 1)
        self.assertTrue(sceneview.showing())

    def test_play_without_the_scene_view_sends_no_view_mode(self):
        self.stream_now(peers=(TAB,))
        self.assertEqual(self.hub.messages("view.mode"), [])


if __name__ == "__main__":
    unittest.main()
