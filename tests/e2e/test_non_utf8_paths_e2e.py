"""A file git names in bytes that are not UTF-8, driven through the CLI.

One such name, tracked, staged or untracked, ended init, inventory, coverage,
verify, doctor and the pre-commit gate with a traceback. Each row now ends one
of three ways:

- a name the scope's own assignment takes (scope path, language extension,
  exclude) refuses with exit 3, in one line naming the escaped path and git mv,
  and no other line about it, in every command that assigns files to scopes;
- a tracked or staged name no scope takes is left out with one warning line,
  listed in `unreadable_names` under --json, and the command goes on;
- an untracked one is a change like any other: lane reuse reruns for it and
  nothing is left out;
- a UTF-8 name, accented, CJK, emoji, quoted by git or not, reads as itself.

Repos are built through `git update-index --index-info`, the one route that puts
a name that is not UTF-8 into the index on every OS: a Windows argv cannot carry
one, and Git for Windows checks such a file out under its UTF-8 spelling or not
at all. Rows that need the name on disk run on POSIX.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import pytest

from conftest import cli_runner
from hang_guard import HANG_SECONDS
from name_bytes import NOT_UTF8_NAMES, STORES_ANY_BYTE

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")

POSIX_NAME = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a POSIX file system that stores any byte in a name")
WINDOWS_CHECKOUT = pytest.mark.skipif(
    sys.platform != "win32", reason="needs Git for Windows, which checks a Latin-1 name out as UTF-8")

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true

[exclude]
globs = ["make_cov.py"]
"""
LANE_CONFIG = """[crapkit]
target = 6

[crapkit.scoped_tests]
src = "python make_cov.py"

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[exclude]
globs = ["make_cov.py"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["src"]
full_suite = false
inputs = ["src", "make_cov.py"]
"""
# The same lane with no declared inputs: its reuse proof reads the whole
# tree's status, untracked files included.
WHOLE_TREE_LANE_CONFIG = LANE_CONFIG.replace('inputs = ["src", "make_cov.py"]\n', "")
# A coverage.py JSON report for src/app.py, written by the lane command itself
# so the lane runs in milliseconds and needs no test runner.
MAKE_COV = (
    "import json\n"
    "open('runs.log', 'a', encoding='ascii').write('run\\n')\n"
    "report = {'meta': {'branch_coverage': True}, 'files': {'src/app.py': {'functions': {\n"
    "    'f': {'start_line': 1, 'executed_lines': [1, 2], 'missing_lines': [],\n"
    "          'summary': {'covered_lines': 2, 'num_statements': 2,\n"
    "                      'num_branches': 0, 'covered_branches': 0}}}}}}\n"
    "open('cov.json', 'w', encoding='utf-8').write(json.dumps(report))\n"
).encode()
SOURCE = b"def f(x):\n    return x\n"
TANGLED = ("def tangled(a, b, c, d):\n"
           + "".join(f"    if {v}:\n        a += 1\n" for v in ("a", "b", "c", "d", "a > b", "b > c", "c > d"))
           + "    return a  # café\n")
REFUSAL = "is in scope 'src', but git names it in bytes that are not UTF-8"
FIX = "rename it (git mv) to a UTF-8 name"
LEFT_OUT = "crapkit: left out "

CLAIMED = [
    ("unquoted-latin1", b"src/caf\xe9.py"),
    ("git-quotes-it", b'src/q"\xff.py'),
    ("cp1252-smart-quote", b"src/o\x92brien.py"),
]
# Staged, git quotes this one for its double quotes and names the byte in octal.
STAGED_CLAIMED = CLAIMED + [("latin1-and-quotes", b'src/caf\xe9"q".py')]
UNCLAIMED = [
    ("docs-txt", b"docs/caf\xe9.txt"),
    ("docs-md", b"docs/caf\xe9.md"),
    ("repo-root-txt", b"caf\xe9.txt"),
    ("outside-scope-resume", b"docs/r\xe9sum\xe9.txt"),
    ("outside-scope-path-py", b"tools/caf\xe9.py"),
    ("no-scope-language", b"src/caf\xe9.txt"),
    ("test-directory", b"src/tests/caf\xe9.py"),
]
VALID = [
    # id, name, whether this OS can hold the name on disk
    ("valid-accent", "src/café.py", True),
    ("cjk", "src/日本.py", True),
    ("emoji", "src/\U0001f680.py", True),
    ("accent-space-cjk", "src/café 李.py", True),
    ("quote-and-accent", 'src/q"é.py', sys.platform != "win32"),
    ("double-quote", 'src/q".py', sys.platform != "win32"),
    ("tab", "src/a\tb.py", sys.platform != "win32"),
    ("newline", "src/a\nb.py", sys.platform != "win32"),
]


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True, check=True,
                          timeout=HANG_SECONDS).stdout


