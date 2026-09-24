"""Four readers that take git's view of a file as the answer to "did it change".

verify's committed/dirty split, the pre-commit hook's "differs from the working
tree" note, the input snapshot `crapkit mutate` seeds its workers with, and the
changed ranges both gates judge all ask git which files differ, and git answers
from its stat cache. Each test here is one reader; each row is one thing done
to a file under one git setup, with the truth (did the bytes git would commit
change) beside it.

A same-bytes touch, with or without `diff.autoRefreshIndex`, a CRLF checkout, an
`eol` or `ident` attribute: git re-reads the file and answers from its bytes,
so every such row holds. A same-size edit whose old mtime was put back (two
writes inside one clock tick, or a copy that keeps times) is the named limit in
docs/ratchet.md: git's stat cache calls the file unchanged, and so does each of
these readers. Those rows pin what the reader says today, so a change to it is
a decision rather than an accident.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import child_env, cli_runner

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src", "tests"]
languages = ["python"]
coverage_optional = true
"""

REL = "src/app.py"
NOTE = "differs from the working tree"

run_cli = cli_runner(env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                         check=True, capture_output=True, text=True, encoding="utf-8")
    return res.stdout


def tangled(name: str) -> str:
    """Seven decisions: ccn 8, over the target of 6 in a cc-only scope."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def plain(name: str) -> str:
    return f"def {name}(n):\n    return n\n"


def committed_repo(tmp_path: Path, attrs: str, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    base = {"crapkit.toml": CONFIG, ".gitignore": ".crapkit/\n", "src/other.py": plain("other"),
            **({".gitattributes": attrs} if attrs else {}), **files}
    for rel, text in base.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(text.encode("utf-8"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def age(path: Path) -> None:
    then = time.time() - 30
    os.utime(path, (then, then))


def settle(repo: Path, rel: str) -> None:
    """An mtime in the past and a refreshed index, so git's entry is neither
    racily clean nor stale before the row does its one thing."""
    age(repo / rel)
    git(repo, "update-index", "-q", "--refresh")


def touch(path: Path) -> None:
    later = path.stat().st_mtime + 120
    os.utime(path, (later, later))


def norefresh(repo: Path) -> None:
    git(repo, "config", "diff.autoRefreshIndex", "false")


def recheckout(repo: Path, rel: str) -> None:
    """Write the file again the way the checkout's filters write it."""
    (repo / rel).unlink()
    git(repo, "checkout", "--", rel)
    settle(repo, rel)


def same_size(repo: Path, rel: str) -> None:
    """Same-length new bytes under the old mtime. core.trustctime=false because
    Linux and macOS move the ctime on a write and git compares it there; with
    it off every platform sees what Windows sees, mtime and size alone."""
    git(repo, "config", "core.trustctime", "false")
    path = repo / rel
    old, stat = path.read_bytes(), path.stat()
    new = old.replace(b"n = n + 1", b"n = n + 2", 1)
    assert len(new) == len(old) and new != old
    path.write_bytes(new)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def append(repo: Path, rel: str) -> None:
    with open(repo / rel, "ab") as fh:
        fh.write(b"# edited\n")


def _touch(repo: Path, rel: str) -> None:
    touch(repo / rel)


def _touch_norefresh(repo: Path, rel: str) -> None:
    norefresh(repo)
    touch(repo / rel)


def _crlf(repo: Path, rel: str) -> None:
    git(repo, "config", "core.autocrlf", "true")
    recheckout(repo, rel)
    assert b"\r\n" in (repo / rel).read_bytes(), "the CRLF checkout did not land"


def _crlf_touch_norefresh(repo: Path, rel: str) -> None:
    _crlf(repo, rel)
    _touch_norefresh(repo, rel)


