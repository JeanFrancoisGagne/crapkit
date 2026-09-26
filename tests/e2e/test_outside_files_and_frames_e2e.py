"""Files and frames crapkit did not write, fed through the command that reads
each one, in every encoding a real writer leaves.

A junit report declares its own encoding, and a runner on a legacy code page
writes ISO-8859-1 or UTF-16. A coverage report can open with a BOM. An MCP
client can write a frame in its ANSI code page. A pull request can touch a file
whose name is not UTF-8. A pytest.ini, a hook payload, an alert command's
answer and a plugin's JSON can each hold bytes crapkit never chose. Each read
used to assume UTF-8 somewhere, and the reds among these rows ended a command
with a traceback, or ended an MCP session in silence.

One parametrized test per site, each row a variation the utf8-author hunt ran:
the red ones with the green controls beside them.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import cli_runner
from foreign_bytes import (APP, INITIALIZE, LANE_SCRIPT, SCAFFOLD, TOML, answered, clean, commit, git,
                           mcp_session, repository, rpc, run_bytes, scored_repo, shown, tool_text)

run_cli = cli_runner(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]


# --- a lane's junit report: lanes._results_summary, _retested_passes, doctor --tune
#
# utf8-author-shape-10, -11, -12 and -boundary-8. The stand-in lane writes a
# coverage report and a junit report whose one test is test_café, encoded the
# way the row's runner would: ISO-8859-1 and UTF-16 each declared in the XML,
# a UTF-8 BOM, CRLF, plain UTF-8, and test names in CJK and emoji.

JUNIT_TAIL = '''
import sys
variant = sys.argv[1]
failed = Path("outcome.txt").read_text().strip() == "fail"
name = "test_café_李雷_😀" if variant == "utf8-cjk" else "test_café"
encoding = {"latin1": "ISO-8859-1", "utf16": "UTF-16"}.get(variant, "utf-8")
failure = '<failure message="boom">boom</failure>' if failed else ""
text = ('<?xml version="1.0" encoding="%s"?>\\n<testsuites><testsuite name="pytest" errors="0" '
        'failures="%d" skipped="0" tests="1" time="0.5"><testcase classname="tests.test_app" '
        'name="%s" time="0.25">%s</testcase></testsuite></testsuites>\\n') % (encoding, failed, name, failure)
data = {"latin1": lambda: text.encode("latin-1"), "utf16": lambda: text.encode("utf-16"),
        "bom": lambda: b"\\xef\\xbb\\xbf" + text.encode(),
        "crlf": lambda: text.replace("\\n", "\\r\\n").encode()}.get(variant, text.encode)()
Path("junit.xml").write_bytes(data)
sys.exit(1 if failed else 0)
'''.encode("utf-8")

JUNIT_VARIANTS = ["latin1", "utf16", "bom", "crlf", "utf8", "utf8-cjk"]


def _junit_toml(variant: str, retest: str | None = None) -> bytes:
    retest_line = f'retest_command = "python lane.py {retest}"\n' if retest else ""
    return (TOML.replace(b'command = "python lane.py"', b'command = "python lane.py %s"' % variant.encode())
            + b'results_artifact = "junit.xml"\n' + retest_line.encode())


def _junit_repo(root: Path, variant: str, retest: str | None = None) -> Path:
    repository(root)
    commit(root, {**SCAFFOLD, b"crapkit.toml": _junit_toml(variant, retest), b"lane.py": LANE_SCRIPT + JUNIT_TAIL,
                  b".gitignore": b".crapkit/\ncov.json\njunit.xml\n", b"outcome.txt": b"pass\n",
                  b"src/app.py": APP})
    return root


def _emit(repo: Path, variant: str) -> None:
    """The report a run of the row's runner leaves on disk, written without crapkit."""
    subprocess.run([sys.executable, "lane.py", variant], cwd=repo, check=True, capture_output=True)


