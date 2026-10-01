"""The release accuracy gate's refusals, each whole line written out by hand.

tests/unit/test_release_accuracy_gate.py checks each refusal's key words; this
module pins every line in full, on real files, and reads the URL the gate asks
GitHub for. It loads release.py by path, the way mutmut's launcher maps a
mutated file to its mutants, so these are the tests that judge the gate's
mutants in the weekly run (the unit tests import the tool under a bare name
mutmut cannot map).

The digests are hashlib's sha256 of the fixture bytes. A short sha in a line is
the first 12 hex digits, as `git log --abbrev=12` prints one.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
VERSION = "0.9.0"
HEAD = "8fb7b45c7248c2ff71a472b40e1111c4a93674ce"
OTHER = "0123456789abcdef0123456789abcdef01234567"
PINS, CORPUS, LEDGER = ("tools/accuracy/pins.toml", "tests/accuracy/corpus_goldens/corpus.toml",
                        "tests/accuracy/suite_strength/retro/ledger.tsv")
FILES = {"tools/accuracy/run.py": b"# the tier runner\n", PINS: b'[radon]\nversion = "6.0.1"\n',
         CORPUS: b'[requests]\ncommit = "0e322af"\n', LEDGER: b"id\ttest\nR08\tt::a\n"}
RERUN = f"rerun `python tools/release/release.py run accuracy {VERSION}`"
RUNS_URL = ("https://api.github.com/repos/JeanFrancoisGagne/crapkit/actions/workflows/"
            f"accuracy.yml/runs?head_sha={HEAD}&event=workflow_dispatch&per_page=100")
GOOD_RUN = {"id": 1, "display_title": f"accuracy release {VERSION}", "head_sha": HEAD,
            "status": "completed", "conclusion": "success"}
NO_RUN = (f"GitHub holds no successful accuracy.yml run named `accuracy release {VERSION}` "
          f"at 8fb7b45c7248; {RERUN}")


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_release_refusals_tool",
                                                  REPO / "tools" / "release" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _saved(**changes) -> dict:
    saved = {"head": HEAD, "tier": "release", "shard": None, "local": True, "os_sensitive": False,
             "outcome": "pass",
             "digests": {name: _sha(FILES[name]) for name in (PINS, CORPUS, LEDGER)},
             "checks": [{"key": "kit", "name": "the contract", "outcome": "pass"}]}
    return {**saved, **changes}


@pytest.fixture
def tree(tmp_path):
    for name, raw in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(raw)
    return tmp_path


def _receipt(root: Path, saved) -> None:
    path = root / ".crapkit" / f"release-accuracy-{VERSION}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved), encoding="utf-8")


@pytest.fixture
def github(monkeypatch):
    """GitHub's answer to the runs query, and every URL the gate asked."""
    state = {"answer": {"workflow_runs": [GOOD_RUN]}, "asked": []}

    def remote_json(url, *, absent=False):
        state["asked"].append(url)
        return state["answer"]

    monkeypatch.setattr(release, "_remote_json", remote_json)
    return state


def _lines(root: Path) -> list[str]:
    with pytest.raises(release.ReleaseError) as refused:
        release.accuracy_gate(root, VERSION, HEAD)
    return str(refused.value).split("\n")


def test_a_tree_with_every_proof_passes_and_asks_for_the_dispatched_runs_at_head(tree, github):
    _receipt(tree, _saved())

    assert release.accuracy_gate(tree, VERSION, HEAD) is None
    assert github["asked"] == [RUNS_URL]


def test_a_tree_without_the_tier_runner_is_not_gated(tree, github):
    (tree / "tools/accuracy/run.py").unlink()

    assert release.accuracy_gate(tree, VERSION, HEAD) is None
    assert github["asked"] == []


def test_a_missing_receipt_names_its_path(tree, github):
    assert _lines(tree) == [f"no readable release accuracy receipt at "
                            f".crapkit/release-accuracy-{VERSION}.json; {RERUN}"]


def test_every_receipt_problem_and_the_missing_run_are_one_line_each(tree, github):
    github["answer"] = {"workflow_runs": []}
    changed = b"# another corpus\n"
    (tree / CORPUS).write_bytes(changed)
    _receipt(tree, _saved(head=OTHER, tier="push", outcome="fail", local=False,
                          checks=[{"key": "kit", "name": "the contract", "outcome": "fail"}]))

    assert _lines(tree) == [
        f"the release accuracy receipt was made at 0123456789ab and the release is at "
        f"8fb7b45c7248; {RERUN}",
        f"the release accuracy receipt records the push tier with outcome fail, not a passing "
        f"release tier; {RERUN}",
        'the release accuracy receipt was selected with {"local": false, "os_sensitive": false, '
        '"shard": null}, not the release tier\'s own selection {"local": true, "os_sensitive": '
        f'false, "shard": null}}; {RERUN}',
        f"release accuracy row `kit: the contract` fail: fix what it names, then {RERUN}",
        f"{CORPUS} hashes to {_sha(changed)[:12]} here and the release accuracy receipt says "
        f"{_sha(FILES[CORPUS])[:12]}; {RERUN}",
        NO_RUN]
    assert github["asked"] == [RUNS_URL]


def test_a_row_that_is_not_an_object_reads_unreadable(tree, github):
    _receipt(tree, _saved(checks=["kit"]))

    assert _lines(tree) == [f"release accuracy row `kit: None` unreadable: fix what it names, "
                            f"then {RERUN}"]


@pytest.mark.parametrize("checks", [None, "kit", {"key": "kit"}])
def test_checks_that_are_not_a_list_record_no_check(tree, github, checks):
    _receipt(tree, _saved(checks=checks))

    assert _lines(tree) == [f"the release accuracy receipt records no check; {RERUN}"]


def test_digests_that_are_not_an_object_vouch_for_nothing(tree, github):
    _receipt(tree, _saved(digests=[_sha(FILES[PINS])]))

    assert _lines(tree) == [
        f"{name} hashes to {_sha(FILES[name])[:12]} here and the release accuracy receipt says "
        f"None; {RERUN}" for name in (PINS, CORPUS, LEDGER)]


def test_a_file_the_receipt_vouches_for_that_is_gone_hashes_to_none(tree, github):
    (tree / LEDGER).unlink()
    _receipt(tree, _saved())

    assert _lines(tree) == [f"{LEDGER} hashes to None here and the release accuracy receipt "
                            f"says {_sha(FILES[LEDGER])[:12]}; {RERUN}"]


def test_a_github_answer_without_a_runs_list_refuses_naming_the_head(tree, github):
    github["answer"] = {"message": "Not Found"}
    _receipt(tree, _saved())

    assert _lines(tree) == ["cannot read accuracy.yml runs at 8fb7b45c7248: no workflow_runs list"]
