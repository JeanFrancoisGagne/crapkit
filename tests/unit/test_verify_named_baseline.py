"""`verify --baseline ID` refusals say what was named and what would have worked.

Naming a run is the deliberate act that bypasses the taint rule, so the answer
to a run that cannot serve is about THAT run: which it is, why it cannot be a
baseline, and which runs can. "No trusted scored baseline, run coverage first"
is true of an empty store; said of a store holding a trusted run 9 and a failed
verify 10 (#27), it sent the operator to a full coverage run when
`--baseline 9` was the answer.
"""
from pathlib import Path

import pytest

from crapkit.cli.verifying import _named_baseline, _untrusted_reason
from crapkit.errors import CrapkitError

ROOT = Path("/repo")


def run(rid: int, kind: str | None, ok: bool | None = None,
        lanes: tuple[str, ...] = ("py",)) -> dict:
    return {"id": rid, "commit": f"{rid:040x}", "tool_versions": {}, "lanes": list(lanes),
            "kind": kind, "verdict_ok": ok, "findings": 0, "created_at": "t"}


class Store:
    def __init__(self, *runs: dict):
        self._runs = list(runs)

    def list_runs(self) -> list[dict]:
        return self._runs


def refusal(store: Store, requested: int) -> str:
    with pytest.raises(CrapkitError) as exc:
        _named_baseline(store, ROOT, requested)
    return str(exc.value)


def test_a_trusted_run_is_the_baseline():
    assert _named_baseline(Store(run(9, "coverage")), ROOT, 9)["id"] == 9


def test_a_failed_verify_is_named_with_its_reason_and_the_trusted_alternative():
    msg = refusal(Store(run(9, "coverage"), run(10, "verify", ok=False)), 10)

    assert msg.startswith("run 10 is a failed verify"), msg
    assert "--baseline 9" in msg, msg
    assert "no trusted scored baseline" not in msg


def test_a_partial_run_is_named_as_such():
    msg = refusal(Store(run(1, "coverage"), run(2, "partial")), 2)

    assert msg.startswith("run 2 is a partial run"), msg
    assert "--baseline 1" in msg, msg


def test_a_hook_run_is_named_as_such():
    msg = refusal(Store(run(1, "coverage"), run(2, "hook")), 2)

    assert msg.startswith("run 2 is a hook run"), msg


def test_an_inventory_run_is_named_as_such():
    msg = refusal(Store(run(1, "coverage"), run(2, "inventory")), 2)

    assert msg.startswith("run 2 is an inventory run"), msg


def test_a_run_of_any_other_kind_measured_no_lanes_and_says_so():
    """A store migrated from before runs carried a kind holds `legacy` rows, and
    a row with no kind at all reads the same way. Every reason the helper can
    give is exercised here, so its CRAP is its ccn and nothing more.

    The last row is a verify whose `verdict_ok` is NULL, the only verify the
    caller ever hands this helper: a passing verify is trusted and never
    reaches it, so asserting the reason on `ok=True` pinned a state the code
    cannot produce."""
    assert _untrusted_reason(run(2, "legacy")) == "a legacy run that measured no lanes"
    assert _untrusted_reason(run(2, None)) == "a legacy run that measured no lanes"
    assert _untrusted_reason(run(2, "verify", ok=None)) == "a verify with no verdict"


def test_the_trusted_runs_are_listed_newest_last_and_the_newest_is_the_hint():
    store = Store(run(3, "coverage"), run(5, "verify", ok=True), run(6, "verify", ok=False))
    msg = refusal(store, 6)

    assert "trusted runs: 3, 5" in msg, msg
    assert "--baseline 5" in msg, msg


def test_an_id_no_run_carries_says_so_and_lists_the_trusted_ones():
    msg = refusal(Store(run(9, "coverage")), 99)

    assert msg.startswith("no run 99"), msg
    assert "trusted runs: 9" in msg, msg


def test_an_empty_store_keeps_the_line_that_was_written_for_it():
    msg = refusal(Store(), 1)

    assert "no trusted scored baseline" in msg and "run `crapkit coverage` first" in msg, msg


def test_a_store_with_only_untrusted_runs_keeps_that_line_too():
    msg = refusal(Store(run(1, "hook"), run(2, "verify", ok=False)), 2)

    assert "no trusted scored baseline" in msg, msg