@pytest.mark.parametrize("variant", JUNIT_VARIANTS)
def test_a_junit_report_in_any_declared_encoding_is_read_as_a_report(tmp_path, variant):
    """coverage, verify --reuse-artifacts and coverage --reuse-artifacts all
    read the report through _results_summary (utf8-author-shape-10, -boundary-8)."""
    fresh = _junit_repo(tmp_path / "fresh", variant)
    answered(run_cli(fresh, "coverage"))

    reused = _junit_repo(tmp_path / "reused", "utf8")
    answered(run_cli(reused, "coverage"))
    _emit(reused, variant)
    answered(run_cli(reused, "verify", "--reuse-artifacts"))
    answered(run_cli(reused, "coverage", "--reuse-artifacts"))


@pytest.mark.parametrize("variant", JUNIT_VARIANTS)
def test_a_flake_retest_report_in_any_declared_encoding_still_decides_exit_8(tmp_path, variant):
    """The main run writes UTF-8 and the retest writes the row's encoding
    (utf8-author-shape-11): the test still fails, so verify exits 8."""
    repo = _junit_repo(tmp_path / "repo", "utf8", retest=variant)
    answered(run_cli(repo, "coverage"))
    commit(repo, {b"outcome.txt": b"fail\n"}, message=b"break it")

    res = run_cli(repo, "verify")

    answered(res, 8)
    assert "NEW FAILURE" in res.stdout + res.stderr


@pytest.mark.parametrize("stamped", [False, True], ids=["no-lane-stamp", "lane-stamp"])
@pytest.mark.parametrize("variant", JUNIT_VARIANTS)
def test_doctor_tune_reads_a_junit_report_in_any_declared_encoding(tmp_path, variant, stamped):
    """With no stamp doctor --tune costs the lane from the report's own time
    (utf8-author-shape-12, -boundary-8); with one it never opens the report.
    The stamp comes from a UTF-8 run, and the row's report is written after."""
    repo = _junit_repo(tmp_path / "repo", "utf8")
    if stamped:
        answered(run_cli(repo, "coverage"))
    _emit(repo, variant)

    res = run_cli(repo, "doctor", "--tune")

    answered(res)
    assert "doctor --tune" in res.stdout


# --- a lane's coverage report: covstream's strict decoder -------------------
#
# utf8-author-shape-13 and -boundary-20. A report crapkit cannot decode is a
# lane refusal that names the lane, exit 5, never a traceback; a raw UTF-8 key
# naming src/café.* is a report.

COV_LANE = b'''import json, os, sys
from pathlib import Path
parser, variant = sys.argv[1], sys.argv[2]
name = "src/caf\\u00e9" if variant in ("raw-utf8-path", "latin1-path-key") else "src/app"
if parser == "coveragepy":
    fn = {"start_line": 1, "executed_lines": [1, 2], "missing_lines": [],
          "summary": {"covered_lines": 2, "num_statements": 2, "num_branches": 0, "covered_branches": 0}}
    report = {"meta": {"branch_coverage": True}, "files": {name + ".py": {"functions": {"f": fn}}}}
else:
    path = os.path.join(os.getcwd(), *(name + ".ts").split("/"))
    loc = {"start": {"line": 1}, "end": {"line": 1}}
    report = {path: {"path": path, "fnMap": {"0": {"name": "f", "decl": loc, "loc": loc}}, "f": {"0": 1},
                     "branchMap": {}, "b": {}, "statementMap": {"0": loc}, "s": {"0": 1}}}
ascii_text = json.dumps(report)
data = {"ascii": lambda: ascii_text.encode(),
        "raw-utf8-path": lambda: json.dumps(report, ensure_ascii=False).encode("utf-8"),
        "bom": lambda: b"\\xef\\xbb\\xbf" + ascii_text.encode(),
        "utf16": lambda: ascii_text.encode("utf-16"),
        "latin1-path-key": lambda: json.dumps(report, ensure_ascii=False).encode("latin-1")}[variant]()
Path("cov.json").write_bytes(data)
'''

COV_SOURCES = {"coveragepy": ("python", ".py", b"def f(x):\n    return x\n"),
               "istanbul": ("typescript", ".ts", b"export function f(a: number) { return a; }\n")}

COV_ROWS = [(parser, variant, code) for parser in COV_SOURCES
            for variant, code in (("bom", 5), ("utf16", 5), ("latin1-path-key", 5),
                                  ("raw-utf8-path", 0), ("ascii", 0))]


