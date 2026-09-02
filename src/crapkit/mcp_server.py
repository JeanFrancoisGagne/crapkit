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

from .invocation import _self

# Newest first. Everything this server does — tools, annotations, structured
# results over stdio — is inside the 2025-06-18 revision, and nothing it does
# was removed from the older two, so any of the three can be spoken verbatim.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_REPO = {"repo": {"type": "string", "description": (
    "path to the scored repo's root (default: the repo the server was started in)")}}

# brief and explain resolve NAME by one rule, so they describe it with one
# string. The bare identifier is the long name's leading token, which is all
# there is before the parameters in Rust and Go.
_NAME_DESCRIPTION = ("the bare identifier (classify, or route for a Rust "
                     "`route cmd : & Cmd`) or the whole long_name next_item "
                     "printed (classify( score , late )); both resolve, exact "
                     "match first")

TOOLS: tuple[dict, ...] = (
    {"name": "next_item", "argv": ["next-item"], "json_flag": False, "positional": (),
     "flags": {"top": "--top", "exclude": "--exclude"},
     "description": "The highest-risk function to refactor next, as one work packet: score, "
                    "uncovered lines, effort estimate and the commands that verify the fix. "
                    "Reach for it when you want one item to act on; worklist is the same "
                    "ranking as a survey.",
     "properties": {"top": {"type": "integer",
                            "description": "return the next N packets instead of one (>= 1, default 1)"},
                    "exclude": {"type": "array", "items": {"type": "string"},
                                "description": "skip items whose path or function name contains "
                                               "this fragment (repeatable)"}}},
    {"name": "worklist", "argv": ["worklist"], "json_flag": True, "positional": (),
     "flags": {"top": "--top"},
     "description": "The run's whole risk ranking: every function over its ceiling, then the "
                    "queue under it ordered by ccn times recency-weighted churn. Use it to "
                    "survey the repo; next_item hands out one packet from the top.",
     "properties": {"top": {"type": "integer",
                            "description": "cap the active list (default: the config's worklist_top)"}}},
    {"name": "runs", "argv": ["runs"], "json_flag": True, "positional": (), "flags": {},
     "description": "Run history, newest first: id, kind, verdict, commit and lane set. Use it "
                    "to date the store or to check which commit the other tools answer from.",
     "properties": {}},
    {"name": "brief", "argv": ["brief"], "json_flag": True, "positional": ("path", "name"),
     "flags": {},
     "description": "One function's whole context in one call: scored row, ratchet mark, "
                    "uncovered lines, duplication twins, churn and change-coupling partners. "
                    "The deepest single read; explain is the slimmer history-only view.",
     "properties": {"path": {"type": "string", "description": "repo-relative source file"},
                    "name": {"type": "string", "description": _NAME_DESCRIPTION}}},
    {"name": "explain", "argv": ["explain"], "json_flag": False, "positional": ("path", "name"),
     "flags": {},
     "description": "One function's score trajectory across runs plus its ratchet mark. Use it "
                    "to see whether a function is improving or decaying; brief adds the full "
                    "context around the newest score.",
     "properties": {"path": {"type": "string", "description": "repo-relative source file"},
                    "name": {"type": "string", "description": _NAME_DESCRIPTION}}},
    {"name": "doctor", "argv": ["doctor"], "json_flag": False, "positional": (), "flags": {},
     "description": "Config and repo agreement check: typo keys, empty scopes, missing lane "
                    "cwds, unresolvable runners. Run it first when any other tool answers "
                    "strangely or an expected file is missing from the ranking.",
     "properties": {}},
    {"name": "coupling", "argv": ["coupling"], "json_flag": True, "positional": (),
     "flags": {"min_support": "--min-support", "min_confidence": "--min-confidence"},
     "description": "File pairs that keep landing in the same commits: dependencies no import "
                    "statement reveals. Use it to learn what else an edit usually drags along "
                    "before touching a file.",
     "properties": {"min_support": {"type": "integer",
                                    "description": "minimum shared commits before a pair counts (default 5)"},
                    "min_confidence": {"type": "number",
                                       "description": "minimum P(pair changes together), 0 to 1 (default 0.5)"}}},
    {"name": "duplication", "argv": ["duplication"], "json_flag": True, "positional": (),
     "flags": {"similarity": "--similarity"},
     "description": "Near-duplicate function pairs by normalized line shingles, with the "
                    "containment percent. Use it before a refactor so twins get folded "
                    "together instead of one copy getting fixed alone.",
     "properties": {"similarity": {"type": "number",
                                   "description": "containment threshold, shared over smaller, "
                                                  "0 to 1 (default 0.8)"}}},
    {"name": "ratchet_report", "argv": ["ratchet", "report"], "json_flag": True, "positional": (),
     "flags": {},
     "description": "The debt burn-down: every open ratchet mark with its age, plus repayment "
                    "velocity from git history. Use it to see whether marked debt is being "
                    "paid down or piling up.",
     "properties": {}},
)