def _on_disk(repo: Path, name: bytes, body: bytes) -> None:
    """The file in the working tree too, when this OS can name it."""
    try:
        path = repo / os.fsdecode(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    except (UnicodeDecodeError, OSError):
        pass  # Windows: no such name on NTFS; git's index holds it all the same


def _stage(repo: Path, files: dict[bytes, bytes]) -> None:
    """Each file staged under its name, bytes as given. Git for Windows refuses
    a `"` in a name it would check out (core.protectNTFS); an index a Linux
    commit filled holds one all the same, so the check is off here."""
    for name, body in files.items():
        blob = _git(repo, "hash-object", "-w", "--stdin", stdin=body).strip()
        _git(repo, "-c", "core.protectNTFS=false", "update-index", "--add", "-z", "--index-info",
             stdin=b"100644 " + blob + b"\t" + name + b"\0")
        _on_disk(repo, name, body)


def _commit(repo: Path, files: dict[bytes, bytes], message: str) -> None:
    _stage(repo, files)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "--no-verify",
         "-m", message)


def _repo(tmp_path: Path, config: str = CONFIG, files: dict[bytes, bytes] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "false")
    _commit(repo, {b"crapkit.toml": config.encode(), b".gitignore": b".crapkit/\ncov.json\nruns.log\n",
                   b"src/app.py": SOURCE, b"make_cov.py": MAKE_COV, **(files or {})}, "base")
    return repo


def _shown(name: bytes) -> str:
    return name.decode("utf-8", "backslashreplace")


def _refused(result, name: bytes) -> None:
    """Exit 3, one line naming the escaped path and git mv, and no left-out
    line beside it that says the opposite."""
    assert result.returncode == 3, result.stdout + result.stderr
    named = [line for line in result.stderr.splitlines() if _shown(name) in line]
    assert len(named) == 1, result.stderr
    assert named[0].startswith(f"crapkit: {_shown(name)} {REFUSAL}") and FIX in named[0], result.stderr
    assert LEFT_OUT not in result.stderr and "Traceback" not in result.stderr


def _left_out(result, name: bytes, codes=(0,)) -> None:
    """The command's own exit, and one warning line naming the file."""
    assert result.returncode in codes, result.stdout + result.stderr
    assert result.stderr.count(f"{LEFT_OUT}{_shown(name)}:") == 1, result.stderr
    assert REFUSAL not in result.stderr and "Traceback" not in result.stderr


# --- a name the scope takes: exit 3 ------------------------------------------------

@pytest.mark.parametrize("name", [row[1] for row in CLAIMED], ids=[row[0] for row in CLAIMED])
@pytest.mark.parametrize("command", ["inventory", "coverage", "doctor"])
def test_a_scoped_name_in_the_first_commit_is_refused(tmp_path, name, command):
    repo = _repo(tmp_path, files={name: SOURCE})

    _refused(run_cli(repo, command), name)


@pytest.mark.parametrize("name", [row[1] for row in CLAIMED], ids=[row[0] for row in CLAIMED])
@pytest.mark.parametrize("command", ["inventory", "coverage", "verify", "doctor"])
def test_a_scoped_name_committed_since_the_base_is_refused(tmp_path, name, command):
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {name: SOURCE}, "add a Latin-1 name")

    _refused(run_cli(repo, command), name)


@pytest.mark.parametrize("name", [row[1] for row in STAGED_CLAIMED], ids=[row[0] for row in STAGED_CLAIMED])
@pytest.mark.parametrize("command", ["hook-precommit", "inventory"])
def test_a_scoped_name_staged_is_refused_and_the_gate_never_passes_it(tmp_path, name, command):
    """The gate used to leave such a staged ccn-8 function out and exit 0."""
    repo = _repo(tmp_path)
    _stage(repo, {name: TANGLED.encode()})

    result = run_cli(repo, command)

    _refused(result, name)
    assert (result.stdout, len(result.stderr.splitlines())) == ("", 1), result.stdout + result.stderr


# hook-precommit judges a staged claimed name through the gate module, which
# refuses it before it judges anything: the hook exits 3 on the 0.8.1 line
# (probed at bug-utf8-author a0f9c6f6) before any other line and any override
# side effect.
MARKS = "crapkit-ratchet.tsv"
CLAIMED_STOP = ("is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit reads every path "
                "as UTF-8; a file a scope takes is refused, not left out, so no gate passes it unread: rename it "
                "(git mv) to a UTF-8 name")
