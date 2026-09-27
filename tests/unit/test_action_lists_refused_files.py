"""The Action's comment lists every file an error object refused.

A scan that meets names a scope takes that are not UTF-8 exits 3 with one
stderr line naming the first and counting the rest (`src/caf\\xe9.ts (and 1
more) ...`). `--json` lists every one in the error object's `unread_files`, and
the comment quoted the message alone: a pull request with two such names showed
one and "(and 1 more)", and the job log's stderr line named no more. The
comment now gives each refused file a bullet under the no-verdict line, the way
it gives each unread file a bullet under a failed verdict.
"""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from cli_inproc_repo import KNOTTY, repo, seed_artifacts, template_repo  # noqa: F401

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
