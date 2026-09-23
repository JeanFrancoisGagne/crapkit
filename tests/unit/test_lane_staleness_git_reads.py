"""Lane staleness starts its git reads together and asks only about lane scopes.

`next-item`, `brief` and `explain` ask, per lane, whether files under its scopes
moved since the artifact's stamp: is the stamp commit behind HEAD, what changed
in the commits since, what is staged or edited, what is untracked. That was one
git process after another, and `ls-files --others` listed every untracked file
in the checkout, a large drafts tree included, to keep only the ones under a
scope. The reads now start together, and diff and ls-files take the scope paths
of the lanes still undecided as a pathspec.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit.config import Lane
from crapkit.lanes import write_stamps
from crapkit.uncovered import lane_states


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=60, check=True).stdout


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


def test_diff_and_untracked_reads_ask_only_about_the_undecided_lanes_scopes(repo, git_spawns):
    root, cfg = repo

    lane_states(root, cfg)

    reads = [argv for kind, argv in git_spawns
             if kind == "start" and ("diff" in argv or "ls-files" in argv)]
    assert any("ls-files" in argv for argv in reads)
    for argv in reads:
        assert _after(argv, "--") == ["src", "web"], argv


def test_scoped_reads_still_judge_each_lane_by_its_own_scope(repo):
    root, cfg = repo
    _write(root, "docs/draft.md")
    _write(root, "web/new.ts")

    states = dict(lane_states(root, cfg))

    assert states["src"] == "", "an untracked file outside every scope leaves src fresh"
    assert "files in its scopes changed" in states["web"]
    assert "no artifact" in states["lib"]


def test_a_committed_change_under_a_scope_is_still_seen(repo):
    root, cfg = repo
    _write(root, "src/a.ts", "changed\n")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "edit src")

    states = dict(lane_states(root, cfg))

    assert "files in its scopes changed" in states["src"]
    assert states["web"] == ""


def test_without_git_every_stamped_lane_reads_stale(repo, monkeypatch):
    """No git executable at all: nothing can prove an artifact current."""
    root, cfg = repo
    monkeypatch.setenv("PATH", str(root))

    states = dict(lane_states(root, cfg))

    assert "files in its scopes changed" in states["src"]
    assert "files in its scopes changed" in states["web"]
