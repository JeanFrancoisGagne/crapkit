"""Batch split: the active worklist cut into at most N file-disjoint batches,
co-changing files kept together (agent-json.md:709-739).

Oracles: model_score.groups (union-find over pairs at confidence 0.5 or more)
on push and networkx's connected components nightly for the groups, and a
brute-force partition for the balance over the groups' summed risk: Graham's
list-scheduling bound, and LPT itself, which crapkit's docstring names and
does not yet follow (rulings SM-BATCH-LPT).
"""
from __future__ import annotations

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import rulings
from accuracy.kit.settings import pure
from accuracy.score_model import cases, model_score, production
from accuracy.score_model.oracles import makespan

BATCH_ROWS = cases.hand("Batch split")


def entry(path: str, risk: float, start: int = 1):
    return production.make("worklist:WorklistEntry", scope="src", path=path,
                           long_name=f"f{start}( )", start=start, end=start + 5, ccn=3, ccn_std=3,
                           nloc=5, commits=1, authors=1, weight=risk / 3, risk=risk,
                           occurrence=1)


def pair(a: str, b: str, confidence: float) -> dict:
    return {"files": [a, b], "confidence": confidence}


def crapkit_split(entries, pairs, batches: int):
    return production.load("worklist:split_batches")(list(entries), list(pairs), batches=batches)


def _pairs_from(text: str) -> list[dict]:
    found = [item.split("|") for item in text.split(",") if item]
    return [pair(a, b, float(c)) for a, b, c in found]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in BATCH_ROWS],
                         ids=[row[0] for row in BATCH_ROWS])
def test_hand_rows(given_, expected):
    units = [item.split(":") for item in given_["units"].split(",")]
    got = crapkit_split([entry(path, float(risk)) for path, risk in units],
                        _pairs_from(given_.get("pairs", "")), int(given_["batches"]))

    assert "/".join("+".join(batch.files) for batch in got) == expected["batches"]


def test_coupling_threshold_is_one_half():
    """SM-BATCH-50: the docs say co-changing files stay together and give no
    threshold; crapkit keeps a pair at 0.5 together and splits one at 0.49."""
    entries = [entry("a.py", 3.0), entry("b.py", 2.0)]
    apart = len(crapkit_split(entries, [pair("a.py", "b.py", 0.49)], 2)) == 2
    together = len(crapkit_split(entries, [pair("a.py", "b.py", 0.5)], 2)) == 1

    rulings.pin_ruling("SM-BATCH-50", crapkit=f"{'apart' if apart else 'together'},"
                       f"{'together' if together else 'apart'}", oracle="unspecified")


# --- generated splits --------------------------------------------------------------------------

@st.composite
def worklists(draw, largest: int = 8):
    """Up to `largest` files, each with 1 to 3 entries at risks in hundredths,
    coupled pairs at confidences on both sides of 0.5, and a batch count."""
    files = [f"src/f{n}.py" for n in range(draw(st.integers(1, largest)))]
    risks = draw(st.lists(st.lists(st.integers(1, 900), min_size=1, max_size=3),
                          min_size=len(files), max_size=len(files)))
    drawn = draw(st.lists(st.tuples(st.sampled_from(files), st.sampled_from(files),
                                    st.sampled_from([0.2, 0.49, 0.5, 0.75, 1.0])), max_size=6))
    return _entries(files, risks), _edges(drawn), draw(st.integers(1, 5))


def _entries(files: list[str], risks: list[list[int]]) -> list:
    return [entry(path, risk / 100, start=1 + 10 * k)
            for path, file_risks in zip(files, risks) for k, risk in enumerate(file_risks)]


def _edges(drawn: list[tuple]) -> list[dict]:
    return [pair(*edge) for edge in drawn if edge[0] != edge[1]]


def _groups(entries, pairs) -> list[frozenset[str]]:
    files = sorted({e.path for e in entries})
    edges = [(p["files"][0], p["files"][1], p["confidence"]) for p in pairs]
    return model_score.groups(files, edges)


@given(worklists())
@pure
def test_batches_are_disjoint_cover_every_entry_and_keep_groups_whole(case):
    entries, pairs, count = case
    batches = crapkit_split(entries, pairs, count)
    files = _batch_files(batches)

    assert 1 <= len(batches) <= count
    assert [batch.files for batch in batches if not batch.entries] == []
    assert sorted(files) == sorted(set(files)) == sorted({e.path for e in entries})
    assert _placed(batches) == sorted((e.path, e.start) for e in entries)
    assert _split_groups(batches, _groups(entries, pairs)) == []


