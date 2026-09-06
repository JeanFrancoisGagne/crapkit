"""Each per-function reader follows the same twin through its stored runs."""
from crapkit.digest import build_digest
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from crapkit.packet import regrowth


def row(name="f( )", start=1, crap=20.0, ccn=4, scope="src"):
    return ScoredRow(scope, "src/a.py", name, start, start + 5, ccn, ccn, ccn,
                     5, 0, 1, 0.0, "measured", crap, "ok" if crap <= 6 else "add-tests")


def test_digest_names_a_twin_regression_even_when_another_change_balances_totals(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    before = [row(start=1, crap=10), row(start=20, crap=5), row("g( )", 40, 5)]
    after = [row(start=3, crap=12), row(start=22, crap=5), row("g( )", 42, 3)]
    a = store.write_run(commit="before", tool_versions={}, rows=before)
    b = store.write_run(commit="after", tool_versions={}, rows=after)

    for prev, cur in ((before, after), (store.read_crap(a), store.read_crap(b))):
        digest = build_digest(prev, cur, ceiling_of=lambda _: 6)
        assert not digest.quiet
        assert "regressed +2.0: src/a.py f( ) (crap 12.0)" in digest.lines


def test_history_and_span_select_one_twin_across_line_shifts_and_duplicate_scopes(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    a = store.write_run(commit="before", tool_versions={}, rows=[
        row(start=1, ccn=9), row(start=20, ccn=4), row(start=40, ccn=8)])
    b = store.write_run(commit="after", tool_versions={}, rows=[
        row(start=3, ccn=9), row(start=22, ccn=5, crap=30), row(start=42, ccn=8),
        row(start=22, ccn=5, crap=20, scope="other")])

    history = store.function_history("src/a.py", "f( )#2")
    assert [(h["run_id"], h["ccn"], h["crap"]) for h in history] == [(a, 4, 20), (b, 5, 30)]
    assert store.function_span(b, "src/a.py", "f( )#2") == (22, 27)
    assert store.function_span(b, "src/a.py", "f( )#4") is None
    assert regrowth(store.function_history("src/a.py", "f( )"))["regrown"] is False


def test_explain_keeps_explicit_twin_and_line_selectors_on_the_same_history(tmp_path, capsys):
    import argparse
    import json
    from crapkit.cli.reports import cmd_explain

    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n')
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")
    store.write_run(commit="known", tool_versions={}, rows=[row(start=1, ccn=9), row(start=20, ccn=4)])
    for name in ("f#2", "20"):
        args = argparse.Namespace(repo=str(tmp_path), path="src/a.py", name=name,
                                  history=False, tests=False, json=True)
        assert cmd_explain(args) == 0
        (function,) = json.loads(capsys.readouterr().out)["functions"]
        assert [h["ccn"] for h in function["history"]] == [4]

    from crapkit.snapshot import InventoryRow

    inventory = [InventoryRow(*r[:11]) for r in [row(start=3, ccn=9), row(start=22, ccn=4)]]
    store.write_run(commit="inventory", tool_versions={}, rows=inventory, kind="inventory")
    for name in ("f#2", "22"):
        args.name = name
        assert cmd_explain(args) == 0
        (function,) = json.loads(capsys.readouterr().out)["functions"]
        assert [h["ccn"] for h in function["history"]] == [4, 4]


def test_worklist_marks_keep_each_twins_verdict_with_its_score(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    bad, good = row(start=1, crap=20), row(start=20, crap=4)
    run = store.write_run(commit="known", tool_versions={}, rows=[bad, good])
    marks = store.read_marks(run)
    assert marks.verdict(bad) == ("measured", "add-tests")
    assert marks.verdict(good) == ("measured", "ok")
    assert marks.score(good) == (4, 0.0)


def test_digest_uses_the_worst_scope_for_one_span():
    before = [row(crap=10), row(crap=3, scope="other")]
    after = [row(crap=12), row(crap=3, scope="other")]
    digest = build_digest(before, after, ceiling_of=lambda _: 6)
    assert "regressed +2.0: src/a.py f( ) (crap 12.0)" in digest.lines


def test_report_drilldown_uses_the_rows_exact_span():
    from crapkit.report import _drill_down

    assert _drill_down({"path": "src/a.py", "function": "f( )", "start": 20}) == (
        "crapkit explain src/a.py 20")
