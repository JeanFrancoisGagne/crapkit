"""The release accuracy gate against a model written from the plan's release-gate rule.

The rule: stage 2b refuses when the local receipt is missing, when its head is
not the release head, when any row failed, when GitHub holds no release-mode
run of accuracy.yml that succeeded at the tag commit, or when a file the
receipt vouches for no longer hashes to the digest it records. The receipt is
a claim; the digest is recomputed from the tree with hashlib, never read back.
It also refuses a receipt the stage's own command did not select: the whole
release tier with --local (no shard, local true, os_sensitive false). A receipt
made without --local on the releasing Windows machine held neither the retro
row nor the mutation row and passed.

`_expected` below is that rule as a boolean over the drawn case. Hypothesis
draws receipts, trees and GitHub answers; release.accuracy_gate must pass
exactly when the model does. The tests in tests/unit/test_release_accuracy_gate.py
check each refusal's wording against hand-built cases.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from unittest import mock

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure

REPO = Path(__file__).resolve().parents[3]
VERSION = "0.9.0"
ROOT = Path("/release-tree")
HEAD = "8fb7b45c7248c2ff71a472b40e1111c4a93674ce"
OTHER = "0123456789abcdef0123456789abcdef01234567"
VOUCHED = ("tools/accuracy/pins.toml", "tests/accuracy/corpus_goldens/corpus.toml",
           "tests/accuracy/suite_strength/retro/ledger.tsv")
GOOD_ROWS = {"pass", "empty"}


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_release_gate_tool",
                                                  REPO / "tools" / "release" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


# Each draw starts from a release that meets every rule and changes up to two
# things about it, so every rule's boundary is drawn often. Some changes break a
# rule and some do not (an `empty` row, a missing file the receipt does not
# vouch for, one more failed run beside the good one); the model decides which
# from the drawn values alone.
CHANGES = ("no receipt", "other head", "push tier", "failed outcome", "no rows", "not local",
           "a shard", "os sensitive", "row fail",
           "row infra", "row unreadable", "row empty", "no runs", "run title", "run version",
           "run head", "run running", "run failed", "run unfinished", "failed run beside",
           *(f"{kind} {name}" for kind in ("claim other", "claim absent", "file absent")
             for name in VOUCHED))
GOOD_RUN = {"display_title": f"accuracy release {VERSION}", "head_sha": HEAD,
            "status": "completed", "conclusion": "success"}
RUN_CHANGES = {"run title": {"display_title": f"accuracy nightly {VERSION}"},
               "run version": {"display_title": "accuracy release 0.8.0"},
               "run head": {"head_sha": OTHER}, "run running": {"status": "in_progress",
                                                               "conclusion": None},
               "run failed": {"conclusion": "failure"}, "run unfinished": {"conclusion": None}}
ROW_CHANGES = {"row fail": "fail", "row infra": "infra", "row unreadable": None, "row empty": "empty"}
CASE_CHANGES = {"other head": ("head", OTHER), "push tier": ("tier", "push"),
                "failed outcome": ("outcome", "fail"), "no rows": ("rows", []),
                "not local": ("local", False), "a shard": ("shard", "corpus"),
                "os sensitive": ("os_sensitive", True)}
STAGE_SELECTION = (None, True, False)  # shard, local, os_sensitive


def _named(changes: set, kind: str) -> set:
    return {change.removeprefix(kind + " ") for change in changes if change.startswith(kind + " ")}


def _claims(changes: set) -> dict:
    claims = dict.fromkeys(VOUCHED, "real")
    claims.update(dict.fromkeys(_named(changes, "claim other"), "other"))
    claims.update(dict.fromkeys(_named(changes, "claim absent"), "absent"))
    return claims


def _case(changes: set) -> dict | None:
    if "no receipt" in changes:
        return None
    case = {"head": HEAD, "tier": "release", "outcome": "pass", "claims": _claims(changes),
            "shard": None, "local": True, "os_sensitive": False}
    case["rows"] = ["pass"] + [ROW_CHANGES[change] for change in sorted(changes & set(ROW_CHANGES))]
    case.update(dict(CASE_CHANGES[change] for change in changes & set(CASE_CHANGES)))
    return case


def _answers(changes: set) -> list[dict]:
    if "no runs" in changes:
        return []
    run = dict(GOOD_RUN)
    for change in sorted(changes & set(RUN_CHANGES)):
        run.update(RUN_CHANGES[change])
    beside = [{**GOOD_RUN, "conclusion": "failure"}] if "failed run beside" in changes else []
    return [*beside, run]


@st.composite
def releases(draw):
    """(receipt case, tree, GitHub's runs) for a release with up to two changes."""
    changes = draw(st.sets(st.sampled_from(CHANGES), max_size=2))
    tree = {name: None if name in _named(changes, "file absent") else draw(st.binary(max_size=12))
            for name in VOUCHED}
    return _case(changes), tree, _answers(changes)


def _sha(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _claim(kind: str, raw: bytes | None) -> str | None:
    return {"real": _sha(raw), "other": "f" * 64, "absent": None}[kind]


def _digests_hold(case: dict, tree: dict) -> bool:
    return all(_claim(case["claims"][name], tree[name]) == _sha(tree[name]) for name in VOUCHED)


def _rows_hold(case: dict) -> bool:
    return bool(case["rows"]) and set(case["rows"]) <= GOOD_ROWS


def _receipt_holds(case: dict | None, tree: dict) -> bool:
    if case is None:
        return False
    fields = (case["head"], case["tier"], case["outcome"])
    selection = (case["shard"], case["local"], case["os_sensitive"])
    return (fields == (HEAD, "release", "pass") and selection == STAGE_SELECTION and _rows_hold(case)
            and _digests_hold(case, tree))


RUN_FIELDS = ("display_title", "head_sha", "status", "conclusion")


def _run_holds(answers: list[dict]) -> bool:
    wanted = (f"accuracy release {VERSION}", HEAD, "completed", "success")
    return any(tuple(run[field] for field in RUN_FIELDS) == wanted for run in answers)


def _expected(case: dict | None, tree: dict, answers: list[dict]) -> bool:
    return _receipt_holds(case, tree) and _run_holds(answers)


def _receipt_json(case: dict, tree: dict) -> dict:
    digests = {name: _claim(case["claims"][name], tree[name]) for name in VOUCHED}
    return {"head": case["head"], "tier": case["tier"], "outcome": case["outcome"],
            "shard": case["shard"], "local": case["local"], "os_sensitive": case["os_sensitive"],
            "checks": [{"key": "k", "name": f"row {index}", "outcome": outcome}
                       for index, outcome in enumerate(case["rows"])],
            "digests": {name: digest for name, digest in digests.items() if digest is not None}}


class Drawn:
    """The drawn tree, receipt and GitHub answer, served where release.py reads them.
    Reading real files is tests/unit/test_release_accuracy_gate.py's job; here the
    rule itself runs on every draw, without a disk in the way of its deadline."""

    def __init__(self, case: dict | None, tree: dict, answers: list[dict]):
        self.case, self.tree, self.answers = case, tree, answers

    def sha256(self, path: Path) -> str | None:
        return _sha(self.tree[path.relative_to(ROOT).as_posix()])

    def receipt(self, root: Path, version: str) -> dict:
        if self.case is None:
            raise release.ReleaseError("no readable release accuracy receipt")
        return _receipt_json(self.case, self.tree)

    def remote_json(self, url: str, absent: bool = False) -> dict:
        return {"total_count": len(self.answers), "workflow_runs": self.answers}


def _gate_passes(drawn: Drawn) -> bool:
    with mock.patch.multiple(release, _file_sha256=drawn.sha256, gates_accuracy=lambda root: True,
                             _read_accuracy_receipt=drawn.receipt, _remote_json=drawn.remote_json):
        try:
            release.accuracy_gate(ROOT, VERSION, HEAD)
        except release.ReleaseError:
            return False
    return True


@given(releases())
@pure
def test_the_gate_matches_the_plan_s_model(drawn):
    case, tree, answers = drawn

    assert _gate_passes(Drawn(case, tree, answers)) == _expected(case, tree, answers)


# One change at a time, each worked out by hand: the release passes after exactly
# these, and after any other single change it is refused.
PASSES_AFTER = {"none", "row empty", "failed run beside",
                *(f"file absent {name}" for name in VOUCHED)}


@pytest.mark.parametrize("change", ["none", *CHANGES])
def test_one_change_from_a_passing_release(change):
    changes = set() if change == "none" else {change}
    tree = {name: None if name in _named(changes, "file absent") else name.encode("utf-8")
            for name in VOUCHED}
    case, answers = _case(changes), _answers(changes)

    assert _expected(case, tree, answers) == (change in PASSES_AFTER)
    assert _gate_passes(Drawn(case, tree, answers)) == (change in PASSES_AFTER)
