"""An override with no alert_command is refused before anything happens.

The audited override needs an alert line, and the audit refused one without
`alert_command`, but only after verify had run every lane and stored a run
with no verdict: the lane's warnings printed, `crapkit runs` listed a new
`verify verdict=-` run, and then the refusal came with exit 3. The hook's
grant stored a run of its own before the same refusal. docs/ratchet.md
promises the refusal comes first. Both now refuse right after the config is
read.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli_inproc_repo import add_knotty, commit_all, git, repo, seed_artifacts, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.store import SnapshotStore

REASON = "shipping the hotfix, ticket 412"


@pytest.fixture()
def unalerted(repo: Path, capsys) -> Path:  # noqa: F811
    """A measured repo with no alert_command and a gate violation in the tree."""
    toml = repo / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('alert_command = "python append_alert.py"\n', ""),
                    encoding="utf-8", newline="\n")
    commit_all(repo, "no alert")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    capsys.readouterr()
    return repo


def _run_ids(root: Path) -> list[int]:
    return [run["id"] for run in SnapshotStore(root / ".crapkit" / "crap.sqlite").list_runs()]


def test_verify_refuses_before_it_stores_a_run(unalerted, capsys):
    code = main(["verify", "--reuse-artifacts", "--override", REASON, "--repo", str(unalerted)])

    err = capsys.readouterr().err
    assert code == 3, err
    assert "no alert_command configured - the override requires a visible alert line" in err
    assert "warning:" not in err, "a lane's verdict lines printed before the refusal"
    assert _run_ids(unalerted) == [1]


def test_verify_refuses_before_any_lane_runs(unalerted, capsys):
    code = main(["verify", "--override", REASON, "--repo", str(unalerted)])

    assert code == 3, capsys.readouterr().err
    assert not list((unalerted / ".crapkit").glob("lane-*.log")), "a lane ran"
    assert _run_ids(unalerted) == [1]


def test_the_hook_refuses_before_it_stores_a_run(unalerted, capsys, monkeypatch):
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", REASON)
    git(unalerted, "add", "-A")

    code = main(["hook-precommit", "--repo", str(unalerted)])

    assert code == 3, capsys.readouterr()
    assert _run_ids(unalerted) == [1]
    assert git(unalerted, "diff", "--cached", "--name-only").split() == ["src/app.ts"]


def test_a_configured_alert_still_grants_the_override(repo, capsys):  # noqa: F811
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)

    code = main(["verify", "--reuse-artifacts", "--override", REASON, "--repo", str(repo)])

    assert code == 0, capsys.readouterr()
    assert REASON in (repo / "alerts.log").read_text(encoding="utf-8")
