"""`crapkit ratchet merge BASE OURS THEIRS`, the git merge driver for the marks file.

docs/ratchet.md (The git merge driver): per key, the side that changed wins
over the side that did not; when both changed, the lower value wins; the
result is written over OURS; sides under different metric stamps are refused.
The outside oracle is git itself: `git merge-file` on one key's one-line
files says whether the key merges cleanly and to what. Where git reports a
conflict, the docs' rule (the lower surviving value) decides.
"""
from __future__ import annotations

from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import drive, repos
from accuracy.kit.settings import process, pure
from accuracy.verdict_model import model_verdict as model

STAMP = "crapkit-analysis=11 lizard=1.24.0"
OTHER = "crapkit-analysis=10 lizard=1.24.0"


def marks_text(marks: dict, stamp: str | None = STAMP, keys: str | None = "1") -> str:
    """A marks file as the docs write one (model_verdict.dump_marks)."""
    lines = model.dump_marks(model.MarksFile(stamp, keys, marks))
    return "\n".join(lines) + "\n"


def sides(tmp: Path, base: dict, ours: dict, theirs: dict, stamps: dict | None = None) -> list[Path]:
    paths = []
    for name, marks in (("base", base), ("ours", ours), ("theirs", theirs)):
        stamp = (stamps or {}).get(name, STAMP)
        path = tmp / f"{name}.tsv"
        path.write_bytes(marks_text(marks, stamp).encode("utf-8"))
        paths.append(path)
    return paths


def merge(tmp: Path, paths: list[Path]) -> drive.Result:
    return drive.Driver(tmp).run("ratchet", "merge", *map(str, paths))


def merged(path: Path) -> dict:
    return model.parse_marks(path.read_bytes().decode("utf-8-sig")).marks


def d(text: str) -> model.Decimal:
    return model.Decimal(text)


# --- hand: the docs' own example ------------------------------------------------------------

@pytest.mark.process
def test_a_branch_that_tightened_and_one_that_added_both_land(tmp_path):
    """docs/ratchet.md#the-git-merge-driver: main tightened foo to 31.5, the
    feature branch added bar at 22.0; the merge keeps both."""
    base = {("app/a.py", "foo( x )"): d("40.0000")}
    ours = {("app/a.py", "foo( x )"): d("31.5000")}
    theirs = {**base, ("app/b.py", "bar( y )"): d("22.0000")}
    paths = sides(tmp_path, base, ours, theirs)

    result = merge(tmp_path, paths)

    assert (result.code, result.stdout.strip()) == (0, "ratchet merge: 2 mark(s)")
    assert paths[1].read_bytes().decode("utf-8") == (
        f"# {STAMP}\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
        "app/a.py\tfoo( x )\t31.5000\napp/b.py\tbar( y )\t22.0000\n")


# --- oracle: git merge-file, one key at a time ----------------------------------------------

VALUES = (None, "30.0000", "25.0000", "44.0000")
TRIPLES = [(b, o, t) for b in VALUES for o in VALUES for t in VALUES]


def git_merge(tmp: Path, name: str, base, ours, theirs) -> tuple[bool, str | None]:
    """git merge-file on three one-line files: (clean, merged value or None)."""
    files = []
    for side, value in (("o", ours), ("b", base), ("t", theirs)):
        path = tmp / f"{name}.{side}"
        path.write_bytes(b"" if value is None else f"{value}\n".encode("ascii"))
        files.append(str(path))
    done = repos.hang_guard.run(["git", "merge-file", "-p", *files])
    text = done.stdout.decode("ascii").strip()
    return done.returncode == 0, (text or None) if done.returncode == 0 else None


def expected(tmp: Path, name: str, triple) -> str | None:
    clean, value = git_merge(tmp, name, *triple)
    if clean:
        return value
    present = [v for v in triple[1:] if v is not None]
    return min(present, key=d) if present else None


def _side(triples: list, index: int) -> dict:
    return {("src/k.py", f"f{number}( )"): d(triple[index])
            for number, triple in enumerate(triples) if triple[index] is not None}


