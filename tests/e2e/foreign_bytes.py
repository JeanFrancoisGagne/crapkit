"""Repos that hold bytes crapkit did not write, and the checks every file that
feeds them through the CLI shares.

Four files use this: test_git_free_text_e2e.py (author names, messages, patch
lines), test_outside_files_and_frames_e2e.py (junit and coverage reports, MCP
frames, the Action's changed-file list, config and payload files),
test_os_text_e2e.py (argv, the environment, a host name, a directory name, the
console) and test_source_and_mutant_bytes_e2e.py (source files, lane output,
the files crapkit writes). Each row there is a byte shape the utf8-author hunt
found red, or the green control beside it, fed through the command that reads
it.

`commit` writes through `git fast-import`, which stores author, committer,
message, path and blob bytes exactly as given, plus an `encoding` header when a
row asks for one. `git commit` re-encodes a message through
i18n.commitEncoding, and a Windows argv cannot carry a name that is not UTF-8,
so no other writer reaches every row on every OS. A history with such bytes is
what an import from another VCS, or a client set to a legacy code page, leaves.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import hang_guard

from conftest import CRAPKIT, child_env

DAY = 86400

# A stand-in for `pytest --cov`: every function in every tracked .py under src
# gets a coverage.py region, its first half of lines run. ast reads the bytes
# with the file's coding cookie, so a cp1252 source scores like a UTF-8 one.
LANE_SCRIPT = b'''import ast, json, subprocess
from pathlib import Path

def region(node):
    lines = list(range(node.lineno, node.end_lineno + 1))
    run = lines[: max(1, len(lines) // 2)]
    return {"start_line": node.lineno, "executed_lines": run,
            "missing_lines": [n for n in lines if n not in run],
            "summary": {"covered_lines": len(run), "num_statements": len(lines),
                        "num_branches": 0, "covered_branches": 0}}

files = {}
names = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files", "-z", "--", "src"],
                       capture_output=True, check=True).stdout.split(b"\\0")
for name in names:
    if name.endswith(b".py"):
        path = name.decode("utf-8")
        tree = ast.parse(Path(path).read_bytes())
        files[path] = {"functions": {node.name: region(node) for node in ast.walk(tree)
                                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}}
Path("cov.json").write_text(json.dumps({"meta": {"branch_coverage": True}, "files": files}),
                            encoding="utf-8")
'''

TOML = (b'[crapkit]\ntarget = 6\nworklist_floor = 1\n\n'
        b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
        b'[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
        b'parser = "coveragepy"\nscopes = ["src"]\n')

GITIGNORE = b".crapkit/\ncov.json\n"

APP = (b"def pick(kind):\n"
       b"    if kind == 'a':\n"
       b"        return 1\n"
       b"    if kind == 'b':\n"
       b"        return 2\n"
       b"    return 0\n")

SCAFFOLD = {b"crapkit.toml": TOML, b"lane.py": LANE_SCRIPT, b".gitignore": GITIGNORE}

CODEC_ERRORS = ("Traceback (most recent call last)", "UnicodeDecodeError", "UnicodeEncodeError")


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True,
                          check=True).stdout


def repository(root: Path) -> Path:
    """An empty repo on `main` that checks files out byte for byte."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "user.name", "foreign bytes test")
    git(root, "config", "user.email", "fb@example.test")
    return root


def _has_head(root: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root,
                          capture_output=True).returncode == 0


def _ident(role: bytes, name: bytes, when: int) -> bytes:
    return role + b" " + name + b" <" + role + b"@example.test> %d +0000\n" % when


def _change(path: bytes, body: bytes) -> bytes:
    return b"M 100644 inline " + path + b"\ndata %d\n" % len(body) + body + b"\n"


def _header(author: bytes, committer: bytes, age_days: float, encoding: bytes | None) -> bytes:
    when = int(time.time() - age_days * DAY)
    header = b"commit refs/heads/main\n" + _ident(b"author", author, when) + _ident(b"committer", committer, when)
    return header + (b"encoding " + encoding + b"\n" if encoding else b"")


def commit(root: Path, files: dict[bytes, bytes] | None = None, *, author: bytes = b"a",
           committer: bytes = b"c", message: bytes = b"edit", encoding: bytes | None = None,
           age_days: float = 0, deletes: tuple[bytes, ...] = ()) -> str:
    """One commit on main whose bytes git keeps as given, checked out; its sha."""
    header = _header(author, committer, age_days, encoding)
    parent = b"from refs/heads/main^0\n" if _has_head(root) else b""
    changes = [_change(path, body) for path, body in (files or {}).items()]
    changes += [b"D " + path + b"\n" for path in deletes]
    git(root, "fast-import", "--quiet", stdin=b"".join(
        [header, b"data %d\n" % len(message), message, b"\n", parent, *changes, b"\n"]))
    git(root, "reset", "-q", "--hard", "main")
    return git(root, "rev-parse", "HEAD").decode().strip()


def scored_repo(root: Path, source: bytes = APP, **first) -> Path:
    """A repo with src/app.py, the stand-in lane and one commit (`first` its
    commit fields); nothing measured yet."""
    repository(root)
    commit(root, {**SCAFFOLD, b"src/app.py": source}, **first)
    return root


def clean(res: subprocess.CompletedProcess) -> bool:
    """No traceback and no codec error on stderr: the class's one symptom."""
    return not any(mark in res.stderr for mark in CODEC_ERRORS)


def shown(res: subprocess.CompletedProcess) -> str:
    return f"exit {res.returncode}\n--- stdout ---\n{res.stdout}\n--- stderr ---\n{res.stderr}"


def answered(res: subprocess.CompletedProcess, *codes: int) -> None:
    """The command ended in one of `codes` with no traceback."""
    assert res.returncode in (codes or (0,)) and clean(res), shown(res)


def rpc(msg_id: int, method: str, params: dict | None = None) -> bytes:
    frame = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        frame["params"] = params
    return json.dumps(frame).encode("ascii")


INITIALIZE = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "0"}})


def _collect(stream, lines: list) -> None:
    with stream:
        lines.extend(stream)


def mcp_session(repo: Path, frames: list[bytes], awaited: set[int], *,
                env_extra: dict | None = None) -> tuple[dict, subprocess.CompletedProcess]:
    """Frames written to a real `crapkit mcp` as bytes; stdin stays open until
    every id in `awaited` has its reply, or the server exits, then closes.
    Every reply by id, and the finished process: a server that ended the
    session early shows as a missing id with its exit and stderr beside it."""
    with tempfile.TemporaryFile() as err:
        process = subprocess.Popen([*CRAPKIT, "mcp", "--repo", str(repo)], cwd=repo, env=child_env(env_extra),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        lines: list[bytes] = []
        threading.Thread(target=_collect, args=(process.stdout, lines), daemon=True).start()
        process.stdin.write(b"".join(frame + b"\n" for frame in frames))
        process.stdin.flush()
        hang_guard.wait_until(lambda: awaited <= _ids(lines) or process.poll() is not None,
                              what=f"replies to ids {sorted(awaited)}")
        process.stdin.close()
        code = hang_guard.exited(process)
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    replies = {reply.get("id"): reply for reply in map(json.loads, list(lines))}
    return replies, subprocess.CompletedProcess(process.args, code, b"".join(lines).decode("utf-8", "replace"),
                                                stderr)


def _ids(lines: list[bytes]) -> set:
    return {json.loads(line).get("id") for line in list(lines)}


def tool_text(reply: dict) -> str:
    return "".join(part.get("text", "") for part in reply["result"]["content"])


LINUX = sys.platform.startswith("linux")
WINDOWS = os.name == "nt"
