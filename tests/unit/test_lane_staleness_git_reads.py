"""Lane staleness for a stamp that records only a commit starts its git reads together.

A stamp crapkit 0.8.0 or older wrote holds no blob ids, so `next-item`, `brief`
and `explain` judge it by git, per lane: is the stamp commit behind HEAD, what
changed in the commits since, what is staged or edited, what is untracked. That
was one git process after another, and `ls-files --others` listed every
untracked file in the checkout, a large drafts tree included, to keep only the
ones under a scope. The reads now start together, and diff and status take
the scope paths of the lanes git judges as a pathspec. The stamp file is read
once per command (lane_freshness.Freshness), so a lane is judged by the stamp
the command read, whatever a concurrent run writes meanwhile.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit.config import Lane
from crapkit import gitio, lane_changes
from crapkit.errors import GitError
from crapkit.gitio import GitFacts
from crapkit.lane_freshness import Freshness
from crapkit.lanes import _warn_stale_artifact, write_stamps
from crapkit.uncovered import lane_states
from hang_guard import HANG_SECONDS


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True).stdout


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


def _lane(name: str, scope: str) -> Lane:
    return Lane(name=name, command="x", artifact=f"cov-{name}.json", parser="istanbul",
                scopes=(scope,))


@pytest.fixture()
def repo(tmp_path: Path):
    """Three lanes: two stamped at HEAD, one whose artifact was never written."""
    for rel in ("src/a.ts", "web/b.ts", "lib/c.ts", "docs/notes.md"):
        _write(tmp_path, rel)
    _write(tmp_path, ".gitignore", "cov-*.json\n.crapkit/\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    lanes = [_lane("src", "src"), _lane("web", "web"), _lane("lib", "lib")]
    for lane in lanes[:2]:
        _write(tmp_path, lane.artifact, "{}")
        write_stamps(tmp_path, {lane.artifact: {"commit": head, "lane": lane.name, "seconds": 1.0}})
    cfg = SimpleNamespace(lanes=lanes, scope_paths={"src": ("src",), "web": ("web",),
                                                    "lib": ("lib",)})
    return tmp_path, cfg


@pytest.fixture()
def git_spawns(monkeypatch):
    """Every git process: ("start", argv) when it spawns, ("wait", argv) when read."""
    events = []
    real = subprocess.Popen

    class Recorded(real):
        def __init__(self, args, *rest, **kwargs):
            super().__init__(args, *rest, **kwargs)
            self._argv = list(args) if isinstance(args, (list, tuple)) else [args]
            if self._argv and self._argv[0] == "git":
                events.append(("start", self._argv))

        def communicate(self, *rest, **kwargs):
            if self._argv and self._argv[0] == "git":
                events.append(("wait", self._argv))
            return super().communicate(*rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recorded)
    return events


def _after(argv: list, word: str) -> list:
    return argv[argv.index(word) + 1:] if word in argv else []


def test_every_staleness_read_starts_before_any_is_waited_on(repo, git_spawns):
    """One `git --no-optional-locks status` compares content and writes no
    index (gitio.STATUS), so no read waits on another: the staged diff and the
    worktree diff used to take turns around the worktree diff's index write."""
    root, cfg = repo

    lane_states(root, cfg)

    kinds = [kind for kind, _ in git_spawns]
    assert kinds, "the staleness check asked git nothing"
    first_wait = kinds.index("wait")
    assert "start" not in kinds[first_wait:], kinds


def _scope_reads(spawns: list) -> list:
    """The argv of every diff, status and file listing git started."""
    return [argv for kind, argv in spawns
            if kind == "start" and ("diff" in argv or "status" in argv or "ls-files" in argv)]


def test_diff_and_status_reads_ask_only_about_lane_scopes(repo, git_spawns):
    root, cfg = repo

    lane_states(root, cfg)

    reads = _scope_reads(git_spawns)
    assert any("status" in argv for argv in reads)
    scopes = {tuple(_after(argv, "--")) for argv in reads}
    assert scopes == {("src", "web")}, "the two lanes git judges, lib has no stamp"


def test_with_no_stamped_lane_no_git_read_starts(tmp_path, git_spawns):
    """Every lane is decided as "no artifact" before git could answer anything."""
    _git(tmp_path, "init", "-q", "-b", "main")
    cfg = SimpleNamespace(lanes=[_lane("src", "src")], scope_paths={"src": ("src",)})
    git_spawns.clear()

    states = dict(lane_states(tmp_path, cfg))

    assert "no artifact" in states["src"]
    assert git_spawns == []