# Every tool shells to a read-only CLI command against a store on this machine:
# nothing writes, reruns answer the same, and no call leaves the repo. Declared
# once, in the field clients read, so the module docstring's promise reaches the
# model that plans with these tools.
_ANNOTATIONS = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}


def tool_listing() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "annotations": dict(_ANNOTATIONS),
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
                   f"Run `{_self()} init` in the repo you want scored, or pass this tool a "
                   "`repo` argument (or start the server with --repo) pointing at one.",
                   is_error=True)


def _run_cli(tool: dict, arguments: dict, repo: str) -> dict:
    """One tool, run as the CLI command it maps to. A command that printed
    nothing answers with its stderr, so a refusal reaches the caller as text."""
    argv = build_argv(tool, arguments) + ["--repo", repo]
    proc = subprocess.run([sys.executable, "-m", "crapkit", *argv],
                          capture_output=True, text=True, timeout=600)
    text = proc.stdout if proc.stdout.strip() else proc.stderr
    return _structured(_result(text, is_error=proc.returncode != 0))


def _call_tool(root: Path, name: str, arguments: dict) -> dict:
    """Name lookup, then the repo the call names, then the run."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return _result(f"unknown tool {name!r}", is_error=True)
    repo = arguments.get("repo") or str(root)
    if not (Path(repo) / "crapkit.toml").is_file():
        return _no_config_result(repo)
    return _run_cli(tool, arguments, repo)


# What a connected model needs before its first call, in the one field the
# protocol reserves for it. The nine error results a model would otherwise
# collect from an unmeasured repo teach the same thing nine times, slower.
_INSTRUCTIONS = (
    "crapkit scores every function as ccn^2 x (1 - coverage)^3 + ccn; these nine tools read "
    "the newest scored run and every one is read-only: nothing here runs a test suite or "
    "writes to the repo. They need a repo measured once (crapkit init, then crapkit "
    "coverage); an unmeasured repo answers with a one-line pointer instead of data. Start "
    "with next_item for one function to fix, worklist for the whole ranking, brief for "
    "everything about one function.")


def _negotiated(params: dict) -> str:
    """The client's revision when this server implements it, else the newest it
    does; the spec leaves proceeding or disconnecting to the client from there."""
    offered = params.get("protocolVersion")
    return offered if offered in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]


def _structured(result: dict) -> dict:
    """Attach the parsed object beside the text when the text is a JSON object.

    The --json commands print for machines; a client on the 2025-06-18 revision
    reads structuredContent directly. Prose, JSON arrays and error text stay
    text-only rather than getting wrapped into shapes the tools never promised.
    """
    if result["isError"]:
        return result
    try:
        parsed = json.loads(result["content"][0]["text"])
    except ValueError:
        return result
    if isinstance(parsed, dict):
        return {**result, "structuredContent": parsed}
    return result


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
            "protocolVersion": _negotiated(msg.get("params", {})),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "crapkit", "version": _version()},
            "instructions": _INSTRUCTIONS})
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
