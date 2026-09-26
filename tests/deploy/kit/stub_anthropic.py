"""A scripted Anthropic Messages API on 127.0.0.1 that records request bodies.

A harness pointed at it (ANTHROPIC_BASE_URL=stub.url, any key) gets one
scripted assistant turn per POST /v1/messages, streamed when the request asks
for a stream. A turn is a dict, or a callable that picks the dict from the
request body and the turn number:

    {"text": "done"}                                     ends the turn
    {"tool_use": {"name": "Read", "input": {...}}}       asks for a tool call

    def script(body, turn):
        if "crapkit advisory" in json.dumps(body):
            return {"text": "seen"}
        return {"tool_use": {"name": "Read", "input": {"file_path": "calc.py"}}}

    with stub_anthropic.serve(script) as stub:
        box.run(["claude", "-p", "..."], env=stub_anthropic.claude_env(stub.url))
        stub.bodies()      # every /v1/messages body, oldest first
"""
from __future__ import annotations

import json

from kit.httpstub import Reply, Request, Stub

# Claude Code -p with a base URL that is not Anthropic's can send its first
# request while a plugin's MCP server is still connecting, and that request
# offers no crapkit tool. Through the shim's second Python start it did in 4
# of 6 runs; waiting up to 5 s for the servers at startup made it 5 of 5.
STARTUP_WAIT = {"CLAUDE_CODE_MCP_STARTUP_WAIT_MS": "5000"}


def claude_env(url: str, key: str = "sk-ant-stub") -> dict[str, str]:
    """What `claude -p` needs to talk to a stub at `url`: the URL, any key, and
    the startup wait that lets MCP servers connect before the first request."""
    return {"ANTHROPIC_BASE_URL": url, "ANTHROPIC_API_KEY": key, **STARTUP_WAIT}


def _turn(script, body: dict, number: int) -> dict:
    if callable(script):
        return script(body, number)
    return script[min(number, len(script) - 1)]


def _block(turn: dict, number: int) -> dict:
    if "tool_use" in turn:
        call = turn["tool_use"]
        return {"type": "tool_use", "id": f"toolu_{number:04d}", "name": call["name"], "input": call["input"]}
    return {"type": "text", "text": turn.get("text", "")}


def message(turn: dict, number: int, model: str) -> dict:
    block = _block(turn, number)
    stop = "tool_use" if block["type"] == "tool_use" else "end_turn"
    return {"id": f"msg_{number:04d}", "type": "message", "role": "assistant", "model": model,
            "content": [block], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def _delta(block: dict) -> dict:
    if block["type"] == "tool_use":
        return {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    return {"type": "text_delta", "text": block["text"]}


def _opening(block: dict) -> dict:
    return {**block, "input": {}} if block["type"] == "tool_use" else {**block, "text": ""}


def events(reply: dict) -> list[tuple[str, dict]]:
    """The same message as the Messages API's server-sent events."""
    block = reply["content"][0]
    start = {**reply, "content": [], "stop_reason": None}
    return [("message_start", {"type": "message_start", "message": start}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": _opening(block)}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": _delta(block)}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": reply["stop_reason"],
                                                                   "stop_sequence": None},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"})]


class AnthropicStub(Stub):
    def __init__(self, script):
        super().__init__(self.answer)
        self.script = script
        self.turns = 0

    def bodies(self) -> list[dict]:
        return [request.json() for request in self.requests if request.path.startswith("/v1/messages")
                and not request.path.startswith("/v1/messages/count_tokens")]

    def answer(self, request: Request) -> Reply:
        path = request.path.split("?")[0]
        if path == "/v1/messages/count_tokens":
            return Reply.json({"input_tokens": 1})
        if path != "/v1/messages":
            return Reply.json({"type": "error", "error": {"type": "not_found_error", "message": path}}, 404)
        return self._message(request.json())

    def _message(self, body: dict) -> Reply:
        reply = message(_turn(self.script, body, self.turns), self.turns, body.get("model", "stub"))
        self.turns += 1
        return Reply.events(events(reply)) if body.get("stream") else Reply.json(reply)


def serve(script) -> AnthropicStub:
    return AnthropicStub(script)