def _judged(tmp: Path, triples: list) -> tuple[dict, dict]:
    paths = sides(tmp, _side(triples, 0), _side(triples, 1), _side(triples, 2))
    result = merge(tmp, paths)
    assert result.code == 0, result.stderr
    oracle = {("src/k.py", f"f{number}( )"): expected(tmp, f"f{number}", triple)
              for number, triple in enumerate(triples)}
    return merged(paths[1]), {key: d(value) for key, value in oracle.items() if value is not None}


@pytest.mark.process
def test_every_key_merges_as_git_merge_file_and_the_docs_resolve_it(tmp_path):
    """All 64 (base, ours, theirs) combinations of absent and three values,
    one key each, in one merge."""
    got, want = _judged(tmp_path, TRIPLES)

    assert got == want


@pytest.mark.process
@process
@given(triples=st.lists(st.tuples(*[st.sampled_from(VALUES)] * 3), min_size=1, max_size=8))
def test_random_files_merge_as_git_merge_file_and_the_docs_resolve_them(tmp_path_factory,
                                                                        triples):
    got, want = _judged(tmp_path_factory.mktemp("merge"), triples)

    assert got == want


# --- refusals -------------------------------------------------------------------------------

@pytest.mark.process
@pytest.mark.parametrize("stamps", [{"theirs": OTHER}, {"theirs": None}, {"ours": OTHER}],
                         ids=["other-metric", "unstamped", "ours-other"])
def test_sides_under_different_metric_stamps_refuse_and_leave_ours(tmp_path, stamps):
    """docs/ratchet.md: the driver refuses to merge across metric versions, and
    git falls back to a text conflict; README exit codes: a metric-stamp
    mismatch is a config error, 3."""
    marks = {("src/a.py", "f( )"): d("30.0000")}
    paths = sides(tmp_path, marks, marks, {("src/a.py", "f( )"): d("20.0000")}, stamps)
    before = paths[1].read_bytes()

    result = merge(tmp_path, paths)

    assert result.code == 3, result.stdout + result.stderr
    assert "ratchet merge refused" in result.stderr
    assert paths[1].read_bytes() == before


@pytest.mark.process
def test_a_bom_on_ours_keeps_its_stamp(tmp_path):
    """A marks file saved with a UTF-8 BOM carries the same stamp: the merge
    goes through and keeps it."""
    base = {("src/a.py", "f( )"): d("30.0000")}
    paths = sides(tmp_path, base, base, {("src/a.py", "f( )"): d("20.0000")})
    paths[1].write_bytes(b"\xef\xbb\xbf" + paths[1].read_bytes())

    result = merge(tmp_path, paths)

    assert result.code == 0, result.stderr
    parsed = model.parse_marks(paths[1].read_bytes().decode("utf-8-sig"))
    assert (parsed.stamp, parsed.marks) == (STAMP, {("src/a.py", "f( )"): d("20.0000")})


# --- properties of crapkit's own merge function (not an independent method) ------------------

KEYS = st.sampled_from([("a.py", "f( )"), ("a.py", "f( )#2"), ("b.py", "g( )")])
SIDE = st.dictionaries(KEYS, st.sampled_from([d("10.0000"), d("20.0000"), d("30.0000")]))


def _crapkit_merge(base: dict, ours: dict, theirs: dict) -> dict:
    from crapkit.ratchet import RatchetEntry, merge_ratchets
    entries = [[RatchetEntry(p, k, float(v)) for (p, k), v in side.items()]
               for side in (base, ours, theirs)]
    return {(e.path, e.long_name): d(f"{e.crap:.4f}") for e in merge_ratchets(*entries)}


@pure
@given(base=SIDE, ours=SIDE, theirs=SIDE)
def test_crapkit_s_merge_is_commutative_and_matches_the_model(base, ours, theirs):
    result = _crapkit_merge(base, ours, theirs)

    assert result == _crapkit_merge(base, theirs, ours)
    assert result == model.merge(base, ours, theirs)


@pure
@given(base=SIDE, side=SIDE)
def test_crapkit_s_merge_is_idempotent_and_a_drop_beats_unchanged(base, side):
    assert _crapkit_merge(base, side, side) == side
    assert _crapkit_merge(base, base, side) == side
