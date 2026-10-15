# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tests for the assistant's OpenAI-compatible provider (assistant/providers.py).

A fake HTTP opener stands in for the server, so these tests need no network and no
API key: request conversion, the streamed reply, stop reasons, error messages, the
retry without OpenAI-only fields, the provider switch and the unknown cost.

Runs as part of blender/tests/addon/run_tests.py (-k assistant), or on its own:
  blender --background --factory-startup --python-exit-code 1 --python blender/tests/addon/test_assistant_providers.py
"""

import io
import json
import os
import pathlib
import sys
import threading
import time
import unittest
import urllib.error
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "blender"))

import bpy  # noqa: E402

import gamestudio  # noqa: E402

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
          "additionalProperties": False}


def sse(*chunks, comment=True) -> bytes:
    """A streamed Chat Completions reply: one data: line per chunk, then [DONE]."""
    lines = [b": keep-alive\n\n"] if comment else []
    lines += [b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks]
    return b"".join(lines) + b"data: [DONE]\n\n"


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def http_error(code, message, param=None):
    body = json.dumps({"error": {"message": message, "type": "invalid_request_error", "param": param}})
    return urllib.error.HTTPError("http://test/v1/chat/completions", code, "error", None, io.BytesIO(body.encode()))


class FakeOpener:
    """urlopen's signature. Each call takes the next reply: bytes to stream, or an exception to raise."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.responses = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        response = io.BytesIO(reply)
        self.responses.append(response)
        return response

    def body(self, n=0) -> dict:
        return json.loads(self.requests[n].data)


