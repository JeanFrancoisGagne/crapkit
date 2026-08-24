"""Ratchet seed/prune: first-class entry and exit for marks. Seeding never raises one."""
from crapkit.ratchet import RatchetEntry, prune_ratchet, seed_ratchet
from crapkit.score import ScoredRow


def scored(path="src/a.ts", name="f( )", ccn=8, cov=0.0, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "decompose")


def test_seed_marks_only_functions_over_their_ceiling():
    rows = [scored(name="hot( )", ccn=8, cov=0.0), scored(name="fine( )", ccn=2, cov=1.0)]
    entries, added, tightened = seed_ratchet([], rows, target=6)
    assert [e.long_name for e in entries] == ["hot( )"]
    assert added == 1 and tightened == 0


def test_seed_never_raises_an_existing_mark():
    prior = [RatchetEntry("src/a.ts", "hot( )", 10.0)]  # audited debt, lower than fresh
    rows = [scored(name="hot( )", ccn=8, cov=0.0)]  # fresh crap = 72
    entries, added, tightened = seed_ratchet(prior, rows, target=6)
    assert entries == prior, "seed must keep the lower audited mark"
    assert added == 0 and tightened == 0


def test_seed_tightens_a_looser_mark_and_is_idempotent():
    prior = [RatchetEntry("src/a.ts", "hot( )", 500.0)]
    rows = [scored(name="hot( )", ccn=8, cov=0.0)]  # 72
    entries, added, tightened = seed_ratchet(prior, rows, target=6)
    assert entries[0].crap == 72.0 and added == 0 and tightened == 1
    again, added2, tightened2 = seed_ratchet(entries, rows, target=6)
    assert again == entries and added2 == 0 and tightened2 == 0


def test_seed_respects_scope_targets():
    rows = [scored(name="hot( )", ccn=8, cov=0.0, scope="legacy")]
    entries, added, _ = seed_ratchet([], rows, target=6, scope_targets={"legacy": 100})
    assert entries == [] and added == 0


def test_prune_drops_only_functions_gone_from_the_run():
    prior = [RatchetEntry("src/a.ts", "gone( )", 30.0),
             RatchetEntry("src/a.ts", "hot( )", 72.0)]
    rows = [scored(name="hot( )", ccn=8, cov=0.0)]
    entries, dropped = prune_ratchet(prior, rows)
    assert [e.long_name for e in entries] == ["hot( )"]
    assert dropped == 1
