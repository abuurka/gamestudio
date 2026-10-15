# SPDX-License-Identifier: GPL-3.0-or-later
"""The conversation session and agent loop.

Threading model (research/04 section 9):

* A worker thread runs the model loop. It does only network I/O and never
  touches bpy.
* Each tool call is posted to MAIN_QUEUE and run on Blender's main thread by
  pump(), which a bpy.app.timers callback drives. Headless tests call pump()
  themselves, because timers never fire in --background mode.
* All session state shown in the UI (transcript, status, cost) is changed on
  the main thread only.

The loop is a manual tool-use loop rather than the SDK tool runner. Write tools
only stage proposals, so the loop never blocks on the developer: it ends its
turn, and the developer's decisions are reported at the start of the next turn.
"""

import json
import queue
import threading
import time
import traceback

import bpy

from ..core import project
from ..prefs import get_prefs
from . import audit, deps, prompts, proposals, tools

# USD per million tokens: input, output, cache read, cache write.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
}
# Server-side refusal fallbacks ("default" routing). Not offered for Haiku 5.5.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64_000
TOOL_TIMEOUT = 120.0
DEFERRED_TOOL_TIMEOUT = 15.0

MAIN_QUEUE: "queue.Queue[callable]" = queue.Queue()


class AssistantError(Exception):
    pass


# ----------------------------------------------------------------- LLM client


class AnthropicLLM:
    """Thin wrapper over the Anthropic SDK's streaming beta Messages API."""

    def __init__(self, api_key: str | None):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def stream(self, params: dict, on_text, on_thinking, cancel: threading.Event):
        with self.client.beta.messages.stream(**params) as stream:
            for event in stream:
                if cancel.is_set():
                    return None
                if event.type == "text":
                    on_text(event.text)
                elif event.type == "thinking":
                    on_thinking(event.thinking)
            return stream.get_final_message()

    def describe_error(self, exc: Exception) -> str | None:
        a = self._anthropic
        if isinstance(exc, a.AuthenticationError):
            return "The API key was rejected. Check ANTHROPIC_API_KEY or the key in add-on preferences."
        if isinstance(exc, a.PermissionDeniedError):
            return "This API key is not allowed to use the selected model."
        if isinstance(exc, a.NotFoundError):
            return "Model not found. Pick another model in add-on preferences."
        if isinstance(exc, a.RateLimitError):
            return "Rate limited by the API. Wait a moment and try again."
        if isinstance(exc, a.BadRequestError):
            return f"The API rejected the request: {exc.message}"
        if isinstance(exc, a.APIStatusError):
            return f"API error {exc.status_code}: {exc.message}"
        if isinstance(exc, a.APIConnectionError):
            return "Could not reach the Claude API. Check your internet connection."
        return None


# -------------------------------------------------------------------- session


