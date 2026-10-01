"""Stage 2b ships past a gate only when release.py's ADVISORY table lists it.

0.8.1 shipped with accuracy.yml's release mode and deploy.yml's release cadence
advisory: neither had ever run before, neither could pass at that tag, and a
person ruled the release could go out on the gates that had run. That ruling
lived in a scratch wrapper that monkeypatched release.py's problem functions.
The table in release.py replaces it: one row per (version, gate) with the
reason. A listed gate's problems are printed as advisory and written into the
release receipt under "advisory"; any gate the table does not list for the
version still refuses, and the local accuracy receipt is never advisory.
"""
from __future__ import annotations

import json

import pytest

from test_release_accuracy_gate import FILES, _receipt as _accuracy_receipt, _saved, _write
from test_release_accuracy_gate import _run as _accuracy_run
from test_release_deploy_gate import VERSION, _gated_repo, _record, _run
from test_release_guards import git
from test_release_recovery import publish_adapter
from test_release_tool import release


def _answer(adapter, deploy_runs, accuracy_runs=()):
    publications = adapter.remote_json

    def remote_json(url, *, absent=False):
        if "/actions/workflows/deploy.yml/runs" in url:
            return {"workflow_runs": list(deploy_runs)}
        if "/actions/workflows/accuracy.yml/runs" in url:
            return {"workflow_runs": list(accuracy_runs)}
        return publications(url, absent=absent)
    return remote_json


def _saved_receipt(root) -> dict:
    return json.loads((root / ".crapkit" / "release-receipt.json").read_text(encoding="utf-8"))


