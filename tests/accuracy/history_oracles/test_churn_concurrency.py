"""A commit that lands between crapkit's read of HEAD and its walk of the window.

The seam is churn_cache's HEAD read, wrapped so the first call commits once
more right after it answers: another session committing in the same worktree
at that moment. The next read carries the table to the new HEAD, and the new
commit must count once. The expected values are git's own window at the final
HEAD (oracles/git_walk.py). This test drives crapkit's API in-process to reach
the seam, so it is not a calc's independent test.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from accuracy.kit import repos
from accuracy.history_oracles.oracles import git_walk
from accuracy.history_oracles.repos import history_specs as specs
from crapkit import churn_cache

pytestmark = pytest.mark.process


def _land_a_commit(built: repos.Built) -> None:
    (built.top / "src" / "a.py").write_text(specs.functions("f", branches=3), encoding="utf-8")
    (built.top / "src" / "c.py").write_text(specs.functions("h"), encoding="utf-8")
    repos.git(built.top, "add", "--", "src/a.py", "src/c.py")
    repos.git(built.top, "commit", "-q", "-m", "landed mid-walk", date=specs.INJECT_LANDS,
              author=specs.CHEN)


def _read_then_commit(built: repos.Built, real, landed: list):
    def head_commit(root):
        head = real(root)
        if not landed:
            _land_a_commit(built)
            landed.append(head)
        return head
    return head_commit


def _said(table: dict) -> dict:
    return {path: (c.commits, c.authors, Decimal(repr(c.weight))) for path, c in table.items()}


def test_commit_during_walk_counts_once(make_repo, monkeypatch):
    built, landed = make_repo(specs.INJECT), []
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(specs.INJECT_NOW))
    monkeypatch.setattr(churn_cache, "head_commit",
                        _read_then_commit(built, churn_cache.head_commit, landed))

    churn_cache.load_churn(built.root, 12)
    carried = churn_cache.load_churn(built.root, 12)

    walked = git_walk.churn(git_walk.walk(built.root, 12))
    assert landed and walked["src/a.py"].commits == 3
    assert _said(carried) == {path: (c.commits, c.authors, c.weight)
                              for path, c in walked.items()}