class Session:
    def __init__(self):
        self.messages: list = []  # API history; only the worker writes while busy
        self.transcript: list[dict] = []  # {"role", "text"}; main thread only
        self.role = "general"
        self.status = "Idle"
        self.busy = False
        self.live_text = ""
        self.cost = 0.0
        self.total_cost = 0.0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.project_root = None
        self.llm_factory = None  # tests inject a fake model here
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    # ----- main-thread API --------------------------------------------------

    def new_conversation(self, note: str = "") -> None:
        if self.busy:
            raise AssistantError("Wait for the current request to finish, or press Stop.")
        self.messages.clear()
        self.transcript.clear()
        self.cost = 0.0
        if note:
            self.transcript.append({"role": "system", "text": note})

    def cancel(self) -> None:
        if self.busy:
            self._cancel.set()
            self.status = "Stopping..."

    def submit(self, prompt: str, *, role: str, mode: str, teach: bool) -> None:
        prompt = prompt.strip()
        if not prompt:
            raise AssistantError("Type a request first.")
        if self.busy:
            raise AssistantError("The assistant is still working on the previous request.")
        prefs = get_prefs()
        if not prefs.assistant_enabled:
            raise AssistantError("The assistant is disabled. Enable it in the add-on preferences.")

        if role != self.role and self.messages:
            self.new_conversation(f"Started a new conversation for the {role} role.")
        self.role = role
        self.project_root = project.project_root()
        proposals.set_project_root(self.project_root)

        factory = self.llm_factory
        if factory is None:
            if not bpy.app.online_access:
                raise AssistantError("Online access is off. Enable it in Preferences > System.")
            if not deps.sdk_available():
                raise AssistantError("The Anthropic SDK is not installed. Click 'Install AI support'.")
            api_key = _api_key(prefs)
            factory = lambda: AnthropicLLM(api_key)  # noqa: E731

        tool_list = tools.tools_for(role)
        allowed = tools.allowed_names(role, mode)
        request = {
            "model": prefs.model,
            "effort": prefs.effort,
            "max_turns": int(prefs.max_turns),
            "system": prompts.build_system(role, teach, self.project_root),
            "tools": [t.api_definition() for t in tool_list],
            "allowed": allowed,
            "factory": factory,
        }
        updates = proposals.STORE.take_updates()
        content = [{"type": "text", "text": prompts.editor_context(mode, teach, updates)},
                   {"type": "text", "text": prompt}]
        self.messages.append({"role": "user", "content": content})
        self.transcript.append({"role": "user", "text": prompt})
        audit.log(self.project_root, "user_message", role=role, mode=mode, teach=teach,
                  model=prefs.model, text=prompt, proposal_updates=updates or None)

        self.busy = True
        self.status = "Thinking..."
        self.live_text = ""
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, args=(request,), daemon=True,
                                        name="gamestudio-assistant")
        self._thread.start()

    # ----- worker thread ----------------------------------------------------

    def _run(self, req: dict) -> None:
        try:
            llm = req["factory"]()
        except Exception as exc:
            self._post(self._finish_error, f"Could not create the API client: {exc}")
            return
        params = {
            "model": req["model"],
            "max_tokens": MAX_TOKENS,
            "system": req["system"],
            "tools": req["tools"],
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": req["effort"]},
            "cache_control": {"type": "ephemeral"},
        }
        if req["model"] in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        json_retries = 0
        turn = 0
        try:
            while turn < req["max_turns"]:
                if self._cancel.is_set():
                    self._post(self._note, "system", "Stopped.")
                    break
                self._post(self._set_status, "Thinking...")
                try:
                    response = llm.stream({**params, "messages": self.messages},
                                          self._on_text, self._on_thinking, self._cancel)
                except ValueError:
                    # The SDK could not parse a streamed tool input at all; re-issue the turn.
                    json_retries += 1
                    if json_retries > 2:
                        raise
                    continue
                json_retries = 0
                if response is None:  # cancelled mid-stream; nothing was appended
                    self._post(self._note, "system", "Stopped.")
                    break
                turn += 1
                self._post(self._account, req["model"], response.usage)
                self.messages.append({"role": "assistant", "content": response.content})

                text = "".join(b.text for b in response.content if b.type == "text").strip()
                for block in response.content:
                    if block.type == "fallback":
                        self._post(self._note, "system", "A fallback model answered this request.")
                if text:
                    self._post(self._note, "assistant", text)

                if response.stop_reason == "refusal":
                    category = getattr(getattr(response, "stop_details", None), "category", None)
                    self._post(self._note, "error", "The model declined this request"
                               + (f" ({category})." if category else "."))
                    self._close_open_tool_uses(response, "Not run: the response was declined.")
                    break
                if response.stop_reason == "pause_turn":
                    continue

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    break
                if response.stop_reason == "max_tokens":
                    self._post(self._note, "error", "The response was cut off mid tool call; try a smaller request.")
                    self._close_open_tool_uses(response, "Not run: the tool input was truncated.")
                    break

                results = []
                for block in tool_uses:
                    if self._cancel.is_set():
                        results.append(_tool_result(block.id, False, "Not run: the developer pressed Stop."))
                        continue
                    ok, content = self._execute_tool(block, req["allowed"])
                    results.append(_tool_result(block.id, ok, content))
                self.messages.append({"role": "user", "content": results})
            else:
                self._post(self._note, "system",
                           f"Stopped after {req['max_turns']} model turns (the limit in preferences).")
        except Exception as exc:  # network or API failure
            message = None
            describe = getattr(llm, "describe_error", None)
            if describe is not None:
                message = describe(exc)
            if message is None:
                message = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            self._post(self._finish_error, message)
            return
        self._post(self._finish)

    def _close_open_tool_uses(self, response, why: str) -> None:
        """Every tool_use needs a tool_result, or the next request is rejected."""
        pending = [b for b in response.content if b.type == "tool_use"]
        if pending:
            self.messages.append({"role": "user",
                                  "content": [_tool_result(b.id, False, why) for b in pending]})

    def _execute_tool(self, block, allowed: set[str]) -> tuple[bool, str]:
        name, args = block.name, block.input
        t = tools.TOOLS.get(name)
        self._post(self._set_status, f"Running {name}...")
        self._post(self._note, "tool", _describe_call(name, args))
        started = time.monotonic()
        reply: queue.Queue = queue.Queue(maxsize=1)

        def done(ok, content):
            if reply.empty():
                reply.put((ok, content))

        def job():
            ctx = tools.ToolContext(source="assistant", project_root=self.project_root)
            tools.invoke(name, args, ctx, done, allowed=allowed)

        if t is not None and not t.main_thread:
            job()
        else:
            self._post(job)
        timeout = DEFERRED_TOOL_TIMEOUT if (t and t.deferred) else TOOL_TIMEOUT
        deadline = started + timeout
        while True:
            try:
                ok, content = reply.get(timeout=0.1)
                break
            except queue.Empty:
                if self._cancel.is_set():
                    ok, content = False, "Not finished: the developer pressed Stop."
                    break
                if time.monotonic() > deadline:
                    ok, content = False, f"The tool did not finish within {timeout:.0f} s."
                    break
        audit.log(self.project_root, "tool_call", tool=name, input=args, ok=ok,
                  ms=round((time.monotonic() - started) * 1000), result=content)
        if ok and t is not None and t.tier == tools.PROPOSE:
            try:
                info = json.loads(content)
                self._post(self._note, "proposal", f"Proposed {info['proposal_id']}: {info['title']} "
                                                   f"[{info['risk']} risk]")
            except (ValueError, KeyError):
                pass
        elif not ok:
            self._post(self._note, "tool", f"  failed: {_first_line(content)}")
        return ok, content

    def _on_text(self, delta: str) -> None:
        self.live_text += delta  # read-only from the UI; a str swap is atomic under the GIL

    def _on_thinking(self, delta: str) -> None:
        pass  # summarized thinking is not shown live; the status line says "Thinking..."

    def _post(self, fn, *args) -> None:
        MAIN_QUEUE.put(lambda: fn(*args))

    # ----- main-thread callbacks posted by the worker ----------------------

    def _set_status(self, text: str) -> None:
        self.status = text

    def _note(self, role: str, text: str) -> None:
        if role == "assistant":
            self.live_text = ""
        self.transcript.append({"role": role, "text": text})

    def _account(self, model: str, usage) -> None:
        if usage is None:
            return
        fields = {
            "input": getattr(usage, "input_tokens", 0) or 0,
            "output": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        for key, value in fields.items():
            self.tokens[key] += value
        p_in, p_out, p_read, p_write = PRICES.get(model, PRICES["claude-opus-5-5"])
        cost = (fields["input"] * p_in + fields["output"] * p_out
                + fields["cache_read"] * p_read + fields["cache_write"] * p_write) / 1_000_000
        self.cost += cost
        self.total_cost += cost
        audit.log(self.project_root, "model_response", model=model, usage=fields, cost_usd=round(cost, 5))

    def _finish(self) -> None:
        self.busy = False
        self.live_text = ""
        self.status = "Idle"

    def _finish_error(self, message: str) -> None:
        self.transcript.append({"role": "error", "text": message})
        audit.log(self.project_root, "error", message=message)
        self._finish()


def _tool_result(tool_use_id: str, ok: bool, content: str) -> dict:
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if not ok:
        result["is_error"] = True
    return result


def _first_line(text: str) -> str:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "error" in data:
            text = str(data["error"])
    except ValueError:
        pass
    return text.splitlines()[0][:160] if text else ""


def _describe_call(name: str, args) -> str:
    if not isinstance(args, dict):
        return f"-> {name}"
    brief = ", ".join(f"{k}={v!r}"[:60] for k, v in args.items()
                      if k not in ("operations", "edits", "content", "rationale"))
    return f"-> {name}({brief})"


def _api_key(prefs) -> str | None:
    import os

    if os.environ.get("ANTHROPIC_API_KEY"):
        return None  # the SDK reads the environment itself
    return prefs.api_key or None


SESSION = Session()


def pump() -> None:
    """Run work posted by the worker thread. Must be called on the main thread."""
    ran = False
    for _ in range(64):  # bound the work per tick so the UI stays responsive
        try:
            job = MAIN_QUEUE.get_nowait()
        except queue.Empty:
            break
        ran = True
        try:
            job()
        except Exception:
            traceback.print_exc()
    if ran or SESSION.busy:
        tools._notify_ui()


def _timer() -> float:
    from . import bridge

    bridge.ensure_handlers()
    pump()
    return 0.05


def register():
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.1, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    SESSION.cancel()
