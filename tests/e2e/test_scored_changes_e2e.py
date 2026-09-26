"""`scored_changes` beside `stale`, and explain's `--tests` and `--history`, judged by content.

`stale` says whether the ranked run's commit is HEAD, and 0.8.1 keeps that
meaning. It never said whether the numbers still describe the files on disk: an
uncommitted rewrite of a scored function left it false, so an agent that
followed it never refreshed, and an amend that moved no byte set it true.
`scored_changes` rides beside it: how many files the run scored now hold other
content than the run recorded, or null when the run recorded none. The rows
below port the loops that found this. Each builds a repo, runs `coverage`,
makes one change, and reads the payloads.

explain's two line-keyed answers had the same flaw. `--tests` read per-test
contexts off any artifact on disk while the dark lines beside them were
withheld as stale, and `--history` asked `git log -L` about HEAD's lines with a
span the run measured on the working tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _touch(path: Path) -> None:
    """A new mtime on the same bytes."""
    later = path.stat().st_mtime + 120
    os.utime(path, (later, later))


def _age_all(repo: Path) -> None:
    """Every file's mtime moved into the past, as time passing would, so a
    later touch lands on another mtime and git's stat cache has to decide."""
    past = time.time() - 30
    for path in repo.rglob("*"):
        if ".git" not in path.parts and path.is_file():
            os.utime(path, (past, past))
    subprocess.run(["git", "update-index", "--refresh"], cwd=repo, capture_output=True)


def _new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return git_init_repo(repo)


