"""One published-cadence run of deploy.yml per release key.

deploy.yml ran its published cadence twice per release: once on the tag push
stage 2b makes (`on: push: tags`), before the PyPI upload it races, and once
when the surfaces stage dispatches it after every surface reads back. The tag
trigger is gone. The surfaces stage keys the run on what the published cadence
installs: the version, the sha256 of each file PyPI serves for it, and the
commit `git ls-remote origin refs/tags/vVERSION` returns. It dispatches on the
tag, so the run's head_sha is that commit, and names the run
`deploy published vVERSION <files digest>`. A green or running run under the
key means no dispatch; anything else (none, red, another tag commit after the
tag moved, other PyPI files) dispatches.
"""
from __future__ import annotations

import hashlib

import pytest
import yaml

from test_release_deploy_gate import WORKFLOW, render
from test_release_guards import git, repo
from test_release_tool import release

VERSION = "0.5.2"
TAG = "8fb7b45c7248c2ff71a472b40e1111c4a93674ce"
MOVED = "0123456789abcdef0123456789abcdef01234567"
PYPI = {"crapkit-0.5.2-py3-none-any.whl": "a1" * 32, "crapkit-0.5.2.tar.gz": "b2" * 32}
# sha256 over the sorted "<filename> <sha256>" lines, one per PyPI file.
DIGEST = hashlib.sha256(("crapkit-0.5.2-py3-none-any.whl " + "a1" * 32 + "\n"
                         "crapkit-0.5.2.tar.gz " + "b2" * 32).encode("utf-8")).hexdigest()
TITLE = f"deploy published v{VERSION} {DIGEST}"
RUNS_URL = ("https://api.github.com/repos/JeanFrancoisGagne/crapkit/actions/workflows/deploy.yml/"
            f"runs?head_sha={TAG}&event=workflow_dispatch&per_page=100")


def _run(title=TITLE, head=TAG, **changes) -> dict:
    """A published-cadence dispatch as the runs listing reports it."""
    return {"id": 36900000001, "name": "deploy", "event": "workflow_dispatch", "head_branch": f"v{VERSION}",
            "head_sha": head, "display_title": title, "status": "completed", "conclusion": "success",
            "run_attempt": 1, "html_url": "https://github.com/JeanFrancoisGagne/crapkit/actions/runs/36900000001",
            **changes}


class GitHub:
    """deploy.yml's runs, PyPI's files, origin's tag, and every command run."""

    def __init__(self, monkeypatch, runs, pypi=PYPI, tag=TAG):
        self.runs, self.pypi, self.tag = runs, pypi, tag
        self.asked, self.commands = [], []
        monkeypatch.setattr(release, "_remote_json", self.remote_json)
        monkeypatch.setattr(release, "_pypi_files", lambda version: dict(self.pypi))
        monkeypatch.setattr(release, "_remote_tag_commit", self.tag_commit)
        monkeypatch.setattr(release, "_execute", lambda command, root, dry_run, env=None: self.commands.append(command))

    def remote_json(self, url, *, absent=False):
        self.asked.append(url)
        return {"total_count": len(self.runs), "workflow_runs": [run for run in self.runs
                                                                  if f"head_sha={run['head_sha']}&" in url]}

    def tag_commit(self, root, version):
        if self.tag is None:
            raise release.ReleaseError(f"origin holds no tag v{version}")
        return self.tag


def _step():
    (step,) = [s for s in release.plan(VERSION) if s.name == "published"]
    return step


def _published(tmp_path):
    release._run_published(_step(), tmp_path, VERSION, {})


DISPATCH = ("gh", "workflow", "run", "deploy.yml", "--repo", "github.com/JeanFrancoisGagne/crapkit",
            "--ref", f"v{VERSION}", "-f", "cadence=published", "-f", f"ref=v{VERSION}", "-f", f"files={DIGEST}")


# --- the key: unchanged, nothing runs ------------------------------------------------------------

def test_a_green_run_under_the_key_dispatches_nothing(tmp_path, monkeypatch, capsys):
    github = GitHub(monkeypatch, [_run()])

    _published(tmp_path)

    assert github.commands == [] and github.asked == [RUNS_URL]
    assert "reuses" in capsys.readouterr().out


def test_a_run_still_going_under_the_key_dispatches_nothing(tmp_path, monkeypatch):
    github = GitHub(monkeypatch, [_run(status="in_progress", conclusion=None)])

    _published(tmp_path)

    assert github.commands == []


def test_one_green_run_among_red_ones_is_enough(tmp_path, monkeypatch):
    github = GitHub(monkeypatch, [_run(conclusion="failure", id=1), _run(id=2)])

    _published(tmp_path)

    assert github.commands == []


# --- the key: changed, one dispatch --------------------------------------------------------------

