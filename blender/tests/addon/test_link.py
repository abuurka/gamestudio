# SPDX-License-Identifier: GPL-3.0-or-later
"""Live link client (§5) against a fake hub, pumped manually (timers never fire headless)."""

import threading
import time
import unittest

import bpy

from gamestudio.core import link, registry

import helpers


class LinkClientTest(unittest.TestCase):
    def setUp(self):
        self.hub = helpers.FakeHub()
        self.client = link.LinkClient(session_provider=self.hub.session)
        self.client.reconnect_interval = 0.1

    def tearDown(self):
        self.client.auto_connect = False
        self.client.disconnect("")
        self.hub.close()

    def pump_until(self, predicate, timeout=3.0):
        return helpers.pump_until(predicate, [self.client.pump], timeout)

    def connect(self):
        self.assertTrue(self.pump_until(lambda: self.client.connected), self.client.last_error)

    def test_hello_and_welcome(self):
        events = []
        self.client.on("link.connected", events.append)
        self.connect()
        hello = self.hub.messages("hello")[0]
        self.assertEqual(hello["role"], "editor")
        self.assertEqual(hello["token"], helpers.TOKEN)
        self.assertEqual(hello["protocol"], 1)
        self.assertTrue(hello["client"].startswith("blender 5."))
        self.assertEqual(self.client.peer_id, "e1")
        self.assertEqual(self.client.game_peers(), [{"peer": "g1", "role": "game"}])
        self.assertEqual(len(events), 1)
        self.assertEqual(self.client.status_text, "Connected, 1 game")

    def test_bad_token_is_rejected(self):
        self.client.session_provider = lambda: dict(self.hub.session(), token="f" * 32)
        self.assertTrue(self.pump_until(lambda: "auth" in self.client.last_error))
        self.assertFalse(self.client.connected)
        self.assertEqual(self.client.state, "idle")

    def test_no_session_stays_idle(self):
        self.client.session_provider = lambda: None
        for _ in range(5):
            self.client.pump()
        self.assertEqual(self.client.state, "idle")
        self.assertEqual(self.hub.hellos, 0)

    def test_send_entity_transform(self):
        self.connect()
        msg = {"t": "entity.transform", "items": [{"id": "gs_000000000001", "p": [1, 2, 3],
                                                    "r": [0, 0, 0, 1], "s": [1, 1, 1]}]}
        self.assertTrue(self.client.send(msg))
        self.assertTrue(self.pump_until(lambda: self.hub.messages("entity.transform")))
        self.assertEqual(self.hub.messages("entity.transform")[0], msg)

    def test_send_when_disconnected_returns_false(self):
        self.assertFalse(self.client.send({"t": "play.start"}))

    def test_send_from_another_thread(self):
        self.connect()
        thread = threading.Thread(target=lambda: self.client.send({"t": "select", "ids": []}))
        thread.start()
        thread.join()
        self.assertTrue(self.pump_until(lambda: self.hub.messages("select")))

    def test_request_response_matches_re(self):
        self.hub.responders["state.request"] = lambda m: {
            "t": "state.response", "re": m["id"], "entities": [{"id": "gs_000000000001"}]}
        self.connect()
        seen_by_handler = []
        self.client.on("state.response", seen_by_handler.append)
        # An unrelated response must not be delivered to our callback.
        self.hub.broadcast({"t": "state.response", "re": "someone-else", "entities": []})
        got = []
        req_id = self.client.request({"t": "state.request", "filter": {"limit": 5}}, got.append)
        self.assertTrue(self.pump_until(lambda: got))
        self.assertEqual(got[0]["re"], req_id)
        self.assertEqual(got[0]["entities"], [{"id": "gs_000000000001"}])
        self.assertEqual(self.hub.messages("state.request")[0]["id"], req_id)
        self.assertTrue(all(m["re"] == "someone-else" for m in seen_by_handler))

    def test_request_timeout(self):
        self.connect()
        got = []
        start = time.monotonic()
        self.client.request({"t": "state.request", "filter": {}}, got.append, timeout=0.2)
        self.client.pump()
        self.assertEqual(got, [])
        self.assertTrue(self.pump_until(lambda: got, timeout=2.0))
        self.assertIsNone(got[0])
        self.assertGreaterEqual(time.monotonic() - start, 0.2)

    def test_request_while_disconnected_calls_back_none(self):
        self.client.session_provider = lambda: None
        got = []
        self.client.request({"t": "state.request"}, got.append)
        self.client.pump()
        self.assertEqual(got, [None])

    def test_partial_lines_and_batched_lines(self):
        self.connect()
        self.hub.broadcast_raw(b'{"t":"log","level":"info","msg":"hel')
        time.sleep(0.05)
        for _ in range(5):
            self.client.pump()
        self.assertEqual(len(self.client.logs), 0)
        self.hub.broadcast_raw(b'lo"}\n{"t":"log","level":"warn","msg":"two"}\n\n{"t":"log","msg":"three"}\n')
        self.assertTrue(self.pump_until(lambda: len(self.client.logs) == 3))
        self.assertEqual([e["msg"] for e in self.client.logs], ["hello", "two", "three"])
        self.assertEqual([e["level"] for e in self.client.logs], ["info", "warn", "info"])

    def test_malformed_and_unknown_messages_are_ignored(self):
        self.connect()
        calls = []

        def broken(_msg):
            raise RuntimeError("handler bug")

        self.client.on("custom.thing", broken)
        self.client.on("custom.thing", calls.append)
        self.hub.broadcast_raw(b'not json\n{"no_t": 1}\n{"t":"brand.new.type"}\n{"t":"custom.thing","x":1}\n')
        self.assertTrue(self.pump_until(lambda: calls))
        self.assertEqual(calls[0]["x"], 1)
        self.assertTrue(self.client.connected)

    def test_logs_errors_and_peers(self):
        self.connect()
        self.hub.broadcast({"t": "error", "msg": "Boom", "file": "src/systems/spin.ts", "line": 12, "col": 3,
                            "from": "g1"})
        self.hub.broadcast({"t": "peers", "peers": [{"peer": "g1", "role": "game"}, {"peer": "g2", "role": "game"},
                                                    {"peer": "t1", "role": "tool"}]})
        self.assertTrue(self.pump_until(lambda: self.client.logs and len(self.client.game_peers()) == 2))
        entry = self.client.logs[-1]
        self.assertEqual((entry["t"], entry["level"], entry["line"]), ("error", "error", 12))
        self.assertTrue(self.client.connected)  # a game error is not a hub error
        self.assertEqual(self.client.logs.maxlen, 500)

    def test_game_status_and_ping(self):
        self.connect()
        self.hub.broadcast({"t": "game.status", "state": "paused", "from": "g1"})
        self.hub.broadcast({"t": "ping"})
        self.assertTrue(self.pump_until(lambda: self.client.game_state == "paused" and self.hub.messages("pong")))

    def test_reconnects_after_the_hub_drops_the_connection(self):
        self.connect()
        dropped = []
        self.client.on("link.disconnected", dropped.append)
        self.hub.drop_connections()
        self.assertTrue(self.pump_until(lambda: dropped))
        self.assertFalse(self.client.connected)
        self.assertTrue(self.pump_until(lambda: self.client.connected))
        self.assertEqual(self.hub.hellos, 2)

    def test_refused_connection_retries_quietly(self):
        port = self.hub.port
        self.hub.close()
        self.client.session_provider = lambda: {"host": "127.0.0.1", "port": port, "token": helpers.TOKEN}
        self.assertTrue(self.pump_until(lambda: self.client.last_error, timeout=5.0))
        self.assertFalse(self.client.connected)


class LinkRegistryTest(helpers.ProjectTestCase):
    def tearDown(self):
        helpers.restore_global_link()
        self.hub.close()
        super().tearDown()

    def test_registry_message_regenerates_property_groups(self):
        doc = helpers.modified_registry(lambda d: d["components"].append({
            "name": "Magnet", "category": "Demo",
            "fields": [{"name": "strength", "type": "float", "default": 3}, {"name": "pulls", "type": "entity"}]}))
        self.hub = helpers.FakeHub(registry_doc=doc)
        self.assertNotIn("Magnet", registry.component_names())
        client = helpers.connect_global_link(self.hub)
        self.assertTrue(helpers.pump_until(lambda: "Magnet" in registry.component_names(), [client.pump]))
        self.assertEqual(registry.source_info()["origin"], "link")
        self.assertIn("gsc_Magnet", bpy.types.Object.bl_rna.properties)
        obj = helpers.new_object("Thing")
        self.assertAlmostEqual(obj.gsc_Magnet.strength, 3.0)

        # A later registry message (after a component file changed) updates again.
        self.hub.broadcast({"t": "registry", "registry": helpers.fixture_registry()})
        self.assertTrue(helpers.pump_until(lambda: "Magnet" not in registry.component_names(), [client.pump]))