def _split_groups(batches, groups) -> list:
    """The coupled groups whose files land in more than one batch."""
    return [group for group in groups if _homes(batches, group) != 1]


def _batch_files(batches) -> list[str]:
    return [path for batch in batches for path in batch.files]


def _placed(batches) -> list[tuple]:
    return sorted((e.path, e.start) for batch in batches for e in batch.entries)


def _homes(batches, group) -> int:
    """How many batches hold a file of `group`."""
    return sum(1 for batch in batches if group & set(batch.files))


def _hundredths(risk: float) -> int:
    """The generated risks are whole hundredths; sum them as integers."""
    return round(risk * 100)


def _loads(batches) -> list[int]:
    return [sum(_hundredths(e.risk) for e in batch.entries) for batch in batches]


def _unit_risks(entries, pairs) -> list[int]:
    return [sum(_hundredths(e.risk) for e in entries if e.path in group)
            for group in _groups(entries, pairs)]


@given(worklists(largest=7))
@pure
def test_the_heaviest_batch_is_within_the_list_scheduling_bound(case):
    """Each coupled group is one job of its summed risk. Greedy to the lightest
    batch, in any order, stays within 2 - 1/k of the best split a brute force
    finds (Graham 1966)."""
    entries, pairs, count = case
    heaviest = max(_loads(crapkit_split(entries, pairs, count)))

    assert heaviest <= makespan.list_bound(count) * makespan.optimal(_unit_risks(entries, pairs), count)


LPT_CASE = ([("src/f0.py", 3.53), ("src/f1.py", 0.01), ("src/f2.py", 2.19), ("src/f3.py", 0.01),
             ("src/f4.py", 3.49), ("src/f5.py", 4.36)],
            [("src/f1.py", "src/f2.py"), ("src/f1.py", "src/f4.py")])


@rulings.applies("SM-BATCH-LPT")
def test_the_split_is_lpt_on_each_group_s_summed_risk():
    """Found by the nightly LPT-bound test: {f1, f2, f4} sums to 5.69 but its
    riskiest entry is 3.49, so ordering groups by their riskiest entry places
    it third and the heavier batch carries 9.22; LPT on summed risk (and the
    optimum) carries 7.89, and Graham's LPT bound for 2 batches is 9.205."""
    entries = [entry(path, risk) for path, risk in LPT_CASE[0]]
    pairs = [pair(a, b, 0.5) for a, b in LPT_CASE[1]]
    heaviest = max(_loads(crapkit_split(entries, pairs, 2)))

    rulings.pin_ruling("SM-BATCH-LPT", crapkit=heaviest,
                       oracle=makespan.lpt(_unit_risks(entries, pairs), 2))


def _ranked(entries) -> list:
    """The active list as the worklist hands it to --batches: risk first, then
    the docs' tail (model_score.rank_key)."""
    return sorted(entries, key=lambda e: model_score.rank_key(
        model_score.Entry(e.path, e.start, e.occurrence, e.ccn, e.commits, e.weight)))


@given(worklists(), st.randoms(use_true_random=False))
@pure
def test_the_split_ignores_the_coupled_pairs_order(case, rnd):
    """agent-json.md:709-740: --batches cuts the active list, which arrives
    ranked. Pairs come off the coupling cache in any order; the split must
    not follow it. (Entries at equal risk keep their ranked order: shuffling
    them is not a relation the docs promise, and a 20,000-example run found a
    split that moves with it.)"""
    entries, pairs, count = case
    shuffled = list(pairs)
    rnd.shuffle(shuffled)

    assert [b.files for b in crapkit_split(_ranked(entries), shuffled, count)] == [
        b.files for b in crapkit_split(_ranked(entries), pairs, count)]


@pytest.fixture(scope="session")
def networkx(oracle):
    """The pinned networkx, imported before any example runs."""
    import importlib

    oracle("networkx")
    return importlib.import_module("networkx")


@pytest.mark.nightly
@given(case=worklists())
@pure
def test_groups_are_networkx_connected_components(networkx, case):
    entries, pairs, _ = case
    graph = networkx.Graph()
    graph.add_nodes_from({e.path for e in entries})
    graph.add_edges_from(tuple(p["files"]) for p in pairs if p["confidence"] >= 0.5)

    assert sorted(map(sorted, networkx.connected_components(graph))) == sorted(
        map(sorted, _groups(entries, pairs)))
