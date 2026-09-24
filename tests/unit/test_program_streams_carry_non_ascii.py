"""A program reading or writing crapkit's streams gets UTF-8 under any encoding setting.

Four surfaces carry a path another program parses, and here the path is
pkg/café.py: `worklist --json` and `coverage --github` on stdout, the MCP
server's frames both ways, and the Claude Code hook's payload on stdin.
PYTHONIOENCODING, PYTHONUTF8 and the locale pick a Python child's stream codec
before crapkit starts, and a client such as the MCP TypeScript SDK or Claude
Code writes raw UTF-8 (JSON.stringify never escapes `é`). Every row reads the
bytes strictly: stdout decodes as UTF-8, and the hook and the server answer
about the file the client named.

Before the measurement owner pinned its channel (_process_owner._utf8_channel),
PYTHONIOENCODING=utf-16 broke `coverage --github` on both OSes and every MCP
tool call on Linux; test_owner_channel_ignores_pythonioencoding.py holds that
fix's rows. The other settings always worked, and these rows keep them working
with a non-ASCII path in every payload. A Latin-1 locale on Linux
(LANG=en_US.ISO-8859-1) needs a locale the CI image lacks and is the git path
decode's row, not this one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard
import mcp_stdio

FILE = "pkg/café.py"
GRADE = '''def grade(score):
    if score > 90:
        return "A"
    elif score > 80:
        return "B"
    elif score > 70:
        return "C"
    elif score > 60:
        return "D"
    return "F"
'''
# One branch more, so the hook sees an edit over the ceiling and owes an advisory.
BIGGER = GRADE.replace('    return "F"', '    if score < 0:\n        return "?"\n    return "F"')
MAKE_COV = f'''import json

entry = {{"start_line": 1, "executed_lines": [1, 2, 4], "missing_lines": [3, 5, 6, 7, 8, 9, 10],
          "summary": {{"covered_lines": 3, "num_statements": 10,
                       "num_branches": 8, "covered_branches": 1}}}}
files = {{{FILE!r}: {{"functions": {{"grade": entry}}, "missing_lines": [3, 5, 6, 7, 8, 9, 10]}}}}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({{"meta": {{"branch_coverage": True}}, "files": files}}, fh)
'''
TOML = """[crapkit]
target = 4

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["pkg"]
"""

KNOBS = ("PYTHONIOENCODING", "PYTHONUTF8", "LANG", "LC_ALL")
ENVS = {
    "no-env": {},
    "PYTHONIOENCODING=utf-8": {"PYTHONIOENCODING": "utf-8"},
    "PYTHONIOENCODING=cp1252": {"PYTHONIOENCODING": "cp1252"},
    "PYTHONIOENCODING=latin-1": {"PYTHONIOENCODING": "latin-1"},
    "PYTHONIOENCODING=ascii": {"PYTHONIOENCODING": "ascii"},
    "PYTHONIOENCODING=utf-16": {"PYTHONIOENCODING": "utf-16"},
    "PYTHONUTF8=0": {"PYTHONUTF8": "0"},
    "PYTHONUTF8=1": {"PYTHONUTF8": "1"},
    # Python coerces the C locale to UTF-8 on Linux; Windows ignores both.
    "LANG=C LC_ALL=C": {"LANG": "C", "LC_ALL": "C"},
}


def _environment(row: str) -> dict:
    """The suite's interpreter first on PATH, so the lane's bare `python`
    finds it, and only the encoding settings the row names."""
    env = {key: value for key, value in os.environ.items() if key not in KNOBS}
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(sys.executable).parent), env.get("PATH"))))
    env.update(ENVS[row])
    return env


def _crapkit(repo: Path, row: str, *args: str, stdin: bytes | None = None):
    return hang_guard.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          env=_environment(row), input=stdin)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture(scope="module")
def cafe_repo(tmp_path_factory) -> Path:
    """One function, grade, over the ceiling in pkg/café.py, scored once."""
    repo = tmp_path_factory.mktemp("streams")
    for rel, text in ((FILE, GRADE), ("make_cov.py", MAKE_COV), ("crapkit.toml", TOML),
                      (".gitignore", ".crapkit/\ncov.json\n")):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    first = _crapkit(repo, "no-env", "coverage")
    assert first.returncode == 0, first.stderr.decode("utf-8", "replace")
    return repo


@pytest.mark.parametrize("row", list(ENVS))
def test_worklist_json_names_the_path_in_utf8(cafe_repo, row):
    result = _crapkit(cafe_repo, row, "worklist", "--json")

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert FILE in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("row", list(ENVS))
def test_github_annotations_name_the_path_in_utf8(cafe_repo, row):
    """Read the way the Actions runner reads a workflow command: one line per
    `\\n`, a trailing `\\r` dropped."""
    result = _crapkit(cafe_repo, row, "coverage", "--github")

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    lines = [line.rstrip(b"\r").decode("utf-8") for line in result.stdout.split(b"\n")]
    assert any(line.startswith("::") and f"file={FILE}," in line for line in lines), lines


@pytest.mark.parametrize("row", list(ENVS))
def test_the_mcp_server_reads_and_answers_a_non_ascii_path(cafe_repo, row):
    frames = "".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "get_function_brief", "arguments": {"path": FILE, "name": "grade"}}},
    ))

    result = mcp_stdio.run([sys.executable, "-m", "crapkit", "mcp", "--repo", str(cafe_repo)],
                           cwd=cafe_repo, frames=frames, env=_environment(row), encoding="utf-8")

    replies = {message["id"]: message for message in map(json.loads, result.stdout.splitlines())}
    assert "error" not in replies[2], replies[2]
    assert replies[2]["result"]["isError"] is False, replies[2]
    assert FILE in json.dumps(replies[2]["result"]["structuredContent"], ensure_ascii=False)


@pytest.mark.parametrize("row", list(ENVS))
def test_the_claude_hook_reads_a_non_ascii_payload(cafe_repo, row):
    """An edit that lifts grade further over the ceiling: the hook owes the
    model one advisory line naming the file, and exit 2."""
    source = cafe_repo / FILE
    payload = {"session_id": "s", "transcript_path": str(cafe_repo / "t.jsonl"),
               "cwd": str(cafe_repo), "hook_event_name": "PostToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": str(source), "old_string": "a", "new_string": "b"},
               "tool_response": {"filePath": str(source), "success": True}}
    source.write_text(BIGGER, encoding="utf-8", newline="\n")
    try:
        result = _crapkit(cafe_repo, row, "claude-hook", "--protocol", "1",
                          stdin=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    finally:
        source.write_text(GRADE, encoding="utf-8", newline="\n")

    said = result.stderr.decode("utf-8").splitlines()
    assert result.returncode == 2, said
    assert said and FILE in said[0], said
