# SPDX-License-Identifier: GPL-3.0-or-later
"""Model providers other than Anthropic: OpenAI, and any server with an OpenAI-compatible
Chat Completions API (OpenRouter, Ollama, LM Studio, ...).

The agent loop (agent.py) works on Anthropic-shaped data: a response has content blocks
of type "text" and "tool_use", a stop_reason and usage, and tool results go back as
"tool_result" blocks in a user message. OpenAICompatibleLLM has the same interface as
AnthropicLLM. It turns the Anthropic-shaped request into a Chat Completions request, and
the streamed reply back into an Anthropic-shaped response, so the loop does not need to
know which provider answered.

Only the standard library is used (urllib, and the server-sent events the API streams),
so this provider needs nothing installed. Like AnthropicLLM it runs on the worker thread
and never touches bpy.
"""

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from types import SimpleNamespace

# The values of the ai_provider preference.
ANTHROPIC = "ANTHROPIC"
OPENAI = "OPENAI"
LABELS = {ANTHROPIC: "Anthropic (Claude)", OPENAI: "OpenAI-compatible"}

OPENAI_BASE_URL = "https://api.openai.com/v1"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT = 600.0  # seconds without a byte from the server; a reasoning model can think for minutes

# Chat Completions finish_reason -> the Anthropic stop_reason the loop understands.
STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}
# Request fields that not every server takes. max_completion_tokens and reasoning_effort
# are sent only to OpenAI's own API; stream_options (for token counts) to every server.
# A 400 that names one of these is retried once without all of them.
OPTIONAL_FIELDS = ("stream_options", "max_completion_tokens", "reasoning_effort")
_OPTIONAL_WORDS = ("stream_options", "include_usage", "max_completion_tokens", "max_tokens", "reasoning")


class OpenAIError(Exception):
    """An error the server reported: an HTTP status, or an error event in the stream."""

    def __init__(self, status: int | None, message: str):
        super().__init__(f"{status}: {message}" if status else message)
        self.status = status
        self.message = message


class BadToolInput(ValueError):
    """The model's tool arguments are not JSON. A ValueError, so the loop re-issues the turn."""


# ------------------------------------------------------------------ settings


def provider(prefs) -> str:
    return getattr(prefs, "ai_provider", ANTHROPIC) or ANTHROPIC