def _input_crlf_bytes(repo: Path, rel: str) -> None:
    """CRLF bytes under core.autocrlf=input normalize to the LF blob git holds."""
    git(repo, "config", "core.autocrlf", "input")
    path = repo / rel
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _filtered_touch(repo: Path, rel: str, written: bytes) -> None:
    """The file as the attribute's filter writes it, then a touch."""
    recheckout(repo, rel)
    assert written in (repo / rel).read_bytes(), f"the checkout did not write {written!r}"
    touch(repo / rel)


def _eol_touch(repo: Path, rel: str) -> None:
    _filtered_touch(repo, rel, b"\r\n")


def _eol_touch_norefresh(repo: Path, rel: str) -> None:
    _eol_touch(repo, rel)
    norefresh(repo)


def _ident_touch(repo: Path, rel: str) -> None:
    _filtered_touch(repo, rel, b"$Id: ")


def _ident_touch_norefresh(repo: Path, rel: str) -> None:
    _ident_touch(repo, rel)
    norefresh(repo)


def _skip_worktree_edit(repo: Path, rel: str) -> None:
    git(repo, "update-index", "--skip-worktree", rel)
    append(repo, rel)


EOL = "*.py text eol=crlf\n"
IDENT = "*.py ident\n"

# name -> (what it does, the .gitattributes it needs, whether the bytes git
# would commit changed). The ident rows open the file with `# $Id$`.
EVENTS = {
    "touch": (_touch, "", False),
    "touch-norefresh": (_touch_norefresh, "", False),
    "crlf-autocrlf-true": (_crlf, "", False),
    "crlf-touch-norefresh": (_crlf_touch_norefresh, "", False),
    "autocrlf-input-crlf-bytes": (_input_crlf_bytes, "", False),
    "eol-attr-touch": (_eol_touch, EOL, False),
    "eol-attr-touch-norefresh": (_eol_touch_norefresh, EOL, False),
    "ident-touch": (_ident_touch, IDENT, False),
    "ident-touch-norefresh": (_ident_touch_norefresh, IDENT, False),
    "skip-worktree-edit": (_skip_worktree_edit, "", True),
    "content-change": (append, "", True),
}

# The named limit: the bytes changed and git's stat cache cannot tell.
LIMIT = "same-size-one-tick"


def source(attrs: str, text: str) -> str:
    return ("# $Id$\n" + text) if attrs == IDENT else text


# --- verify: committed or dirty ---------------------------------------------------

def verify_route(tmp_path: Path, event: str) -> dict:
    """A baseline run, then a committed `route` over the ceiling, then the row's
    event on its file. Returns route's gate finding from `verify --json`."""
    act, attrs, _ = EVENTS.get(event, (same_size, "", True))
    repo = committed_repo(tmp_path, attrs, {REL: source(attrs, plain("keep"))})
    assert run_cli(repo, "coverage").returncode == 0
    (repo / REL).write_bytes(source(attrs, plain("keep") + "\n\n" + tangled("route")).encode())
    git(repo, "commit", "-q", "-am", "route over the ceiling")
    settle(repo, REL)
    act(repo, REL)
    res = run_cli(repo, "verify", "--no-tighten", "--json")
    assert res.returncode == 6, res.stdout + res.stderr
    [finding] = [g for g in json.loads(res.stdout)["gate_violations"] if "route" in g["long_name"]]
    return finding


@pytest.mark.parametrize("event", sorted(EVENTS))
def test_verify_blames_uncommitted_edits_only_for_bytes_that_moved(tmp_path: Path, event: str):
    finding = verify_route(tmp_path, event)

    assert finding["dirty"] is EVENTS[event][2], finding


def test_verify_counts_a_same_size_edit_under_its_old_mtime_as_committed(tmp_path: Path):
    """The named limit: the edit is uncommitted, and git's stat cache says the
    file matches HEAD, so the finding reads committed."""
    assert verify_route(tmp_path, LIMIT)["dirty"] is False


