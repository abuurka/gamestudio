# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scene view in a UI file (docs/GAME-UI.md §11.2, core/sceneview.py): the canvas camera's frame in
the region's pixels (camera_frame, ui_message), the page drawing the active screen over the start
level's last export (scene.load) or a checkerboard, `view.mode` ui following the view, a one-line hint
out of camera view, another screen, and Play and Stop handing the page over and back.

Background Blender has no windows: the areas and their views are test_sceneview's stand-ins; the
drawing itself (the game's frame under the overlays, the hint) needs a UI Blender.
"""

import json
import math
import shutil
import time
import types
import unittest
from unittest import mock

import bpy
from mathutils import Vector

from gamestudio.core import export, gameui, project, sceneview
from gamestudio.editors import scene_view

import helpers
from test_game_stream import GAME
from test_sceneview import SceneViewCase, frustum, look_at, ortho

REGION = (1600, 900)  # the big area's main region (test_game_stream.FakeArea)


def canvas_scene(name: str):
    """A screen of a UI file: a scene with the standard canvas camera (1920 x 1080, 100 px per unit)."""
    scene = bpy.data.scenes.new(name)
    cam = gameui.setup_canvas_scene(scene, (1920, 1080))
    cam.matrix_world = cam.matrix_basis.copy()  # what the depsgraph would give the screen's camera
    return scene


class CameraFrameTest(unittest.TestCase):
    def test_an_orthographic_camera_view(self):
        # A 19.2 x 10.8 frame seen by a view 24 units wide in a 1600 x 900 region, panned 2 units right.
        corners = [Vector((x, y, 0.0)) for x in (-9.6, 9.6) for y in (-5.4, 5.4)]
        frame = sceneview.camera_frame(corners, ortho(-12, 12, -6.75, 6.75, 0.1, 100), *REGION)
        self.assertEqual(frame, [160.0, 90.0, 1280.0, 720.0])
        frame = sceneview.camera_frame(corners, ortho(-10, 14, -6.75, 6.75, 0.1, 100), *REGION)
        self.assertEqual(frame, [26.67, 90.0, 1280.0, 720.0])

    def test_a_perspective_view_and_what_it_cannot_frame(self):
        view = look_at((0.0, 0.0, 10.0), (0.0, 0.0, 0.0))
        corners = [Vector((x, y, 0.0)) for x in (-0.1, 0.1) for y in (-0.1, 0.1)]
        frame = sceneview.camera_frame(corners, frustum(-0.1, 0.1, -0.1, 0.1, 1.0, 100.0) @ view, 1000, 1000)
        self.assertEqual(frame, [450.0, 450.0, 100.0, 100.0], "0.1 at 10 units' distance: a tenth of the half width")
        behind = [Vector((0.0, 0.0, 20.0))] + corners[1:]
        self.assertIsNone(sceneview.camera_frame(behind, frustum(-0.1, 0.1, -0.1, 0.1, 1.0, 100.0) @ view, 1000, 1000))
        tiny = [Vector((x, 0.0, 0.0)) for x in (0.0, 1e-6)] * 2
        self.assertIsNone(sceneview.camera_frame(tiny, ortho(-1, 1, -1, 1, 0.1, 10), 100, 100), "an empty rectangle")

    def test_the_hint_sits_at_the_bottom_centre(self):
        x, y, width, height = scene_view.hint_box(1000, 300, 13, 1.0)
        self.assertEqual((x + width / 2, y, height), (500, 24, 29))


class UiSceneViewCase(SceneViewCase):
    def setUp(self):
        super().setUp()
        self.title = canvas_scene("Title")
        self.window.scene = self.title
        self.look_through(self.title)
        patches = (
            mock.patch.object(sceneview, "_ui_file", lambda: True),
            mock.patch.object(sceneview.export, "export_scene",
                              side_effect=AssertionError("a UI file's Scene view exports no level")),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def look_through(self, scene, half_width=12.0, pan=0.0, area=None):
        """The area's view through the screen's camera (Numpad 0), `half_width` units each side."""
        r3d = (area or self.big).space.region_3d
        r3d.view_perspective = "CAMERA"
        r3d.view_matrix = scene.camera.matrix_world.inverted()
        half_height = half_width * REGION[1] / REGION[0]
        r3d.window_matrix = ortho(-half_width + pan, half_width + pan, -half_height, half_height, 0.1, 100.0)
        space = (area or self.big).space
        space.camera, space.use_local_camera = None, False

    def write_start_level(self, rev=4):
        path = project.export_dir(self.root) / "main.scene.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"scene": "main", "rev": rev, "entities": []}), encoding="utf-8")

    def tick(self, client, times=3):
        for _ in range(times):
            sceneview._s.ui_at -= 1.0  # the rate never holds a test back
            super().tick(client, 1)

    def showing(self, peers=()):
        """On in the big area, its page on the link, a first frame: (client, session)."""
        client = self.connect(list(peers))
        self.assertEqual(self.enable(), "")
        session = self.fake.sessions[-1]
        self.page_joins(client, session, (*peers, GAME))
        self.tick(client)
        session.frame_id += 1
        self.tick(client)
        return client, session


class UiFileTest(UiSceneViewCase):
    def test_the_page_draws_the_screen_over_the_start_level(self):
        self.write_start_level(rev=4)
        client, session = self.showing()
        received = [m["t"] for m in self.hub.messages() if m.get("to") == "g2"]
        self.assertEqual(received, ["scene.load", "view.mode"], "no view.camera: the screen's frame is in view.mode")
        load = self.to_page("scene.load")[0]
        self.assertEqual((load["scene"], load["url"], load["rev"]), ("main", "/__gs/export/main.scene.json", 4))
        mode = self.to_page("view.mode")[0]
        self.assertEqual({k: mode[k] for k in ("mode", "canvas", "frame", "region", "background", "animate")},
                         {"mode": "ui", "canvas": "Title", "frame": [160.0, 90.0, 1280.0, 720.0],
                          "region": list(REGION), "background": "level", "animate": True})
        self.assertTrue(sceneview.showing())
        self.assertEqual(sceneview.status(), ("", ""))
        self.assertIs(sceneview.frame_region(), self.big.region())
        self.assertEqual(self.big.space.shading.background_type, "VIEWPORT", "the frame's settings, as for a level")
        self.assertTrue(self.title.gs.live_enabled, "the UI live link keeps the page's screens current")
        self.tick(client)
        self.assertEqual(len(self.to_page("view.mode")), 1, "an unchanged view is not sent again")

    def test_without_a_start_level_export_a_checkerboard(self):
        self.showing()
        self.assertEqual(self.to_page("scene.load"), [])
        self.assertEqual(self.to_page("view.mode")[0]["background"], "checker")
        self.assertTrue(sceneview.showing())

    def test_the_frame_follows_the_view(self):
        client, _session = self.showing()
        self.look_through(self.title, half_width=24.0)  # zoomed out: half the size
        self.tick(client, 1)
        modes = self.to_page("view.mode")
        self.assertEqual(len(modes), 2)
        self.assertEqual(modes[-1]["frame"], [480.0, 270.0, 640.0, 360.0])
        # Drawing the region gives the freshest frame; within 1/60 s it waits, then the latest goes.
        r3d, region, space = self.big.space.region_3d, self.big.region(), self.big.space
        self.look_through(self.title, half_width=24.0, pan=2.0)
        sceneview._s.ui_at = time.monotonic()
        sceneview.note_view(r3d, region, space, self.title)
        self.sync(client)
        self.assertEqual(len(self.to_page("view.mode")), 2, "rate limited")
        self.look_through(self.title, half_width=24.0, pan=4.0)
        sceneview.note_view(r3d, region, space, self.title)
        sceneview._s.ui_at -= 1.0
        sceneview.flush_ui()
        self.sync(client)
        modes = self.to_page("view.mode")
        self.assertEqual(len(modes), 3, "one message, the latest view")
        self.assertEqual(modes[-1]["frame"], [346.67, 270.0, 640.0, 360.0])
        # A wider region: the frame and the region size it is measured in.
        region.width = 1200
        self.tick(client, 1)
        self.assertEqual(self.to_page("view.mode")[-1]["region"], [1200, 900])

    def test_out_of_camera_view_the_area_says_how_to_get_there(self):
        client, session = self.showing()
        self.big.space.region_3d.view_perspective = "PERSP"
        self.tick(client, 1)
        self.assertEqual(sceneview.status(), (sceneview.UI_HINT, "hint"))
        self.assertFalse(sceneview.showing())
        self.assertIsNone(sceneview.frame_region(), "no frames meanwhile")
        self.assertEqual(self.big.space.shading.background_type, "THEME", "Blender draws the view")
        # Through another camera (the area's local one) it is the same.
        other = bpy.data.objects.new("Other", bpy.data.cameras.new("Other"))
        self.title.collection.objects.link(other)
        self.big.space.region_3d.view_perspective = "CAMERA"
        self.big.space.camera, self.big.space.use_local_camera = other, True
        self.tick(client, 1)
        self.assertEqual(sceneview.status()[1], "hint")
        self.assertEqual(len(self.to_page("view.mode")), 1)
        # Back through the screen's camera: drawn again.
        self.big.space.use_local_camera = False
        self.tick(client, 1)
        session.frame_id += 1
        self.tick(client, 1)
        self.assertTrue(sceneview.showing())
        self.assertEqual(sceneview.status(), ("", ""))

    def test_another_screen(self):
        client, _session = self.showing()
        hud = canvas_scene("HUD")
        hud.camera.location.x = 4.0  # elsewhere in the file: its own camera
        hud.camera.matrix_world = hud.camera.matrix_basis.copy()
        hud.gs.live_enabled = False  # turned off by the user
        self.window.scene = hud
        self.look_through(hud)
        self.tick(client, 1)
        mode = self.to_page("view.mode")[-1]
        self.assertEqual((mode["canvas"], mode["frame"]), ("HUD", [160.0, 90.0, 1280.0, 720.0]))
        self.assertTrue(hud.gs.live_enabled, "its edits reach the page too")
        self.assertEqual(len(self.to_page("scene.load")), 0, "the background stays")

    def test_play_hands_the_page_over_and_stop_brings_the_screen_back(self):
        self.write_start_level()
        client, session = self.showing()
        sceneview.before_play(client)  # Play from the UI file (core/play.py send_play)
        self.sync(client)
        self.assertEqual(self.to_page("view.mode")[-1]["mode"], "game")
        self.hub.broadcast({"t": "game.status", "state": "playing", "view": "game", "from": "g2"})
        self.tick(client)
        self.assertEqual(sceneview.status()[1], "playing")
        self.assertFalse(sceneview.showing())
        modes = len(self.to_page("view.mode"))
        loads = len(self.to_page("scene.load"))
        self.hub.broadcast({"t": "game.status", "state": "stopped", "view": "game", "from": "g2"})
        self.tick(client)
        self.assertEqual([m["mode"] for m in self.to_page("view.mode")[modes:]], ["ui"])
        self.assertEqual(len(self.to_page("scene.load")), loads + 1, "the start level again (the game may have left it)")
        session.frame_id += 1
        self.tick(client, 1)
        self.assertTrue(sceneview.showing())

    def test_a_page_that_left_the_ui_view_gets_it_again(self):
        client, _session = self.showing()
        self.hub.broadcast({"t": "game.status", "state": "stopped", "view": "scene", "from": "g2"})  # a tool's scene shot
        sceneview._s.mode_at -= sceneview.RESEND_AFTER + 1.0
        self.tick(client, 1)
        self.assertEqual(len(self.to_page("view.mode")), 2)
        self.assertEqual(self.to_page("view.mode")[-1]["mode"], "ui")


class StartLevelTest(unittest.TestCase):
    def test_the_start_levels_export(self):
        root = helpers.make_project()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        self.assertIsNone(sceneview.start_level_export(root))
        config = json.loads((root / "gamestudio.json").read_text(encoding="utf-8"))
        config["startScene"] = "Level One"
        (root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
        out = project.export_dir(root)
        out.mkdir(parents=True, exist_ok=True)
        name = export._sanitize("Level One")
        (out / f"{name}.scene.json").write_text("not json", encoding="utf-8")
        self.assertIsNone(sceneview.start_level_export(root))
        (out / f"{name}.scene.json").write_text(json.dumps({"scene": name, "rev": 7}), encoding="utf-8")
        self.assertEqual(sceneview.start_level_export(root),
                         {"scene": name, "url": f"/__gs/export/{name}.scene.json", "rev": 7})


class UiMessageTest(unittest.TestCase):
    def test_what_the_camera_view_shows(self):
        scene = canvas_scene("Turned")
        self.addCleanup(lambda: bpy.data.scenes.remove(scene))
        space = types.SimpleNamespace(camera=None, use_local_camera=False)
        region = types.SimpleNamespace(width=REGION[0], height=REGION[1])
        cam = scene.camera
        cam.rotation_euler = (0.0, 0.0, math.radians(90))  # a camera view turns with its camera
        cam.matrix_world = cam.matrix_basis.copy()
        r3d = types.SimpleNamespace(view_perspective="CAMERA", view_matrix=cam.matrix_world.inverted(),
                                    window_matrix=ortho(-12, 12, -6.75, 6.75, 0.1, 100.0))
        message = sceneview.ui_message(scene, space, region, r3d, background="level")
        self.assertEqual((message["canvas"], message["frame"], message["region"], message["background"]),
                         ("Turned", [160.0, 90.0, 1280.0, 720.0], list(REGION), "level"))
        space.region_3d = r3d
        self.assertEqual(sceneview.ui_message(scene, space, region)["frame"], message["frame"], "the space's own view")
        r3d.view_perspective = "ORTHO"  # Numpad 7: not through the camera
        self.assertIsNone(sceneview.ui_message(scene, space, region, r3d))
        r3d.view_perspective = "CAMERA"
        scene.camera = bpy.data.objects.new("NotACamera", None)
        self.assertIsNone(sceneview.ui_message(scene, space, region, r3d))
        scene.camera = None
        self.assertIsNone(sceneview.ui_message(scene, space, region, r3d))


if __name__ == "__main__":
    unittest.main()
