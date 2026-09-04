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
from .rootfind import find_root

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

# The partition a large repo needs before `top` means anything: one --scope
# per element, exact names as declared in crapkit.toml.
_SCOPE = {"type": "array", "items": {"type": "string"},
          "description": "restrict the ranking to these declared scopes (exact names from "
                         "crapkit.toml, one --scope each)"}

TOOLS: tuple[dict, ...] = (
    {"name": "next_item", "argv": ["next-item"], "json_flag": False, "positional": (),
     "flags": {"top": "--top", "exclude": "--exclude", "scope": "--scope"},
     "description": "The highest-risk function to refactor next, as one work packet: score, "
                    "uncovered lines, effort estimate and the commands that verify the fix. "
                    "Reach for it when you want one item to act on; worklist is the same "
                    "ranking as a survey.",
     "properties": {"top": {"type": "integer",
                            "description": "return the next N packets instead of one (>= 1, default 1)"},
                    "exclude": {"type": "array", "items": {"type": "string"},
                                "description": "skip items whose path or function name contains "
                                               "this fragment (repeatable)"},
                    "scope": _SCOPE}},
    {"name": "worklist", "argv": ["worklist"], "json_flag": True, "positional": (),
     "flags": {"top": "--top", "scope": "--scope"},
     "description": "The run's whole risk ranking: every function over its ceiling, then the "
                    "queue under it ordered by ccn times recency-weighted churn. Use it to "
                    "survey the repo; next_item hands out one packet from the top.",
     "properties": {"top": {"type": "integer",
                            "description": "cap the active list (default: the config's worklist_top)"},
                    "scope": _SCOPE}},
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
    {"name": "explain", "argv": ["explain"], "json_flag": True, "positional": ("path", "name"),
     "flags": {"history": "--history", "tests": "--tests"},
     "description": "One function's score trajectory across runs plus its ratchet mark. Use it "
                    "to see whether a function is improving or decaying; brief adds the full "
                    "context around the newest score.",
     "properties": {"path": {"type": "string", "description": "repo-relative source file"},
                    "name": {"type": "string", "description": _NAME_DESCRIPTION},
                    "history": {"type": "boolean",
                                "description": "also list the commits that touched this "
                                               "function (git log -L), as `commits`"},
                    "tests": {"type": "boolean",
                              "description": "also list the tests covering this function "
                                             "(coverage.py contexts), as `tests`"}}},
    {"name": "doctor", "argv": ["doctor"], "json_flag": True, "positional": (), "flags": {},
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
    # `rescore PATH --gate --json`: read-only like the rest (it writes the
    # analysis cache, never a run). Exit 6 is the verdict, not a failure, so it
    # comes back as a result whose `gate.ok` is false; 3, 4 and 5 stay errors.
    {"name": "gate", "argv": ["rescore", "--gate"], "json_flag": True, "positional": ("path",),
     "flags": {}, "verdict_exits": (6,),
     "description": "Whether an edited file clears the commit gate, judged the way the "
                    "pre-commit hook will: fresh ccn of every function the working tree "
                    "changed against its scope's ceiling, minus marked debt, as rescore "
                    "--gate. Call it after an edit and before the commit; a breach answers "
                    "ok false with the functions, not a tool error.",
     "properties": {"path": {"type": "string", "description": "repo-relative source file "
                                                               "to judge as edited"}}},
)

# Every tool shells to a read-only CLI command against a store on this machine:
# nothing writes, reruns answer the same, and no call leaves the repo. Declared
# once, in the field clients read, so the module docstring's promise reaches the
# model that plans with these tools.
_ANNOTATIONS = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}


def _accepted(tool: dict) -> dict:
    """Every argument one tool takes, `repo` first, in the order the listing
    shows them: the one table both the schema and the refusals read from."""
    return {**_REPO, **tool["properties"]}


def _schema(tool: dict) -> dict:
    """The positionals are the required arguments; `required` is left out when
    there are none, which JSON Schema reads the same way."""
    schema = {"type": "object", "properties": _accepted(tool)}
    if tool["positional"]:
        schema["required"] = list(tool["positional"])
    return schema


def tool_listing() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "annotations": dict(_ANNOTATIONS), "inputSchema": _schema(t)}
            for t in TOOLS]


def _flag_values(value) -> list:
    """What one argument contributes to argv: nothing when absent or false, one
    bare flag for true, one flag per element for a list, else one flag."""
    if value is None or value is False:
        return []
    return value if isinstance(value, list) else [value]


def _flag_args(flags: dict, arguments: dict) -> list[str]:
    out: list[str] = []
    for key, flag in flags.items():
        for v in _flag_values(arguments.get(key)):
            out += [flag] if v is True else [flag, str(v)]
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
    return _result(f"no crapkit.toml in {repo} - nothing measured here. "
                   f"Run `{_self()} init` in the repo you want scored, or pass this tool a "
                   "`repo` argument (or start the server with --repo) pointing at one.",
                   is_error=True)


def _run_cli(tool: dict, arguments: dict, repo: str) -> dict:
    """One tool, run as the CLI command it maps to. A command that printed
    nothing answers with its stderr, so a refusal reaches the caller as text.
    An exit the tool declares in `verdict_exits` is an answer, not a failure:
    `gate` exits 6 on a breach and its payload says so in `gate.ok`."""
    argv = build_argv(tool, arguments) + ["--repo", repo]
    proc = subprocess.run([sys.executable, "-m", "crapkit", *argv],
                          capture_output=True, text=True, timeout=600)
    text = proc.stdout if proc.stdout.strip() else proc.stderr
    failed = proc.returncode != 0 and proc.returncode not in tool.get("verdict_exits", ())
    return _structured(_result(text, is_error=failed))


