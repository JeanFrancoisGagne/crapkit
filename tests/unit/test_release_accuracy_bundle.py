"""The accuracy stage hands the local tier the retro history bundle, or refuses.

The release tier's retro row replays the bundle rows, whose commits live only in
the pre-2026-08-24 history bundle. retro.py reads that bundle from
CRAPKIT_RETRO_BUNDLE and exits 3 when it is unset, so 0.8.1's local tier could
pass only with the variable set by hand in the shell that ran the stage.
release.py now sets it for the tier from CRAPKIT_RETRO_BUNDLE, or else from the
path tools/release/retro-bundle.path names, and refuses the stage by name before
the tier starts when that path holds no file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_release_accuracy_gate import FILES, HEAD, VERSION, _receipt, _run, _saved, _write
from test_release_tool import release

ROOT = Path(__file__).resolve().parents[2]
RERUN = f"rerun `python tools/release/release.py run accuracy {VERSION}`"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A release tree with no passing receipt yet, so the local tier must run."""
    monkeypatch.delenv("CRAPKIT_RETRO_BUNDLE", raising=False)
    root = tmp_path / "tree"
    _write(root, FILES)
    return root


@pytest.fixture
def bundle(tmp_path) -> Path:
    path = tmp_path / "history" / "crapkit-history.bundle"
    path.parent.mkdir()
    path.write_bytes(b"# v2 git bundle\n")
    return path


class Tier:
    """The commands the stage starts with the environment each one is given."""

    def __init__(self):
        self.ran = []

    def execute(self, command, root, dry_run, env=None):
        self.ran.append((command, dict(env or {})))
        if "--tier" in command:
            _receipt(root, _saved())


@pytest.fixture
def tier(monkeypatch):
    fake = Tier()
    monkeypatch.setattr(release, "_execute", fake.execute)
    monkeypatch.setattr(release, "_remote_json",
                        lambda url, *, absent=False: {"workflow_runs": [_run()]})
    return fake


def _step():
    (step,) = [s for s in release.plan(VERSION) if s.name == "accuracy"]
    return step


def _local(root):
    release._local_accuracy(_step(), root, VERSION, HEAD)


def _refusal(root) -> str:
    with pytest.raises(release.ReleaseError) as refused:
        _local(root)
    return str(refused.value)


def _config(root, text):
    path = root / "tools" / "release" / "retro-bundle.path"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_tier_runs_with_the_bundle_the_environment_names(tree, tier, bundle, monkeypatch):
    monkeypatch.setenv("CRAPKIT_RETRO_BUNDLE", str(bundle))

    _local(tree)

    ((command, env),) = tier.ran
    assert command[1:4] == ("tools/accuracy/run.py", "--tier", "release")
    assert env == {"CRAPKIT_RETRO_BUNDLE": str(bundle)}


def test_without_the_variable_the_file_under_tools_release_names_the_bundle(tree, tier, bundle,
                                                                             monkeypatch):
    monkeypatch.setenv("HOME", str(bundle.parent.parent))
    monkeypatch.setenv("USERPROFILE", str(bundle.parent.parent))
    _config(tree, "# the history bundle the retro bundle rows replay from\n~/history/crapkit-history.bundle\n")

    _local(tree)

    ((_, env),) = tier.ran
    assert Path(env["CRAPKIT_RETRO_BUNDLE"]) == bundle


def test_the_variable_wins_over_the_file(tree, tier, bundle, monkeypatch):
    monkeypatch.setenv("CRAPKIT_RETRO_BUNDLE", str(bundle))
    _config(tree, "~/nowhere.bundle\n")

    _local(tree)

    assert tier.ran[0][1] == {"CRAPKIT_RETRO_BUNDLE": str(bundle)}


def test_a_bundle_path_that_holds_no_file_refuses_before_the_tier(tree, tier, tmp_path, monkeypatch):
    missing = tmp_path / "gone.bundle"
    monkeypatch.setenv("CRAPKIT_RETRO_BUNDLE", str(missing))

    said = _refusal(tree)

    assert said == (f"the accuracy stage needs the retro history bundle: CRAPKIT_RETRO_BUNDLE names "
                    f"{missing}, which is not a file. The release tier's retro row replays the bundle "
                    f"rows, whose commits live only in that bundle; put it there, then {RERUN}")
    assert tier.ran == []


def test_a_file_naming_a_missing_bundle_refuses_by_the_files_name(tree, tier, tmp_path):
    missing = tmp_path / "gone.bundle"
    _config(tree, f"{missing}\n")

    assert "tools/release/retro-bundle.path names " + str(missing) in _refusal(tree)
    assert tier.ran == []


def test_no_bundle_configured_anywhere_refuses_and_says_where_to_name_one(tree, tier):
    said = _refusal(tree)

    assert said == ("the accuracy stage needs the retro history bundle: set CRAPKIT_RETRO_BUNDLE, or "
                    "name it in tools/release/retro-bundle.path. The release tier's retro row replays "
                    "the bundle rows, whose commits live only in that bundle; then " + RERUN)
    assert tier.ran == []


def test_a_passing_receipt_needs_no_bundle_and_runs_nothing(tree, tier):
    """The tier does not run again for a tree that holds its passing receipt."""
    _receipt(tree, _saved())

    _local(tree)

    assert tier.ran == []


def test_the_bundle_reaches_the_tier_process(tmp_path, bundle, capfd):
    code = "import os; print(os.environ['CRAPKIT_RETRO_BUNDLE'])"

    release._execute((release.PY, "-c", code), tmp_path, False, {"CRAPKIT_RETRO_BUNDLE": str(bundle)})

    assert capfd.readouterr().out.splitlines()[-1] == str(bundle)


def test_the_release_tree_names_a_history_bundle():
    lines = [line.strip() for line in
             (ROOT / "tools" / "release" / "retro-bundle.path").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]

    assert len(lines) == 1 and lines[0].endswith(".bundle")


def test_a_dry_run_reads_no_bundle(tree, capsys):
    assert release.main(["run", "accuracy", VERSION, "--repo", str(tree), "--dry-run"]) == 0
    assert "tools/accuracy/run.py --tier release" in capsys.readouterr().out
