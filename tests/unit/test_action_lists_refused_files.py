"""The Action's comment lists every file an error object refused.

A scan that meets names a scope takes that are not UTF-8 exits 3 with one
stderr line naming the first and counting the rest (`src/caf\\xe9.ts (and 1
more) ...`). `--json` lists every one in the error object's `unread_files`, and
the comment quoted the message alone: a pull request with two such names showed
one and "(and 1 more)", and the job log's stderr line named no more. The
comment now gives each refused file a bullet under the no-verdict line, the way
it gives each unread file a bullet under a failed verdict.

Since 0.9.0 verify meets such a name with a verdict, not an error object: exit 3,
run_id null and one `unreadable_name` item per name in `findings`. The comment
gives each its own bullet under `verify failed, exit 3: unreadable name`. An
error object still renders as the line that wrote no verdict.
"""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from crapkit import universe
from crapkit.cli import main

ROOT = Path(__file__).resolve().parent.parent.parent
BUILDER = ROOT / "tools" / "action" / "comment.py"
REASON = ("its name is not UTF-8, and crapkit reads every path as UTF-8: "
          "rename it (git mv) to a UTF-8 name")


@lru_cache(maxsize=None)
def _builder():
    """Loaded by path, the way the Action runs it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("crapkit_action_comment_refused", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _staged(repo, names: tuple[bytes, ...]) -> None:
    """Each name in the index under its own bytes, the route that works on every OS."""
    for name in names:
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=KNOTTY.encode(),
                              capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-index", "--add", "-z", "--index-info"], cwd=repo,
                       input=b"100644 " + blob + b"\t" + name + b"\0", capture_output=True, check=True)


def test_the_no_verdict_line_lists_every_file_coverage_refused(repo, capsys):
    """The error object comes from the real command, so the reader and the
    writer meet here."""
    seed_artifacts(repo)
    _staged(repo, (b"src/caf\xe9.ts", b"src/o\x92brien.ts"))
    assert main(["coverage", "--reuse-artifacts", "--json", "--repo", str(repo)]) == 3
    coverage = json.loads(capsys.readouterr().out)

    lines = _builder().no_verdict_line(coverage, 3).splitlines()

    assert lines[0].startswith("**no verdict: `crapkit coverage` exited 3 (src/caf\\xe9.ts (and 1 more)")
    assert lines[1:] == ["", f"- refused: `src/caf\\xe9.ts`: {REASON}",
                         f"- refused: `src/o\\x92brien.ts`: {REASON}"]


@pytest.fixture()
def measured(repo, capsys):
    """A trusted baseline run, so verify has a baseline to stop against."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def test_a_claimed_name_reaches_the_comment_as_a_verdict_with_its_own_bullet(measured, capsys):
    """The payload comes from the real command: verify stops before any lane
    runs, so the counts line says nothing was measured."""
    _staged(measured, (b"src/caf\xe9.ts",))
    assert main(["verify", "--reuse-artifacts", "--json", "--repo", str(measured)]) == 3
    verify = json.loads(capsys.readouterr().out)
    sentence = universe.claimed_text([("src/caf\udce9.ts", "src")])

    lines = _builder().verdict_line(verify, 3).splitlines()

    assert lines == ["**verify failed, exit 3: unreadable name.**", "",
                     f"- unreadable name: `src/caf\\xe9.ts`: {sentence}", "",
                     "Nothing was measured against baseline 1: 1 unreadable name, 0 gate violations, "
                     "0 ratchet regressions, 0 new test failures, 0 uncovered changed lines."]


def test_a_verify_config_refusal_at_exit_3_still_wrote_no_verdict():
    """A config error at exit 3 is an error object, not a claimed name: no
    verdict, and no count of anything."""
    verify = {"error": {"exit": 3, "kind": "config", "message": "crapkit.toml: unknown language 'cobol'\n"},
              "schema": 1}

    assert _builder().verdict_line(verify, 3) == (
        "**`crapkit verify` exited 3 and wrote no verdict: crapkit.toml: unknown language 'cobol'.**")


def test_a_verify_that_refused_lists_every_file_under_its_line():
    verify = {"error": {"exit": 3, "kind": "config", "message": "src/a\\xe9.ts (and 1 more) ...",
                        "unread_files": [{"path": "src/a\\xe9.ts", "reason": REASON, "dirty": True},
                                         {"path": "src/b\\xe9.ts", "reason": REASON, "dirty": False}]},
              "schema": 1}

    lines = _builder().verdict_line(verify, 3).splitlines()

    assert lines[0] == "**`crapkit verify` exited 3 and wrote no verdict: src/a\\xe9.ts (and 1 more) ....**"
    assert lines[2:] == [f"- refused: `src/a\\xe9.ts`: {REASON}", f"- refused: `src/b\\xe9.ts`: {REASON}"]


def test_an_error_object_that_refused_no_file_stays_one_line():
    error = {"error": {"exit": 5, "kind": "tool", "message": "lizard is not importable\n"}, "schema": 1}

    assert "\n" not in _builder().no_verdict_line(error, 5)
    assert "\n" not in _builder().verdict_line(error, 5)


def test_a_path_holding_a_backtick_or_a_bar_cannot_break_the_bullet():
    coverage = {"error": {"exit": 3, "kind": "config", "message": "m",
                          "unread_files": [{"path": "src/a`b|c.ts", "reason": REASON, "dirty": True}]}}

    bullet = _builder().no_verdict_line(coverage, 3).splitlines()[-1]

    assert bullet == f"- refused: `src/a\\u0060b\\|c.ts`: {REASON}"