class FakeLLM:
    """Replays (content, stop_reason) steps for Session tests."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def stream(self, params, on_text, on_thinking, cancel):
        self.calls.append({**params, "messages": list(params["messages"])})  # the loop appends to the list
        content, stop = self.steps.pop(0)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


def params(**extra):
    base = {
        "model": "test-model",
        "max_tokens": 64_000,
        "system": [{"type": "text", "text": "You are the GameStudio assistant."}],
        "tools": [{"name": "get_object", "description": "One object.", "input_schema": SCHEMA,
                   "eager_input_streaming": True}],
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "high"},
        "cache_control": {"type": "ephemeral"},
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "<editor_context/>"},
                                                  {"type": "text", "text": "Where is the crate?"}]}],
    }
    base.update(extra)
    return base


def stream(opener, p=None, base_url="https://api.openai.com/v1", key="sk-test", cancel=None):
    from gamestudio.assistant import providers

    llm = providers.OpenAICompatibleLLM(base_url, key, opener=opener)
    texts, thoughts = [], []
    response = llm.stream(p or params(), texts.append, thoughts.append, cancel or threading.Event())
    return llm, response, texts, thoughts


class ProviderConversionTests(unittest.TestCase):
    def test_request_converts_system_tools_and_messages(self):
        from gamestudio.assistant import providers

        history = params()["messages"] + [
            {"role": "assistant", "content": [
                SimpleNamespace(type="thinking", thinking="hmm", signature="x"),
                SimpleNamespace(type="text", text="Let me look."),
                SimpleNamespace(type="tool_use", id="call_a", name="get_object", input={"name": "Crate"}),
                SimpleNamespace(type="tool_use", id="call_b", name="get_object", input={"name": "Nope"})]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_a", "content": '{"location": [0, 0, 2]}'},
                {"type": "tool_result", "tool_use_id": "call_b", "content": '{"error": "no such object"}',
                 "is_error": True}]},
            {"role": "assistant", "content": [SimpleNamespace(type="text", text="It is at z=2.")]},
            {"role": "user", "content": [{"type": "text", "text": "Thanks"}]},
        ]
        body = providers.chat_request(params(messages=history), openai=False)
        for dropped in ("thinking", "output_config", "cache_control", "betas", "fallbacks", "system",
                        "max_tokens", "max_completion_tokens", "reasoning_effort"):
            self.assertNotIn(dropped, body)
        self.assertTrue(body["stream"])
        self.assertEqual(body["tools"], [{"type": "function", "function": {
            "name": "get_object", "description": "One object.", "parameters": SCHEMA}}])
        msgs = body["messages"]
        self.assertEqual([m["role"] for m in msgs],
                         ["system", "user", "assistant", "tool", "tool", "assistant", "user"])
        self.assertEqual(msgs[0]["content"], "You are the GameStudio assistant.")
        self.assertEqual(msgs[1]["content"], "<editor_context/>\n\nWhere is the crate?")
        self.assertEqual(msgs[2]["content"], "Let me look.")
        calls = msgs[2]["tool_calls"]
        self.assertEqual([c["id"] for c in calls], ["call_a", "call_b"])
        self.assertEqual(calls[0]["type"], "function")
        self.assertEqual(calls[0]["function"]["name"], "get_object")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"name": "Crate"})
        self.assertEqual(msgs[3], {"role": "tool", "tool_call_id": "call_a", "content": '{"location": [0, 0, 2]}'})
        self.assertEqual(msgs[4]["tool_call_id"], "call_b")
        self.assertTrue(msgs[4]["content"].startswith("Error: "))
        self.assertEqual(msgs[5], {"role": "assistant", "content": "It is at z=2."})

    def test_openai_only_fields_go_to_openai_only(self):
        from gamestudio.assistant import providers

        body = providers.chat_request(params(), openai=True)
        self.assertEqual(body["max_completion_tokens"], 64_000)
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertTrue(providers.is_openai("https://api.openai.com/v1"))
        self.assertFalse(providers.is_openai("https://openrouter.ai/api/v1"))
        for local in ("http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://[::1]:8080/v1"):
            self.assertTrue(providers.is_local(local), local)
        self.assertFalse(providers.is_local("https://api.openai.com/v1"))

    def test_assistant_tool_calls_round_trip(self):
        """A streamed reply, stored in the history, converts back to the same tool calls."""
        from gamestudio.assistant import providers

        opener = FakeOpener(sse(
            delta(role="assistant", content="Checking."),
            delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "get_object", "arguments": '{"name": "Crate"}'}}]),
            finish("tool_calls")))
        _, response, _, _ = stream(opener)
        history = params()["messages"] + [{"role": "assistant", "content": response.content}]
        message = providers.chat_request(params(messages=history), openai=False)["messages"][-1]
        self.assertEqual(message["content"], "Checking.")
        self.assertEqual(message["tool_calls"], [{"id": "call_1", "type": "function", "function": {
            "name": "get_object", "arguments": json.dumps({"name": "Crate"})}}])


class ProviderStreamTests(unittest.TestCase):
    def test_streamed_text_tool_calls_and_usage(self):
        opener = FakeOpener(sse(
            delta(role="assistant", content=""),
            delta(reasoning_content="The user wants "),
            delta(content="Let me "),
            delta(content="look."),
            delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "get_object", "arguments": ""}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"na'}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": 'me": "Crate"}'}}]),
            delta(tool_calls=[{"index": 1, "id": "call_2", "type": "function",
                               "function": {"name": "get_selection", "arguments": ""}}]),
            finish("tool_calls"),
            {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 80,
                                      "prompt_tokens_details": {"cached_tokens": 1000}}}))
        llm, response, texts, thoughts = stream(opener)
        self.assertEqual(texts, ["Let me ", "look."])
        self.assertEqual(thoughts, ["The user wants "])
        self.assertEqual([b.type for b in response.content], ["text", "tool_use", "tool_use"])
        self.assertEqual(response.content[0].text, "Let me look.")
        self.assertEqual((response.content[1].id, response.content[1].name, response.content[1].input),
                         ("call_1", "get_object", {"name": "Crate"}))
        self.assertEqual((response.content[2].name, response.content[2].input), ("get_selection", {}))
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertEqual((response.usage.input_tokens, response.usage.output_tokens,
                          response.usage.cache_read_input_tokens), (200, 80, 1000))
        self.assertTrue(opener.responses[0].closed)

        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")
        self.assertEqual(opener.body()["model"], "test-model")

    def test_local_server_gets_no_key_and_no_openai_fields(self):
        opener = FakeOpener(sse(delta(content="Hi"), finish("stop")))
        _, response, _, _ = stream(opener, base_url="http://localhost:11434/v1/", key="")
        self.assertIsNone(opener.requests[0].get_header("Authorization"))
        self.assertEqual(opener.requests[0].full_url, "http://localhost:11434/v1/chat/completions")
        body = opener.body()
        self.assertNotIn("max_completion_tokens", body)
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertIsNone(response.usage)  # the server sent no usage: tokens are not counted

    def test_tool_calls_without_index(self):
        """Some servers leave out the index: a new id starts a new call."""
        opener = FakeOpener(sse(
            delta(tool_calls=[{"id": "a", "function": {"name": "get_object", "arguments": '{"name":'}}]),
            delta(tool_calls=[{"function": {"arguments": ' "Crate"}'}}]),
            delta(tool_calls=[{"id": "b", "function": {"name": "get_selection", "arguments": {}}}]),
            finish("stop")))
        _, response, _, _ = stream(opener, base_url="http://localhost:1234/v1")
        self.assertEqual([(b.id, b.input) for b in response.content], [("a", {"name": "Crate"}), ("b", {})])
        self.assertEqual(response.stop_reason, "tool_use")  # finished with "stop" but called tools

    def test_stop_reason_mapping(self):
        for reason, expected in (("stop", "end_turn"), ("tool_calls", "tool_use"), ("length", "max_tokens"),
                                 ("content_filter", "refusal"), (None, "end_turn")):
            chunks = [delta(content="x")] + ([finish(reason)] if reason else [])
            _, response, _, _ = stream(FakeOpener(sse(*chunks)))
            self.assertEqual(response.stop_reason, expected, reason)

    def test_truncated_tool_call_is_max_tokens_not_an_error(self):
        from gamestudio.assistant import providers

        opener = FakeOpener(sse(
            delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "get_object", "arguments": '{"na'}}]),
            finish("length")))
        _, response, _, _ = stream(opener)
        self.assertEqual(response.stop_reason, "max_tokens")
        self.assertEqual(response.content[0].input, {})
        # Not cut off, yet not JSON: a ValueError, so the loop asks again.
        opener = FakeOpener(sse(
            delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "get_object", "arguments": "{oops"}}]),
            finish("tool_calls")))
        with self.assertRaises(providers.BadToolInput):
            stream(opener)
        self.assertTrue(issubclass(providers.BadToolInput, ValueError))

    def test_cancel_closes_the_stream(self):
        from gamestudio.assistant import providers

        cancel = threading.Event()
        opener = FakeOpener(sse(delta(content="one"), delta(content="two"), finish("stop")))
        llm = providers.OpenAICompatibleLLM("https://api.openai.com/v1", "k", opener=opener)
        texts = []

        def on_text(t):
            texts.append(t)
            cancel.set()
        self.assertIsNone(llm.stream(params(), on_text, lambda t: None, cancel))
        self.assertEqual(texts, ["one"])
        self.assertTrue(opener.responses[0].closed)

    def test_retry_without_openai_only_fields_once(self):
        from gamestudio.assistant import providers

        opener = FakeOpener(http_error(400, "Unsupported parameter: 'reasoning_effort' is not supported with "
                                            "this model.", "reasoning_effort"),
                            sse(delta(content="ok"), finish("stop")))
        _, response, _, _ = stream(opener)
        self.assertEqual(response.content[0].text, "ok")
        self.assertIn("reasoning_effort", opener.body(0))
        retried = opener.body(1)
        for field in providers.OPTIONAL_FIELDS:
            self.assertNotIn(field, retried)
        # A 400 about something else is not retried.
        opener = FakeOpener(http_error(400, "messages: invalid role"))
        with self.assertRaises(providers.OpenAIError):
            stream(opener)
        self.assertEqual(len(opener.requests), 1)

    def test_error_descriptions(self):
        from gamestudio.assistant import providers

        cases = [
            (http_error(401, "Incorrect API key provided"), "OPENAI_API_KEY"),
            (http_error(403, "Project does not have access"), "not allowed to use the model 'test-model'"),
            (http_error(404, "The model `test-model` does not exist"), "base URL"),
            (http_error(429, "You exceeded your current quota"), "exceeded your current quota"),
            (http_error(400, "test-model does not support tools"), "function calling"),
            (http_error(503, "overloaded"), "API error 503: overloaded"),
        ]
        for error, expected in cases:
            opener = FakeOpener(error)
            llm = providers.OpenAICompatibleLLM("https://api.openai.com/v1", "k", opener=opener)
            with self.assertRaises(providers.OpenAIError) as caught:
                llm.stream(params(), lambda t: None, lambda t: None, threading.Event())
            self.assertIn(expected, llm.describe_error(caught.exception), error.code)

        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        llm = providers.OpenAICompatibleLLM("http://localhost:11434/v1", None, opener=FakeOpener(refused))
        with self.assertRaises(urllib.error.URLError) as caught:
            llm.stream(params(), lambda t: None, lambda t: None, threading.Event())
        message = llm.describe_error(caught.exception)
        self.assertIn("http://localhost:11434/v1", message)
        self.assertIn("server is running", message)

        mid_stream = FakeOpener(sse(delta(content="a"), {"error": {"code": 502, "message": "upstream died"}}))
        llm = providers.OpenAICompatibleLLM("https://openrouter.ai/api/v1", "k", opener=mid_stream)
        with self.assertRaises(providers.OpenAIError) as caught:
            llm.stream(params(), lambda t: None, lambda t: None, threading.Event())
        self.assertIn("upstream died", llm.describe_error(caught.exception))
        self.assertIsNone(llm.describe_error(KeyError("x")))


class ProviderSessionTests(unittest.TestCase):
    """The provider preference in the Session: a switch starts over, an unknown price shows no cost."""

    @classmethod
    def setUpClass(cls):
        cls.registered_here = not hasattr(bpy.types.WindowManager, "gs_ai")
        if cls.registered_here:
            gamestudio.register()

    @classmethod
    def tearDownClass(cls):
        if cls.registered_here:
            gamestudio.unregister()

    def setUp(self):
        from gamestudio import prefs
        from gamestudio.assistant import agent, providers

        self.prefs = prefs._defaults
        self.saved = (self.prefs.ai_provider, self.prefs.openai_base_url, self.prefs.openai_model,
                      os.environ.get("OPENAI_API_KEY"), providers.OpenAICompatibleLLM)
        self.session = agent.Session()

    def tearDown(self):
        from gamestudio.assistant import providers

        (self.prefs.ai_provider, self.prefs.openai_base_url, self.prefs.openai_model,
         key, providers.OpenAICompatibleLLM) = self.saved
        if key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = key

    def run_session(self, steps, prompt="Hello"):
        fake = FakeLLM(steps)
        self.session.llm_factory = lambda: fake
        self.session.submit(prompt, role="general", mode="ask", teach=False)
        self.wait()
        return fake

    def wait(self):
        from gamestudio.assistant import agent

        deadline = time.monotonic() + 20
        while self.session.busy:
            agent.pump()
            time.sleep(0.005)
            if time.monotonic() > deadline:
                raise AssertionError("agent did not finish")
        agent.pump()

    def test_switching_provider_starts_a_new_conversation(self):
        text = SimpleNamespace(type="text", text="Hi.")
        self.prefs.ai_provider = "ANTHROPIC"
        first = self.run_session([([text], "end_turn")])
        self.assertEqual(first.calls[0]["model"], "claude-opus-5-5")
        self.assertGreater(self.session.cost, 0)
        self.assertFalse(self.session.cost_unknown)
        self.run_session([([text], "end_turn")], "Again")  # same provider: the history grows
        self.assertEqual(len(self.session.messages), 4)

        self.prefs.ai_provider = "OPENAI"
        self.prefs.openai_model = "my-model"
        second = self.run_session([([text], "end_turn")], "And now?")
        self.assertEqual(second.calls[0]["model"], "my-model")
        self.assertEqual(len(second.calls[0]["messages"]), 1, "the Anthropic history was not sent")
        self.assertEqual(self.session.transcript[0]["role"], "system")
        self.assertIn("OpenAI-compatible", self.session.transcript[0]["text"])
        # No known price: tokens are counted, the cost is not.
        self.assertEqual(self.session.cost, 0)
        self.assertTrue(self.session.cost_unknown)
        self.assertEqual(self.session.tokens["input"], 3000)

    def test_unknown_price_counts_tokens_but_no_cost(self):
        from gamestudio.assistant import agent, providers

        self.assertIsNotNone(agent.price_for(providers.ANTHROPIC, "claude-opus-5-5"))
        self.assertIsNone(agent.price_for(providers.ANTHROPIC, "claude-unknown-9"))
        self.assertIsNone(agent.price_for(providers.OPENAI, "claude-opus-5-5"))
        usage = SimpleNamespace(input_tokens=500, output_tokens=50, cache_read_input_tokens=100,
                                cache_creation_input_tokens=0)
        self.session._account("gpt-x", usage, None)
        self.assertEqual(self.session.cost, 0)
        self.assertTrue(self.session.cost_unknown)
        self.assertEqual((self.session.tokens["input"], self.session.tokens["cache_read"]), (500, 100))
        self.session.new_conversation()
        self.assertFalse(self.session.cost_unknown)

    def test_ui_hints_follow_the_provider(self):
        from gamestudio.assistant import agent, ui

        os.environ.pop("OPENAI_API_KEY", None)
        self.prefs.ai_provider = "OPENAI"
        self.prefs.openai_base_url = "https://openrouter.ai/api/v1"
        self.assertIn("OPENAI_API_KEY", ui._key_hint(self.prefs))
        self.assertFalse(ui._needs_sdk(self.prefs))
        self.assertTrue(ui.ready())
        saved, agent.SESSION.cost_unknown = agent.SESSION.cost_unknown, False
        try:
            self.assertEqual(ui._cost_text(self.prefs), "cost unknown")
        finally:
            agent.SESSION.cost_unknown = saved
        self.prefs.openai_base_url = "http://localhost:11434/v1"
        self.assertEqual(ui._key_hint(self.prefs), "")  # a local server needs no key

    def test_local_server_works_offline_with_the_key_from_the_environment(self):
        from gamestudio.assistant import agent, providers

        made = []
        fake = FakeLLM([([SimpleNamespace(type="text", text="Hi.")], "end_turn")])

        def fake_client(base_url, api_key):
            made.append((base_url, api_key))
            return fake
        providers.OpenAICompatibleLLM = fake_client
        os.environ["OPENAI_API_KEY"] = "sk-env"
        self.prefs.ai_provider = "OPENAI"
        self.prefs.openai_base_url = "http://127.0.0.1:1234/v1/"
        self.session.submit("Hello", role="general", mode="ask", teach=False)
        self.wait()
        self.assertEqual(made, [("http://127.0.0.1:1234/v1", "sk-env")])
        if not bpy.app.online_access:  # a remote server needs online access
            self.prefs.openai_base_url = "https://api.openai.com/v1"
            with self.assertRaisesRegex(agent.AssistantError, "Online access is off"):
                self.session.submit("Hello", role="general", mode="ask", teach=False)


def main():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in
                               (ProviderConversionTests, ProviderStreamTests, ProviderSessionTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
