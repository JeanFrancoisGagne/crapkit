"""What a batch of packets is allowed to read twice.

`brief --batch N` exists so a session pays for one process instead of N. That is
only true if the repo-wide reads happen once: the store, the config, the churn
window, the git log, the file texts, the tool versions. Every count here is a
census of the calls a two-packet batch actually made, so a regression that
re-reads per packet fails loudly rather than getting slower quietly.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from crapkit.cli import queue
from crapkit.score import ScoredRow
from crapkit.uncovered import MissingLines

LATEST = {"id": 7, "commit": "abc123def4567"}


def row(name: str, *, path: str = "core/alpha.py", start: int = 1, end: int = 20,
        ccn: int = 8, remedy: str = "decompose", scope: str = "core") -> ScoredRow:
    return ScoredRow(scope, path, name, start, end, ccn, ccn, ccn, 18, 2, 1, 0.5,
                     "measured", float(ccn * ccn), remedy, 0)


ALPHA = row("alpha( a , b )")
HELPER = row("helper( a )", start=22, end=25, ccn=2, remedy="ok")
BETA = row("beta( a )", path="core/beta.py", ccn=7)

CFG = SimpleNamespace(
    churn_window_months=12, worklist_floor=1, target=6,
    scope_targets={"core": 6}, scopes=(SimpleNamespace(name="core"),),
    scope_paths={"core": ("core",)}, lanes=(), scoped_tests=(),
    diff_uncovered_max=None, ratchet_file="crapkit-ratchet.tsv")


class _Store:
    """A store that counts what the loader asks it for."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def _seen(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def read_rows(self, run_id, **kw):
        self._seen("read_rows")
        return [ALPHA, HELPER, BETA]

    def read_scored(self, run_id, **kw):
        self._seen("read_scored")
        return [ALPHA, HELPER, BETA]

    def read_scored_file(self, run_id, path):
        self._seen("read_scored_file")
        return [r for r in (ALPHA, HELPER, BETA) if r.path == path]

    def function_history(self, path, long_name):
        self._seen("function_history")
        return [{"run_id": 7, "ccn": 8}]

    def attempts_for(self, keys):
        self._seen("attempts_for")
        return {key: [] for key in keys}


@pytest.fixture()
def counted(monkeypatch) -> dict:
    """Every repo read a brief makes, replaced by a counter."""
    import crapkit.churn_log
    import crapkit.coupling
    import crapkit.dup
    import crapkit.gitio

    seen: dict[str, int] = {}

    def counter(name, answer):
        def fake(*args, **kwargs):
            seen[name] = seen.get(name, 0) + 1
            return answer
        return fake

    monkeypatch.setattr(queue, "_load_sources", counter("sources", {"core/alpha.py": "x\n"}))
    monkeypatch.setattr(queue, "load_churn", counter("churn", {}))
    monkeypatch.setattr(queue, "load_uncovered", counter("uncovered", MissingLines({}, "no lane")))
    monkeypatch.setattr(queue, "head_commit", counter("head", "abc123def4567"))
    monkeypatch.setattr(queue, "_ratchet_entries", counter("ratchet", None))
    monkeypatch.setattr(queue, "_brief_versions", counter("versions", {"crapkit": "0"}))
    monkeypatch.setattr(crapkit.churn_log, "log_lines", counter("log", []))
    monkeypatch.setattr(crapkit.coupling, "change_coupling_lines", counter("coupling", []))
    monkeypatch.setattr(crapkit.dup, "find_twins", counter("twins", []))
    monkeypatch.setattr(crapkit.gitio, "file_log_patches", counter("mark_history", []))
    return seen


def loader(store) -> queue._BriefLoader:
    return queue._BriefLoader(Path("/repo"), CFG, store, LATEST)


# --- the amortization -----------------------------------------------------------

def test_two_packets_in_one_file_read_the_repo_once(counted):
    store = _Store()
    ld = loader(store)

    queue._brief_packet(ld, ALPHA)
    queue._brief_packet(ld, HELPER)

    assert counted["sources"] == 1, "one source read for the whole batch"
    assert counted["churn"] == 1 and counted["log"] == 1
    assert counted["coupling"] == 1, "the global ranking is cut per path, not rebuilt"
    assert counted["uncovered"] == 1 and counted["head"] == 1 and counted["versions"] == 1
    assert store.calls["read_rows"] == 1
    assert store.calls["read_scored_file"] == 1, "one scored-file read per distinct path"


def test_a_second_file_costs_its_own_scored_read_and_nothing_else(counted):
    store = _Store()
    ld = loader(store)

    queue._brief_packet(ld, ALPHA)
    queue._brief_packet(ld, BETA)

    assert store.calls["read_scored_file"] == 2, "two paths, two reads"
    assert counted["sources"] == 1 and counted["churn"] == 1 and counted["log"] == 1


def test_a_function_with_no_mark_never_asks_git_for_the_marks_history(counted):
    queue._brief_packet(loader(_Store()), ALPHA)

    assert "mark_history" not in counted, "no mark, no age, no spawn"


def test_the_batch_asks_for_every_functions_prior_claims_in_one_query(counted):
    store = _Store()

    out = queue._brief_batch(loader(store), 3)

    assert store.calls["attempts_for"] == 1, "one query for the whole batch"
    assert [p["function"] for p in out["packets"]] == ["alpha( a , b )", "beta( a )"], \
        "ranked by crap; the ok row is not actionable"


# --- the envelope ---------------------------------------------------------------

def test_the_batch_envelope_names_the_run_and_its_staleness(counted):
    out = queue._brief_batch(loader(_Store()), 2)

    assert out["run_id"] == 7 and out["commit"] == "abc123def4567"
    assert out["stale"] is False, "the snapshot commit is HEAD"
    assert len(out["packets"]) == 2


def test_a_moved_head_makes_the_batch_and_its_packets_stale(monkeypatch, counted):
    monkeypatch.setattr(queue, "head_commit", lambda root: "0000000000000")

    out = queue._brief_batch(loader(_Store()), 1)

    assert out["stale"] is True
    assert out["packets"][0]["stale"] is True, "a packet carries its own verdict"


def test_a_packet_is_what_a_single_brief_emits(counted):
    ld = loader(_Store())

    single = queue._brief_packet(ld, ALPHA)
    batched = queue._brief_batch(loader(_Store()), 1)["packets"][0]

    assert batched == single, "one contract, whether or not --batch asked for it"


def test_a_batch_of_zero_is_refused_rather_than_answered_empty():
    from crapkit.errors import ConfigError

    with pytest.raises(ConfigError, match="--batch"):
        queue._resolve_batch(0)