def _cov_repo(root: Path, parser: str, variant: str) -> Path:
    language, suffix, body = COV_SOURCES[parser]
    toml = (b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["%s"]\n\n'
            b'[[lane]]\nname = "unit"\ncommand = "python cov_lane.py %s %s"\nartifact = "cov.json"\n'
            b'parser = "%s"\nscopes = ["src"]\n' % (language.encode(), parser.encode(), variant.encode(),
                                                   parser.encode()))
    repository(root)
    commit(root, {b"crapkit.toml": toml, b"cov_lane.py": COV_LANE, b".gitignore": b".crapkit/\ncov.json\n",
                  b"src/app" + suffix.encode(): body, "src/café".encode() + suffix.encode(): body})
    return root


@pytest.mark.parametrize("parser, variant, code", COV_ROWS, ids=[f"{p}-{v}" for p, v, _ in COV_ROWS])
def test_a_coverage_report_crapkit_cannot_decode_is_a_lane_refusal(tmp_path, parser, variant, code):
    repo = _cov_repo(tmp_path / "repo", parser, variant)

    res = run_cli(repo, "coverage")

    answered(res, code)
    assert (code == 5) is ("lane 'unit'" in res.stderr), shown(res)


# --- MCP stdin frames: _mcp_stdio._lines ----------------------------------------
#
# utf8-author-shape-16, -boundary-14, -history-11 and -shape-34. One frame
# holding a byte that is not UTF-8 ended the session in silence, and a BOM
# before the first frame cost `initialize` its reply. Every session here ends
# with ping 9, so a session the frame ended shows as a missing 9.

def _ping(msg_id: int, note: bytes) -> bytes:
    return b'{"jsonrpc":"2.0","id":%d,"method":"ping","params":{"note":"%s"}}' % (msg_id, note)


def _call(msg_id: int, tool: str, arguments: dict, encoding: str = "ascii") -> bytes:
    frame = {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
             "params": {"name": tool, "arguments": arguments}}
    return json.dumps(frame, ensure_ascii=encoding == "ascii").encode(encoding if encoding != "ascii" else "utf-8")


CAFE = "src/café.py"
HISTORY = {"path": CAFE, "name": "pick", "history": True}


def _frame_row(row_id, frames, answered_ids, *, first=INITIALIZE, env=None, tool_ok=False):
    return pytest.param(first, frames, answered_ids, env or {}, tool_ok, id=row_id)


FRAME_ROWS = [
    _frame_row("invalid-utf8-inside-json-string", [_ping(2, b"caf\xe9")], {1, 2, 9}),
    _frame_row("invalid-utf8-junk-line", [b"\xff\xfe junk"], {1, 9}),
    _frame_row("utf8-bom-before-first-request", [], {1, 9}, first=b"\xef\xbb\xbf" + INITIALIZE),
    _frame_row("utf8-bom-before-a-later-request", [b"\xef\xbb\xbf" + rpc(2, "tools/list")], {1, 2, 9}),
    _frame_row("cp1252-path-in-arguments", [_call(2, "get_function_history", HISTORY, "cp1252")], {1, 2, 9}),
    _frame_row("notification-holding-0xff",
               [b'{"jsonrpc":"2.0","method":"notifications/progress","params":{"x":"\xff"}}'], {1, 9}),
    _frame_row("junk-ascii-line-control", [b"junk"], {1, 9}),
    _frame_row("junk-valid-utf8-control", ["not json at all, café".encode()], {1, 9}),
    _frame_row("crlf-line-endings", [_ping(2, b"x") + b"\r"], {1, 2, 9}),
    _frame_row("valid-non-ascii-arguments", [_call(2, "get_function_brief", {"path": CAFE, "name": "pick"}, "utf-8")],
               {1, 2, 9}, tool_ok=True),
    _frame_row("valid-non-ascii-request-under-pythonioencoding-cp1252",
               [_call(2, "get_function_brief", {"path": CAFE, "name": "pick"}, "utf-8")], {1, 2, 9},
               env={"PYTHONIOENCODING": "cp1252"}, tool_ok=True),
    _frame_row("child-output-non-ascii-reply", [_call(2, "get_function_history", HISTORY, "utf-8")], {1, 2, 9},
               env={"PYTHONIOENCODING": "cp1252"}, tool_ok=True),
]