def test_a_lane_stamped_after_the_command_read_the_stamps_is_judged_by_that_read(repo):
    """A concurrent `crapkit coverage` can finish while this command runs. The
    stamps were read once, so the lane that had no stamp then has none for the
    whole command, and every reader of it says the same thing."""
    root, cfg = repo
    lib = cfg.lanes[2]
    head = _git(root, "rev-parse", "HEAD").strip()
    _write(root, lib.artifact, "{}")

    with Freshness(root, cfg.lanes, cfg.scope_paths) as fresh:
        write_stamps(root, {lib.artifact: {"commit": head, "lane": lib.name, "seconds": 1.0}})
        verdicts = (fresh.lines(lib), fresh.reuse(lib).reason)

    assert verdicts == ("no stamp records the commit cov-lib.json was built at",) * 2


def test_scoped_reads_still_judge_each_lane_by_its_own_scope(repo):
    root, cfg = repo
    _write(root, "docs/draft.md")
    _write(root, "web/new.ts")

    states = dict(lane_states(root, cfg))

    assert states["src"] == "", "an untracked file outside every scope leaves src fresh"
    assert "1 file(s) in its scopes changed" in states["web"] and "(web/new.ts)" in states["web"]
    assert "no artifact" in states["lib"]


def test_a_committed_change_under_a_scope_is_still_seen(repo):
    root, cfg = repo
    _write(root, "src/a.ts", "changed\n")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "edit src")

    states = dict(lane_states(root, cfg))

    assert "1 file(s) in its scopes changed" in states["src"] and "(src/a.ts)" in states["src"]
    assert states["web"] == ""


def _refuse_kill(self):
    raise AssertionError(f"a running git read was killed: {self.args}")


def test_a_stamp_commit_outside_history_reads_stale_and_kills_no_git_read(repo, monkeypatch):
    """After an amend the verdict stops at the ancestry answer, so the status
    reads are never collected. A worktree `git diff` refreshes the index under
    .git/index.lock when tracked files are stat-dirty; killed mid-refresh it
    leaves the lock behind and every later `git add` or commit fails."""
    root, cfg = repo
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--amend", "-m", "amended")
    monkeypatch.setattr(subprocess.Popen, "kill", _refuse_kill)

    states = dict(lane_states(root, cfg))

    for name in ("src", "web"):
        assert "which is not behind HEAD" in states[name], "an amend moved history, not files"
        assert "file(s) in its scopes changed" not in states[name]
    assert not (root / ".git" / "index.lock").exists()


def test_without_git_every_stamped_lane_reads_stale(repo, monkeypatch):
    """No git executable at all: nothing can prove an artifact current, and the
    note says git could not answer rather than that files changed."""
    root, cfg = repo
    monkeypatch.setenv("PATH", str(root))

    states = dict(lane_states(root, cfg))

    for name in ("src", "web"):
        assert "git cannot say" in states[name] and "git executable not found" in states[name]
        assert "file(s) in its scopes changed" not in states[name]


def test_the_note_names_each_file_that_changed(repo):
    root, cfg = repo
    _write(root, "src/a.ts", "edited\n")
    _write(root, "src/new.ts")

    states = dict(lane_states(root, cfg))

    assert "2 file(s) in its scopes changed" in states["src"]
    assert "src/a.ts, src/new.ts" in states["src"]


def test_an_artifact_no_stamp_vouches_for_says_so(repo):
    """An artifact copied in by hand has no commit to diff against. Nothing
    under its scope changed, so the note must not say that anything did."""
    root, cfg = repo
    _write(root, cfg.lanes[2].artifact, "{}")

    states = dict(lane_states(root, cfg))

    assert "no stamp records the commit cov-lib.json was built at" in states["lib"]
    assert "file(s) in its scopes changed" not in states["lib"]


def test_the_reuse_warning_names_each_file_that_changed(repo, capsys):
    root, cfg = repo
    _write(root, "src/a.ts", "edited\n")

    _warn_stale_artifact(GitFacts(root), cfg.lanes[0], cfg.scope_paths)

    err = capsys.readouterr().err
    assert "1 file(s) in its scopes changed" in err and "(src/a.ts)" in err, err


def test_the_reuse_warning_says_when_git_cannot_answer(repo, capsys):
    """A stamp naming a commit this clone does not hold, the way an artifact
    cache restored into a fresh shallow checkout arrives: git refuses the diff,
    and that refusal used to print nothing, the same as a scope that never moved."""
    root, cfg = repo
    src = cfg.lanes[0]
    write_stamps(root, {src.artifact: {"commit": "f" * 40, "lane": src.name, "seconds": 1.0}})

    _warn_stale_artifact(GitFacts(root), src, cfg.scope_paths)

    err = capsys.readouterr().err
    assert "git cannot say which files in its scopes changed" in err, err
    assert "stale" in err