@pytest.fixture
def deploy_repo(tmp_path, monkeypatch):
    """A tree the deploy gate reads, whose receipt holds no deploy record."""
    root = _gated_repo(tmp_path, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    monkeypatch.setattr(release, "_remote_json", _answer(adapter, []))
    return root, adapter


def test_a_listed_gate_does_not_refuse_and_lands_in_the_receipt(deploy_repo, monkeypatch, capsys):
    root, adapter = deploy_repo
    monkeypatch.setattr(release, "ADVISORY", {(VERSION, "deploy"): "the cadence never ran before"})

    release.run("stage2b", VERSION, root)

    redo = f"rerun `python tools/release/release.py run deploy {VERSION}`"
    line = f"deploy gate: the release receipt holds no deploy record; {redo}"
    assert "push" in adapter.events
    assert f"advisory (deploy): {line}" in capsys.readouterr().out
    assert _saved_receipt(root)["advisory"] == {
        "deploy": {"reason": "the cadence never ran before", "problems": [line]}}


def test_the_same_gate_for_an_unlisted_version_refuses(deploy_repo, monkeypatch):
    root, adapter = deploy_repo
    monkeypatch.setattr(release, "ADVISORY", {("9.9.9", "deploy"): "another release's ruling"})

    with pytest.raises(release.ReleaseError, match="^deploy gate: the release receipt holds no deploy record"):
        release.run("stage2b", VERSION, root)

    assert adapter.events == []
    assert "advisory" not in _saved_receipt(root)


def test_another_gate_listed_for_the_version_leaves_this_one_refusing(deploy_repo, monkeypatch):
    root, adapter = deploy_repo
    monkeypatch.setattr(release, "ADVISORY", {(VERSION, "accuracy-remote"): "a ruling on accuracy only"})

    with pytest.raises(release.ReleaseError, match="^deploy gate: "):
        release.run("stage2b", VERSION, root)

    assert adapter.events == []


def test_a_gate_that_passes_writes_no_advisory(deploy_repo, monkeypatch):
    root, adapter = deploy_repo
    head = git(root, "rev-parse", "HEAD")
    _record(root, sha=head, tree="3f1c09a2b7de" + "0" * 28)
    monkeypatch.setattr(release, "_remote_json", _answer(adapter, [_run(head=head)]))
    monkeypatch.setattr(release, "ADVISORY", {(VERSION, "deploy"): "the cadence never ran before"})

    release.run("stage2b", VERSION, root)

    assert "push" in adapter.events and "advisory" not in _saved_receipt(root)


# --- accuracy: the remote run may be advisory, the local receipt never ------------------------

@pytest.fixture
def accuracy_repo(tmp_path, monkeypatch):
    """A tree the accuracy gate reads (and no deploy kit), with no accuracy.yml run."""
    from test_release_accuracy_gate import _gated_repo as gated
    root = gated(tmp_path, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    monkeypatch.setattr(release, "_remote_json", _answer(adapter, [], []))
    monkeypatch.setattr(release, "ADVISORY", {(VERSION, "accuracy-remote"): "no ghcr image package"})
    return root, adapter


def test_a_listed_remote_accuracy_run_does_not_refuse(accuracy_repo, capsys):
    root, adapter = accuracy_repo
    _accuracy_receipt(root, _saved(head=git(root, "rev-parse", "HEAD")))

    release.run("stage2b", VERSION, root)

    assert "push" in adapter.events
    (line,) = _saved_receipt(root)["advisory"]["accuracy-remote"]["problems"]
    assert line.startswith("GitHub holds no successful accuracy.yml run named `accuracy release 0.5.2`")
    assert f"advisory (accuracy-remote): {line}" in capsys.readouterr().out


def test_a_failed_local_receipt_refuses_whatever_the_table_says(accuracy_repo):
    root, adapter = accuracy_repo
    _accuracy_receipt(root, _saved(head=git(root, "rev-parse", "HEAD"), outcome="fail"))

    with pytest.raises(release.ReleaseError, match="not a passing release tier"):
        release.run("stage2b", VERSION, root)

    assert adapter.events == []


# --- the table itself -------------------------------------------------------------------------

def test_every_row_names_a_gate_that_can_be_advisory_and_says_why():
    assert release.ADVISORY_GATES == ("accuracy-remote", "deploy")
    for (version, gate), reason in release.ADVISORY.items():
        assert release._parse(version) and gate in release.ADVISORY_GATES
        assert isinstance(reason, str) and len(reason) > 40


def test_081_shipped_with_both_remote_gates_advisory():
    assert {gate for version, gate in release.ADVISORY if version == "0.8.1"} == {"accuracy-remote", "deploy"}


# --- one gate function for the accuracy stage and stage 2b -------------------------------------
#
# The calc row "Release accuracy gate" mutates release.accuracy_gate and the
# problem functions it calls. Round 1 gave stage 2b a copy of that logic, so a
# mutant in accuracy_gate no longer reached stage 2b's refusal. Both stages
# now refuse through accuracy_gate itself.

class Spy:
    """release.accuracy_gate, with what each call raised."""

    def __init__(self, monkeypatch):
        self.real, self.raised, self.calls = release.accuracy_gate, [], 0
        monkeypatch.setattr(release, "accuracy_gate", self)

    def __call__(self, *args, **kwargs):
        self.calls += 1
        try:
            return self.real(*args, **kwargs)
        except release.ReleaseError as exc:
            self.raised.append(str(exc))
            raise


def test_a_planted_receipt_problem_refuses_both_stages_through_accuracy_gate(accuracy_repo, monkeypatch):
    root, adapter = accuracy_repo
    head = git(root, "rev-parse", "HEAD")
    _accuracy_receipt(root, _saved(head=head, tier="push"))
    monkeypatch.setattr(release, "_remote_json", _answer(adapter, [], [_accuracy_run(head=head)]))
    monkeypatch.setattr(release, "_local_accuracy", lambda *args: None)
    monkeypatch.setattr(release, "_remote_accuracy", lambda *args: None)
    spy = Spy(monkeypatch)

    (step,) = [s for s in release.plan(VERSION) if s.name == "accuracy"]
    with pytest.raises(release.ReleaseError, match="not a passing release tier") as accuracy_stage:
        release._run_accuracy(step, root, VERSION, {"head": head})
    with pytest.raises(release.ReleaseError, match="not a passing release tier") as stage2b:
        release.run("stage2b", VERSION, root)

    assert spy.raised == [str(accuracy_stage.value), str(stage2b.value)]
    assert adapter.events == []


def test_a_listed_remote_problem_passes_accuracy_gate_as_an_advisory_line(accuracy_repo, capsys, monkeypatch):
    root, adapter = accuracy_repo
    _accuracy_receipt(root, _saved(head=git(root, "rev-parse", "HEAD")))
    spy = Spy(monkeypatch)

    release.run("stage2b", VERSION, root)

    (line,) = _saved_receipt(root)["advisory"]["accuracy-remote"]["problems"]
    assert spy.calls > 0 and spy.raised == [] and "push" in adapter.events
    assert f"advisory (accuracy-remote): {line}" in capsys.readouterr().out
    with pytest.raises(release.ReleaseError, match="GitHub holds no successful accuracy.yml run"):
        release.accuracy_gate(root, VERSION, git(root, "rev-parse", "HEAD"))