# A TypeScript arrow body the reader refuses, so the staged file is unread.
REFUSED_ARROW = b"export const old = {\n  pick: ({ x }) => new Set<string>([x]).has(x),\n};\n"
TS_CONFIG = CONFIG.replace('languages = ["python"]', 'languages = ["python", "typescript"]')


def _index(repo: Path) -> bytes:
    return _git(repo, "ls-files", "-s", "-z")


@pytest.mark.parametrize("name", [row[1] for row in STAGED_CLAIMED], ids=[row[0] for row in STAGED_CLAIMED])
def test_an_override_reason_grants_nothing_on_a_staged_claimed_name(tmp_path, name):
    """CRAPKIT_OVERRIDE_REASON changes nothing: exit 3 on the same line, no
    marks file written or staged, and the index as it was."""
    repo = _repo(tmp_path)
    _stage(repo, {name: TANGLED.encode()})
    index = _index(repo)

    result = run_cli(repo, "hook-precommit", env_extra={"CRAPKIT_OVERRIDE_REASON": "hotfix, ticket 7"})

    _refused(result, name)
    assert (result.stdout, len(result.stderr.splitlines())) == ("", 1), result.stdout + result.stderr
    assert not (repo / MARKS).exists()
    assert _index(repo) == index
    assert MARKS not in _git(repo, "diff", "--cached", "--name-only").decode()


@pytest.mark.parametrize("claimed", [True, False], ids=["claimed", "control"])
def test_a_claimed_name_beside_an_unread_file_and_a_breach_prints_only_its_line(tmp_path, claimed):
    """The control, with no claimed name, shows the unread file and the
    breach each refuse the commit; beside a claimed name only its line shows."""
    repo = _repo(tmp_path, TS_CONFIG)
    files = {b"src/old.ts": REFUSED_ARROW, b"src/tangled.py": TANGLED.encode()}
    _stage(repo, {**files, **({b"src/caf\xe9.py": SOURCE} if claimed else {})})

    result = run_cli(repo, "hook-precommit")

    if claimed:
        _refused(result, b"src/caf\xe9.py")
        assert (result.stdout, len(result.stderr.splitlines())) == ("", 1), result.stdout + result.stderr
    else:
        assert result.returncode == 6, result.stdout + result.stderr
        assert "src/old.ts" in result.stdout and "tangled( a , b , c , d )" in result.stdout, result.stdout


def test_two_staged_claimed_names_print_one_line_naming_the_first(tmp_path):
    repo = _repo(tmp_path)
    _stage(repo, {b"src/o\x92brien.py": SOURCE, b"src/caf\xe9.py": TANGLED.encode()})

    result = run_cli(repo, "hook-precommit")

    assert result.returncode == 3, result.stdout + result.stderr
    assert (result.stdout, result.stderr) == ("", f"crapkit: src/caf\\xe9.py (and 1 more) {CLAIMED_STOP}\n")


# verify judges a claimed name through the gate module, which refuses it before
# it judges anything: verify stops before any lane runs, stores no run, and gives
# the stop as a verdict. The exit stays 3 and stderr the line the 0.8.1 tree
# printed (probed at bug-utf8-author a0f9c6f6); --json prints a verify payload
# whose findings hold one unreadable_name item per name, --sarif writes a result
# and --github an annotation for each, and an override grants nothing.
VERIFY_STOP = ("{shown} is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit reads every "
               "path as UTF-8; a file a scope takes is refused, not left out, so no gate passes it unread: rename "
               "it (git mv) to a UTF-8 name")
VERIFY_FLAGS = {"plain": (), "json": ("--json",), "override": ("--override", "reason"),
                "sarif": ("--sarif", "out.sarif"), "github": ("--github",)}
# An override with no alert_command is refused before any other check, so the
# repo names one; verify stops before it would run.
ALERTING_LANE_CONFIG = LANE_CONFIG.replace(
    "[crapkit]\n", "[crapkit]\nalert_command = 'python -c \"import sys; sys.stdin.read()\"'\n", 1)


def _stored_runs(repo: Path) -> int:
    with closing(sqlite3.connect(repo / ".crapkit" / "crap.sqlite")) as db:
        return db.execute("SELECT count(*) FROM runs").fetchone()[0]


