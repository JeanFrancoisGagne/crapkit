"""Class U: a staged file a scope takes whose name is not UTF-8 stops the commit gate.

The class U fixture stages `src/caf\\xe9.py`, a ccn-8 function under scope
`src`, and commits it through the hook README Route 1 writes. hook-precommit
judges the staged rows through the gate module, which refuses a claimed name
before it judges anything (gate-group-08), so git refuses the commit with the
line the 0.8.1 tree printed, nothing reaches the index or the marks file, and
CRAPKIT_OVERRIDE_REASON grants nothing.

Fresh: the candidate installed as the README's 60-second start installs it.
Upgrade: crapkit N-1 arms the hook and commits through it, then the guide's pip
row upgrades it to the candidate.
"""
from __future__ import annotations

import os
from pathlib import Path

from kit import gitsurf, profiles, state, wheels
from kit.cells import cell

PACKET = "gate-group-08"
NAME = b"src/caf\xe9.py"
SHOWN = "src/caf\\xe9.py"
STOP = (f"crapkit: {SHOWN} is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit reads "
        "every path as UTF-8; a file a scope takes is refused, not left out, so no gate passes it unread: rename "
        "it (git mv) to a UTF-8 name")
MARKS = "crapkit-ratchet.tsv"
PIP = "pip in the active environment"
USE_CASES = "commit gate, class U path names"

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""
SOURCE = "def f(x):\n    return x\n"
# ccn 8: seven decisions, over the target of 6.
TANGLED = ("def tangled(a, b, c, d):\n"
           + "".join(f"    if {v}:\n        a += 1\n" for v in ("a", "b", "c", "d", "a > b", "b > c", "c > d"))
           + "    return a\n")


def _armed_repo(box) -> Path:
    """The fixture repo committed, then the README Route 1 hook armed and one
    clean commit made through it, by the crapkit first on PATH."""
    repo = box.root / "repo"
    (repo / "src").mkdir(parents=True)
    state.write(repo, {"crapkit.toml": CONFIG, ".gitignore": ".crapkit/\n", "src/app.py": SOURCE})
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "base")
    gitsurf.route1(box, repo)
    (repo / "src" / "clean.py").write_text(SOURCE, encoding="utf-8")
    profiles.commit(box, repo, "a clean commit through the hook")
    return repo


def _head(box, repo: Path) -> str:
    return box.run(["git", "rev-parse", "HEAD"], cwd=repo, expect=0).stdout.strip()


def _staged(box, repo: Path) -> str:
    return box.run(["git", "diff", "--cached", "--name-only"], cwd=repo, expect=0).stdout


def assert_the_commit_stops(box, repo: Path) -> None:
    """The claimed name staged and committed: git refuses on the 0.8.1 line,
    with and without CRAPKIT_OVERRIDE_REASON, and nothing is written."""
    (repo / os.fsdecode(NAME)).write_text(TANGLED, encoding="utf-8")
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    head, staged = _head(box, repo), _staged(box, repo)

    for env in ({}, {"CRAPKIT_OVERRIDE_REASON": "hotfix, ticket 7"}):
        step = box.run(["git", "commit", "-m", "add a Latin-1 name"], cwd=repo, env={**box.commit_env(), **env})

        assert step.exit == 1, box.transcript.text()
        assert STOP in step.stderr.splitlines(), box.transcript.text()
        assert "Traceback" not in step.stderr and "crapkit: left out" not in step.stderr, box.transcript.text()
        assert (_head(box, repo), _staged(box, repo)) == (head, staged), box.transcript.text()
        assert not (repo / MARKS).exists(), box.transcript.text()


@cell("lin-claimed-name-hook", channel="pip venv", harness="git", use_cases=USE_CASES, os="linux",
      image="core", cadence="nightly",
      scenario="fresh: README pip start, Route 1 hook; committing a staged src/caf\\xe9.py stops with the 0.8.1 "
               "line, with and without CRAPKIT_OVERRIDE_REASON, and writes no mark")
def test_claimed_name_commit_fresh(box, templates):
    profiles.session_crapkit(box, templates)

    assert_the_commit_stops(box, _armed_repo(box))


@cell("lin-up-claimed-name-hook", channel="pip venv", harness="git", use_cases=USE_CASES, os="linux",
      image="core", cadence="nightly",
      scenario="upgrade from N-1 by the guide's pip row: the Route 1 hook N-1 armed stops a commit of a staged "
               "src/caf\\xe9.py with the 0.8.1 line, with and without CRAPKIT_OVERRIDE_REASON")
def test_claimed_name_commit_upgrade(box, candidate, record_property):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _armed_repo(box)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))

    assert_the_commit_stops(box, repo)
