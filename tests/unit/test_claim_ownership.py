"""Claims reserve one function across processes and release only finished work."""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from crapkit.packet import handles
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from crapkit.worklist import closable_claims


def twins():
    return [ScoredRow("src", "src/a.py", "f( )", start, start + 5, 4, 4, 4,
                      5, 0, 1, 0.0, "measured", crap, "ok" if crap <= 6 else "add-tests")
            for start, crap in ((1, 4), (20, 20))]


def test_named_twins_have_exact_handles_even_when_the_first_is_not_the_worst():
    assert handles(twins()) == {("src/a.py", "f( )", 1, 0): "f#1",
                               ("src/a.py", "f( )", 20, 0): "f#2"}


def test_a_clean_twin_does_not_release_its_indebted_siblings_claim(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.record_claim(path="src/a.py", long_name="f( )", commit="known", handle="f#2")
    assert closable_claims(store.open_claims(), twins(), target=6,
                           scope_targets={}, stale_commits=set()) == []


def test_an_ambiguous_legacy_claim_waits_until_all_its_twins_are_healthy(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    cid = store.record_claim(path="src/a.py", long_name="f( )", commit="known")
    assert closable_claims(store.open_claims(), twins(), target=6,
                           scope_targets={}, stale_commits=set()) == []
    healthy = [r._replace(crap=4) for r in twins()]
    assert closable_claims(store.open_claims(), healthy, target=6,
                           scope_targets={}, stale_commits=set()) == [cid]


def test_competing_processes_cannot_both_acquire_the_same_claim(tmp_path):
    path = tmp_path / "crap.sqlite"
    store = SnapshotStore(path)
    script = """
import sys
from crapkit.store import SnapshotStore
store = SnapshotStore(sys.argv[1])
print(store.record_claim(path='src/a.py', long_name='f( )', commit='known', handle='f#2'))
"""

    def acquire(_):
        return subprocess.run([sys.executable, "-B", "-c", script, str(path)],
                              capture_output=True, text=True, check=True, timeout=20).stdout.strip()

    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(acquire, range(3)))
    assert answers.count("None") == 2
    assert len(store.open_claims()) == 1
    assert store.open_claims()[0]["handle"] == "f#2"


def test_old_writer_claim_keeps_group_history_after_explicit_release(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    with store._conn:
        store._conn.execute(
            "INSERT INTO attempts(path,long_name,commit_sha,handle) VALUES(?,?,?,?)",
            ("src/a.py", "f( )", "old", "f#1"))
    assert store.record_claim(path="src/a.py", long_name="f( )", commit="new", handle="f#2") is None
    assert store.close_claims([store.open_claims()[0]["id"]]) == 1
    second = store.record_claim(path="src/a.py", long_name="f( )", commit="new", handle="f#2")
    assert second is not None
    assert store.close_claims([second]) == 1
    assert store.close_claims([second]) == 0
    again = store.record_claim(path="src/a.py", long_name="f( )", commit="new", handle="f#2")
    assert again is not None
    histories = store.attempts_for([("src/a.py", "f( )"), ("src/a.py", "f( )#2")])
    assert len(histories[("src/a.py", "f( )")]) == 1
    assert [a["closed"] is None for a in histories[("src/a.py", "f( )#2")]] == [False, False, True]


def test_explicit_selector_counts_duplicate_scopes_as_one_twin():
    from crapkit.cli.queue import _pick_function

    first, second = twins()
    duplicate = first._replace(scope="other")
    assert _pick_function("src/a.py", [first, duplicate, second], "f#2").start == 20


def test_release_accepts_the_full_canonical_twin_name(tmp_path):
    from crapkit.cli.queue import _named_claims

    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.record_claim(path="src/a.py", long_name="f( )", commit="known", handle="f#1")
    cid = store.record_claim(path="src/a.py", long_name="f( )", commit="known", handle="f#2")
    assert [c["id"] for c in _named_claims(store.open_claims(), "src/a.py", "f( )#2")] == [cid]


def test_legacy_anonymous_global_ordinal_does_not_become_a_signature_ordinal():
    good, bad = twins()
    first = good._replace(long_name="(anonymous)")
    held = bad._replace(long_name="(anonymous) ( x )")
    other = good._replace(long_name="(anonymous) ( x )", start=40)
    claim = {"id": 1, "path": held.path, "long_name": held.long_name,
             "commit": "known", "handle": "(anonymous)#2"}
    assert closable_claims([claim], [first, held, other], target=6,
                           scope_targets={}, stale_commits=set()) == []