def _stop_item(name: bytes) -> dict:
    """The name's findings item. NTFS and APFS refuse such a name, so the
    commit leaves no file on disk and git reads it deleted: dirty. ext4 holds
    the file, and git reads it clean."""
    return {"kind": "unreadable_name", "fails": True, "exit_code": 3, "overridable": False,
            "dirty": not STORES_ANY_BYTE, "rule": "unreadable name", "path": _shown(name), "scope": "src",
            "reason": VERIFY_STOP.format(shown=_shown(name))}


def _plain(repo: Path, result, name: bytes) -> None:
    assert result.stdout == "", result.stdout


def _as_json(repo: Path, result, name: bytes) -> None:
    """A verify payload, not an error object: its one finding is the name's
    unreadable_name item, and nothing was measured."""
    printed = json.loads(result.stdout)
    assert printed["findings"] == [_stop_item(name)], printed["findings"]
    assert (printed["ok"], printed["run_id"], "error" in printed) == (False, None, False)
    assert printed["counts"]["diff_uncovered_count"] == 0


def _overridden(repo: Path, result, name: bytes) -> None:
    """Grants nothing: the refusal names the file and the rename."""
    assert result.stdout == "", result.stdout
    assert result.stderr.splitlines()[1:] == [
        f"override refused: 1 unreadable name ({_shown(name)}) never qualifies for an override; {FIX}"]


def _sarif(repo: Path, result, name: bytes) -> None:
    """One result whose uri percent-encodes the name's own bytes."""
    (found,) = json.loads((repo / "out.sarif").read_text(encoding="utf-8"))["runs"][0]["results"]
    assert (found["ruleId"], found["level"]) == ("crapkit/unreadable-name", "error")
    assert found["locations"][0]["physicalLocation"] == {
        "artifactLocation": {"uri": quote(name, safe="/")}, "region": {"startLine": 1}}
    assert found["message"]["text"] == VERIFY_STOP.format(shown=_shown(name))


def _github(repo: Path, result, name: bytes) -> None:
    (line,) = result.stdout.splitlines()
    named = _shown(name).replace("%", "%25").replace(":", "%3A").replace(",", "%2C")
    assert line.startswith(f"::error file={named},line=1,title=crapkit/unreadable-name::"), line


VERIFY_CHECKS = {"plain": _plain, "json": _as_json, "override": _overridden, "sarif": _sarif, "github": _github}