@pytest.fixture(scope="module")
def mcp_repo(tmp_path_factory) -> Path:
    repo = scored_repo(tmp_path_factory.mktemp("mcp") / "repo")
    commit(repo, {CAFE.encode(): APP})
    answered(run_cli(repo, "coverage"))
    return repo


@pytest.mark.parametrize("first, frames, answered_ids, env, tool_ok", FRAME_ROWS)
def test_a_frame_in_any_bytes_leaves_the_mcp_session_reading(mcp_repo, first, frames, answered_ids, env, tool_ok):
    replies, done = mcp_session(mcp_repo, [first, *frames, rpc(9, "ping")], answered_ids, env_extra=env)

    assert set(replies) == answered_ids, shown(done)
    assert done.returncode == 0 and clean(done), shown(done)
    if tool_ok:
        assert replies[2]["result"]["isError"] is False, tool_text(replies[2])
        # The reply is ASCII JSON, so the name reads back once it is parsed.
        assert CAFE in json.dumps(json.loads(tool_text(replies[2])), ensure_ascii=False)


# --- the Action's changed-file list: tools/action/comment.py ----------------
#
# utf8-author-shape-17, -boundary-15 and -history-10. action.yml writes
# `git diff --name-only "$BASE_SHA...HEAD" -z`, which names each path in the
# bytes git stores. A name that is not UTF-8 failed the comment step, so the
# pull request got no comment. The rows assert only that the comment is built.

def _diff_argv(base: str) -> list[str]:
    command = next(line.strip().split(" > ")[0]
                   for line in (ROOT / "action.yml").read_text(encoding="utf-8").splitlines()
                   if 'diff --name-only "$BASE_SHA...HEAD"' in line)
    return [part.replace("$BASE_SHA", base) for part in shlex.split(command)]


PR_ROWS = [
    pytest.param(b"src/caf\xe9.py", False, id="changed-path-invalid-utf8"),
    pytest.param(b"docs/caf\xe9.txt", False, id="changed-unscored-path-invalid-utf8"),
    pytest.param(b"docs/r\xe9sum\xe9.md", False, id="changed-path-latin1-resume"),
    pytest.param(b"src/o\x92brien.py", False, id="changed-path-cp1252-smart-quote"),
    pytest.param(b"src/caf\xe9.py", True, id="deleted-in-pr-path-invalid-utf8"),
    pytest.param("src/café.py".encode(), False, id="changed-path-valid-accent"),
    pytest.param("docs/café 李.txt".encode(), False, id="changed-path-valid-accent-cjk-space"),
    pytest.param("src/日本\U0001f680.py".encode(), False, id="changed-path-cjk-emoji"),
]


def _pull_request(root: Path, path: bytes, deleted: bool) -> str:
    """A base commit and a head commit that adds, or deletes, `path`; the base sha."""
    repository(root)
    commit(root, {**SCAFFOLD, b"src/app.py": APP, **({path: APP} if deleted else {})})
    base = git(root, "rev-parse", "HEAD").decode().strip()
    commit(root, {b"src/app.py": APP + b"# pr\n", **({} if deleted else {path: APP})},
           deletes=(path,) if deleted else ())
    return base


@pytest.mark.parametrize("path, deleted", PR_ROWS)
def test_the_action_builds_its_comment_whatever_bytes_a_changed_name_holds(tmp_path, path, deleted):
    repo = tmp_path / "repo"
    base = _pull_request(repo, path, deleted)
    state = tmp_path / "state"
    state.mkdir()
    (state / "changed.z").write_bytes(subprocess.run(_diff_argv(base), cwd=repo, capture_output=True,
                                                     check=True).stdout)
    assert path in (state / "changed.z").read_bytes()
    coverage = run_cli(repo, "coverage", "--json")
    (state / "worklist.json").write_text(run_cli(repo, "worklist", "--json").stdout, encoding="utf-8")
    (state / "coverage.json").write_text(coverage.stdout, encoding="utf-8")

    built = subprocess.run([sys.executable, str(ROOT / "tools" / "action" / "comment.py"),
                            "--coverage", str(state / "coverage.json"), "--coverage-exit", str(coverage.returncode),
                            "--worklist", str(state / "worklist.json"), "--changed-z", str(state / "changed.z"),
                            "--top", "5", "--out", str(state / "comment.md")], capture_output=True)

    assert built.returncode == 0, built.stderr.decode("utf-8", "replace")
    assert (state / "comment.md").read_text(encoding="utf-8").startswith("<!-- crapkit-action -->")