def _json(repo: Path, *args: str) -> dict:
    res = run_cli(repo, *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def _measure(repo: Path) -> None:
    res = run_cli(repo, "coverage")
    assert res.returncode == 0, res.stdout + res.stderr


# --- a Python scope measured by a stub coveragepy lane -----------------------
#
# The stub reads the source on disk, so a rerun measures whatever the file holds
# now, and it records one test id per function as that line's context.

MAKE_COV = '''import ast, json, pathlib

files = {}
for path in sorted(pathlib.Path("src").rglob("*.py")):
    functions, contexts = {}, {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef):
            lines = list(range(node.lineno, node.end_lineno + 1))
            functions[node.name] = {"start_line": node.lineno, "executed_lines": lines,
                                    "missing_lines": [],
                                    "summary": {"covered_lines": len(lines),
                                                "num_statements": len(lines),
                                                "num_branches": 2, "covered_branches": 1}}
            for line in lines:
                contexts[str(line)] = ["tests/test_app.py::test_" + node.name + "|run"]
    files[path.as_posix()] = {"functions": functions, "missing_lines": [], "contexts": contexts}

with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True, "show_contexts": True}, "files": files}, fh)
'''

PY_CONFIG = """[crapkit]
target = 3
worklist_floor = 1

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["src"]
full_suite = false
"""

APP = ("def classify(n):\n"
       "    if n < 0:\n"
       "        return 'neg'\n"
       "    if n == 0:\n"
       "        return 'zero'\n"
       "    if n > 100:\n"
       "        return 'big'\n"
       "    return 'pos'\n")

SIMPLE = "def classify(n):\n    return 'pos' if n > 0 else 'neg'\n"


def _py_repo(tmp_path: Path, files: dict | None = None) -> Path:
    repo = _new_repo(tmp_path)
    for rel, text in {"crapkit.toml": PY_CONFIG, "make_cov.py": MAKE_COV,
                      ".gitignore": ".crapkit/\ncov.json\n__pycache__/\n",
                      **(files or {"src/app.py": APP})}.items():
        _write(repo, rel, text)
    git_commit_all(repo, "init")
    return repo


def _uncommitted_edit(repo: Path) -> None:
    _write(repo, "src/app.py", SIMPLE)


def _touch_app(repo: Path) -> None:
    _age_all(repo)
    _touch(repo / "src" / "app.py")


def _empty_commit(repo: Path) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", "nothing")


def _amend_message_only(repo: Path) -> None:
    _git(repo, "commit", "-q", "--amend", "-m", "reworded")


def _commit_outside_every_scope(repo: Path) -> None:
    _write(repo, "README.md", "docs\n")
    git_commit_all(repo, "docs only")


def _nothing(repo: Path) -> None:
    return None


# variation -> (change after the run, stale, whether a scored file changed).
# `stale` keeps its 0.8.x meaning in every row: the run's commit is not HEAD.
QUEUE_ROWS = {
    "clean": (_nothing, False, False),
    "touch": (_touch_app, False, False),
    "dirty-edit": (_uncommitted_edit, False, True),
    "empty-commit": (_empty_commit, True, False),
    "amend-message-only": (_amend_message_only, True, False),
    "commit-outside-every-scope": (_commit_outside_every_scope, True, False),
}


def _assert_fresh(payload: dict, stale: bool, changed: bool) -> None:
    assert payload["stale"] is stale, payload
    if changed:
        assert payload["scored_changes"] >= 1, payload
    else:
        assert payload["scored_changes"] == 0, payload


@pytest.mark.parametrize("variation", sorted(QUEUE_ROWS))
def test_next_item_counts_scored_files_whose_content_moved_beside_stale(tmp_path, variation):
    change, stale, changed = QUEUE_ROWS[variation]
    repo = _py_repo(tmp_path)
    _measure(repo)
    change(repo)

    _assert_fresh(_json(repo, "next-item"), stale, changed)


def test_a_run_measured_dirty_then_reverted_counts_the_revert(tmp_path):
    """coverage ran on two uncommitted lines above classify, then they were
    reverted: HEAD is the run's commit, and the span the run handed out is two
    lines off the file."""
    repo = _py_repo(tmp_path)
    _write(repo, "src/app.py", "# one\n# two\n" + APP)
    _measure(repo)
    _git(repo, "checkout", "--", "src/app.py")

    out = _json(repo, "next-item")

    assert out["stale"] is False
    assert out["scored_changes"] == 1
    assert (out["item"]["start"], out["item"]["end"]) == (3, 10), "the run's span, as measured"


def test_every_payload_that_carries_stale_carries_the_same_count_and_the_refresh(tmp_path):
    repo = _py_repo(tmp_path)
    _measure(repo)
    _uncommitted_edit(repo)

    payloads = {"next-item": _json(repo, "next-item"),
                "brief": _json(repo, "brief", "src/app.py", "classify", "--json"),
                "brief --batch": _json(repo, "brief", "--batch", "1"),
                "worklist": _json(repo, "worklist", "--json")}

    assert {name: p["scored_changes"] for name, p in payloads.items()} == dict.fromkeys(payloads, 1)
    assert {name: p["commands"]["refresh"] for name, p in payloads.items()} == dict.fromkeys(
        payloads, "crapkit coverage --reuse-unchanged")
    assert payloads["brief --batch"]["packets"][0]["scored_changes"] == 1


def test_the_refresh_brings_scored_changes_back_to_zero(tmp_path):
    repo = _py_repo(tmp_path)
    _measure(repo)
    _uncommitted_edit(repo)
    assert _json(repo, "next-item")["scored_changes"] == 1

    _measure(repo)

    assert _json(repo, "next-item")["scored_changes"] == 0


def test_a_deleted_scored_file_counts_as_changed(tmp_path):
    repo = _py_repo(tmp_path, {"src/app.py": APP, "src/gone.py": "def gone(a):\n    return a\n"})
    _measure(repo)
    (repo / "src" / "gone.py").unlink()

    assert _json(repo, "worklist", "--json")["scored_changes"] == 1


def _corrupt_index(repo: Path) -> None:
    """An index git cannot read: every `git diff` against the tree fails."""
    (repo / ".git" / "index").write_bytes(b"not an index\n")


def test_a_corrupt_index_reads_null_and_names_git_s_error(tmp_path):
    """shape-21: git fails while crapkit reads the tree. The count is null,
    neither changed nor unchanged, and the text warning carries git's error."""
    repo = _py_repo(tmp_path)
    _measure(repo)
    _corrupt_index(repo)

    out = _json(repo, "worklist", "--json")
    res = run_cli(repo, "worklist")

    assert (out["stale"], out["scored_changes"]) == (False, None), out
    assert res.returncode == 0, res.stdout + res.stderr
    assert "because git failed" in res.stderr and "index" in res.stderr, res.stderr


def test_the_worklist_warning_names_up_to_three_changed_files(tmp_path):
    names = ["a", "b", "c", "d"]
    repo = _py_repo(tmp_path, {f"src/{n}.py": f"def {n}(x):\n    return x\n" for n in names})
    _measure(repo)
    for n in names:
        _write(repo, f"src/{n}.py", f"def {n}(x):\n    return x + 1\n")

    res = run_cli(repo, "worklist")

    assert res.returncode == 0, res.stderr
    assert ("warning: 4 file(s) changed since run 1 scored them: src/a.py, src/b.py, "
            "src/c.py and 1 more") in res.stderr, res.stderr
    assert "HEAD has moved on" not in res.stderr, "HEAD did not move"


def test_the_worklist_stays_quiet_when_nothing_moved(tmp_path):
    repo = _py_repo(tmp_path)
    _measure(repo)
    _touch_app(repo)

    res = run_cli(repo, "worklist")

    assert res.returncode == 0, res.stderr
    assert "warning:" not in res.stderr, res.stderr


# --- c12: a lane scope and a coverage_optional scope, through brief ----------

TS_APP = """export function dispatch(kind: string): number {
  switch (kind) {
    case "a": return 1;
    case "b": return 2;
    case "c": return 3;
    case "d": return 4;
    case "e": return 5;
    case "f": return 6;
    default: return 0;
  }
}

export function plain(x: number): number {
  if (x > 10) {
    return x;
  }
  return -x;
}
"""

TS_COV = '''import json, os
arts = {"src/app.ts": {"path": "src/app.ts",
    "fnMap": {"0": {"name": "dispatch", "decl": {"start": {"line": 1}},
                    "loc": {"start": {"line": 1}, "end": {"line": 11}}},
              "1": {"name": "plain", "decl": {"start": {"line": 13}},
                    "loc": {"start": {"line": 13}, "end": {"line": 18}}}},
    "f": {"0": 3, "1": 1},
    "statementMap": {"0": {"start": {"line": 3}, "end": {"line": 3}},
                     "1": {"start": {"line": 9}, "end": {"line": 9}},
                     "2": {"start": {"line": 15}, "end": {"line": 15}},
                     "3": {"start": {"line": 17}, "end": {"line": 17}}},
    "s": {"0": 1, "1": 0, "2": 1, "3": 0},
    "branchMap": {"0": {"loc": {"start": {"line": 2}},
                        "locations": [{"start": {"line": 3}}, {"start": {"line": 9}}]},
                  "1": {"loc": {"start": {"line": 14}},
                        "locations": [{"start": {"line": 15}}, {"start": {"line": 17}}]}},
    "b": {"0": [1, 0], "1": [1, 0]}}}
os.makedirs("coverage", exist_ok=True)
with open(os.path.join("coverage", "coverage-final.json"), "w", encoding="utf-8") as fh:
    json.dump(arts, fh)
'''

TS_SCOPE = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]
"""

TS_LANE = """
[[lane]]
name = "unit"
command = "python make_cov.py"
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
"""

WORSE = ('  switch (kind) {',
         '  if (kind === "z") { return 9; }\n  if (kind === "y") { return 8; }\n  switch (kind) {')


def _ts_repo(tmp_path: Path, cc_only: bool) -> Path:
    config = TS_SCOPE + ("coverage_optional = true\n" if cc_only else TS_LANE)
    repo = _new_repo(tmp_path)
    for rel, text in {"crapkit.toml": config, "make_cov.py": TS_COV, "src/app.ts": TS_APP,
                      ".gitignore": ".crapkit/\ncoverage/\n"}.items():
        _write(repo, rel, text)
    git_commit_all(repo, "init")
    _measure(repo)
    return repo


def _ts_edit(repo: Path) -> None:
    path = repo / "src" / "app.ts"
    _write(repo, "src/app.ts", path.read_text(encoding="utf-8").replace(*WORSE))


def _ts_touch(repo: Path) -> None:
    _age_all(repo)
    _touch(repo / "src" / "app.ts")


C12_ROWS = {
    "lane-scope/uncommitted-edit": (False, _ts_edit, False, True),
    "cc-only-scope/uncommitted-edit": (True, _ts_edit, False, True),
    "lane-scope/touch": (False, _ts_touch, False, False),
    "cc-only-scope/touch": (True, _ts_touch, False, False),
    "lane-scope/commit-outside-scope": (False, _commit_outside_every_scope, True, False),
}


@pytest.mark.parametrize("variation", sorted(C12_ROWS))
def test_brief_counts_an_uncommitted_edit_in_a_lane_scope_and_a_cc_only_scope(tmp_path, variation):
    cc_only, change, stale, changed = C12_ROWS[variation]
    repo = _ts_repo(tmp_path, cc_only)
    change(repo)

    out = _json(repo, "brief", "src/app.ts", "dispatch", "--json")

    _assert_fresh(out, stale, changed)
    assert ('kind === "z"' in out["source"]) is (change is _ts_edit), "source is read from disk"


def test_a_commit_outside_every_scope_still_warns_that_head_moved(tmp_path):
    """True as stated: HEAD did move. The count beside it says no scored byte did."""
    repo = _ts_repo(tmp_path, cc_only=False)
    _commit_outside_every_scope(repo)

    res = run_cli(repo, "worklist")

    assert res.returncode == 0, res.stderr
    assert "HEAD has moved on" in res.stderr
    assert "changed since run" not in res.stderr


# --- explain --tests: per-test contexts are withheld with the dark lines -----

HALF_FIRST = ('def half(n):\n    return n / 2\n\n\n'
              'def grade(n):\n    if n > 90:\n        return "a"\n'
              '    if n > 80:\n        return "b"\n    return "c"\n')
GRADE_FIRST = ('def grade(n):\n    if n > 90:\n        return "a"\n'
               '    if n > 80:\n        return "b"\n    return "c"\n\n\n'
               'def half(n):\n    return n / 2\n')


def _swap_committed(repo: Path) -> None:
    _write(repo, "src/mod.py", GRADE_FIRST)
    git_commit_all(repo, "swap")


def _swap_dirty(repo: Path) -> None:
    _write(repo, "src/mod.py", GRADE_FIRST)


@pytest.mark.parametrize("variation", ["control", "swap-committed", "swap-dirty"])
def test_explain_tests_never_credits_a_test_the_stale_lane_recorded_for_other_lines(
        tmp_path, variation):
    repo = _py_repo(tmp_path, {"src/mod.py": HALF_FIRST})
    _measure(repo)
    {"control": _nothing, "swap-committed": _swap_committed, "swap-dirty": _swap_dirty}[
        variation](repo)
    assert run_cli(repo, "inventory").returncode == 0

    fn = _json(repo, "explain", "src/mod.py", "grade", "--tests", "--json")["functions"][0]

    if variation == "control":
        assert fn["tests"] == ["tests/test_app.py::test_grade"]
        return
    assert fn["tests"] is None, fn
    assert fn["tests_note"] == fn["uncovered_lines_note"], fn
    assert fn["tests_note"].startswith("lane 'py': ") and "src/mod.py" in fn["tests_note"], fn


def test_explain_tests_text_prints_the_stale_note_instead_of_a_test(tmp_path):
    repo = _py_repo(tmp_path, {"src/mod.py": HALF_FIRST})
    _measure(repo)
    _swap_dirty(repo)
    assert run_cli(repo, "inventory").returncode == 0

    res = run_cli(repo, "explain", "src/mod.py", "grade", "--tests")

    assert res.returncode == 0, res.stderr
    assert "covered by" not in res.stdout, res.stdout
    assert "  tests: lane 'py': " in res.stdout, res.stdout


# --- explain --history: the run's span, mapped onto HEAD's lines -------------

HISTORY_CONFIG = """[crapkit]
target = 6

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]
"""

V1 = "def a(n):\n    return n\n\n\ndef b(n):\n    return n + 1\n"


def _history_repo(tmp_path: Path) -> Path:
    repo = _new_repo(tmp_path)
    _write(repo, "crapkit.toml", HISTORY_CONFIG)
    _write(repo, ".gitignore", ".crapkit/\n")
    module = _write(repo, "pkg/m.py", V1)
    git_commit_all(repo, "init")
    module.write_text(V1.replace("return n + 1", "return n + 2"), encoding="utf-8", newline="\n")
    git_commit_all(repo, "edit b only")
    module.write_text(module.read_text(encoding="utf-8").replace("    return n\n",
                                                                 "    return n * 1\n", 1),
                      encoding="utf-8", newline="\n")
    git_commit_all(repo, "edit a only")
    return repo


def _pad(repo: Path, lines: int) -> None:
    module = repo / "pkg" / "m.py"
    padding = "".join(f"# pad {i}\n" for i in range(lines))
    module.write_text(padding + module.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")


def _history(repo: Path, name: str) -> dict:
    assert run_cli(repo, "inventory").returncode == 0
    return _json(repo, "explain", "pkg/m.py", name, "--history", "--json")["functions"][0]


@pytest.mark.parametrize("pad", [0, 4, 40], ids=["clean", "dirty-shift", "dirty-past-end"])
def test_explain_history_lists_the_function_s_own_commits_under_uncommitted_lines(tmp_path, pad):
    repo = _history_repo(tmp_path)
    _pad(repo, pad)

    fn = _history(repo, "a")

    assert [c["subject"] for c in fn["commits"]] == ["edit a only", "init"], fn


def test_explain_history_names_a_span_head_holds_no_line_of(tmp_path):
    repo = _history_repo(tmp_path)
    module = repo / "pkg" / "m.py"
    module.write_text(module.read_text(encoding="utf-8") + "\n\ndef c(n):\n    return -n\n",
                      encoding="utf-8", newline="\n")

    fn = _history(repo, "c")

    assert fn["commits"] is None, fn
    assert fn["commits_note"] == ("pkg/m.py:9-10 holds only uncommitted lines, so no commit "
                                  "has touched it yet"), fn


def test_explain_history_text_prints_the_note_for_a_file_staged_before_its_first_commit(tmp_path):
    repo = _history_repo(tmp_path)
    _write(repo, "pkg/new.py", "def fresh(n):\n    return n\n")
    _git(repo, "add", "pkg/new.py")
    assert run_cli(repo, "inventory").returncode == 0

    res = run_cli(repo, "explain", "pkg/new.py", "fresh", "--history")

    assert res.returncode == 0, res.stderr
    assert ("  commits: pkg/new.py:1-2 holds only uncommitted lines, so no commit has "
            "touched it yet\n") in res.stdout, res.stdout


# --- the MCP tools hand out the CLI's own answer -----------------------------
#
# Each tool spawns the CLI command it maps to, so the rows compare the tool's
# structuredContent with that command spawned the same way on the same tree.

spawned = cli_runner(spawn=True, timeout=180, encoding="utf-8", errors="replace")

MCP_ROWS = {
    "get_next_item": ({}, ("next-item",)),
    "get_function_brief": ({"path": "src/app.py", "name": "classify"},
                           ("brief", "src/app.py", "classify", "--json")),
    "list_worklist": ({}, ("worklist", "--json")),
}


def _tool(repo: Path, name: str, arguments: dict) -> dict:
    frame = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}})
    res = spawned(repo, "mcp", "--repo", str(repo), stdin=frame + "\n")
    assert res.returncode == 0, res.stderr
    result = json.loads(res.stdout.strip().splitlines()[-1])["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


@pytest.mark.parametrize("tool", sorted(MCP_ROWS))
def test_each_mcp_tool_carries_the_count_and_the_refresh_the_cli_prints(tmp_path, tool):
    arguments, argv = MCP_ROWS[tool]
    repo = _py_repo(tmp_path)
    _measure(repo)
    _uncommitted_edit(repo)
    res = spawned(repo, *argv)
    assert res.returncode == 0, res.stderr
    cli = json.loads(res.stdout)

    out = _tool(repo, tool, arguments)

    assert "scored_changes" in out, out
    assert (out["stale"], out["scored_changes"]) == (cli["stale"], cli["scored_changes"])
    assert out["commands"]["refresh"] == "crapkit coverage --reuse-unchanged"


def test_an_empty_queue_over_an_uncommitted_regression_does_not_meet_the_stop_rule(tmp_path):
    """The stop rule reads `empty: true` with `scored_changes: 0`. A run whose
    queue was finished, then an uncommitted edit that puts a ccn-4 function
    over the ceiling: the queue still reads empty and `stale` false, so only
    the count keeps the loop from stopping on numbers the edit made false."""
    repo = _py_repo(tmp_path, {"src/app.py": SIMPLE})
    _measure(repo)
    done = _json(repo, "next-item")
    assert (done["empty"], done["scored_changes"]) == (True, 0), done

    _write(repo, "src/app.py", APP)
    out = _json(repo, "next-item")

    assert (out["empty"], out["stale"], out["scored_changes"]) == (True, False, 1), out
