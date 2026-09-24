"""What the release pages say about freshness, held to the code that decides it.

0.8.1 changes how crapkit judges whether a file, a lane or a run still describes
the tree: the lane stamp records content, a failed lane's leftover stays refused
until new bytes replace it, agents read `scored_changes` beside `stale`, a failed
git question is named, claude-hook remembers what it judged, and watch compares
content. CHANGELOG.md, docs/upgrading.md, README.md, AGENTS.md, the handbook and
the onboard skill each describe some of that. Each test here reads one claim off
a page and checks it against the code that makes it true, and a line a page
quotes is built by the code that prints it.

Several of those changes land in other changes of this release. Until that code
is in the tree, `landed` marks the row as an expected failure, and a strict one:
a row that passes while its probe says the code is absent fails, so a probe that
stops finding the code cannot hide the row.
"""
from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import lanes
from crapkit.cli import verifying

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def _page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """Each run of whitespace read as one space, so a phrase the page wraps
    across two lines still reads as one."""
    return " ".join(text.split())


def _release(version: str = "0.8.1") -> str:
    """The CHANGELOG section of one release, heading to the next release."""
    text = _page("CHANGELOG.md")
    start = text.index(f"\n## {version} ")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def landed(present: bool, change: str):
    """An expected failure while `change` is not in the tree yet; nothing once it is."""
    return pytest.mark.xfail(not present, strict=True,
                             reason=f"{change} lands with another change of this release")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                   check=True, capture_output=True)


def _one_commit_repo(root: Path) -> None:
    _git(root, "init", "-q")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "a.py")
    _git(root, "commit", "-q", "-m", "one")


# -- Q11: the 0.8.0 staleness reader stays, and warns ----------------------------

def test_the_changelog_no_longer_tells_library_callers_to_rename():
    """0.8.1 keeps `lanes.lane_sources_unchanged` through 0.8.x as a wrapper that
    warns. A line telling callers the name was replaced would send them to edit
    code that still works."""
    section = _prose(_release())

    assert "is now `lanes.lane_sources_moved`" not in section
    assert "`lanes.lane_sources_unchanged`" in section
    assert "`DeprecationWarning`" in section and "0.9.0 removes it" in section


@landed(hasattr(lanes, "lane_sources_unchanged"), "the lane_sources_unchanged shim")
def test_the_shim_the_changelog_names_answers_a_bool_and_warns(tmp_path):
    from crapkit.config import Lane

    lane = Lane(name="py", command="true", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("calc",))

    with pytest.warns(DeprecationWarning):
        answer = lanes.lane_sources_unchanged(tmp_path, lane, {"calc": ("calc",)})

    assert answer is False, "no stamp vouches for the artifact, so its lines are not fresh"


# -- Q38: the same-size edit under a restored modification time ------------------

def test_no_release_line_claims_a_same_size_edit_under_a_restored_mtime_is_caught():
    """git's index answers "unchanged" from its stat data, and 0.8.1 trusts it
    rather than read every scope file on every run. A same-size edit whose old
    modification time was put back passes that check, so a line saying it is
    caught, or that it reruns the lane, promises what the code does not do."""
    section = _prose(_release())

    assert "modification time was put back is caught too" not in section
    assert "so a same-size edit whose old modification time was put back" not in section


def test_the_changelog_names_the_same_size_edit_as_a_limit():
    section = _prose(_release())

    assert "A same-size edit whose old modification time was put back" in section
    assert "is still not seen" in section
    assert "measured for 0.9.0" in section


# -- Q37: verify tells a commit the clone lacks from a rewrite --------------------

_BASELINE = "a74260f321f" + "4e0b9d2c61a8f3e57d0c1b2a9e8f7d6c5"


def _not_behind(shallow: bool, held: bool) -> str:
    git = SimpleNamespace(is_shallow=lambda: shallow)
    return verifying._not_behind(git, _BASELINE, lambda commit: held)


@landed(hasattr(verifying, "_not_behind"), "verify's not-in-this-clone refusal")
def test_the_readme_quotes_the_refusal_for_a_baseline_the_clone_does_not_hold(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])

    missing = _not_behind(shallow=False, held=False)
    rewritten = _not_behind(shallow=False, held=True)

    assert f"crapkit: {missing}" in _page("README.md")
    assert "not in this clone" in missing and f"git fetch origin {_BASELINE}" in missing
    assert "rewrote history" in rewritten, "a clone that holds the commit still blames a rewrite"


def test_the_readme_no_longer_says_a_full_clone_always_blames_a_rewrite():
    text = _prose(_page("README.md"))

    assert "On a full clone the same exit blames what it used to" not in text
    assert "does not hold it at all" in text


@landed(hasattr(verifying, "_not_behind"), "verify's not-in-this-clone refusal")
def test_the_changelog_quotes_the_not_in_this_clone_refusal(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    missing = _not_behind(shallow=False, held=False)
    head = missing.split(";")[0]

    assert head in _prose(_release())
    assert re.search(r"blamed a rebase or an amend", _prose(_release()))


# -- S23: the history caches key on the clone's depth ----------------------------

def _has_history_depth() -> bool:
    from crapkit import churn_log

    return hasattr(churn_log, "history_depth")


# Each part of the coupling cache's key, as the handbook names it.
_COUPLING_KEY_WORDS = {"head": "HEAD", "months": "the churn window", "date": "the UTC date",
                       "paths": "the path format", "depth": "history depth",
                       "tracked": "a digest of the tracked set"}


@landed(_has_history_depth(), "the history depth in the churn and coupling keys")
def test_the_handbook_names_every_part_of_the_coupling_cache_key(tmp_path):
    from crapkit import coupling_cache

    _one_commit_repo(tmp_path)
    key = coupling_cache._cache_key(tmp_path, 12, ["a.py"])
    sentence = next(s for s in _prose(_page("docs/handbook.html")).split(". ")
                    if s.startswith("Its key is HEAD"))

    assert set(key) == set(_COUPLING_KEY_WORDS)
    assert [word for word in _COUPLING_KEY_WORDS.values() if word not in sentence] == []
    assert "git fetch --unshallow</code> rebuilds it the same day" in sentence


def test_the_changelog_says_a_deepened_clone_rebuilds_the_history_caches():
    section = _prose(_release())

    assert "`git fetch --unshallow` or `--deepen` at an unmoved HEAD" in section
    assert "A cache 0.8.0 wrote reads as a full clone's" in section
