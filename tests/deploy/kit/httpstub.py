"""A local HTTP server on 127.0.0.1 that records every request.

pyindex, stub_anthropic and stub_openai are each one handler on top of this:
the server binds an ephemeral port, serves from a daemon thread, and keeps
(method, path, headers, body) for every request so a cell asserts on what the
client actually sent. A HEAD gets the headers a GET would, Content-Length
included, and no body: uv asks HEAD before it fetches a wheel, and a body on a
keep-alive connection made its next response parse as `invalid HTTP version`.

    with Stub(handler) as stub:
        stub.url            # http://127.0.0.1:PORT
        stub.requests       # [Request(...), ...]
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: dict
    body: bytes

    def json(self):
        return json.loads(self.body or b"null")


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    headers: tuple = ()

    @classmethod
    def json(cls, data, status: int = 200, headers: tuple = ()) -> "Reply":
        return cls(status, json.dumps(data).encode("utf-8"), (("Content-Type", "application/json"), *headers))

    @classmethod
    def events(cls, events: list[tuple[str | None, dict | str]]) -> "Reply":
        """A text/event-stream body: one `event:`/`data:` pair per event."""
        chunks = []
        for name, data in events:
            head = f"event: {name}\n" if name else ""
            payload = data if isinstance(data, str) else json.dumps(data)
            chunks.append(f"{head}data: {payload}\n\n")
        return cls(200, "".join(chunks).encode("utf-8"), (("Content-Type", "text/event-stream"),))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    stub: "Stub"

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        request = Request(self.command, self.path, dict(self.headers), self.rfile.read(length) if length else b"")
        self.stub.requests.append(request)
        reply = self.stub.handler(request)
        self.send_response(reply.status)
        for name, value in reply.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(reply.body)

    do_GET = do_POST = do_HEAD = _serve

    def log_message(self, *args):
        pass


class Stub:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[Request] = []
        handler_class = type("Handler", (_Handler,), {"stub": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "Stub":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "Stub":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
