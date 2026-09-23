"""docs/ratchet.md quotes the two lines a named seed is about, as the code prints them.

"Naming the run to seed from" shows the refusal a named run that cannot serve gets,
and the legacy-identity refusal on a run a failed verify pins. Reword either and
this pins the page to the new text instead of leaving a transcript nobody can
reproduce.
"""
import sys
from pathlib import Path

import pytest

from crapkit.cli.ratchet_cmds import _identity_advice, _WorkRun
from crapkit.errors import CrapkitError, ToolError
from crapkit.keys import require_unambiguous
from crapkit.score import ScoredRow
from crapkit.store import admit_baseline

ROOT = Path(__file__).resolve().parent.parent.parent


def page() -> str:
    return (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8")


def run(rid: int, kind: str, ok: bool | None = None) -> dict:
    return {"id": rid, "kind": kind, "verdict_ok": ok, "lanes": ["py"]}


def twin() -> ScoredRow:
    return ScoredRow("src", "src/a.ts", "(anonymous)", 1, 1, 1, 1, 1, 1, 0, 0,
                     1.0, "measured", 2.0, "ok", 0, 0)


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    """The page's sessions run `$ crapkit ...`, so the messages name that spelling."""
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/crapkit", "ratchet", "seed"])


def test_the_page_quotes_the_refusal_a_named_failed_verify_gets():
    runs = [run(1, "coverage"), run(2, "verify", ok=False), run(3, "coverage")]

    with pytest.raises(CrapkitError) as refused:
        admit_baseline(runs, 2, none_trusted="")

    assert f"crapkit: {refused.value}\nEXIT=1" in page()


def test_the_page_quotes_the_legacy_refusal_on_a_pinned_run():
    work = _WorkRun(run={"id": 1}, skipped=[{"id": 2}], newer={"id": 3}, named=False,
                    blocker={"id": 2})

    with pytest.raises(ToolError) as refused:
        require_unambiguous([twin(), twin()], run_id=1, advice=_identity_advice(work, "seed"))

    assert f"crapkit: {refused.value}\nEXIT=5" in page()


def test_the_page_quotes_the_prune_first_refusal_on_legacy_keys():
    from crapkit.cli.ratchet_cmds import _refuse_unkeyable_twins
    from crapkit.errors import ConfigError
    from crapkit.ratchet import RatchetEntry

    work = _WorkRun(run={"id": 3}, skipped=[], newer=None, named=True, blocker=None)
    fresh = [twin()._replace(occurrence=1), twin()._replace(occurrence=2)]
    prior = [RatchetEntry("src/gone.ts", "gone( )", 50.0)]
    seeded = prior + [RatchetEntry("src/a.ts", "(anonymous)", 2.0),
                      RatchetEntry("src/a.ts", "(anonymous)#2", 2.0)]

    with pytest.raises(ConfigError) as refused:
        _refuse_unkeyable_twins("crapkit-ratchet.tsv", work, prior, seeded, fresh, 0)

    assert f"crapkit: {refused.value}\nEXIT=3" in page()