# --- pytest's own config files: config._pytest_text -------------------------
#
# utf8-author-shape-23 and -boundary-30. The full-suite guard reads
# `testpaths` from pytest.ini, pyproject.toml or setup.cfg to decide whether a
# lane's positional `tests` narrows the suite. Whatever the file's bytes,
# crapkit must read testpaths the way pytest itself does, and never raise.

INI = "[pytest]\ntestpaths = tests\n# généré café\n"
PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n# généré café\n'
PYTEST_ROWS = [
    pytest.param("pytest.ini", INI.encode(), id="pytest-ini-utf8"),
    pytest.param("pytest.ini", b"\xef\xbb\xbf" + INI.encode(), id="pytest-ini-utf8-bom"),
    pytest.param("pytest.ini", INI.encode("cp1252"), id="pytest-ini-cp1252"),
    pytest.param("pytest.ini", INI.replace("\n", "\r\n").encode(), id="pytest-ini-crlf"),
    pytest.param("pyproject.toml", PYPROJECT.encode(), id="pyproject-utf8"),
    pytest.param("pyproject.toml", b"\xef\xbb\xbf" + PYPROJECT.encode(), id="pyproject-utf8-bom"),
    pytest.param("pyproject.toml", PYPROJECT.encode("cp1252"), id="pyproject-cp1252"),
    pytest.param("setup.cfg", b"\xef\xbb\xbf" + INI.replace("[pytest]", "[tool:pytest]").encode(),
                 id="setup-cfg-utf8-bom"),
]

GUARDED_LANE = (b'[[lane]]\nname = "py"\n'
                b'command = "python -m pytest -q -p no:cacheprovider --cov --cov-report=json:cov.json tests"\n'
                b'artifact = "cov.json"\nparser = "coveragepy"\nscopes = ["src"]\n')


def _pytest_testpaths(repo: Path) -> bool | None:
    """pytest's own answer: its collection header names the testpaths it used.
    None when pytest refuses the config file, so no lane runs on it at all."""
    probe = subprocess.run([sys.executable, "-m", "pytest", "--co", "-q", "-v", "-p", "no:cacheprovider"],
                           cwd=repo, capture_output=True)
    if probe.returncode not in (0, 5):
        return None
    return "testpaths: tests" in probe.stdout.decode("utf-8", "replace")


@pytest.mark.parametrize("name, body", PYTEST_ROWS)
def test_the_full_suite_guard_reads_testpaths_as_pytest_does(tmp_path, name, body):
    repo = repository(tmp_path / "repo")
    toml = b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    commit(repo, {b"crapkit.toml": toml + GUARDED_LANE, name.encode(): body, b"src/app.py": APP,
                  b"tests/test_app.py": b"def test_a():\n    assert 1\n", b".gitignore": b".crapkit/\ncov.json\n"})

    res = run_cli(repo, "inventory")
    honoured = _pytest_testpaths(repo)

    assert clean(res), shown(res)
    # pytest stops on a cp1252 or BOM config before it collects, so the lane
    # fails loudly whatever the guard says; the guard only has to read the file.
    assert honoured is None or ("narrows a full-suite" in res.stderr) is not honoured, shown(res)


@pytest.mark.parametrize("body", [INI.encode("cp1252"), b"\xef\xbb\xbf[pytest]\ntestpaths = tests\n", INI.encode()],
                         ids=["cp1252-pytest-ini", "bom-pytest-ini", "utf8-pytest-ini-control"])
def test_init_reads_a_pytest_ini_in_any_bytes(tmp_path, body):
    """utf8-author-boundary-30: pytest refuses the first two, so no lane runs on
    them; init still reads each one without a traceback."""
    repo = repository(tmp_path / "repo")
    commit(repo, {b"pytest.ini": body, b"tests/test_a.py": b"def test_a():\n    assert 1\n",
                  b"src/app.py": APP})

    answered(run_cli(repo, "init"))