def test_verify_counts_the_changed_file_once_when_another_is_touched_under_norefresh(
        tmp_path: Path):
    repo = committed_repo(tmp_path, "", {REL: plain("keep")})
    assert run_cli(repo, "coverage").returncode == 0
    (repo / REL).write_text(plain("keep") + "\n\n" + tangled("route"), encoding="utf-8")
    git(repo, "commit", "-q", "-am", "route over the ceiling")
    settle(repo, "src/other.py")
    _touch_norefresh(repo, "src/other.py")

    res = run_cli(repo, "verify", "--no-tighten", "--json")
    payload = json.loads(res.stdout)
    assert payload["changed_paths"] == [REL], payload
    assert [g["dirty"] for g in payload["gate_violations"]] == [False], payload


# --- hook-precommit: the re-stage note ---------------------------------------------

def staged_route(tmp_path: Path, event: str) -> Path:
    """`route` over the ceiling staged, then the row's event on the working copy."""
    act, attrs, _ = EVENTS.get(event, (same_size, "", True))
    repo = committed_repo(tmp_path, attrs, {REL: source(attrs, plain("keep"))})
    (repo / REL).write_bytes(source(attrs, plain("keep") + "\n\n" + tangled("route")).encode())
    git(repo, "add", REL)
    settle(repo, REL)
    act(repo, REL)
    return repo


# skip-worktree needs a file the index already holds unchanged; a staged edit is
# not one, so that row belongs to verify and mutate only.
HOOK_EVENTS = sorted(set(EVENTS) - {"skip-worktree-edit"})


