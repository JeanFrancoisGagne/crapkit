"""The ordinal handle: a name for a function lizard could not name.

A file's anonymous functions all print as `(anonymous)`, so the only string a
session could pass back was the start line — and a start line is invalidated by
any edit above it, including the decomposition the session was told to make. The
handle counts positions instead: `(anonymous)#2` is the file's second anonymous
function, whatever line it has drifted to.

Every seam here is pure or a tmp SQLite file, so the resolution rules can be
argued about without a repo, a lane or a git history.
"""
import pytest
from crapkit import packet
from crapkit.cli import _claims_to_release, _next_item_payload, _pick_function
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

PATH = "web/app.js"


def anon(start: int, *, ccn: int = 8, cov: float = 0.5) -> ScoredRow:
    return ScoredRow("web", PATH, "(anonymous)", start, start + 5, ccn, ccn, ccn,
                     10, 1, 1, cov, "measured", float(ccn * ccn), "decompose")


def named(name: str, start: int, *, ccn: int = 8, cov: float = 0.5) -> ScoredRow:
    return ScoredRow("web", PATH, f"{name}( a , b )", start, start + 5, ccn, ccn,
                     ccn, 10, 2, 1, cov, "measured", float(ccn * ccn), "decompose")


# --- what a handle is --------------------------------------------------------

def test_a_named_function_is_its_own_handle():
    assert packet.handles([named("mount", 3)]) == {3: "mount"}


def test_anonymous_functions_number_in_file_order_not_in_row_order():
    """The rows arrive in whatever order the store hands them back; the ordinal
    is a claim about the file."""
    rows = [anon(80), anon(9), anon(41)]

    assert packet.handles(rows) == {9: "(anonymous)#1", 41: "(anonymous)#2",
                                    80: "(anonymous)#3"}


def test_a_named_neighbour_does_not_take_an_ordinal():
    rows = [anon(9), named("mount", 20), anon(41)]

    assert packet.handles(rows) == {9: "(anonymous)#1", 20: "mount",
                                    41: "(anonymous)#2"}


def test_a_handle_survives_an_edit_that_moves_an_earlier_function():
    """The whole reason the handle exists. An edit above the third callback
    pushes every start line down; the order is untouched, so the handle is too.
    """
    before = packet.handles([anon(9), anon(41), anon(80)])
    after = packet.handles([anon(9), anon(52), anon(91)])

    assert sorted(before.values()) == sorted(after.values())
    assert after[91] == "(anonymous)#3", "the third callback is still the third"


# --- resolving one -----------------------------------------------------------

def test_the_second_of_three_resolves_to_the_second_span():
    rows = [anon(80), anon(9), anon(41)]

    assert _pick_function(PATH, rows, "(anonymous)#2").start == 41


def test_the_handle_form_resolves_beside_the_three_forms_that_already_worked():
    rows = [anon(9), named("mount", 41)]

    assert _pick_function(PATH, rows, "mount").start == 41
    assert _pick_function(PATH, rows, "mount( a , b )").start == 41
    assert _pick_function(PATH, rows, "9").start == 9
    assert _pick_function(PATH, rows, "(anonymous)#1").start == 9


def test_an_ordinal_past_the_end_lists_the_handles_the_file_does_hold():
    with pytest.raises(CrapkitError) as err:
        _pick_function(PATH, [anon(9), anon(41)], "(anonymous)#5")

    assert "(anonymous)#1, (anonymous)#2" in str(err.value)
    assert PATH in str(err.value)


def test_an_ordinal_in_a_file_with_no_anonymous_functions_says_so():
    with pytest.raises(CrapkitError) as err:
        _pick_function(PATH, [named("mount", 41)], "(anonymous)#1")

    assert "no anonymous functions" in str(err.value)


def test_a_name_that_merely_contains_a_hash_is_not_a_handle():
    rows = [named("size#px", 41)]

    assert _pick_function(PATH, rows, "size#px").start == 41
    assert packet.handle_ordinal("size#px") is None
    assert packet.handle_ordinal("mount") is None


# --- the handle rides in the payloads ----------------------------------------

