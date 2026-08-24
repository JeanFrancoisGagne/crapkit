"""Burn-down from the ratchet file's own git history: when marks entered, when
they dropped, how old the survivors are. No timestamps in the TSV — the commits
already carry them, and a fixed history reports deterministically."""
from crapkit.ratchet_report import mark_events, report_from_events

DAY = 86400


def _patch(ts, added=(), dropped=()):
    lines = [f"+{p}\t{n}\t{c:.4f}" for p, n, c in added]
    lines += [f"-{p}\t{n}\t{c:.4f}" for p, n, c in dropped]
    return ts, "\n".join(["--- a/crapkit-ratchet.tsv", "+++ b/crapkit-ratchet.tsv"] + lines)


def test_mark_events_reads_adds_and_drops_from_patches():
    patches = [
        _patch(1000, added=[("src/a.ts", "f( )", 50.0), ("src/b.ts", "g( )", 30.0)]),
        _patch(1000 + 10 * DAY, dropped=[("src/b.ts", "g( )", 30.0)]),
    ]
    events = mark_events(patches)
    assert (1000, ("src/a.ts", "f( )"), "added", 50.0) in events
    assert (1000 + 10 * DAY, ("src/b.ts", "g( )"), "dropped", 30.0) in events


def test_a_tightened_mark_is_not_an_add_or_drop():
    patches = [
        _patch(1000, added=[("src/a.ts", "f( )", 50.0)]),
        _patch(2000, added=[("src/a.ts", "f( )", 20.0)], dropped=[("src/a.ts", "f( )", 50.0)]),
    ]
    report = report_from_events(mark_events(patches))
    assert report["open"] == 1
    assert report["dropped_total"] == 0, "same key changing value is a tightening, not a repayment"


def test_report_ages_and_velocity_anchor_on_the_newest_commit():
    patches = [
        _patch(1000, added=[("src/a.ts", "f( )", 50.0), ("src/b.ts", "g( )", 30.0),
                            ("src/c.ts", "h( )", 40.0)]),
        _patch(1000 + 20 * DAY, dropped=[("src/b.ts", "g( )", 30.0)]),
        _patch(1000 + 60 * DAY, dropped=[("src/c.ts", "h( )", 40.0)]),
    ]
    report = report_from_events(mark_events(patches))
    assert report["open"] == 1
    assert report["dropped_total"] == 2
    assert report["dropped_last_30d"] == 1, "only the drop within 30 days of the newest commit"
    (oldest,) = report["oldest"][:1]
    assert oldest["path"] == "src/a.ts"
    assert oldest["age_days"] == 60


# --- the working tree decides which marks are open -------------------------

def test_a_mark_that_was_never_committed_is_open():
    """`ratchet seed` writes the TSV and prints "added 1". The report has to
    agree before anything is committed, or the seed reads as a no-op."""
    report = report_from_events([], working={("src/a.ts", "f( )"): 63.6})
    assert report["open"] == 1
    assert report["uncommitted"] == 1
    assert report["oldest"] == [{"path": "src/a.ts", "long_name": "f( )", "age_days": 0}]


def test_a_working_tree_matching_history_reports_nothing_uncommitted():
    patches = [_patch(1000, added=[("src/a.ts", "f( )", 50.0)])]
    report = report_from_events(mark_events(patches), working={("src/a.ts", "f( )"): 50.0})
    assert (report["open"], report["uncommitted"]) == (1, 0)


def test_a_mark_deleted_from_the_working_tree_is_no_longer_open():
    patches = [_patch(1000, added=[("src/a.ts", "f( )", 50.0), ("src/b.ts", "g( )", 30.0)])]
    report = report_from_events(mark_events(patches), working={("src/a.ts", "f( )"): 50.0})
    assert (report["open"], report["uncommitted"]) == (1, 1)
    assert report["dropped_total"] == 0, "repayment counts from history, so it waits for the commit"


def test_an_uncommitted_tightening_leaves_the_mark_open_and_flagged():
    patches = [_patch(1000, added=[("src/a.ts", "f( )", 50.0)])]
    report = report_from_events(mark_events(patches), working={("src/a.ts", "f( )"): 20.0})
    assert (report["open"], report["uncommitted"]) == (1, 1)


def test_a_committed_mark_keeps_its_age_beside_an_uncommitted_one():
    """Ages still anchor on the newest commit; a mark with no commit entered at
    the anchor, so it reports 0d instead of borrowing a neighbour's age."""
    patches = [_patch(1000, added=[("src/a.ts", "f( )", 50.0)]),
               _patch(1000 + 30 * DAY, added=[("src/z.ts", "z( )", 10.0)])]
    working = {("src/a.ts", "f( )"): 50.0, ("src/z.ts", "z( )"): 10.0,
               ("src/new.ts", "n( )"): 20.0}
    report = report_from_events(mark_events(patches), working=working)
    assert (report["open"], report["uncommitted"]) == (3, 1)
    assert {e["long_name"]: e["age_days"] for e in report["oldest"]} == {
        "f( )": 30, "z( )": 0, "n( )": 0}


def test_no_working_tree_argument_reports_the_committed_state_alone():
    patches = [_patch(1000, added=[("src/a.ts", "f( )", 50.0)])]
    report = report_from_events(mark_events(patches))
    assert (report["open"], report["uncommitted"]) == (1, 0)


def test_header_and_context_lines_are_ignored():
    ts, patch = _patch(1000, added=[("src/a.ts", "f( )", 50.0)])
    patch = "+path\tlong_name\tcrap\n" + patch + "\n context line\n+++ b/x\n--- a/x"
    events = mark_events([(ts, patch)])
    assert len(events) == 1


ZERO_CONTEXT_PATCH = "\n".join([
    "diff --git a/crapkit-ratchet.tsv b/crapkit-ratchet.tsv",
    "index 1c0ffee..deadbee 100644",
    "--- a/crapkit-ratchet.tsv",
    "+++ b/crapkit-ratchet.tsv",
    "@@ -2,0 +3 @@ path\tlong_name\tcrap",
    "+src/a.ts\tf( )\t50.0000",
    "@@ -7 +8,0 @@ src/z.ts\tz( )\t9.0000",
    "-src/b.ts\tg( )\t30.0000",
])


def test_marks_are_read_from_a_zero_context_patch():
    """`git log -p -U0` is what gitio asks for: no context lines, and hunk
    headers that carry a ratchet row as their trailing context string."""
    events = mark_events([(1000, ZERO_CONTEXT_PATCH)])
    assert events == [
        (1000, ("src/a.ts", "f( )"), "added", 50.0),
        (1000, ("src/b.ts", "g( )"), "dropped", 30.0),
    ]
    report = report_from_events(events)
    assert report["open"] == 1
    assert report["dropped_total"] == 1


def test_the_ratchet_log_asks_git_for_zero_context_and_no_rename_walk(monkeypatch):
    """--follow costs 0.6s of rename detection over the whole history and the
    report reads only +/- lines, so context is pipe traffic nobody parses."""
    from pathlib import Path

    from crapkit import gitio

    seen = []
    monkeypatch.setattr(gitio, "_git", lambda root, *args: seen.append(args) or "")
    gitio.file_log_patches(Path("."), "crapkit-ratchet.tsv")
    (args,) = seen
    assert "-U0" in args
    assert "--follow" not in args
    assert "--reverse" in args, "oldest-first ordering is what mark_events assumes"