@pytest.mark.skipif(sys.platform != "win32", reason="the index race needs Windows file sharing rules")
def test_three_hundred_touches_raise_no_index_race(repo):
    """After a same-bytes touch the worktree `git diff` writes the
    refreshed index back, and on Windows a sibling read that opened .git/index
    during that write failed with `index file open failed: Permission denied`
    (9 of 300 touches), which read as a changed file. Slow on purpose: 300 is
    the count the race showed at."""
    from crapkit.errors import GitError
    from crapkit.gitio import index_blobs, worktree_changes
    from crapkit.lane_changes import ChangeReads

    root, _ = repo
    head = _git(root, "rev-parse", "HEAD").strip()
    source = root / "src" / "a.ts"
    failures, changes = [], []
    for step in range(300):
        later = source.stat().st_mtime + 200 + step
        os.utime(source, (later, later))
        try:
            with ChangeReads(root, (head,), ("src",)) as reads:
                changes.extend(reads.changed_since(head))
            changes.extend(worktree_changes(root, ("src",)))
            index_blobs(root, ("src",))
        except GitError as exc:
            failures.append(str(exc))

    assert (failures[:3], changes[:3]) == ([], [])


# git exits 128 with "fatal: Needed a single revision" for a ref it cannot read,
# the exit a read gets when git cannot read the repository.
FAILED_GIT = ("rev-parse", "--verify", "refs/crapkit/no-such-ref")


@pytest.mark.parametrize("command", ["merge-base", "diff", "status"])
def test_a_failed_read_names_no_changed_file(repo, monkeypatch, command):
    """One read behind the verdict fails on a tree nobody touched. The lines
    go null, since nothing proved them, and the note says git cannot say: it
    used to say files in the lane's scopes changed and to commit or revert
    them."""
    root, cfg = repo
    real = lane_changes._start
    monkeypatch.setattr(lane_changes, "_start", lambda r, *args: real(
        r, *(FAILED_GIT if command in args[:2] else args)))

    states = dict(lane_states(root, cfg))

    for lane in ("src", "web"):
        assert "git cannot say" in states[lane] and "no-such-ref" in states[lane], states
        assert "file(s) in its scopes changed" not in states[lane]
        assert "commit or revert" not in states[lane]


def test_an_unreadable_commit_since_the_stamp_names_the_failed_read(repo):
    """A commit between the stamp and HEAD cannot be read, so `merge-base
    --is-ancestor` exits 1, its "no", and prints `error: Could not read <sha>`.
    The stamp commit is still behind HEAD and the tree is clean: the note used
    to say the artifact was built at a commit not behind HEAD."""
    root, cfg = repo
    _write(root, "docs/notes.md", "two\n")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "two")
    lost = _git(root, "rev-parse", "HEAD").strip()
    _write(root, "docs/notes.md", "three\n")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "three")
    loose = root / ".git" / "objects" / lost[:2] / lost[2:]
    loose.chmod(0o644)
    loose.unlink()

    states = dict(lane_states(root, cfg))

    for lane in ("src", "web"):
        assert "git cannot say" in states[lane] and lost in states[lane], states
        assert "not behind HEAD" not in states[lane] and "commit or revert" not in states[lane]


def test_trace_output_leaves_a_rewritten_history_named_as_such(repo, monkeypatch):
    """GIT_TRACE=1 prints trace lines on stderr beside the ancestry read's plain
    "no". Read as a failed read, the note said git could not tell which files
    changed, where the stamp commit had left history."""
    root, cfg = repo
    stamped = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--amend", "-m", "amended")
    monkeypatch.setenv("GIT_TRACE", "1")

    states = dict(lane_states(root, cfg))

    assert stamped[:11] in states["src"] and "which is not behind HEAD" in states["src"], states
    assert "git cannot say" not in states["src"]


def test_reuse_says_when_git_could_not_tell_whether_an_artifact_is_stale(repo, monkeypatch, capsys):
    """`--reuse-artifacts` warns about an artifact that predates a change. A
    failed diff read used to warn nothing, which reads as a fresh artifact."""
    root, cfg = repo

    def unavailable(*_args):
        raise GitError("git unavailable")

    monkeypatch.setattr(gitio, "diff_names_since", unavailable)
    _warn_stale_artifact(GitFacts(root), cfg.lanes[0], cfg.scope_paths)

    err = capsys.readouterr().err
    assert "git cannot say which files in its scopes changed" in err and "git unavailable" in err, err
