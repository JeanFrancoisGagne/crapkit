"""A stdio MCP client for profiles the TypeScript SDK does not model: rmcp,
the Go SDK and closed clients, each marked 'modelled' in its cell.

It speaks newline-delimited JSON-RPC over the server's stdin and stdout, keeps
stdin open between requests the way a real client does, and waits on each
reply under the hang bound, never on a sleep. Every message both ways goes to
the transcript.

    with McpClient([launcher, "mcp"], cwd=repo, env=box.env, transcript=box.transcript) as client:
        client.initialize("2025-06-18")
        names = [tool["name"] for tool in client.tools()]
        result = client.call("get_next_item", {})
"""
from __future__ import annotations

import itertools
import json
import queue
import subprocess
import tempfile
import threading

import hang_guard

PROTOCOL = "2025-06-18"


class McpError(AssertionError):
    """The server answered a request with a JSON-RPC error, or never answered."""


def _read(stream, replies: queue.Queue) -> None:
    with stream:
        for line in stream:
            replies.put(line)
    replies.put(None)


class McpClient:
    def __init__(self, argv: list[str], *, cwd, env: dict, transcript=None):
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen([str(arg) for arg in argv], cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr, text=True, encoding="utf-8")
        self.replies: queue.Queue = queue.Queue()
        threading.Thread(target=_read, args=(self.process.stdout, self.replies), daemon=True).start()
        self.ids = itertools.count(1)
        self.log: list[tuple[str, dict]] = []
        self.transcript = transcript

    @classmethod
    def in_box(cls, box, argv: list[str], cwd) -> "McpClient":
        """A client started from the sandbox: its env, its PATH for argv[0], its transcript."""
        return cls([box.resolve(str(argv[0])), *argv[1:]], cwd=cwd, env=box.env, transcript=box.transcript)

    def send(self, message: dict) -> None:
        self.log.append(("sent", message))
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    def _next(self) -> dict | None:
        try:
            line = self.replies.get(timeout=hang_guard.HANG_SECONDS)
        except queue.Empty:
            raise McpError(f"no reply within the hang bound\n{self.stderr_text()}") from None
        if line is None:
            raise McpError(f"the server closed stdout (exit {self.process.poll()})\n{self.stderr_text()}")
        return json.loads(line) if line.strip() else None

    def reply(self, request_id: int) -> dict:
        """The response to `request_id`; notifications and other ids are logged and skipped."""
        while True:
            message = self._next()
            if message is None:
                continue
            self.log.append(("received", message))
            if message.get("id") == request_id and "method" not in message:
                return message

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = next(self.ids)
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method,
                   **({"params": params} if params is not None else {})})
        message = self.reply(request_id)
        if "error" in message:
            raise McpError(f"{method} answered an error: {message['error']}")
        return message["result"]

    def initialize(self, protocol: str = PROTOCOL, client: str = "crapkit-deploy-kit") -> dict:
        result = self.request("initialize", {"protocolVersion": protocol, "capabilities": {},
                                             "clientInfo": {"name": client, "version": "1"}})
        self.notify("notifications/initialized")
        return result

    def tools(self) -> list[dict]:
        return self.request("tools/list")["tools"]

    def call(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def stderr_text(self) -> str:
        self.stderr.seek(0)
        return self.stderr.read().decode("utf-8", "replace")

    def close(self) -> int:
        """Close stdin as a client does and wait for the server to exit."""
        if not self.process.stdin.closed:
            self.process.stdin.close()
        code = hang_guard.exited(self.process)
        if self.transcript is not None:
            self.transcript.attach(f"mcp-{id(self)}", {"messages": self.log, "stderr": self.stderr_text(),
                                                       "exit": code})
        return code

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *exc) -> None:
        if self.process.poll() is None:
            self.close()
        self.stderr.close()
