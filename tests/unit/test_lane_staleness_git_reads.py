"""Lane staleness starts its git reads together and asks only about lane scopes.

`next-item`, `brief` and `explain` ask, per lane, whether files under its scopes
moved since the artifact's stamp: is the stamp commit behind HEAD, what changed
in the commits since, what is staged, edited or untracked. That was one git
process after another, and `ls-files --others` listed every untracked file in
the checkout, a large drafts tree included, to keep only the ones under a
scope. The reads now start together, and diff and status take the scope paths
of every lane as a pathspec: a lane with no artifact when the reads start can
have one by the time it is judged.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import gitio, lane_changes, lanes
from crapkit.config import Lane
from crapkit.errors import GitError
from crapkit.invocation import _self
from crapkit.lanes import lane_sources_gap, staleness_reads, write_stamps
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
    root, cfg = repo

    lane_states(root, cfg)

    kinds = [kind for kind, _ in git_spawns]
    assert kinds, "the staleness check asked git nothing"
    first_wait = kinds.index("wait")
    assert "start" not in kinds[first_wait:], kinds


def _diff_and_status_starts(events: list) -> list:
    return [argv for kind, argv in events
            if kind == "start" and ("diff" in argv or "status" in argv)]


def test_diff_and_status_reads_ask_only_about_lane_scopes(repo, git_spawns):
    root, cfg = repo

    lane_states(root, cfg)

    reads = _diff_and_status_starts(git_spawns)
    assert any("status" in argv for argv in reads)
    for argv in reads:
        assert _after(argv, "--") == ["src", "web", "lib"], argv


def test_with_no_stamped_lane_no_git_read_starts(tmp_path, git_spawns):
    """Every lane is decided as "no artifact" before git could answer anything."""
    _git(tmp_path, "init", "-q", "-b", "main")
    cfg = SimpleNamespace(lanes=[_lane("src", "src")], scope_paths={"src": ("src",)})
    git_spawns.clear()

    states = dict(lane_states(tmp_path, cfg))

    assert "no artifact" in states["src"]
    assert git_spawns == []


def test_a_lane_stamped_after_the_reads_started_is_judged_on_its_own_scope(repo):
    """A concurrent `crapkit coverage` can finish between the reads starting and
    the verdict, so a lane with no artifact when they started can have one when
    it is judged. An uncommitted edit under its scope still makes it stale."""
    root, cfg = repo
    lib = cfg.lanes[2]
    head = _git(root, "rev-parse", "HEAD").strip()
    _write(root, "lib/c.ts", "edited\n")

    with staleness_reads(root, cfg.lanes, cfg.scope_paths) as facts:
        _write(root, lib.artifact, "{}")
        write_stamps(root, {lib.artifact: {"commit": head, "lane": lib.name, "seconds": 1.0}})
        gap = lane_sources_gap(root, lib, cfg.scope_paths, facts)

    assert gap.changed is True


def test_scoped_reads_still_judge_each_lane_by_its_own_scope(repo):
    root, cfg = repo
    _write(root, "docs/draft.md")
    _write(root, "web/new.ts")

    states = dict(lane_states(root, cfg))

    assert states["src"] == "", "an untracked file outside every scope leaves src fresh"
    assert states["web"] == ("lane 'web': files in its scopes changed since cov-web.json was "
                             "written (uncommitted edits count), so its line numbers are stale "
                             f"\u2014 commit or revert them, then rerun `{_self()} coverage`")
    assert "no artifact" in states["lib"]


def test_a_committed_change_under_a_scope_is_still_seen(repo):
    root, cfg = repo
    _write(root, "src/a.ts", "changed\n")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "edit src")

    states = dict(lane_states(root, cfg))

    assert "files in its scopes changed" in states["src"]
    assert states["web"] == ""


def _refuse_kill(self):
    raise AssertionError(f"a running git read was killed: {self.args}")


def test_a_stamp_commit_outside_history_reads_stale_and_kills_no_git_read(repo, monkeypatch):
    """After an amend the verdict stops at the ancestry answer, so the status
    reads are never collected, and none is killed: every git process ends
    before the command does. No file changed, so the note asks for a fresh
    run, not a commit."""
    root, cfg = repo
    stamped = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--amend", "-m", "amended")
    monkeypatch.setattr(subprocess.Popen, "kill", _refuse_kill)

    states = dict(lane_states(root, cfg))

    assert states["src"] == (f"lane 'src': cov-src.json was built at {stamped[:11]}, which is not "
                             "behind HEAD, so nothing proves its line numbers current — rerun "
                             f"`{_self()} coverage`")
    assert "not behind HEAD" in states["web"]
    assert not (root / ".git" / "index.lock").exists()


def test_without_git_every_stamped_lane_reads_stale(repo, monkeypatch):
    """No git executable at all: nothing can prove an artifact current, and
    the note says git could not tell rather than naming a change."""
    root, cfg = repo
    monkeypatch.setenv("PATH", str(root))

    states = dict(lane_states(root, cfg))

    for lane in ("src", "web"):
        assert states[lane].startswith(f"lane '{lane}': git could not tell which files in its "
                                       f"scopes changed after cov-{lane}.json was written "
                                       "(git executable not found), so nothing proves"), states


# git exits 128 with "fatal: Needed a single revision" for a ref it cannot read,
# the exit a read gets when git cannot read the repository.
FAILED_GIT = ("rev-parse", "--verify", "refs/crapkit/no-such-ref")


@pytest.mark.parametrize("command", ["merge-base", "diff", "status"])
def test_a_failed_read_names_no_changed_file(repo, monkeypatch, command):
    """One read behind the verdict fails on a tree nobody touched. The lines
    go null, since nothing proved them, and the note says git could not tell:
    it used to say files in the lane's scopes changed and to commit or revert
    them."""
    root, cfg = repo
    real = lane_changes._start
    monkeypatch.setattr(lane_changes, "_start", lambda r, *args: real(
        r, *(FAILED_GIT if command in args[:2] else args)))

    states = dict(lane_states(root, cfg))

    for lane in ("src", "web"):
        assert states[lane].startswith(f"lane '{lane}': git could not tell which files"), states
        assert "no-such-ref" in states[lane]
        assert "changed since" not in states[lane] and "commit or revert" not in states[lane]


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
        assert states[lane].startswith(f"lane '{lane}': git could not tell which files"), states
        assert lost in states[lane]
        assert "not behind HEAD" not in states[lane] and "commit or revert" not in states[lane]


def test_an_artifact_no_stamp_records_is_named_as_such(repo):
    """An artifact with no stamp proves nothing about its lines; no file changed."""
    root, cfg = repo
    _write(root, cfg.lanes[2].artifact, "{}")

    states = dict(lane_states(root, cfg))

    assert states["lib"] == ("lane 'lib': no stamp records the commit cov-lib.json was built at, "
                             "so nothing proves its line numbers current — rerun "
                             f"`{_self()} coverage`")


def test_reuse_says_when_git_could_not_tell_whether_an_artifact_is_stale(repo, monkeypatch, capsys):
    """`--reuse-artifacts` warns about an artifact that predates a change. A
    failed read used to warn nothing, which reads as a fresh artifact."""
    root, cfg = repo
    head = _git(root, "rev-parse", "HEAD").strip()

    def unavailable(*_args):
        raise GitError("git unavailable")

    monkeypatch.setattr(gitio, "diff_names_since", unavailable)
    lanes._warn_stale_artifact(gitio.GitFacts(root), cfg.lanes[0], cfg.scope_paths)

    assert capsys.readouterr().err == (
        f"crapkit: lane 'src' artifact was built at {head[:11]}; git could not tell which "
        "files in its scopes changed after it, so its coverage may be stale (git unavailable)\n")
