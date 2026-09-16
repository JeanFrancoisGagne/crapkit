"""A run that meets shared source line spans names them and carries on.

Through 0.7.4 the first such span ended the run: one consumer repo holds 591 of
them, 459 measured, so its weekly coverage run never finished and the repo kept
no fresh coverage at all.
"""
from crapkit.analyze import analyze_source
from crapkit.cli.scoring import _note_shared_spans
from crapkit.score import SharedSpanFold
from crapkit.snapshot import build_inventory_rows


class _Config:
    """Only what the note reads: the ceiling accessor every command goes through."""

    def __init__(self, target=6, scope_targets=None):
        self.target = target
        self._scope_targets = scope_targets or {}

    def ceiling_of(self, scope: str) -> int:
        return self._scope_targets.get(scope, self.target)


def members(ccn=1, scope="src"):
    source = "function live() { return 1; } function dead() { return 2; }"
    rows = build_inventory_rows({scope: analyze_source("app.ts", source)})
    return [rows[0]._replace(ccn=ccn), rows[1]]


def fold_of(*sites) -> SharedSpanFold:
    fold = SharedSpanFold()
    for site in sites:
        fold.add(site)
    return fold


def test_a_run_without_a_shared_span_says_nothing(capsys):
    _note_shared_spans(fold_of(), _Config())
    assert capsys.readouterr().err == ""


def test_the_note_counts_the_spans_and_says_what_it_cost(capsys):
    _note_shared_spans(fold_of(members(), members()), _Config())
    err = capsys.readouterr().err
    assert "2 source line span(s) hold more than one function" in err
    assert "score as uncovered" in err
    assert "separate lines" in err


def test_a_span_no_ceiling_can_fail_is_counted_but_not_named(capsys):
    """Complexity 2 scores 6 at zero coverage, which no target of 6 fails."""
    _note_shared_spans(fold_of(members(ccn=2)), _Config())
    err = capsys.readouterr().err
    assert "1 source line span(s)" in err
    assert "app.ts:1" not in err


def test_a_span_over_its_target_at_zero_coverage_is_named(capsys):
    _note_shared_spans(fold_of(members(ccn=3)), _Config())
    assert "crapkit:   app.ts:1 (complexity 3)" in capsys.readouterr().err


def test_the_scope_target_decides_what_counts_as_over(capsys):
    _note_shared_spans(fold_of(members(ccn=3, scope="ui")), _Config(scope_targets={"ui": 12}))
    err = capsys.readouterr().err
    assert "1 source line span(s)" in err
    assert "app.ts:1" not in err


def test_more_spans_over_target_than_it_names_are_counted(capsys):
    _note_shared_spans(fold_of(*[members(ccn=n) for n in (3, 4, 5, 6)]), _Config())
    err = capsys.readouterr().err
    assert "4 of them hold a function over its target" in err
    assert "(complexity 6)" in err
    assert "... and 1 more" in err
