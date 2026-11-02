"""Burn-down from the ratchet file's own git history: when marks entered, when
they dropped, how old the survivors are. No timestamps in the TSV — the commits
already carry them, and a fixed history reports deterministically.

The history is the marks file's parsed revisions (marks_history.MarksRevision):
mark_events diffs the marks each revision found against the marks it left."""
from crapkit.marks_history import MarksRevision
from crapkit.ratchet_report import held_event, mark_events, report_from_events

DAY = 86400


def _history(*steps):
    """(ts, {(path, name): crap}) per revision, oldest first, each revision
    changing the one before it."""
    revisions, before = [], None
    for ts, marks in steps:
        revisions.append(MarksRevision(f"c{ts}", ts, marks, before))
        before = marks
    return revisions


A, B, C = ("src/a.ts", "f( )"), ("src/b.ts", "g( )"), ("src/c.ts", "h( )")


def test_mark_events_reads_adds_and_drops_between_revisions():
    events = mark_events(_history((1000, {A: 50.0, B: 30.0}), (1000 + 10 * DAY, {A: 50.0})))

    assert events == [(1000, A, "added", 50.0), (1000, B, "added", 30.0),
                      (1000 + 10 * DAY, B, "dropped", 30.0)]


def test_a_revision_names_each_key_it_changed_and_no_other():
    """A changed value is an update at its new value, a key moved to a new
    name enters that name and repays the old one, and a key both revisions
    hold at one value is no event."""
    moved = ("src/b.ts", "g2( )")
    events = mark_events(_history((1000, {A: 50.0, B: 30.0, C: 40.0}),
                                  (2000, {C: 40.0, moved: 30.0, A: 20.0})))

    assert events[3:] == [(2000, A, "updated", 20.0), (2000, moved, "added", 30.0),
                          (2000, B, "dropped", 30.0)]


def test_a_tightened_mark_is_not_an_add_or_drop():
    report = report_from_events(mark_events(_history((1000, {A: 50.0}), (2000, {A: 20.0}))))
    assert report["open"] == 1
    assert report["dropped_total"] == 0, "same key changing value is a tightening, not a repayment"


def test_a_revision_with_no_mark_change_advances_the_clock():
    events = mark_events(_history((1000, {A: 50.0}), (1000 + 30 * DAY, {A: 50.0})))

    assert events[-1] == (1000 + 30 * DAY, None, "observed", 0.0)
    assert report_from_events(events)["oldest"][0]["age_days"] == 30


def test_a_deleted_file_repays_every_mark_and_a_restored_one_enters_again():
    events = mark_events(_history((1000, {A: 50.0}), (2000, None), (3000, {A: 50.0})))

    assert [(ts, kind) for ts, _, kind, _ in events] == [
        (1000, "added"), (2000, "dropped"), (3000, "added")]


def test_report_ages_and_velocity_anchor_on_the_newest_commit():
    revisions = _history((1000, {A: 50.0, B: 30.0, C: 40.0}),
                         (1000 + 20 * DAY, {A: 50.0, C: 40.0}),
                         (1000 + 60 * DAY, {A: 50.0}))
    report = report_from_events(mark_events(revisions))
    assert report["open"] == 1
    assert report["dropped_total"] == 2
    assert report["dropped_last_30d"] == 1, "only the drop within 30 days of the newest commit"
    (oldest,) = report["oldest"][:1]
    assert oldest["path"] == "src/a.ts"
    assert oldest["age_days"] == 60


# --- the committed marks: the newest revision's own -------------------------

def test_the_held_event_is_the_newest_revisions_marks():
    revisions = _history((1000, {A: 50.0}), (2000, {A: 20.0, B: 30.0}))

    assert held_event(revisions) == [(2000, None, "held", {A: 20.0, B: 30.0})]
    assert held_event(_history((1000, {A: 50.0}), (2000, None))) == []
    assert held_event([]) == []


def test_a_merge_that_shows_no_change_still_decides_the_committed_marks():
    """A merge's revision diffs against itself, so the replay keeps a mark the
    merge resolved as repaid; the held event says it is gone."""
    revisions = _history((1000, {A: 50.0, B: 30.0}))
    revisions.append(MarksRevision("merge", 2000, {A: 50.0}, {A: 50.0}))

    report = report_from_events(mark_events(revisions) + held_event(revisions))

    assert (report["open"], report["uncommitted"]) == (1, 0)


# --- the working tree decides which marks are open -------------------------

def test_a_mark_that_was_never_committed_is_open():
    """`ratchet seed` writes the TSV and prints "added 1". The report has to
    agree before anything is committed, or the seed reads as a no-op."""
    report = report_from_events([], working={("src/a.ts", "f( )"): 63.6})
    assert report["open"] == 1
    assert report["uncommitted"] == 1
    assert report["oldest"] == [{"path": "src/a.ts", "long_name": "f( )", "age_days": 0}]