def test_no_run_dispatches_on_the_tag_named_for_the_pypi_files(tmp_path, monkeypatch):
    github = GitHub(monkeypatch, [])

    _published(tmp_path)

    assert github.commands == [DISPATCH]


@pytest.mark.parametrize("runs", [
    [_run(conclusion="failure")],
    [_run(conclusion="cancelled")],
    [_run(head=MOVED)],
    [_run(title=f"deploy published v{VERSION} " + "0" * 64)],
    [_run(title=f"deploy published v0.5.1 {DIGEST}")],
    [_run(title=f"deploy release {'3f' * 20}")],
    [_run(title=f"deploy published v{VERSION}")],
], ids=["red", "cancelled", "tag-moved", "other-pypi-files", "other-version", "release-cadence", "no-digest"])
def test_a_run_under_another_key_or_red_dispatches(tmp_path, monkeypatch, runs):
    github = GitHub(monkeypatch, runs)

    _published(tmp_path)

    assert github.commands == [DISPATCH]


def test_a_moved_tag_is_read_from_origin_and_keys_a_new_run(tmp_path, monkeypatch):
    """The green run tested the old tag commit through `uses: ...@vVERSION` and
    `rev: vVERSION`; after the tag moves it proves nothing about the new one."""
    github = GitHub(monkeypatch, [_run()], tag=MOVED)

    _published(tmp_path)

    assert github.commands == [DISPATCH]
    assert github.asked == [RUNS_URL.replace(TAG, MOVED)]


def test_another_pypi_file_moves_the_key(tmp_path, monkeypatch):
    github = GitHub(monkeypatch, [_run()], pypi={**PYPI, "crapkit-0.5.2-py3-none-win_amd64.whl": "c3" * 32})

    _published(tmp_path)

    (command,) = github.commands
    assert command[:-1] == DISPATCH[:-1] and command[-1] != DISPATCH[-1]


def test_the_files_digest_ignores_the_order_pypi_lists_them_in():
    assert release.pypi_digest(PYPI) == release.pypi_digest(dict(reversed(PYPI.items()))) == DIGEST


# --- refusals before anything is dispatched -------------------------------------------------------

def test_a_tag_origin_does_not_hold_refuses(tmp_path, monkeypatch):
    github = GitHub(monkeypatch, [], tag=None)

    with pytest.raises(release.ReleaseError, match=f"origin holds no tag v{VERSION}"):
        _published(tmp_path)

    assert github.commands == []


def test_a_version_pypi_lists_no_file_for_refuses(tmp_path, monkeypatch):
    github = GitHub(monkeypatch, [], pypi={})

    with pytest.raises(release.ReleaseError, match=f"PyPI lists no file for crapkit {VERSION}"):
        _published(tmp_path)

    assert github.commands == []


# --- origin's tag commit ---------------------------------------------------------------------------

def test_the_tag_commit_is_read_from_origin_for_a_plain_and_an_annotated_tag(tmp_path):
    root = repo(tmp_path, bumped=True)
    head = git(root, "rev-parse", "HEAD")
    git(root, "tag", f"v{VERSION}")
    git(root, "push", "-q", "origin", f"v{VERSION}")

    assert release._remote_tag_commit(root, VERSION) == head

    git(root, "tag", "-f", "-a", "-m", "annotated", f"v{VERSION}")
    git(root, "push", "-q", "-f", "origin", f"v{VERSION}")

    assert release._remote_tag_commit(root, VERSION) == head


def test_a_tag_only_the_local_checkout_holds_is_not_on_origin(tmp_path):
    root = repo(tmp_path, bumped=True)
    git(root, "tag", f"v{VERSION}")

    with pytest.raises(release.ReleaseError, match=f"origin holds no tag v{VERSION}"):
        release._remote_tag_commit(root, VERSION)


# --- the plan and the workflow ---------------------------------------------------------------------

def test_the_surfaces_stage_hands_the_published_step_to_its_handler(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(release, "_run_published", lambda step, root, version, receipt: seen.append(step.name))
    monkeypatch.setattr(release, "_execute", lambda *args, **kwargs: pytest.fail("ran a command"))

    release._stage_step(_step(), tmp_path, VERSION, {}, 0)

    assert seen == ["published"]


def test_the_run_is_named_for_the_version_and_the_files_digest():
    title = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["run-name"]

    assert render(title, "workflow_dispatch", {"cadence": "published", "ref": f"v{VERSION}", "files": DIGEST}) \
        == TITLE == release.published_title(VERSION, DIGEST)
    assert render(title, "workflow_dispatch", {"cadence": "release", "tree": "3f" * 20}) \
        == release.deploy_title("3f" * 20)


def test_the_workflow_takes_the_files_digest_and_no_tag_push():
    on = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = on.get("on", on.get(True))

    assert on["workflow_dispatch"]["inputs"]["files"]["default"] == ""
    assert "push" not in on