@pytest.mark.parametrize("event", HOOK_EVENTS)
def test_the_hook_notes_a_stale_staging_only_when_the_working_copy_moved(
        tmp_path: Path, event: str):
    res = run_cli(staged_route(tmp_path, event), "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert (NOTE in res.stdout + res.stderr) is EVENTS[event][2], res.stdout + res.stderr


def test_the_hook_misses_a_same_size_edit_under_its_old_mtime(tmp_path: Path):
    """The named limit: the working copy differs from the staged blob and git's
    stat cache says it does not, so no note."""
    res = run_cli(staged_route(tmp_path, LIMIT), "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert NOTE not in res.stdout + res.stderr


def commit_through_the_hook(repo: Path) -> subprocess.CompletedProcess:
    """A real `git commit` that runs `crapkit hook-precommit` as its pre-commit
    hook, the path a committer takes; git refreshes the index before hooks."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    python = sys.executable.replace("\\", "/")
    (hooks / "pre-commit").write_text(f'#!/bin/sh\nexec "{python}" -m crapkit hook-precommit\n',
                                      encoding="utf-8", newline="\n")
    os.chmod(hooks / "pre-commit", 0o755)
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                           "-m", "route"], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env=child_env({"CRAPKIT_OVERRIDE_REASON": None}))


@pytest.mark.parametrize("event", ["touch", "touch-norefresh", "crlf-touch-norefresh",
                                   "content-change"])
def test_a_real_commit_gets_the_note_only_when_the_working_copy_moved(tmp_path: Path, event: str):
    res = commit_through_the_hook(staged_route(tmp_path, event))

    assert res.returncode != 0, "the hook refuses the ccn 8 function"
    assert (NOTE in res.stdout + res.stderr) is EVENTS[event][2], res.stdout + res.stderr


# --- mutate: what every worker tree is seeded with ---------------------------------

TEST_REL = "tests/test_app.py"
TEST_TEXT = "from src.app import keep\n\n\ndef test_keep(n=1):\n    n = n + 1\n    assert keep(n) == n\n"


def worker_copy(tmp_path: Path, event: str) -> tuple[bytes, bytes]:
    """(the test file a worker runs, the test file on disk) after the row's
    event on the test file, which is not a mutation target."""
    from crapkit.mutate_pool import _input_snapshot

    act, attrs, _ = EVENTS.get(event, (same_size, "", True))
    repo = committed_repo(tmp_path, attrs, {REL: plain("keep"),
                                            TEST_REL: source(attrs, TEST_TEXT)})
    settle(repo, TEST_REL)
    act(repo, TEST_REL)
    _, files = _input_snapshot(repo, [REL], Path(".crapkit"))
    seeded = files.get(TEST_REL)
    head = subprocess.run(["git", "show", f"HEAD:{TEST_REL}"], cwd=repo, check=True,
                          capture_output=True).stdout
    return (seeded[0] if seeded else head), (repo / TEST_REL).read_bytes()


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


@pytest.mark.parametrize("event", ["touch", "touch-norefresh", "crlf-touch-norefresh",
                                   "skip-worktree-edit", "content-change"])
def test_every_worker_runs_the_test_file_on_disk(tmp_path: Path, event: str):
    runs, disk = worker_copy(tmp_path, event)

    assert _lf(runs) == _lf(disk)


def test_a_same_size_test_edit_under_its_old_mtime_reaches_no_worker(tmp_path: Path):
    """The named limit: git's stat cache calls the test unchanged, so the
    workers run HEAD's copy."""
    runs, disk = worker_copy(tmp_path, LIMIT)

    assert runs != disk
    assert b"n = n + 1" in runs and b"n = n + 2" in disk


# --- the gates: which functions changed --------------------------------------------

BRANCHES = "    b = a if a > 1 and a > 2 and a > 3 and a > 4 and a > 5 and a > 6 else 0\n"
PAD = "    #" + "x" * (len(BRANCHES) - 6) + "\n"
PADDED = "def pad(a):\n    b = 0\n" + PAD + "    return b\n"
assert len(PAD) == len(BRANCHES)


def padded_repo(tmp_path: Path) -> Path:
    """`pad` holds a comment the length of one line that takes it from ccn 1 to
    ccn 7, over the target of 6."""
    repo = committed_repo(tmp_path, "", {REL: PADDED})
    settle(repo, REL)
    return repo


def branch(repo: Path, how: str) -> None:
    path = repo / REL
    old = path.read_bytes()
    new = old.replace(PAD.encode(), BRANCHES.encode())
    if how == "content-change":
        path.write_bytes(new)
    elif how == LIMIT:
        git(repo, "config", "core.trustctime", "false")
        stat = path.stat()
        path.write_bytes(new)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        _touch_norefresh(repo, REL)


# (what happened, whether the gate refuses). The same-size row is the named
# limit: the new ccn is on disk and the gate judges no changed function.
GATE_ROWS = [("touch-norefresh", False), ("content-change", True), (LIMIT, False)]


@pytest.mark.parametrize("how, refused", GATE_ROWS)
def test_rescore_gate_judges_the_functions_gits_diff_names(tmp_path: Path, how: str,
                                                          refused: bool):
    repo = padded_repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    branch(repo, how)

    res = run_cli(repo, "rescore", REL, "--gate")

    assert (res.returncode == 6) is refused, res.stdout + res.stderr
    if not refused:
        assert "gate: 0 changed function(s) judged" in res.stdout, res.stdout


@pytest.mark.parametrize("how, refused", GATE_ROWS)
def test_verify_gate_judges_the_functions_gits_diff_names(tmp_path: Path, how: str,
                                                         refused: bool):
    repo = padded_repo(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    branch(repo, how)

    res = run_cli(repo, "verify", "--no-tighten", "--json")

    assert (res.returncode == 6) is refused, res.stdout + res.stderr
    assert bool(json.loads(res.stdout)["gate_violations"]) is refused


@pytest.mark.parametrize("how", ["content-change", LIMIT])
def test_the_hook_judges_what_git_add_staged(tmp_path: Path, how: str):
    """`git add` skips a stat-clean file too, so the commit would not carry the
    same-size edit either: the hook and the commit agree on every row."""
    repo = padded_repo(tmp_path)
    branch(repo, how)
    git(repo, "add", REL)
    staged = git(repo, "diff", "--cached", "--name-only").strip()

    res = run_cli(repo, "hook-precommit")

    assert bool(staged) is (how == "content-change")
    assert (res.returncode == 6) is bool(staged), res.stdout + res.stderr