# JSON Schema type names to the Python shapes json.loads produces for them. A
# bool is an int in Python and never one in JSON, so integer and number say so.
_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
}
_TYPE_NAMES = {"string": "a string", "integer": "an integer", "number": "a number",
               "boolean": "a boolean", "array": "an array of strings"}


def _missing_positional(tool: dict, arguments: dict) -> str | None:
    """A positional left out or sent as null: the served schema declares it a
    required string, so either is refused here and never reaches argv."""
    for key in tool["positional"]:
        if arguments.get(key) is None:
            return f"{tool['name']} needs {key} (see inputSchema.required)"
    return None


def _unknown_key(tool: dict, arguments: dict) -> str | None:
    accepted = _accepted(tool)
    for key in arguments:
        if key not in accepted:
            return f"{tool['name']} does not take {key!r}; accepted: {', '.join(accepted)}"
    return None


def _wrong_type(tool: dict, arguments: dict) -> str | None:
    for key, prop in _accepted(tool).items():
        value = arguments.get(key)
        if value is not None and not _TYPES[prop["type"]](value):
            return f"{key} must be {_TYPE_NAMES[prop['type']]} (got {json.dumps(value)})"
    return None


def _argument_error(tool: dict, arguments: dict) -> str | None:
    """The first refusal the tool's own table finds, or None when the call can run.

    Answered as a tool result with isError true, in the tool's vocabulary, not
    as the protocol's -32602 example: ADR 0001 keeps the house precedent set by
    the unknown-tool and missing-config answers, because a coding agent reads
    tool results and corrects its next call, while a protocol error surfaces in
    many clients as a transport failure the agent never sees.
    """
    return (_missing_positional(tool, arguments) or _unknown_key(tool, arguments)
            or _wrong_type(tool, arguments))


def _tool_named(name: str) -> dict | None:
    return next((t for t in TOOLS if t["name"] == name), None)


def _config_root(repo: str) -> Path | None:
    """The crapkit root at or above `repo`, the call's own argument or the
    server's default, found the way every command finds it (ADR 0002): a
    server a global client started in a workspace serves the root
    configuration that claims the workspace."""
    return find_root(Path(repo).resolve())


def _call_tool(root: Path, name: str, arguments: dict) -> dict:
    """Name lookup, then the arguments against the table, then the repo the
    call names, then the run. Every refusal is decided before a CLI spawns."""
    tool = _tool_named(name)
    if tool is None:
        return _result(f"unknown tool {name!r}", is_error=True)
    refusal = _argument_error(tool, arguments)
    if refusal:
        return _result(refusal, is_error=True)
    repo = arguments.get("repo") or str(root)
    found = _config_root(repo)
    if found is None:
        return _no_config_result(repo)
    return _run_cli(tool, arguments, str(found))


# What a connected model needs before its first call, in the one field the
# protocol reserves for it. The ten error results a model would otherwise
# collect from an unmeasured repo teach the same thing ten times, slower.
_INSTRUCTIONS = (
    "crapkit scores every function as ccn^2 x (1 - coverage)^3 + ccn; these ten tools read "
    "the newest scored run and every one is read-only: nothing here runs a test suite or "
    "writes to the repo. They need a repo measured once (crapkit init, then crapkit "
    "coverage); an unmeasured repo answers with a one-line pointer instead of data. Start "
    "with next_item for one function to fix, worklist for the whole ranking, brief for "
    "everything about one function, and gate after an edit to learn whether the file "
    "clears the commit gate.")


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


def _initialize_result(params: dict) -> dict:
    return {"protocolVersion": _negotiated(params),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "crapkit", "version": _version()},
            "instructions": _INSTRUCTIONS}


# The methods that need no repo, keyed as the wire spells them. ping answers
# the empty object the spec asks for, so a client's keepalive is not -32601.
_METHODS = {"initialize": _initialize_result,
            "tools/list": lambda params: {"tools": tool_listing()},
            "ping": lambda params: {}}


def _tools_call(root: Path, params: dict) -> dict:
    return _call_tool(root, params.get("name", ""), params.get("arguments") or {})


def _handle(root: Path, msg: dict) -> dict | None:
    if "id" not in msg:
        return None  # a notification (e.g. notifications/initialized) needs no reply
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if method == "tools/call":
        return _respond(msg["id"], _tools_call(root, params))
    handler = _METHODS.get(method)
    if handler is None:
        return _respond(msg["id"], error={"code": -32601,
                                          "message": f"unknown method {method!r}"})
    return _respond(msg["id"], handler(params))


def _version() -> str:
    from . import __version__
    return __version__


def _parse(line: str) -> dict | None:
    """One request object, or None for a blank line, junk, or a frame that is
    not an object: those get no reply, and the loop reads on."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _reply(root: Path, msg: dict) -> dict | None:
    """The reply to one message. An exception escaping a handler becomes the
    JSON-RPC -32603 reply instead of the end of the session; a notification
    that raised gets nothing, since it asked for nothing."""
    try:
        return _handle(root, msg)
    except Exception as exc:  # noqa: BLE001 - the loop must outlive any one call
        if "id" not in msg:
            return None
        return _respond(msg["id"], error={"code": -32603,
                                          "message": f"{type(exc).__name__}: {exc}"})


def serve(root: Path) -> int:
    """Newline-delimited JSON-RPC over stdio until EOF."""
    for line in sys.stdin:
        msg = _parse(line)
        if msg is None:
            continue
        resp = _reply(root, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0
