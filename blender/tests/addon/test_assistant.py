# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tests for the AI assistant: tools, proposals, the apply step and the agent loop.

A scripted fake model stands in for the Claude API, so these tests need no
network and no API key.

Runs as part of blender/tests/addon/run_tests.py, or on its own:
  blender --background --factory-startup --python-exit-code 1 --python blender/tests/addon/test_assistant.py
"""

import json
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "blender"))

import bpy  # noqa: E402

import gamestudio  # noqa: E402

REGISTRY = {
    "format": "gamestudio.registry",
    "formatVersion": 1,
    "generatedAt": "2026-10-09T00:00:00.000Z",
    "components": [
        {"name": "Spin", "builtin": True, "category": "Motion", "description": "Rotates continuously.",
         "fields": [{"name": "speed", "type": "float", "default": 1.0, "min": 0, "max": 100},
                    {"name": "axis", "type": "vec3", "default": [0, 1, 0]}]},
        {"name": "RigidBody", "builtin": True, "category": "Physics",
         "fields": [{"name": "type", "type": "enum", "default": "dynamic",
                     "options": ["dynamic", "fixed", "kinematic"]},
                    {"name": "mass", "type": "float", "default": 1.0, "min": 0}]},
        {"name": "Coin", "builtin": False, "category": "Demo", "source": "src/components/coin.ts",
         "fields": [{"name": "value", "type": "int", "default": 1, "min": 0}]},
        {"name": "FollowCamera", "builtin": True, "category": "Camera",
         "fields": [{"name": "target", "type": "entity", "default": None},
                    {"name": "offset", "type": "vec3", "default": [0, 4, 8]}]},
    ],
}

SPIN_SYSTEM = """import { defineSystem } from '@gamestudio/engine';
import { Spin } from '../components/spin';

export default defineSystem({
  name: 'SpinSystem',
  query: [Spin],
  update(ctx, entities) {
    for (const e of entities) {
      e.object3d.rotation.y += e.get(Spin).speed * ctx.dt;
    }
  },
});
"""


class FakeLLM:
    """Replays scripted responses. A step may be a callable taking the request params."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def stream(self, params, on_text, on_thinking, cancel):
        self.calls.append(json.loads(json.dumps(params, default=_jsonable)))
        step = self.steps.pop(0)
        if callable(step):
            step = step(params)
        content, stop = step
        for block in content:
            if block.type == "text":
                on_text(block.text)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


def _jsonable(obj):
    return vars(obj) if hasattr(obj, "__dict__") else str(obj)


def text(t):
    return SimpleNamespace(type="text", text=t)


_tool_ids = iter(range(1, 10_000))


def call(name, **inputs):
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_tool_ids)}", name=name, input=inputs)


def collect(name, args, source="assistant", allowed=None):
    from gamestudio.assistant import tools
    from gamestudio.core import project

    box = []
    ctx = tools.ToolContext(source=source, project_root=project.project_root())
    tools.invoke(name, args, ctx, lambda ok, content: box.append((ok, content)), allowed=allowed)
    assert box, "tool did not answer"
    ok, content = box[0]
    try:
        return ok, json.loads(content)
    except ValueError:
        return ok, content


def run_session(prompt, steps, *, role="general", mode="propose", teach=False):
    from gamestudio.assistant import agent

    fake = FakeLLM(steps)
    agent.SESSION.llm_factory = lambda: fake
    agent.SESSION.submit(prompt, role=role, mode=mode, teach=teach)
    deadline = time.monotonic() + 20
    while agent.SESSION.busy:
        agent.pump()
        time.sleep(0.005)
        if time.monotonic() > deadline:
            raise AssertionError("agent did not finish")
    agent.pump()
    return fake


class AssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_ai_test_"))
        root = cls.tmp / "proj"
        (root / ".gamestudio").mkdir(parents=True)
        (root / "src" / "systems").mkdir(parents=True)
        (root / "src" / "components").mkdir(parents=True)
        (root / "scenes").mkdir()
        (root / "gamestudio.json").write_text(json.dumps({"name": "ai-test", "formatVersion": 1,
                                                          "startScene": "main"}))
        (root / ".gamestudio" / "components.json").write_text(json.dumps(REGISTRY))
        (root / "src" / "systems" / "spin.ts").write_text(SPIN_SYSTEM)
        (root / "ASSISTANT.md").write_text("Use 2-space indentation.\n")
        cls.root = root

        bpy.ops.wm.read_factory_settings(use_empty=True)
        # run_tests.py registers the add-on once for the whole suite.
        cls.registered_here = not hasattr(bpy.types.WindowManager, "gs_ai")
        if cls.registered_here:
            gamestudio.register()
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "scenes" / "main.blend"))
        from gamestudio.core import registry
        assert registry.reload_registry(root), "registry failed to load"

    @classmethod
    def tearDownClass(cls):
        if cls.registered_here:
            gamestudio.unregister()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from gamestudio.assistant import agent
        from gamestudio.assistant.proposals import STORE

        STORE.clear()
        agent.SESSION.new_conversation()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj)
        scene = bpy.context.scene
        mesh = bpy.data.meshes.new("CrateMesh")
        crate = bpy.data.objects.new("Crate", mesh)
        scene.collection.objects.link(crate)
        crate.location = (0, 0, 2)
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        scene.collection.objects.link(cam)
        bpy.context.view_layer.objects.active = crate

    # ------------------------------------------------------------- read tools

    def test_scene_overview_lists_objects_and_components(self):
        from gamestudio.core import components
        components.add_component(bpy.data.objects["Crate"], "RigidBody")
        ok, data = collect("get_scene_overview", {})
        self.assertTrue(ok, data)
        names = {o["name"]: o for o in data["objects"]}
        self.assertIn("Crate", names)
        self.assertEqual(names["Crate"]["components"], ["RigidBody"])
        self.assertEqual(data["project"], "ai-test")
        self.assertIn("Coin", data["component_types"])

    def test_get_object_includes_component_values(self):
        from gamestudio.core import components
        components.add_component(bpy.data.objects["Crate"], "Spin", {"speed": 3})
        ok, data = collect("get_object", {"name": "Crate"})
        self.assertTrue(ok, data)
        self.assertEqual(data["components"]["Spin"]["values"]["speed"], 3)
        self.assertEqual(data["location"], [0, 0, 2])

    def test_unknown_object_suggests_close_names(self):
        ok, data = collect("get_object", {"name": "Crat"})
        self.assertFalse(ok)
        self.assertIn("Did you mean: Crate", data["error"])

    def test_invalid_input_is_rejected_before_running(self):
        ok, data = collect("get_object", {"nam": "Crate"})
        self.assertFalse(ok)
        self.assertTrue(any("missing required field 'name'" in p for p in data["problems"]))

    def test_project_file_tools(self):
        ok, data = collect("list_project_files", {"glob": "src/*"})
        self.assertTrue(ok, data)
        self.assertIn("src/systems/spin.ts", data["files"])
        ok, data = collect("read_project_file", {"path": "src/systems/spin.ts", "start_line": 4, "end_line": 5})
        self.assertTrue(ok, data)
        self.assertIn("defineSystem({", data["content"])
        ok, data = collect("search_project", {"pattern": r"name: '\w+'"})
        self.assertTrue(ok, data)
        self.assertEqual(len(data["matches"]), 1)

    def test_paths_are_confined_to_the_project(self):
        for bad in ("../outside.txt", "node_modules/x/index.js", ".gamestudio/session.json", "C:/Windows/win.ini"):
            ok, data = collect("read_project_file", {"path": bad})
            self.assertFalse(ok, bad)

    def test_engine_docs_tool_answers(self):
        ok, data = collect("get_engine_docs", {})
        # The docs may not exist yet in a fresh checkout; either way the tool must answer cleanly.
        self.assertIsNotNone(data)

    # ---------------------------------------------------------- proposals

    def test_scene_proposal_is_staged_not_applied(self):
        ok, data = collect("propose_scene_changes", {
            "title": "Add a coin", "rationale": "The level needs a pickup.",
            "operations": [
                {"op": "create_object", "name": "Coin1", "primitive": "cylinder", "size": 0.5,
                 "location": [1, 2, 1], "rotation_deg": [90, 0, 0], "color": [1, 0.8, 0.1]},
                {"op": "add_component", "object": "Coin1", "component": "Coin", "values": {"value": 5}},
                {"op": "add_component", "object": "Coin1", "component": "Spin"},
                {"op": "set_component_field", "object": "Coin1", "component": "Spin", "field": "speed", "value": 4},
            ]})
        self.assertTrue(ok, data)
        self.assertEqual(data["status"], "pending")
        self.assertNotIn("Coin1", bpy.data.objects, "a proposal must not change the scene")

        result = bpy.ops.gamestudio.ai_apply(proposal_id=data["proposal_id"])
        self.assertEqual(result, {"FINISHED"})
        from gamestudio.core import components, ids
        coin = bpy.data.objects["Coin1"]
        self.assertEqual(components.list_components(coin), ["Coin", "Spin"])
        self.assertEqual(components.get_values(coin, "Coin")["value"], 5)
        self.assertAlmostEqual(components.get_values(coin, "Spin")["speed"], 4)
        self.assertEqual(coin.gs.ai_touched, data["proposal_id"])
        self.assertTrue(ids.get_id(coin))
        self.assertAlmostEqual(coin.location.z, 1)

    def test_invalid_component_field_fails_at_proposal_time(self):
        ok, data = collect("propose_scene_changes", {
            "title": "t", "rationale": "r",
            "operations": [{"op": "add_component", "object": "Crate", "component": "Spin",
                            "values": {"speed": 500}}]})
        self.assertFalse(ok)
        self.assertIn("speed", data["error"])
        ok, data = collect("propose_scene_changes", {
            "title": "t", "rationale": "r",
            "operations": [{"op": "add_component", "object": "Crate", "component": "Spinner"}]})
        self.assertFalse(ok)
        self.assertIn("Known:", data["error"])

    def test_entity_field_accepts_object_names(self):
        ok, data = collect("propose_scene_changes", {
            "title": "Camera follows crate", "rationale": "r",
            "operations": [{"op": "add_component", "object": "Camera", "component": "FollowCamera",
                            "values": {"target": "Crate"}}]})
        self.assertTrue(ok, data)
        bpy.ops.gamestudio.ai_apply(proposal_id=data["proposal_id"])
        from gamestudio.core import components
        values = components.get_values(bpy.data.objects["Camera"], "FollowCamera", for_export=True)
        self.assertEqual(values["target"], bpy.data.objects["Crate"]["gs_id"])

    def test_locked_objects_are_refused(self):
        bpy.data.objects["Crate"].gs.ai_lock = True
        try:
            ok, data = collect("propose_scene_changes", {
                "title": "Move", "rationale": "r",
                "operations": [{"op": "set_transform", "object": "Crate", "location": [5, 5, 5]}]})
            self.assertFalse(ok)
            self.assertIn("locked", data["error"])
        finally:
            bpy.data.objects["Crate"].gs.ai_lock = False

    def test_delete_is_high_risk_and_stale_detection_works(self):
        ok, data = collect("propose_scene_changes", {
            "title": "Remove crate", "rationale": "r",
            "operations": [{"op": "delete_object", "object": "Crate"}]})
        self.assertTrue(ok, data)
        self.assertEqual(data["risk"], "high")
        from gamestudio.assistant import proposals
        p = proposals.STORE.get(data["proposal_id"])
        self.assertEqual(proposals.stale_reasons(p), [])
        bpy.data.objects["Crate"].location.x = 9  # the developer moves it afterwards
        self.assertTrue(proposals.stale_reasons(p))
        self.assertEqual(bpy.ops.gamestudio.ai_apply(proposal_id=p.id), {"CANCELLED"})
        self.assertIn("Crate", bpy.data.objects)

    def test_file_edit_apply_and_revert(self):
        path = self.root / "src" / "systems" / "spin.ts"
        original = path.read_text()
        ok, data = collect("propose_file_edit", {
            "title": "Spin twice as fast", "rationale": "r", "path": "src/systems/spin.ts",
            "edits": [{"old_text": "e.get(Spin).speed * ctx.dt", "new_text": "e.get(Spin).speed * 2 * ctx.dt"}]})
        self.assertTrue(ok, data)
        self.assertEqual(path.read_text(), original, "a proposal must not touch the file")
        self.assertEqual(bpy.ops.gamestudio.ai_apply_file(proposal_id=data["proposal_id"]), {"FINISHED"})
        self.assertIn("speed * 2 * ctx.dt", path.read_text())
        backup = self.root / ".gamestudio" / "ai" / "backups" / data["proposal_id"] / "src" / "systems" / "spin.ts"
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(bpy.ops.gamestudio.ai_revert(proposal_id=data["proposal_id"]), {"FINISHED"})
        self.assertEqual(path.read_text(), original)

    def test_file_edit_refuses_ambiguous_or_missing_text(self):
        ok, data = collect("propose_file_edit", {
            "title": "t", "rationale": "r", "path": "src/systems/spin.ts",
            "edits": [{"old_text": "Spin", "new_text": "Twirl"}]})
        self.assertFalse(ok)
        self.assertIn("matches", data["error"])
        ok, data = collect("propose_file_edit", {
            "title": "t", "rationale": "r", "path": "src/systems/spin.ts",
            "edits": [{"old_text": "doesNotExist()", "new_text": "x"}]})
        self.assertFalse(ok)

    def test_file_proposal_without_a_project_is_stale_not_an_error(self):
        ok, data = collect("propose_file_edit", {
            "title": "t", "rationale": "r", "path": "src/systems/spin.ts",
            "edits": [{"old_text": "name: 'SpinSystem'", "new_text": "name: 'Spinner'"}]})
        self.assertTrue(ok, data)
        from gamestudio.assistant import proposals
        p = proposals.STORE.get(data["proposal_id"])
        root = proposals._project_root
        proposals.set_project_root(None)  # a scene outside the project was opened (the panel still draws)
        try:
            self.assertEqual(proposals.stale_reasons(p), ["no GameStudio project is open"])
        finally:
            proposals.set_project_root(root)

    def test_stale_file_edit_is_not_written(self):
        path = self.root / "src" / "systems" / "spin.ts"
        original = path.read_text()
        ok, data = collect("propose_file_edit", {
            "title": "t", "rationale": "r", "path": "src/systems/spin.ts",
            "edits": [{"old_text": "name: 'SpinSystem'", "new_text": "name: 'Spinner'"}]})
        self.assertTrue(ok, data)
        path.write_text(original + "\n// edited by the developer\n")
        try:
            # The operator reports an error, which bpy.ops raises as RuntimeError in scripts.
            with self.assertRaisesRegex(RuntimeError, "changed since the proposal was made"):
                bpy.ops.gamestudio.ai_apply_file(proposal_id=data["proposal_id"])
            self.assertIn("edited by the developer", path.read_text())
            self.assertNotIn("Spinner", path.read_text())
        finally:
            path.write_text(original)

    def test_new_file_with_risky_code_is_high_risk(self):
        ok, data = collect("propose_new_file", {
            "title": "t", "rationale": "r", "path": "src/systems/net.ts",
            "content": "const cp = require('child_process');\n"})
        self.assertTrue(ok, data)
        self.assertEqual(data["risk"], "high")
        self.assertFalse((self.root / "src" / "systems" / "net.ts").exists())

    def test_ask_mode_and_mentor_cannot_propose(self):
        from gamestudio.assistant import tools
        self.assertNotIn("propose_scene_changes", tools.allowed_names("general", "ask"))
        self.assertFalse(any(t.tier == tools.PROPOSE for t in tools.tools_for("mentor")))
        ok, data = collect("propose_scene_changes",
                           {"title": "t", "rationale": "r", "operations": [{"op": "delete_object", "object": "Crate"}]},
                           allowed=tools.allowed_names("general", "ask"))
        self.assertFalse(ok)
        self.assertIn("read-only", data["error"])

    # ---------------------------------------------------------- agent loop

    def test_agent_loop_stages_a_proposal_and_reports_the_decision(self):
        def propose(params):
            # The model saw the overview result before proposing.
            last = params["messages"][-1]["content"][0]
            assert last["type"] == "tool_result" and "Crate" in last["content"]
            return ([text("I'll add a coin."),
                     call("propose_scene_changes", title="Add coin", rationale="Pickup above the crate",
                          operations=[{"op": "create_object", "name": "Coin9", "primitive": "cylinder",
                                       "location": [0, 0, 4]}])], "tool_use")

        fake = run_session("Add a coin above the crate", [
            ([call("get_scene_overview")], "tool_use"),
            propose,
            ([text("Proposed p1. Review it in the Proposals list.")], "end_turn"),
        ])
        from gamestudio.assistant import agent
        from gamestudio.assistant.proposals import STORE
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.calls[0]["model"], "claude-opus-5-5")
        self.assertEqual(fake.calls[0]["fallbacks"], "default")
        self.assertTrue(all(t["eager_input_streaming"] for t in fake.calls[0]["tools"]))
        self.assertIn("engine", fake.calls[0]["system"][0]["text"])
        self.assertIn("Use 2-space indentation.", fake.calls[0]["system"][0]["text"])
        self.assertEqual([p.status for p in STORE.all()], ["pending"])
        self.assertNotIn("Coin9", bpy.data.objects)
        self.assertGreater(agent.SESSION.cost, 0)

        pid = STORE.all()[0].id
        bpy.ops.gamestudio.ai_apply(proposal_id=pid)
        self.assertIn("Coin9", bpy.data.objects)
        fake2 = run_session("Thanks", [([text("Great.")], "end_turn")])
        context_text = fake2.calls[0]["messages"][-1]["content"][0]["text"]
        self.assertIn(f"{pid} \"Add coin\": applied", context_text)

    def test_parallel_tool_calls_return_in_one_message(self):
        fake = run_session("Describe", [
            ([call("get_selection"), call("list_component_types")], "tool_use"),
            ([text("Done")], "end_turn"),
        ])
        results = fake.calls[1]["messages"][-1]["content"]
        self.assertEqual([r["type"] for r in results], ["tool_result", "tool_result"])

    def test_truncated_tool_call_keeps_history_valid(self):
        from gamestudio.assistant import agent
        run_session("Do something big", [([call("get_selection")], "max_tokens")])
        last = agent.SESSION.messages[-1]
        self.assertEqual(last["role"], "user")
        self.assertTrue(last["content"][0]["is_error"])
        self.assertEqual(agent.SESSION.transcript[-1]["role"], "error")

    def test_ask_mode_tool_call_is_refused_in_loop(self):
        fake = run_session("Delete the crate", [
            ([call("propose_scene_changes", title="t", rationale="r",
                   operations=[{"op": "delete_object", "object": "Crate"}])], "tool_use"),
            ([text("I can't change things in Ask mode.")], "end_turn"),
        ], mode="ask")
        result = fake.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("Crate", bpy.data.objects)

    # ---------------------------------------------------------- MCP bridge

    def test_bridge_answers_tool_calls(self):
        from gamestudio.assistant import bridge
        from gamestudio.core import link as link_mod

        sent = []
        fake_client = SimpleNamespace(send=sent.append, on=lambda t, h: None)
        original = link_mod.get_link
        link_mod.get_link = lambda: fake_client
        try:
            bridge._on_tool_list({"t": "tool.list", "id": "r1", "from": "p9"})
            self.assertEqual(sent[-1]["t"], "tool.list.result")
            self.assertEqual(sent[-1]["to"], "p9")
            names = {t["name"] for t in sent[-1]["tools"]}
            self.assertIn("propose_scene_changes", names)
            bridge._on_tool_call({"t": "tool.call", "id": "r2", "from": "p9", "name": "get_object",
                                  "input": {"name": "Crate"}})
            self.assertEqual(sent[-1]["re"], "r2")
            self.assertTrue(sent[-1]["ok"])
            bridge._on_tool_call({"t": "tool.call", "id": "r3", "from": "p9", "name": "propose_scene_changes",
                                  "input": {"title": "Move", "rationale": "r",
                                            "operations": [{"op": "set_transform", "object": "Crate",
                                                            "location": [1, 1, 1]}]}})
            self.assertTrue(sent[-1]["ok"], sent[-1])
            from gamestudio.assistant.proposals import STORE
            self.assertEqual(STORE.all()[-1].source, "mcp")
            self.assertEqual(tuple(bpy.data.objects["Crate"].location), (0, 0, 2))
        finally:
            link_mod.get_link = original


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AssistantTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
