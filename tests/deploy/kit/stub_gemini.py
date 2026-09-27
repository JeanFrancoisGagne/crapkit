"""A scripted Gemini API on 127.0.0.1 that records request bodies.

Gemini CLI pointed at it (GOOGLE_GEMINI_BASE_URL=stub.url with gemini-api-key
auth and any key) gets one scripted model turn per generateContent or
streamGenerateContent request. A turn is a dict, or a callable that picks the
dict from the request body and the turn number, as in kit/stub_openai.py:

    {"text": "done"}                                            ends the turn
    {"function_call": {"name": "crapkit__get_next_item", "args": {}}}   asks for a tool call

Gemini CLI also asks the model for JSON between turns (its next-speaker
check, generationConfig.responseMimeType application/json): that request gets
an object naming the user as the next speaker and takes no scripted turn.
countTokens answers a count.

    with stub_gemini.serve(script) as stub:
        box.run(["gemini", "-p", "..."], env={"GOOGLE_GEMINI_BASE_URL": stub.url, ...})
        stub.bodies()      # every generateContent body, oldest first
"""
from __future__ import annotations

import json

from kit.httpstub import Reply, Request, Stub

NEXT_SPEAKER = {"reasoning": "the model answered", "next_speaker": "user"}


def _turn(script, body: dict, number: int) -> dict:
    if callable(script):
        return script(body, number)
    return script[min(number, len(script) - 1)]


def parts(turn: dict) -> list[dict]:
    """The model's content parts for one turn: a functionCall, or text."""
    if "function_call" in turn:
        call = turn["function_call"]
        return [{"functionCall": {"name": call["name"], "args": call.get("args", {})}}]
    return [{"text": turn.get("text", "")}]


def generated(content_parts: list[dict]) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": content_parts}, "finishReason": "STOP",
                            "index": 0}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
            "modelVersion": "stub"}


def wants_json(body: dict) -> bool:
    return (body.get("generationConfig") or {}).get("responseMimeType") == "application/json"


class GeminiStub(Stub):
    def __init__(self, script):
        super().__init__(self.answer)
        self.script = script
        self.turns = 0

    def bodies(self) -> list[dict]:
        """The scripted turns' request bodies: generateContent, never a JSON check or a count."""
        found = [request.json() for request in self.requests if "enerateContent" in request.path]
        return [body for body in found if not wants_json(body)]

    def answer(self, request: Request) -> Reply:
        if ":countTokens" in request.path:
            return Reply.json({"totalTokens": 10})
        if "enerateContent" not in request.path:
            return Reply.json({"error": {"code": 404, "message": f"stub has no {request.path}"}}, 404)
        reply = generated(self._parts(request.json()))
        return Reply.events([(None, reply)]) if "streamGenerateContent" in request.path else Reply.json(reply)

    def _parts(self, body: dict) -> list[dict]:
        if wants_json(body):
            return [{"text": json.dumps(NEXT_SPEAKER)}]
        number = self.turns
        self.turns += 1
        return parts(_turn(self.script, body, number))


def serve(script) -> GeminiStub:
    return GeminiStub(script)
