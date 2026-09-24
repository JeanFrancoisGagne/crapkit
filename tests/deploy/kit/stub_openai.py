"""A scripted OpenAI API on 127.0.0.1: Chat Completions and Responses, each
streamed when asked, recording every request body.

Harnesses that take a provider base URL (OpenCode, Goose, Cline, Continue,
Crush, Junie, Copilot BYOK) are pointed at it. A turn is a dict, or a callable
of (body, turn number) returning one:

    {"text": "done"}
    {"tool_call": {"name": "crapkit_get_next_item", "arguments": {}}}

    with stub_openai.serve([{"tool_call": {...}}, {"text": "done"}]) as stub:
        base_url = stub.url + "/v1"
"""
from __future__ import annotations

import json

from kit.httpstub import Reply, Request, Stub


def _turn(script, body: dict, number: int) -> dict:
    if callable(script):
        return script(body, number)
    return script[min(number, len(script) - 1)]


def _call(turn: dict, number: int) -> dict:
    call = turn["tool_call"]
    return {"id": f"call_{number:04d}", "name": call["name"], "arguments": json.dumps(call.get("arguments", {}))}


# --- Chat Completions ------------------------------------------------------------

def chat_message(turn: dict, number: int) -> dict:
    if "tool_call" in turn:
        call = _call(turn, number)
        tool = {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
        return {"role": "assistant", "content": None, "tool_calls": [tool]}
    return {"role": "assistant", "content": turn.get("text", "")}


def completion(turn: dict, number: int, model: str) -> dict:
    msg = chat_message(turn, number)
    finish = "tool_calls" if msg.get("tool_calls") else "stop"
    return {"id": f"chatcmpl-{number:04d}", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def _chunk(reply: dict, delta: dict, finish: str | None) -> dict:
    return {"id": reply["id"], "object": "chat.completion.chunk", "created": 0, "model": reply["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def chat_events(reply: dict) -> list[tuple[None, dict | str]]:
    choice = reply["choices"][0]
    delta = dict(choice["message"])
    if delta.get("tool_calls"):
        delta["tool_calls"] = [{**call, "index": 0} for call in delta["tool_calls"]]
    return [(None, _chunk(reply, delta, None)), (None, _chunk(reply, {}, choice["finish_reason"])),
            (None, "[DONE]")]


# --- Responses -------------------------------------------------------------------

def response_item(turn: dict, number: int) -> dict:
    if "tool_call" in turn:
        call = _call(turn, number)
        return {"type": "function_call", "id": f"fc_{number:04d}", "call_id": call["id"], "name": call["name"],
                "arguments": call["arguments"], "status": "completed"}
    text = {"type": "output_text", "text": turn.get("text", ""), "annotations": []}
    return {"type": "message", "id": f"msg_{number:04d}", "role": "assistant", "status": "completed",
            "content": [text]}


def response(turn: dict, number: int, model: str) -> dict:
    return {"id": f"resp_{number:04d}", "object": "response", "created_at": 0, "status": "completed",
            "model": model, "output": [response_item(turn, number)],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}


def response_events(reply: dict) -> list[tuple[str, dict]]:
    item = reply["output"][0]
    pending = {**reply, "status": "in_progress", "output": []}
    return [("response.created", {"type": "response.created", "response": pending}),
            ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": item}),
            ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
            ("response.completed", {"type": "response.completed", "response": reply})]


# --- the server --------------------------------------------------------------------

class OpenAIStub(Stub):
    def __init__(self, script):
        super().__init__(self.answer)
        self.script = script
        self.turns = 0

    def bodies(self, path: str = "") -> list[dict]:
        return [request.json() for request in self.requests if request.method == "POST" and path in request.path]

    def answer(self, request: Request) -> Reply:
        path = request.path.split("?")[0].rstrip("/")
        routes = {"/v1/chat/completions": self._chat, "/v1/responses": self._responses,
                  "/chat/completions": self._chat, "/responses": self._responses}
        if path.endswith("/models"):
            return Reply.json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub"}]})
        route = routes.get(path)
        return route(request.json()) if route else Reply.json({"error": {"message": path}}, 404)

    def _next(self, body: dict) -> tuple[dict, int]:
        number = self.turns
        self.turns += 1
        return _turn(self.script, body, number), number

    def _chat(self, body: dict) -> Reply:
        turn, number = self._next(body)
        reply = completion(turn, number, body.get("model", "stub-model"))
        return Reply.events(chat_events(reply)) if body.get("stream") else Reply.json(reply)

    def _responses(self, body: dict) -> Reply:
        turn, number = self._next(body)
        reply = response(turn, number, body.get("model", "stub-model"))
        return Reply.events(response_events(reply)) if body.get("stream") else Reply.json(reply)


def serve(script) -> OpenAIStub:
    return OpenAIStub(script)
