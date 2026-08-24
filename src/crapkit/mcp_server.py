"""Minimal MCP stdio server. JSON-RPC 2.0, newline-delimited, no SDK dependency.

Read-side only: every tool shells to the CLI's own --json surface, so the MCP
view can never drift from what the CLI reports, and nothing here writes a
baseline, a ratchet, or a mutant. Runs that mutate state stay in the CLI.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"

_REPO = {"repo": {"type": "string", "description": "repo root (default: the server's)"}}

TOOLS: tuple[dict, ...] = (
    {"name": "next_item", "argv": ["next-item"], "json_flag": False, "positional": (),
     "flags": {"top": "--top", "exclude": "--exclude"},
     "description": "The highest-risk function(s) to refactor next, with scores and effort estimates",
     "properties": {"top": {"type": "integer"}, "exclude": {"type": "array", "items": {"type": "string"}}}},
    {"name": "worklist", "argv": ["worklist"], "json_flag": True, "positional": (),
     "flags": {"top": "--top"},
     "description": "Risk-ranked decomposition queue (ccn x recency-weighted churn)",
     "properties": {"top": {"type": "integer"}}},
    {"name": "runs", "argv": ["runs"], "json_flag": True, "positional": (), "flags": {},
     "description": "Run history: id, kind, verdict, commit, lane set", "properties": {}},
    {"name": "brief", "argv": ["brief"], "json_flag": True, "positional": ("path", "name"),
     "flags": {},
     "description": "One function's whole context: scored row, ratchet mark, uncovered lines, "
                    "duplication twins, churn and change-coupling partners",
     "properties": {"path": {"type": "string", "description": "repo-relative source file"},
                    "name": {"type": "string",
                             "description": "the bare identifier (classify) or the whole "
                                            "long_name next_item printed "
                                            "(classify( score , late )); both resolve"}}},
    {"name": "explain", "argv": ["explain"], "json_flag": False, "positional": ("path", "name"),
     "flags": {},
     "description": "One function's score trajectory across runs plus its ratchet mark",
     "properties": {"path": {"type": "string"}, "name": {"type": "string"}}},
    {"name": "doctor", "argv": ["doctor"], "json_flag": False, "positional": (), "flags": {},
     "description": "Config/repo agreement check: typo keys, empty scopes, missing lane cwds",
     "properties": {}},
    {"name": "coupling", "argv": ["coupling"], "json_flag": True, "positional": (),
     "flags": {"min_support": "--min-support", "min_confidence": "--min-confidence"},
     "description": "File pairs that keep landing in the same commits (hidden dependencies)",
     "properties": {"min_support": {"type": "integer"}, "min_confidence": {"type": "number"}}},
    {"name": "duplication", "argv": ["duplication"], "json_flag": True, "positional": (),
     "flags": {"similarity": "--similarity"},
     "description": "Near-duplicate functions by normalized line shingles",
     "properties": {"similarity": {"type": "number"}}},
    {"name": "ratchet_report", "argv": ["ratchet", "report"], "json_flag": True, "positional": (),
     "flags": {},
     "description": "Debt burn-down: open mark ages and repayment velocity from git history",
     "properties": {}},
)


def tool_listing() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": {"type": "object", "properties": {**_REPO, **t["properties"]}}}
            for t in TOOLS]


def _flag_args(flags: dict, arguments: dict) -> list[str]:
    out: list[str] = []
    for key, flag in flags.items():
        value = arguments.get(key)
        if value is None:
            continue
        for v in (value if isinstance(value, list) else [value]):
            out += [flag, str(v)]
    return out


def build_argv(tool: dict, arguments: dict) -> list[str]:
    argv = list(tool["argv"])
    argv += [str(arguments[p]) for p in tool["positional"]]
    argv += _flag_args(tool["flags"], arguments)
    if tool["json_flag"]:
        argv.append("--json")
    return argv


def _result(text: str, *, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _no_config_result(repo: str) -> dict:
    """What every tool answers in a directory crapkit has never measured.

    A tool result, never a JSON-RPC error: the client keeps the session, and the
    caller reads one sentence that says what to run instead of a transport
    failure. isError stays true so nothing reads an unmeasured directory as a
    repo with nothing to report.
    """
    return _result(f"no crapkit.toml in {repo} — nothing measured here. "
                   "Run `crapkit init` in the repo you want scored, or pass this tool a "
                   "`repo` argument (or start the server with --repo) pointing at one.",
                   is_error=True)


def _run_cli(tool: dict, arguments: dict, repo: str) -> dict:
    """One tool, run as the CLI command it maps to. A command that printed
    nothing answers with its stderr, so a refusal reaches the caller as text."""
    argv = build_argv(tool, arguments) + ["--repo", repo]
    proc = subprocess.run([sys.executable, "-m", "crapkit", *argv],
                          capture_output=True, text=True, timeout=600)
    text = proc.stdout if proc.stdout.strip() else proc.stderr
    return _result(text, is_error=proc.returncode != 0)


def _call_tool(root: Path, name: str, arguments: dict) -> dict:
    """Name lookup, then the repo the call names, then the run."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return _result(f"unknown tool {name!r}", is_error=True)
    repo = arguments.get("repo") or str(root)
    if not (Path(repo) / "crapkit.toml").is_file():
        return _no_config_result(repo)
    return _run_cli(tool, arguments, repo)


def _respond(msg_id, result=None, error=None) -> dict:
    resp = {"jsonrpc": "2.0", "id": msg_id}
    resp["error" if error else "result"] = error if error else result
    return resp


def _handle(root: Path, msg: dict) -> dict | None:
    method = msg.get("method", "")
    if "id" not in msg:
        return None  # a notification (e.g. notifications/initialized) needs no reply
    if method == "initialize":
        return _respond(msg["id"], {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "crapkit", "version": _version()}})
    if method == "tools/list":
        return _respond(msg["id"], {"tools": tool_listing()})
    if method == "tools/call":
        params = msg.get("params", {})
        return _respond(msg["id"], _call_tool(root, params.get("name", ""),
                                              params.get("arguments", {})))
    return _respond(msg["id"], error={"code": -32601, "message": f"unknown method {method!r}"})


def _version() -> str:
    from . import __version__
    return __version__


def serve(root: Path) -> int:
    """Newline-delimited JSON-RPC over stdio until EOF."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = _handle(root, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0
