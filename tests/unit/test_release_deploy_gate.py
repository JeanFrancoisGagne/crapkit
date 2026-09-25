"""A release waits for the deploy suite, and hands it the published version.

`release.py check` is stage 1's first command. It now also asks gh for
deploy.yml's runs at HEAD and refuses unless one of them ran the release
cadence green, so a candidate nobody installed through pip, uv, the plugin
marketplaces and the hook routes never gets bumped. After the surfaces read
back, the `surfaces` stage dispatches the published cadence, which installs
the new version from PyPI, the tag and the registry the way a user does.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from test_release_tool import _tree, release

GREEN = {"event": "workflow_dispatch", "displayTitle": "deploy release", "conclusion": "success",
         "url": "https://github.com/JeanFrancoisGagne/crapkit/actions/runs/1"}


def _git(root, *arguments):
    return subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = _tree(tmp_path)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.name=Release Test", "-c", "user.email=release@example.test",
         "add", ".")
    _git(root, "-c", "user.name=Release Test", "-c", "user.email=release@example.test",
         "commit", "-qm", "fixture")
    return root


def _answering(*runs):
    asked = []

    def runs_at(root, head):
        asked.append(head)
        return list(runs)
    runs_at.asked = asked
    return runs_at


def test_a_green_release_cadence_run_at_head_opens_the_gate(repo):
    runs = _answering(GREEN)

    assert release.deploy_gate(repo, runs=runs) == []
    assert runs.asked == [_git(repo, "rev-parse", "HEAD")], "the gate asks about HEAD, nothing else"


@pytest.mark.parametrize("run", [
    {**GREEN, "event": "schedule", "displayTitle": "deploy schedule"},
    {**GREEN, "displayTitle": "deploy nightly"},
    {**GREEN, "event": "pull_request", "displayTitle": "deploy release"},
], ids=["nightly-schedule", "dispatched-nightly", "a-pull-request-titled-release"])
def test_a_green_run_of_another_cadence_leaves_it_shut(repo, run):
    """A nightly at the same commit ran fewer cells than the release cadence
    (no weekly, no online), so its green says nothing about the release."""
    (problem,) = release.deploy_gate(repo, runs=_answering(run))

    assert problem.startswith("deploy gate: no green release-cadence run of deploy.yml at ")
    assert "gh workflow run deploy.yml --ref main -f cadence=release" in problem


def test_a_failed_release_run_is_named_with_its_url(repo):
    failed = {**GREEN, "conclusion": "failure"}
    running = {**GREEN, "conclusion": "", "url": "https://github.com/x/actions/runs/2"}

    (problem,) = release.deploy_gate(repo, runs=_answering(failed, running))

    assert "failure https://github.com/JeanFrancoisGagne/crapkit/actions/runs/1" in problem
    assert "not finished https://github.com/x/actions/runs/2" in problem


def test_a_gh_that_cannot_answer_shuts_the_gate(repo):
    def logged_out(root, head):
        raise release.ReleaseError("gh run list failed: To get started with GitHub CLI, please run:  gh auth login")

    assert release.deploy_gate(repo, runs=logged_out) == [
        "deploy gate: gh run list failed: To get started with GitHub CLI, please run:  gh auth login"]


def test_a_tree_with_no_head_shuts_the_gate_before_asking_gh(tmp_path):
    runs = _answering(GREEN)

    (problem,) = release.deploy_gate(_tree(tmp_path), runs=runs)

    assert problem.startswith("deploy gate: ")
    assert runs.asked == []


def test_gh_is_asked_for_deploy_yml_at_the_commit(repo, monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([GREEN]), "")
    monkeypatch.setattr(release.subprocess, "run", fake_run)

    assert release._deploy_runs(repo, "abc123") == [GREEN]
    (argv,) = seen
    assert argv[:3] == ["gh", "run", "list"]
    assert argv[argv.index("--workflow") + 1] == "deploy.yml"
    assert argv[argv.index("--commit") + 1] == "abc123"
    assert set(argv[argv.index("--json") + 1].split(",")) >= {"conclusion", "event", "displayTitle"}


def test_a_failing_gh_names_its_first_error_line(repo, monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 4, "", "HTTP 401: Bad credentials\nmore\n")
    monkeypatch.setattr(release.subprocess, "run", fake_run)

    with pytest.raises(release.ReleaseError, match="^gh run list failed: HTTP 401: Bad credentials$"):
        release._deploy_runs(repo, "abc123")


def test_the_cli_check_refuses_until_the_deploy_suite_passed(repo, monkeypatch, capsys):
    monkeypatch.setattr(release, "preflight", lambda: [])
    monkeypatch.setattr(release, "_deploy_runs", _answering())

    assert release.main(["check", "0.5.2", "--repo", str(repo)]) == 1
    assert "deploy gate: no green release-cadence run" in capsys.readouterr().out

    monkeypatch.setattr(release, "_deploy_runs", _answering(GREEN))
    assert release.main(["check", "0.5.2", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.strip() == "ok: every surface at 0.5.1, 0.5.2 next"


def test_the_surfaces_stage_dispatches_the_published_cadence_after_the_readback():
    steps = [step for step in release.plan("0.5.2") if step.stage == "surfaces"]

    assert [step.name for step in steps] == ["surfaces", "published"]
    (command,) = steps[1].commands
    assert command[:4] == ("gh", "workflow", "run", "deploy.yml")
    assert command[command.index("--ref") + 1] == "main"
    assert ("-f", "cadence=published") == command[-4:-2]
    assert ("-f", "ref=v0.5.2") == command[-2:]


def test_a_dry_run_of_the_surfaces_stage_prints_the_dispatch(tmp_path, capsys):
    assert release.main(["run", "surfaces", "0.5.2", "--repo", str(_tree(tmp_path)), "--dry-run"]) == 0

    printed = capsys.readouterr().out
    assert printed.index("verify 0.5.2") < printed.index("gh workflow run deploy.yml")
    assert "cadence=published" in printed and "ref=v0.5.2" in printed