def test_a_working_tree_matching_history_reports_nothing_uncommitted():
    revisions = _history((1000, {A: 50.0}))
    report = report_from_events(mark_events(revisions) + held_event(revisions), working={A: 50.0})
    assert (report["open"], report["uncommitted"]) == (1, 0)


def test_a_mark_deleted_from_the_working_tree_is_no_longer_open():
    revisions = _history((1000, {A: 50.0, B: 30.0}))
    report = report_from_events(mark_events(revisions) + held_event(revisions), working={A: 50.0})
    assert (report["open"], report["uncommitted"]) == (1, 1)
    assert report["dropped_total"] == 0, "repayment counts from history, so it waits for the commit"


def test_an_uncommitted_tightening_leaves_the_mark_open_and_flagged():
    revisions = _history((1000, {A: 50.0}))
    report = report_from_events(mark_events(revisions) + held_event(revisions), working={A: 20.0})
    assert (report["open"], report["uncommitted"]) == (1, 1)


def test_a_committed_mark_keeps_its_age_beside_an_uncommitted_one():
    """Ages still anchor on the newest commit; a mark with no commit entered at
    the anchor, so it reports 0d instead of borrowing a neighbour's age."""
    z = ("src/z.ts", "z( )")
    revisions = _history((1000, {A: 50.0}), (1000 + 30 * DAY, {A: 50.0, z: 10.0}))
    working = {A: 50.0, z: 10.0, ("src/new.ts", "n( )"): 20.0}
    report = report_from_events(mark_events(revisions) + held_event(revisions), working=working)
    assert (report["open"], report["uncommitted"]) == (3, 1)
    assert {e["long_name"]: e["age_days"] for e in report["oldest"]} == {
        "f( )": 30, "z( )": 0, "n( )": 0}


def test_no_working_tree_argument_reports_the_committed_state_alone():
    revisions = _history((1000, {A: 50.0}))
    report = report_from_events(mark_events(revisions) + held_event(revisions))
    assert (report["open"], report["uncommitted"]) == (1, 0)


# --- the git read under the history ----------------------------------------

def test_the_ratchet_history_reads_one_patch_stream_and_only_the_created_blob(monkeypatch):
    """One `git log -p -U0` that also names object ids (--raw, --cc for a
    merge), with no rename walk and gitio's patch protocol flags, then one
    `cat-file --batch` process for the one blob no patch carries: the
    revision that created the file."""
    from pathlib import Path

    from crapkit import gitio

    old, new = "1" * 40, "2" * 40
    log = (b"\x001000 " + b"a" * 40 + b"\n\n:000000 100644 " + b"0" * 40 + b" " + old.encode()
           + b" A\tcrapkit-ratchet.tsv\n\ndiff --git a/crapkit-ratchet.tsv b/crapkit-ratchet.tsv\n"
           + b"new file mode 100644\n--- /dev/null\n+++ b/crapkit-ratchet.tsv\n@@ -0,0 +1,2 @@\n"
           + b"+h\n+1\n\x002000 " + b"b" * 40 + b"\n\n:100644 100644 "
           + old.encode() + b" " + new.encode() + b" M\tcrapkit-ratchet.tsv\n\n"
           + b"diff --git a/crapkit-ratchet.tsv b/crapkit-ratchet.tsv\n--- a/crapkit-ratchet.tsv\n"
           + b"+++ b/crapkit-ratchet.tsv\n@@ -2 +2 @@\n-1\n+-- a/x\r\n\\ No newline at end of file\n")
    seen, batches = [], []
    # The log is read as bytes and framed before any decode: a CR inside a row
    # must not become a Git header line.
    monkeypatch.setattr(gitio, "_git_bytes",
                        lambda root, *args, **kwargs: seen.append((args, kwargs)) or log)
    monkeypatch.setattr(gitio, "_batch_stream", lambda root, requests: batches.append(requests) or (
        b"%s blob 4\nh\n1\n\n" % old.encode()))

    read = gitio.file_revisions(Path("."), "crapkit-ratchet.tsv")

    ((args, kwargs),) = seen
    assert "--raw" in args and "--cc" in args and "--no-renames" in args
    assert "-p" in args and "-U0" in args
    assert {"--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/"} <= set(args)
    assert "--follow" not in args
    assert "--reverse" in args, "oldest-first ordering is what mark_events assumes"
    assert kwargs == {}
    assert batches == [f"{old}\n".encode()]
    assert [(r.time, r.data, r.before, r.merge, r.blob, r.base, r.rows) for r in read] == [
        (1000, b"h\n1\n", None, False, old, None, None),
        (2000, None, None, False, new, old,
         gitio.PatchRows((b"1",), (b"-- a/x\r",), False, False))]