@pytest.mark.parametrize("flags", VERIFY_FLAGS, ids=VERIFY_FLAGS.keys())
@pytest.mark.parametrize("name", [row[1] for row in CLAIMED], ids=[row[0] for row in CLAIMED])
def test_verify_stops_on_a_claimed_name_before_any_lane_runs(tmp_path, name, flags):
    """Exit 3 and the 0.8.1 stderr line first, byte for byte, and nothing after
    it but an override's refusal; the lane's log and the store's runs
    untouched; each output names the file as its own finding."""
    repo = _repo(tmp_path, ALERTING_LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    runs, stored = _runs(repo), _stored_runs(repo)
    _commit(repo, {name: SOURCE}, "add a Latin-1 name")

    result = run_cli(repo, "verify", *VERIFY_FLAGS[flags])

    stop = f"crapkit: {VERIFY_STOP.format(shown=_shown(name))}\n"
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stderr.startswith(stop), result.stderr
    assert len(result.stderr.splitlines()) == (2 if flags == "override" else 1), result.stderr
    assert (_runs(repo), _stored_runs(repo)) == (runs, stored)
    VERIFY_CHECKS[flags](repo, result, name)


def test_verify_names_the_first_of_two_claimed_names_and_counts_the_other(tmp_path):
    """Two items, under one stderr line naming the first."""
    repo = _repo(tmp_path, LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    runs = _runs(repo)
    _commit(repo, {b"src/o\x92brien.py": SOURCE, b"src/caf\xe9.py": SOURCE}, "add two Latin-1 names")

    result = run_cli(repo, "verify", "--json")

    first = _shown(b"src/caf\xe9.py") + " (and 1 more)"
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stderr == f"crapkit: {VERIFY_STOP.format(shown=first)}\n", result.stderr
    assert json.loads(result.stdout)["findings"] == [_stop_item(b"src/caf\xe9.py"), _stop_item(b"src/o\x92brien.py")]
    assert _runs(repo) == runs


def test_a_second_coverage_with_a_lane_stamp_refuses_a_scoped_name(tmp_path):
    repo = _repo(tmp_path, LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {b"src/caf\xe9.py": SOURCE}, "add a Latin-1 name")

    _refused(run_cli(repo, "coverage", "--reuse-unchanged"), b"src/caf\xe9.py")


ARGUMENT_REFUSAL = ("is named in bytes that are not UTF-8, and crapkit reads every path as UTF-8: "
                    "rename it (git mv) to a UTF-8 name")


# A lookup command answers for the one file it is handed, so it refuses such a
# name whether a scope takes it or not. rescore leaves out a name no scope takes.
RENAME_ROWS = [
    pytest.param(name, command, id=f"{kind}-{cid}")
    for kind, name in (("scoped", b"src/caf\xe9.py"), ("unscoped", b"docs/caf\xe9.md"))
    for cid, command in (("rescore", ("rescore",)), ("rescore-gate", ("rescore", "--gate")),
                         ("explain", ("explain",)), ("brief", ("brief",)),
                         ("ratchet-move-to", ("ratchet", "move", "src/app.py")))
    if kind == "scoped" or command[0] != "rescore"
]


@POSIX_NAME
@pytest.mark.parametrize(("name", "command"), RENAME_ROWS)
@NOT_UTF8_NAMES
def test_a_command_handed_a_name_that_is_not_utf8_names_the_rename(tmp_path, name, command):
    """A Linux shell hands the name over as its own bytes. rescore answered
    'src/caf\ufffd.py does not exist', a file nobody named, where the file does
    exist and only a rename lets crapkit read it."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {name: SOURCE}, "add a Latin-1 name")
    tail = ("f",) if command[0] in ("explain", "brief") else ()

    result = run_cli(repo, *command, os.fsdecode(name), *tail)

    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stderr == f"crapkit: {_shown(name)} {ARGUMENT_REFUSAL}\n", result.stderr


@POSIX_NAME
@pytest.mark.parametrize("command", [("rescore",), ("rescore", "--gate")], ids=["rescore", "rescore-gate"])
@NOT_UTF8_NAMES
def test_rescore_leaves_out_a_name_no_scope_takes_with_one_line(tmp_path, command):
    """hook-precommit leaves such a staged file out, and rescore --gate
    refused the same file at exit 3."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {b"docs/caf\xe9.md": SOURCE}, "add a Latin-1 name")

    result = run_cli(repo, *command, os.fsdecode(b"docs/caf\xe9.md"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ("crapkit: left out docs/caf\\xe9.md: its name is not UTF-8 and no "
                             "scope takes it, so nothing in it is scored\n"), result.stderr


# The argument a caller hands for a file whose name is not UTF-8: on POSIX the
# surrogateescape spelling of b"src/caf\xe9.py", on NTFS a lone UTF-16 surrogate.
# Either OS keeps such a file on disk, and no lookup can key it.
UNREADABLE_ARGUMENT = "src/caf\udce9.py"
UNREAD_FILE = {"path": "src/caf\\xe9.py",
               "reason": "its name is not UTF-8, and crapkit reads every path as UTF-8: "
                         "rename it (git mv) to a UTF-8 name"}


def _measured_with_an_unreadable_file(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    (repo / UNREADABLE_ARGUMENT).write_text(TANGLED, encoding="utf-8")
    return repo


@NOT_UTF8_NAMES
def test_the_gates_error_object_lists_the_name_in_unread_files(tmp_path):
    """rescore --gate still exits 3 with its one stderr line; --json adds the
    name, the reason and `dirty` as `unread_files`, the shape a gate verdict
    lists. Nobody added the file, so it is dirty."""
    repo = _measured_with_an_unreadable_file(tmp_path)

    result = run_cli(repo, "rescore", "--gate", UNREADABLE_ARGUMENT, "--json")

    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stderr == f"crapkit: src/caf\\xe9.py {ARGUMENT_REFUSAL}\n", result.stderr
    assert json.loads(result.stdout)["error"]["unread_files"] == [{**UNREAD_FILE, "dirty": True}]


def _check_gate_reply(repo: Path, path: str) -> dict:
    """check_gate's result over `crapkit mcp`, the way a host calls it."""
    frames = "\n".join(json.dumps(frame) for frame in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "check_gate", "arguments": {"path": path}}}))

    result = run_cli(repo, "mcp", stdin=frames)

    return json.loads(result.stdout.splitlines()[-1])["result"]


@NOT_UTF8_NAMES
def test_check_gate_answers_such_a_name_with_a_failed_verdict(tmp_path):
    """check_gate answered isError true with the exit-3 error object: no
    verdict, no finding. Under a uv-built venv on Windows it answered `src/caf\ufffd.py does not
    exist`, because the child it spawned read the name off argv as U+FFFD. It
    speaks MCP, so it returns the gate's refusal as a verdict that fails, with
    the name in unread_files and the baseline every verdict names."""
    repo = _measured_with_an_unreadable_file(tmp_path)

    reply = _check_gate_reply(repo, UNREADABLE_ARGUMENT)

    assert reply["isError"] is False, reply
    gate = reply["structuredContent"]["gate"]
    assert (gate["ok"], gate["judged"], gate["breaches"]) == (False, 0, []), gate
    assert gate["unread_files"] == [{**UNREAD_FILE, "dirty": True}]
    assert {"baseline_run", "baseline_commit", "note"} <= set(reply["structuredContent"])


@pytest.mark.parametrize("name", ["docs/caf\udce9.md", "tools/caf\udce9.py", "src/caf\udce9.txt"],
                         ids=["docs-md", "outside-scope-path-py", "no-scope-language"])
@NOT_UTF8_NAMES
def test_check_gate_judges_a_name_no_scope_takes_as_any_unscoped_file(tmp_path, name):
    """A name no scope takes is skipped. check_gate failed the gate on it."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(TANGLED, encoding="utf-8")

    reply = _check_gate_reply(repo, name)

    assert reply["isError"] is False, reply
    gate = reply["structuredContent"]["gate"]
    assert (gate["ok"], gate["judged"], gate["unread_files"]) == (True, 0, []), gate


@pytest.mark.parametrize("command", ["inventory", "coverage", "verify"])
@NOT_UTF8_NAMES
def test_a_refused_scoped_name_is_listed_in_the_error_objects_unread_files(tmp_path, command):
    """The scan's refusal names the first file on stderr and counts the rest;
    --json lists each one. Committed as they are on POSIX, both are clean; Git
    for Windows cannot check either name out, so git reads both deleted.
    verify's stop prints its own payload, with one unreadable_name finding
    per name."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {b"src/caf\xe9.py": SOURCE, b"src/o\x92brien.py": SOURCE}, "add two Latin-1 names")

    result = run_cli(repo, command, "--json")

    assert result.returncode == 3, result.stdout + result.stderr
    dirty = sys.platform == "win32"
    printed = json.loads(result.stdout)
    if command == "verify":
        names = [item for item in printed["findings"] if item["kind"] == "unreadable_name"]
        assert [(item["path"], item["dirty"], item["reason"]) for item in names] == [
            (path, dirty, VERIFY_STOP.format(shown=path)) for path in ("src/caf\\xe9.py", "src/o\\x92brien.py")]
    else:
        assert printed["error"]["unread_files"] == [
            {**UNREAD_FILE, "dirty": dirty}, {**UNREAD_FILE, "path": "src/o\\x92brien.py", "dirty": dirty}]


# --- a name no scope takes: left out, one line ---------------------------------------

@pytest.mark.parametrize("name", [row[1] for row in UNCLAIMED], ids=[row[0] for row in UNCLAIMED])
@pytest.mark.parametrize("command", ["inventory", "coverage", "verify", "doctor"])
def test_a_name_no_scope_takes_is_left_out_with_one_line(tmp_path, name, command):
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {name: SOURCE}, "add a Latin-1 name")

    _left_out(run_cli(repo, command), name)


@pytest.mark.parametrize("name", [row[1] for row in CLAIMED + UNCLAIMED],
                         ids=[row[0] for row in CLAIMED + UNCLAIMED])
def test_worklist_reads_the_churn_of_a_commit_that_adds_such_a_name(tmp_path, name):
    """worklist lists no files; its churn walk reads the name as U+FFFD
    (7565b2d), so it answers from the stored run whatever the name."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {name: SOURCE}, "add a Latin-1 name")

    result = run_cli(repo, "worklist")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr and REFUSAL not in result.stderr


@pytest.mark.parametrize("name", ["café.txt", "docs/café 李雷 \U0001f600.txt"],
                         ids=["root-accent", "docs-accent-cjk-emoji"])
@pytest.mark.parametrize("command", ["inventory", "coverage", "doctor", "worklist", "hook-precommit"])
def test_a_utf8_name_no_scope_takes_is_read_without_a_word(tmp_path, name, command):
    """Committed for the commands that list the tree, staged for the gate."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    if command == "hook-precommit":
        _stage(repo, {name.encode(): SOURCE})
    else:
        _commit(repo, {name.encode(): SOURCE}, "add a UTF-8 name")

    result = run_cli(repo, command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert LEFT_OUT not in result.stderr and REFUSAL not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("name", [row[1] for row in UNCLAIMED], ids=[row[0] for row in UNCLAIMED])
def test_the_gate_leaves_out_a_staged_name_no_scope_takes(tmp_path, name):
    repo = _repo(tmp_path)
    _stage(repo, {name: TANGLED.encode()})

    _left_out(run_cli(repo, "hook-precommit"), name)


@pytest.mark.parametrize("name, then", [(b"src/caf\xe9.py", 3), (b"docs/caf\xe9.md", 0)],
                         ids=["scoped", "unscoped"])
def test_init_leaves_a_name_out_and_writes_the_config(tmp_path, name, then):
    """No scope exists before init writes one, so init warns and writes the
    config; the first command that assigns files under it decides the claim."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {b"src/app.py": SOURCE, name: SOURCE}, "base")

    _left_out(run_cli(repo, "init"), name)
    assert (repo / "crapkit.toml").is_file()
    assert run_cli(repo, "inventory").returncode == then


@pytest.mark.parametrize("command", [("inventory", "--json"), ("coverage", "--json"), ("verify", "--json")],
                         ids=["inventory", "coverage", "verify"])
def test_a_left_out_name_is_listed_in_the_json_payload(tmp_path, command):
    """`unreadable_names` lists what the stderr line names, escaped the same
    way, so a wrapper reading --json sees the skip."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {b"docs/caf\xe9.md": SOURCE, b"tools/r\xe9sum\xe9.txt": SOURCE}, "add two Latin-1 names")

    result = run_cli(repo, *command)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["unreadable_names"] == ["docs/caf\\xe9.md", "tools/r\\xe9sum\\xe9.txt"]


@pytest.mark.parametrize("command", [("inventory", "--json"), ("coverage", "--json"), ("verify", "--json")],
                         ids=["inventory", "coverage", "verify"])
def test_a_readable_tree_lists_no_unreadable_name(tmp_path, command):
    repo = _repo(tmp_path, files={"docs/café.md".encode(): SOURCE})
    assert run_cli(repo, "coverage").returncode == 0

    result = run_cli(repo, *command)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["unreadable_names"] == []


# --- untracked names: a change, never left out (POSIX) --------------------------------

def _runs(repo: Path) -> int:
    return (repo / "runs.log").read_text(encoding="ascii").count("run")


@POSIX_NAME
@pytest.mark.parametrize("name", [b"caf\xe9.log", b"src/caf\xe9.py", b"src/caf\xe9.txt"],
                         ids=["repo-root-log", "would-be-scoped-py", "scope-txt"])
@pytest.mark.parametrize("command", ["coverage", "verify"])
def test_an_untracked_name_is_neither_left_out_nor_refused(tmp_path, name, command):
    """git lists untracked files for the lane's whole-tree reuse proof and for
    verify's dirty set; no scope assignment takes an untracked file, and
    nothing drops one, so no line names it."""
    repo = _repo(tmp_path, WHOLE_TREE_LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    _on_disk(repo, name, SOURCE)

    result = run_cli(repo, command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert LEFT_OUT not in result.stderr and REFUSAL not in result.stderr and "Traceback" not in result.stderr


@POSIX_NAME
@NOT_UTF8_NAMES
def test_an_untracked_name_reruns_a_whole_tree_lane_and_the_reason_names_it(tmp_path):
    """The whole-tree proof read the tree as clean and reused the lane."""
    repo = _repo(tmp_path, WHOLE_TREE_LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    _on_disk(repo, b"caf\xe9.log", b"x")

    result = run_cli(repo, "coverage", "--reuse-unchanged")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _runs(repo) == 2, result.stderr
    assert "the working tree has 1 uncommitted change(s): caf\\xe9.log" in result.stderr, result.stderr


def _untracked_input(repo: Path, name: bytes) -> None:
    _on_disk(repo, name, b"x")


def _committed_input(repo: Path, name: bytes) -> None:
    _commit(repo, {name: b"x"}, "add an input")


# Where a lane's input comes from: untracked on disk (POSIX), or committed since
# the lane's stamp (every OS, through the index), each beside its controls.
INPUT_CHANGES = [
    pytest.param(_untracked_input, b"src/caf\xe9.txt", id="untracked-latin1",
                 marks=(POSIX_NAME, NOT_UTF8_NAMES)),
    pytest.param(_untracked_input, b"src/cafe.txt", id="untracked-ascii"),
    pytest.param(_untracked_input, "src/café.txt".encode(), id="untracked-utf8"),
    pytest.param(_committed_input, b"src/caf\xe9.txt", id="committed-latin1"),
    pytest.param(_committed_input, b"src/cafe.txt", id="committed-ascii"),
    pytest.param(_committed_input, "src/café.txt".encode(), id="committed-utf8"),
]


@pytest.mark.parametrize("add_input, name", INPUT_CHANGES)
def test_a_new_file_under_a_lanes_inputs_reruns_the_lane(tmp_path, add_input, name):
    """The Latin-1 name read as no change, and the lane was reused
    with 'measurement inputs unchanged', where its ASCII and UTF-8 twins rerun."""
    repo = _repo(tmp_path, LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    add_input(repo, name)

    result = run_cli(repo, "coverage", "--reuse-unchanged")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _runs(repo) == 2, result.stderr
    assert "reusing without rerun" not in result.stderr, result.stderr
    assert REFUSAL not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("name", [b"src/caf\xe9.txt", "src/café.txt".encode()], ids=["latin1", "utf8"])
def test_doctor_reads_an_input_under_any_name_without_a_word(tmp_path, name):
    """doctor's inputs check lists the files under a lane's inputs; a name
    that is not UTF-8 there matches the entry like any other."""
    repo = _repo(tmp_path, LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    _commit(repo, {name: b"x"}, "add an input")

    result = run_cli(repo, "doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count(LEFT_OUT) == (1 if name == b"src/caf\xe9.txt" else 0), result.stderr
    assert REFUSAL not in result.stderr and "Traceback" not in result.stderr


# --- UTF-8 names: read as themselves ---------------------------------------------------

@pytest.mark.parametrize("quotepath", ["false", "true"], ids=["quotepath-off", "quotepath-on"])
@pytest.mark.parametrize("name, on_disk", [row[1:] for row in VALID], ids=[row[0] for row in VALID])
def test_a_utf8_name_is_scored_and_gated_as_itself(tmp_path, name, on_disk, quotepath):
    repo = _repo(tmp_path)
    _git(repo, "config", "core.quotePath", quotepath)
    if on_disk:
        _commit(repo, {name.encode(): SOURCE}, "add a UTF-8 name")
        result = run_cli(repo, "coverage", "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        summary = json.loads(result.stdout)
        assert (summary["files"], summary["functions"]) == (2, 2), summary
        assert LEFT_OUT not in result.stderr
    _stage(repo, {name.encode(): TANGLED.encode()})

    gate = run_cli(repo, "hook-precommit")
    assert gate.returncode == 6, gate.stderr
    assert "tangled" in gate.stdout and LEFT_OUT not in gate.stderr


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8-sig", "utf-16"], ids=["cp1252", "utf8-bom", "utf16"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_staged_body_in_any_encoding_under_a_utf8_name_is_gated(tmp_path, encoding, newline):
    """The body is bytes the scorer decodes by its own rule; only the name is
    held to UTF-8."""
    repo = _repo(tmp_path)
    _stage(repo, {b"src/body.py": TANGLED.replace("\n", newline).encode(encoding)})

    gate = run_cli(repo, "hook-precommit")
    assert gate.returncode == 6, gate.stderr
    assert "src/body.py" in gate.stdout and LEFT_OUT not in gate.stderr


@WINDOWS_CHECKOUT
def test_a_rename_away_from_a_latin1_name_on_windows_is_gated_under_the_new_name(tmp_path):
    """Git for Windows checks src/caf\\xe9.py out as src/café.py. `git add -A`
    stages the rename, and the gate judges the file under its UTF-8 name."""
    repo = _repo(tmp_path, files={b"src/caf\xe9.py": SOURCE})
    _git(repo, "reset", "-q", "--hard")
    assert (repo / "src" / "café.py").is_file()
    (repo / "src" / "café.py").write_text(TANGLED, encoding="utf-8")
    _git(repo, "add", "-A")

    gate = run_cli(repo, "hook-precommit")
    assert gate.returncode == 6, gate.stderr
    assert "src/café.py" in gate.stdout and REFUSAL not in gate.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="needs NTFS, which holds a name with a lone UTF-16 surrogate")
@pytest.mark.parametrize("command", ["inventory", "coverage", "verify"])
def test_an_untracked_name_with_a_lone_surrogate_on_windows_reads_as_git_spells_it(tmp_path, command):
    """Git for Windows lists such a name with U+FFFD, which is UTF-8, so
    nothing is left out or refused."""
    repo = _repo(tmp_path, WHOLE_TREE_LANE_CONFIG)
    assert run_cli(repo, "coverage").returncode == 0
    (repo / "src" / "bad\udcff.py").write_bytes(SOURCE)

    result = run_cli(repo, command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert LEFT_OUT not in result.stderr and REFUSAL not in result.stderr and "Traceback" not in result.stderr
