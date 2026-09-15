"""stage2b's Pages step agrees with `verify` on what a Pages build proves.

`verify` accepts the newest build when its commit carries the release commit
(b83c218). The publish side kept an equality test, so the 0.7.3 rerun read the
tip build main had moved to, answered "other commit", and would have asked for
a second POST. Both sides now use the same ancestry answer.
"""
import pytest

from test_release_recovery import VERSION, publish_adapter, receipt as read_receipt
from test_release_guards import repo, verified
from test_release_tool import release

HEAD = "0123456789abcdef0123456789abcdef01234567"
LATER = "fedcba9876543210fedcba9876543210fedcba98"


def _pages(monkeypatch, tmp_path, build, carries):
    """A release receipt at HEAD, a Pages API answering `build`, and an
    ancestry answer supplied by the test instead of git."""
    monkeypatch.setattr(release, "_remote_json", lambda url, *, absent=False: build)
    monkeypatch.setattr(release, "_contains", lambda root, ancestor, built: carries(ancestor, built))
    return tmp_path, {"version": VERSION, "head": HEAD}


def test_a_build_at_a_descendant_of_the_head_confirms_pages(tmp_path, monkeypatch):
    root, rec = _pages(monkeypatch, tmp_path, {"commit": LATER, "status": "built"},
                       lambda ancestor, built: (ancestor, built) == (HEAD, LATER))

    assert release._pages_built(root, rec) is True


def test_a_build_that_does_not_carry_the_head_is_another_commit(tmp_path, monkeypatch):
    root, rec = _pages(monkeypatch, tmp_path, {"commit": LATER, "status": "built"},
                       lambda ancestor, built: False)

    assert release._pages_state(root, rec) == "other commit"
    assert release._pages_built(root, rec) is False


def test_a_build_at_the_head_itself_is_still_built(tmp_path, monkeypatch):
    root, rec = _pages(monkeypatch, tmp_path, {"commit": HEAD, "status": "built"},
                       lambda ancestor, built: ancestor == built)

    assert release._pages_state(root, rec) == "built"
    assert release._pages_built(root, rec) is True


@pytest.mark.parametrize("status", ["queued", "building"])
def test_a_pending_build_at_a_descendant_still_asks_to_wait(tmp_path, monkeypatch, status):
    root, rec = _pages(monkeypatch, tmp_path, {"commit": LATER, "status": status},
                       lambda ancestor, built: (ancestor, built) == (HEAD, LATER))

    with pytest.raises(release.ReleaseError, match="Pages build is pending"):
        release._pages_built(root, rec)


@pytest.mark.parametrize("build, state", [(None, "absent"), ({"commit": LATER, "status": "errored"}, "errored")])
def test_absent_and_errored_answers_are_unchanged(tmp_path, monkeypatch, build, state):
    root, rec = _pages(monkeypatch, tmp_path, build, lambda ancestor, built: True)

    assert release._pages_state(root, rec) == state
    assert release._pages_built(root, rec) is False


def test_a_malformed_answer_is_still_refused(tmp_path, monkeypatch):
    root, rec = _pages(monkeypatch, tmp_path, {"commit": None, "status": "built"}, lambda ancestor, built: True)

    with pytest.raises(release.ReleaseError, match="cannot confirm Pages build"):
        release._pages_state(root, rec)


def test_stage2b_rerun_records_pages_done_when_the_site_carries_the_release(tmp_path, monkeypatch):
    """The 0.7.3 shape: the release published, main moved two commits on, Pages
    built the tip. A rerun must read that build as the release being live, not
    ask GitHub for a second build."""
    from test_release_guards import git
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("pages") == 1
    head = read_receipt(root)["head"]

    # The checkout stays at the tag (stage2b insists on it); main moves on from
    # another clone and the rerun's repository has fetched it, as JF's had.
    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", "--branch", "main", str(tmp_path / "remote.git"), str(other))
    git(other, "-c", "user.name=Release Test", "-c", "user.email=release@example.test",
        "commit", "-q", "--allow-empty", "-m", "main moves past the release")
    git(other, "push", "--quiet", "origin", "main")
    later = git(other, "rev-parse", "HEAD")
    git(root, "fetch", "--quiet", "origin")
    assert later != head and git(root, "rev-parse", "HEAD") == head
    adapter.pages = {"commit": later, "status": "built"}
    before = list(adapter.events)

    release.run("stage2b", VERSION, root)
    assert adapter.events == before
    assert not read_receipt(root)["pending"]