def base_url(prefs) -> str:
    """The OpenAI-compatible base URL, without a trailing slash or a pasted /chat/completions."""
    url = (getattr(prefs, "openai_base_url", "") or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    return url


def model(prefs) -> str:
    """The model id the chosen provider is asked for."""
    if provider(prefs) == OPENAI:
        return (getattr(prefs, "openai_model", "") or "").strip()
    return prefs.model


def openai_api_key(prefs) -> str:
    return os.environ.get("OPENAI_API_KEY") or getattr(prefs, "openai_api_key", "") or ""


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_local(url: str) -> bool:
    """True for a server on this computer (Ollama, LM Studio, ...): no online access or key needed."""
    return _host(url) in LOCAL_HOSTS


def is_openai(url: str) -> bool:
    """True for OpenAI's own API, the only server sent the OpenAI-only request fields."""
    return _host(url) == "api.openai.com"


# ---------------------------------------------------------------- the request


def _get(block, name: str, default=None):
    """A field of a content block: a dict we built, or a response object from a model."""
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _system_text(system) -> str:
    if isinstance(system, str):
        return system
    return "\n\n".join(_get(b, "text") or "" for b in system or [] if _get(b, "type") == "text")


def _result_text(block) -> str:
    content = _get(block, "content", "")
    if not isinstance(content, str):  # a list of content blocks
        content = "\n".join(_get(b, "text") or "" for b in content or [] if _get(b, "type") == "text")
    if _get(block, "is_error"):
        content = "Error: " + content
    return content


def _assistant_message(content) -> dict:
    texts, calls = [], []
    for block in content:
        kind = _get(block, "type")
        if kind == "text":
            texts.append(_get(block, "text") or "")
        elif kind == "tool_use":
            calls.append({"id": _get(block, "id"), "type": "function",
                          "function": {"name": _get(block, "name"),
                                       "arguments": json.dumps(_get(block, "input") or {})}})
        # Thinking and other Anthropic-only blocks have no Chat Completions form.
    message = {"role": "assistant", "content": "".join(texts) or None}
    if calls:
        message["tool_calls"] = calls
    elif message["content"] is None:
        message["content"] = ""
    return message


def chat_messages(system, messages: list) -> list[dict]:
    """Anthropic-shaped system blocks and messages as Chat Completions messages."""
    out = []
    text = _system_text(system)
    if text:
        out.append({"role": "system", "content": text})
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
        elif role == "assistant":
            out.append(_assistant_message(content))
        else:
            texts = []
            for block in content:
                kind = _get(block, "type")
                if kind == "tool_result":  # each result is a "tool" message, in call order
                    out.append({"role": "tool", "tool_call_id": _get(block, "tool_use_id"),
                                "content": _result_text(block)})
                elif kind == "text":
                    texts.append(_get(block, "text") or "")
            if texts:  # one string: local servers often take no list of content parts
                out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def chat_tools(tools: list) -> list[dict]:
    """Anthropic tool definitions (name, description, input_schema) as Chat Completions functions."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools]


def chat_request(params: dict, *, openai: bool) -> dict:
    """The Chat Completions request body for the loop's Anthropic-shaped params.

    thinking, output_config, cache_control, betas and fallbacks are Anthropic-only and
    dropped; with openai (OpenAI's own API), max_tokens and the effort are sent as
    max_completion_tokens and reasoning_effort.
    """
    body = {"model": params["model"],
            "messages": chat_messages(params.get("system"), params.get("messages", [])),
            "stream": True,
            "stream_options": {"include_usage": True}}
    if params.get("tools"):
        body["tools"] = chat_tools(params["tools"])
    if openai:
        if params.get("max_tokens"):
            body["max_completion_tokens"] = params["max_tokens"]
        effort = (params.get("output_config") or {}).get("effort")
        if effort:
            body["reasoning_effort"] = effort
    return body


# ------------------------------------------------------------------ the reply


def _arguments(raw: str, truncated: bool):
    raw = raw.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        if truncated:  # cut off by the token limit: the loop reports it and runs nothing
            return {}
        raise BadToolInput(f"The model's tool input is not valid JSON: {raw[:200]}") from None


def _usage(usage):
    if not usage:
        return None
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    # prompt_tokens includes the cached ones; Anthropic's input_tokens does not.
    return SimpleNamespace(input_tokens=max(0, prompt - cached),
                           output_tokens=int(usage.get("completion_tokens") or 0),
                           cache_read_input_tokens=cached, cache_creation_input_tokens=0)


class _Reply:
    """Gathers a streamed Chat Completions reply into an Anthropic-shaped response."""

    def __init__(self):
        self.text: list[str] = []
        self.refusal: list[str] = []
        self.calls: dict[int, dict] = {}  # index -> {"id", "name", "arguments"}
        self.finish = None
        self.usage = None

    def _call(self, delta: dict) -> dict:
        index = delta.get("index")
        if not isinstance(index, int):  # some servers leave index out: a new id starts a new call
            known = [c["id"] for c in self.calls.values()]
            new = not self.calls or (delta.get("id") and delta["id"] not in known)
            index = len(self.calls) if new else max(self.calls)
        return self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})

    def add(self, chunk: dict, on_text, on_thinking) -> None:
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str) and text:
                self.text.append(text)
                on_text(text)
            refusal = delta.get("refusal")
            if isinstance(refusal, str) and refusal:
                self.refusal.append(refusal)
            thinking = delta.get("reasoning_content") or delta.get("reasoning")  # DeepSeek, OpenRouter, ...
            if isinstance(thinking, str) and thinking:
                on_thinking(thinking)
            for part in delta.get("tool_calls") or []:
                call = self._call(part)
                if part.get("id"):
                    call["id"] = part["id"]
                function = part.get("function") or {}
                name = function.get("name")
                if name and name != call["name"]:  # a name sent whole, or (rarely) in pieces
                    call["name"] += name
                arguments = function.get("arguments")
                if isinstance(arguments, dict):  # a few servers send the whole object
                    call["arguments"] = json.dumps(arguments)
                elif arguments:
                    call["arguments"] += arguments
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]

    def response(self):
        content = []
        text = "".join(self.text)
        if self.refusal:
            text = (text + "\n" if text else "") + "".join(self.refusal)
        if text:
            content.append(SimpleNamespace(type="text", text=text))
        truncated = self.finish == "length"
        for index in sorted(self.calls):
            call = self.calls[index]
            content.append(SimpleNamespace(type="tool_use", id=call["id"] or f"call_{uuid.uuid4().hex[:24]}",
                                           name=call["name"], input=_arguments(call["arguments"], truncated)))
        stop = STOP_REASONS.get(self.finish, "end_turn")
        if self.refusal:
            stop = "refusal"
        elif self.calls and stop == "end_turn":  # some servers finish a tool call with "stop"
            stop = "tool_use"
        return SimpleNamespace(content=content, stop_reason=stop, stop_details=None, usage=_usage(self.usage))


def _error_text(error) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("error") or json.dumps(error)
        return str(message)
    return str(error)


def _http_error(exc: urllib.error.HTTPError) -> OpenAIError:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    message = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            error = data.get("error", data)
            message = _error_text(error)
            param = error.get("param") if isinstance(error, dict) else None
            if param and param not in message:
                message += f" (parameter: {param})"
    except ValueError:
        pass
    return OpenAIError(exc.code, message[:500] or exc.reason or "no details")


# ------------------------------------------------------------------- the client


class OpenAICompatibleLLM:
    """A Chat Completions client with AnthropicLLM's interface. opener is urlopen's
    signature; tests pass a fake one, so they never touch the network."""

    def __init__(self, base_url: str, api_key: str | None, opener=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.model = ""
        self._opener = opener or urllib.request.urlopen

    def _open(self, body: dict):
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:  # a local server needs none
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", method="POST",
                                         data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            return self._opener(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from None

    def stream(self, params: dict, on_text, on_thinking, cancel):
        self.model = params["model"]
        body = chat_request(params, openai=is_openai(self.base_url))
        try:
            response = self._open(body)
        except OpenAIError as exc:
            plain = {k: v for k, v in body.items() if k not in OPTIONAL_FIELDS}
            named = (exc.message or "").lower()
            if exc.status != 400 or plain == body or not any(w in named for w in _OPTIONAL_WORDS):
                raise
            response = self._open(plain)  # once, without the fields this server rejected
        try:
            return self._read(response, on_text, on_thinking, cancel)
        finally:
            response.close()

    def _read(self, response, on_text, on_thinking, cancel):
        reply = _Reply()
        for raw in response:
            if cancel.is_set():
                return None
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue  # blank lines, ": keep-alive" comments, event: and id: fields
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                raise OpenAIError(None, f"The server sent a reply that is not JSON: {data[:200]}") from None
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):  # OpenRouter and others report failures mid-stream
                error = chunk["error"]
                code = error.get("code") if isinstance(error, dict) else None
                raise OpenAIError(code if isinstance(code, int) else None, _error_text(error))
            reply.add(chunk, on_text, on_thinking)
        return reply.response()

    def describe_error(self, exc: Exception) -> str | None:
        where = self.base_url
        if isinstance(exc, BadToolInput):
            return ("The model sent tool input that is not valid JSON. Try again, or pick a model that "
                    "supports function calling.")
        if isinstance(exc, OpenAIError):
            message = exc.message
            if exc.status == 401:
                return "The API key was rejected. Check OPENAI_API_KEY or the key in add-on preferences."
            if exc.status == 403:
                return f"This API key is not allowed to use the model '{self.model}'" + (
                    f": {message}" if message else ".")
            if exc.status == 404:
                return (f"Not found: the model '{self.model}' does not exist on {where}, or the base URL is "
                        "wrong. Check both in add-on preferences.")
            if exc.status == 429:
                return f"Rate limited by the server ({message}). Wait a moment and try again."
            if exc.status == 400:
                hint = (" The assistant needs a model that supports function calling (tools)."
                        if "tool" in message.lower() else "")
                return f"The server rejected the request: {message}{hint}"
            if exc.status:
                return f"API error {exc.status}: {message}"
            return f"The server reported an error: {message}"
        if isinstance(exc, urllib.error.URLError):
            if isinstance(exc.reason, TimeoutError):
                return f"{where} did not answer within {TIMEOUT:.0f} s."
            check = "that the server is running" if is_local(where) else "your internet connection"
            return f"Could not reach {where} ({exc.reason}). Check the base URL in add-on preferences and {check}."
        if isinstance(exc, TimeoutError):
            return f"{where} did not answer within {TIMEOUT:.0f} s."
        if isinstance(exc, (OSError, http.client.HTTPException)):  # dropped mid-reply, TLS, ...
            return f"The connection to {where} failed: {exc}"
        return None