def test_the_next_item_payload_carries_the_handle_it_was_given():
    from types import SimpleNamespace

    from crapkit.uncovered import MissingLines
    from crapkit.worklist import admission

    payload = _next_item_payload(anon(41), admission({}, 5),
                                 SimpleNamespace(target=6, scope_targets={}),
                                 MissingLines({}, "no lanes here"), "(anonymous)#2")

    assert payload["handle"] == "(anonymous)#2"


# --- a claim remembers the handle it was taken under -------------------------

def test_a_claim_reads_back_the_handle_it_was_taken_under(tmp_path):
    db = tmp_path / "crap.sqlite"
    SnapshotStore(db).record_claim(path=PATH, long_name="(anonymous)",
                                   commit="dead00", handle="(anonymous)#2")

    (held,) = SnapshotStore(db).open_claims()

    assert held["handle"] == "(anonymous)#2"
    assert held["long_name"] == "(anonymous)"


def test_explain_resolves_the_handle_form_the_packets_print(tmp_path):
    """`explain` reaches the store by name fragment, and `#2` is a fragment no
    long_name holds. Its own resolution is positional or it rejects a string
    every other command accepts."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.write_run(commit="c1", tool_versions={},
                    rows=[anon(9), anon(41), named("mount", 60)])

    assert store.find_functions(PATH, "(anonymous)#2") == ["(anonymous)"]
    assert store.find_functions(PATH, "(anonymous)#3") == [], "there is no third"
    assert store.find_functions(PATH, "mount") == ["mount( a , b )"]


def test_a_handle_for_a_path_no_run_scored_resolves_to_nothing(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")

    assert store.find_functions("web/gone.js", "(anonymous)#1") == []


def test_a_claim_taken_before_handles_existed_reads_back_null(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.record_claim(path=PATH, long_name="mount( a , b )", commit="dead00")

    assert store.open_claims()[0]["handle"] is None


def claim(cid: int, long_name: str, handle: str | None) -> dict:
    return {"id": cid, "path": PATH, "long_name": long_name, "handle": handle,
            "commit": "c1", "created_at": "2026-01-01T00:00:00Z"}


def test_a_claim_taken_by_handle_releases_by_that_handle():
    """Releasing by long_name cannot work here: every anonymous claim in the
    file carries the same `(anonymous)`, so the release would close a stranger's.
    """
    claims = [claim(3, "(anonymous)", "(anonymous)#1"),
              claim(4, "(anonymous)", "(anonymous)#2")]

    closing = _claims_to_release(claims, False, [PATH, "(anonymous)#2"])

    assert [c["id"] for c in closing] == [4]


def test_a_named_claim_still_releases_by_either_name_form():
    claims = [claim(7, "mount( a , b )", "mount")]

    for form in ("mount", "mount( a , b )"):
        assert [c["id"] for c in _claims_to_release(claims, False, [PATH, form])] == [7]


# --- one budget, two readers -------------------------------------------------

def test_the_budget_helper_is_what_next_item_publishes():
    """next-item owned the two estimates and brief did not, so a session that
    opened on a packet re-derived numbers the queue had already computed."""
    from types import SimpleNamespace

    from crapkit.uncovered import MissingLines
    from crapkit.worklist import admission

    row = anon(9, ccn=13, cov=0.25)
    payload = _next_item_payload(row, admission({}, 5),
                                 SimpleNamespace(target=6, scope_targets={}),
                                 MissingLines({}, "no lanes here"), "(anonymous)#1")
    shared = packet.budget(row, 6)

    assert shared == {"est_splits": 3, "est_uncovered_paths": 10}
    assert (payload["est_splits"], payload["est_uncovered_paths"]) == \
        (shared["est_splits"], shared["est_uncovered_paths"])


def test_a_row_at_its_ceiling_needs_no_pieces():
    assert packet.budget(named("mount", 3, ccn=6, cov=1.0), 6) == \
        {"est_splits": 0, "est_uncovered_paths": 0}


# --- the refresh command has to clear what it claims to clear ----------------

def test_the_refresh_command_writes_a_run_rather_than_rereading_one():
    out = packet.commands("core/alpha.py", 'pytest "core/alpha.py"')

    assert out["refresh"] == "crapkit coverage --reuse-unchanged"
    assert out["refresh_writes_run"] is True