# --- the advisory hook's payload: claude_hook._payload ------------------------
#
# utf8-author-shape-27 and -boundary-22. A ccn-8 edit must draw the advisory,
# exit 2 naming the function, whatever the payload or the edited file holds.

BREACH = b"def sprawl(n):\n" + b"".join(b"    if n == %d:\n        n += %d\n" % (i, i) for i in range(1, 8)) + b"    return n\n"
HOOK_TOML = b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "pkg"\npaths = ["pkg"]\nlanguages = ["python"]\n'


def _payload(repo: Path, rel: str, *, ensure_ascii: bool = False, codec: str = "utf-8", extra: bytes = b"") -> bytes:
    body = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(repo),
            "tool_input": {"file_path": str(repo / rel), "old_string": "OLD", "new_string": "x"}}
    return json.dumps(body, ensure_ascii=ensure_ascii).encode(codec).replace(b"OLD", b"OLD" + extra)


def _hook_row(row_id, leaf, *, body=BREACH, env=None, legacy_name=False, **payload):
    return pytest.param(leaf, body, env or {}, legacy_name, payload, id=row_id)


HOOK_ROWS = [
    _hook_row("payload-utf8-ascii-path", "big.py", ensure_ascii=True),
    _hook_row("payload-utf8-raw-accent-path", "café.py"),
    _hook_row("payload-escaped-accent-path", "café.py", ensure_ascii=True),
    _hook_row("payload-cjk-emoji-path", "渡辺\U0001f680.py"),
    _hook_row("edited-file-cp1252-body", "big.py", body=BREACH + b"# caf\xe9\n"),
    _hook_row("non-ascii-name-under-pythonioencoding-cp1252", "café.py", env={"PYTHONIOENCODING": "cp1252"}),
    _hook_row("payload-old-string-holds-0xff", "big.py", extra=b"\xff"),
    _hook_row("repo-holds-a-committed-name-that-is-not-utf8", "big.py", legacy_name=True),
]


@pytest.mark.parametrize("leaf, body, env, legacy_name, payload", HOOK_ROWS)
def test_the_advisory_hook_reads_any_payload_claude_code_sends(tmp_path, leaf, body, env, legacy_name, payload):
    repo = repository(tmp_path / "repo")
    commit(repo, {b"crapkit.toml": HOOK_TOML, b"pkg/plain.py": APP,
                  **({b"docs/caf\xe9.txt": b"x\n"} if legacy_name else {})})
    (repo / "pkg" / leaf).write_bytes(body)

    res = run_bytes(repo, "claude-hook", "--protocol", "1", stdin=_payload(repo, f"pkg/{leaf}", **payload),
                    env_extra=env)

    answered(res, 2)
    assert "sprawl" in res.stderr


@pytest.mark.parametrize("stdin", [
    pytest.param(lambda repo: b"\xef\xbb\xbf" + _payload(repo, "pkg/big.py", ensure_ascii=True), id="payload-utf8-bom"),
    pytest.param(lambda repo: _payload(repo, "pkg/café.py", codec="cp1252"), id="payload-cp1252-bytes"),
])
def test_a_payload_claude_code_never_writes_ends_without_a_traceback(tmp_path, stdin):
    """Unreachable from Claude Code, which writes UTF-8 with no BOM; fed by hand,
    the hook still answers instead of raising."""
    repo = repository(tmp_path / "repo")
    commit(repo, {b"crapkit.toml": HOOK_TOML, b"pkg/plain.py": APP})
    for leaf in ("big.py", "café.py"):
        (repo / "pkg" / leaf).write_bytes(BREACH)

    answered(run_bytes(repo, "claude-hook", "--protocol", "1", stdin=stdin(repo)), 0, 2)


# --- an alert command's answer: override._alert_or_refuse --------------------
#
# utf8-author-boundary-28. The alert line goes to alert_command on stdin as
# UTF-8, and what the command prints comes back into the refusal.

ALERT_OK = b"import sys\nopen('alert.bin', 'wb').write(sys.stdin.buffer.read())\n"
ALERT_REFUSES = (b"import sys\nsys.stdin.buffer.read()\n"
                 b"sys.stdout.buffer.write(b'relay refused: caf\\xe9 \\xff\\n')\nsys.exit(1)\n")
REASON = "urgent café fix 李雷 \U0001f600"


@pytest.mark.parametrize("alert, code", [(ALERT_REFUSES, 1), (ALERT_OK, 0)],
                         ids=["alert-command-writes-invalid-utf8", "alert-command-receives-utf8-control"])
def test_an_override_alert_command_answers_in_any_bytes(tmp_path, alert, code):
    toml = TOML.replace(b"[crapkit]\n", b'[crapkit]\nalert_command = "python alert.py"\n', 1)
    repo = scored_repo(tmp_path / "repo")
    commit(repo, {b"crapkit.toml": toml, b"alert.py": alert})
    answered(run_cli(repo, "coverage"))
    (repo / "src" / "breach.py").write_bytes(BREACH)
    git(repo, "add", "-A")

    res = run_cli(repo, "hook-precommit", env_extra={"CRAPKIT_OVERRIDE_REASON": REASON})

    assert res.returncode != 0 if code else res.returncode == 0, shown(res)
    assert clean(res), shown(res)
    if code:
        assert "relay refused: caf" in res.stderr
    else:
        assert REASON.encode("utf-8") in (repo / "alert.bin").read_bytes()


# --- the plugin's JSON and Claude Code's installed_plugins.json ---------------
#
# utf8-author-boundary-29. doctor --plugin-root reads the plugin tree's JSON
# and, with no path, Claude Code's record of where it installed the plugin.
# Under a directory named with an accent and CJK the verdict is the same.

def _launcher(bin_dir: Path) -> dict:
    """A `crapkit` on PATH that runs this interpreter's crapkit."""
    bin_dir.mkdir()
    if os.name == "nt":
        (bin_dir / "crapkit.cmd").write_text(f'@"{sys.executable}" -m crapkit %*\r\n', encoding="ascii")
    else:
        script = bin_dir / "crapkit"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m crapkit "$@"\n', encoding="utf-8")
        script.chmod(0o755)
    return {"PATH": os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]])}


def _doctor_plugin(tmp_path: Path, parent: str, *, recorded: bool) -> tuple[int, str]:
    import shutil
    plugin = shutil.copytree(ROOT / "plugin", tmp_path / parent / "crapkit-plugin")
    env = _launcher(tmp_path / parent / "bin")
    args = ["doctor", "--plugin-root", str(plugin)]
    if recorded:
        config = tmp_path / parent / "config"
        (config / "plugins").mkdir(parents=True)
        (config / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"crapkit@crapkit": [{"installPath": str(plugin), "version": "x"}]}},
            ensure_ascii=False), encoding="utf-8")
        env["CLAUDE_CONFIG_DIR"], args = str(config), ["doctor", "--plugin-root"]
    res = run_cli(tmp_path, *args, env_extra=env)
    assert clean(res), shown(res)
    return res.returncode, res.stdout.replace(str(plugin), "<PLUGIN>").replace(str(tmp_path / parent), "<DIR>")


@pytest.mark.parametrize("recorded", [False, True], ids=["plugin-root-under-non-ascii-dir",
                                                         "installed-plugins-json-non-ascii-install-path"])
def test_doctor_plugin_root_answers_the_same_under_a_non_ascii_directory(tmp_path, recorded):
    assert _doctor_plugin(tmp_path, "plugin-José 李雷", recorded=recorded) == \
        _doctor_plugin(tmp_path, "plugin-ascii", recorded=recorded)


# --- rows no input reaches ------------------------------------------------------
#
# utf8-author-shape-17, comment.py's _read_text on the payload, base sha and
# base reason files: action.yml fills each one with crapkit's own output (ASCII
# JSON, or the first line of its stderr, which crapkit writes as UTF-8 to a
# file) or with a sentence and a sha of its own, so no byte a repo holds reaches
# that read in any other encoding.
# utf8-author-boundary-8, lanes._still_failed on a Latin-1 or UTF-16 junit
# report: nothing called it, and it is deleted (utf8-author-shape-33). The
# flake retest reads its report through _retested_passes, in the rows above.
