"""3-way ratchet merge (git merge driver semantics per key): marks only fall,
a deliberate drop beats an unchanged copy, and concurrent improvements meet at min."""
import pytest

from crapkit.cli import main
from crapkit.ratchet import (KEY_VERSION, RatchetEntry, dump_ratchet, load_ratchet, merge_ratchets,
                             metric_version, read_key_version, read_stamp)


def e(name: str, crap: float) -> RatchetEntry:
    return RatchetEntry("src/a.ts", name, crap)


def test_both_sides_lowered_takes_the_min():
    merged = merge_ratchets([e("f", 50.0)], [e("f", 30.0)], [e("f", 20.0)])
    assert merged == [e("f", 20.0)]


def test_a_drop_beats_an_unchanged_copy():
    merged = merge_ratchets([e("f", 50.0)], [], [e("f", 50.0)])
    assert merged == [], "one side fixed or pruned it; the other never touched it"


def test_a_drop_loses_to_a_concurrent_lowering():
    merged = merge_ratchets([e("f", 50.0)], [], [e("f", 20.0)])
    assert merged == [e("f", 20.0)], "keep the mark: it can only fall, and prune is re-runnable"


def test_new_debt_on_one_side_is_kept():
    merged = merge_ratchets([], [e("f", 40.0)], [])
    assert merged == [e("f", 40.0)]


def test_identical_sides_pass_through():
    merged = merge_ratchets([e("f", 50.0)], [e("f", 50.0)], [e("f", 50.0)])
    assert merged == [e("f", 50.0)]


def test_disjoint_new_debt_unions_sorted():
    merged = merge_ratchets([], [RatchetEntry("src/b.ts", "g", 10.0)], [e("f", 40.0)])
    assert merged == [e("f", 40.0), RatchetEntry("src/b.ts", "g", 10.0)]


# --- the driver on an add/add merge ------------------------------------------------

def _marks(path, *marks: RatchetEntry, key_version: int = KEY_VERSION):
    path.write_text(dump_ratchet(list(marks), stamp=metric_version(), key_version=key_version),
                    encoding="utf-8")
    return path


def _driver(tmp_path, base: bytes, theirs_keys: int = KEY_VERSION):
    """BASE as git hands it over, OURS and THEIRS as two first seeds wrote them:
    one mark each of their own, and one shared mark at different values."""
    base_file = tmp_path / "base.tsv"
    base_file.write_bytes(base)
    ours = _marks(tmp_path / "ours.tsv", RatchetEntry("a.py", "f( )", 8.0),
                  RatchetEntry("c.py", "h( )", 12.0))
    theirs = _marks(tmp_path / "theirs.tsv", RatchetEntry("b.py", "g( )", 9.0),
                    RatchetEntry("c.py", "h( )", 10.0), key_version=theirs_keys)
    return main(["ratchet", "merge", str(base_file), str(ours), str(theirs)]), ours


@pytest.mark.parametrize("base", [b"", b"\n", b" \r\n\r\n"], ids=["empty", "newline", "blank-lines"])
def test_an_add_add_merge_unions_both_sides_at_the_lower_mark(tmp_path, capsys, base):
    """Two branches that each created crapkit-ratchet.tsv (two first seeds)
    share no copy of it, and git hands the driver an empty %O. The driver read
    that as a legacy file of key version 0, refused with `ratchet key identity
    versions differ`, and git recorded a conflict the recover skill forbids
    resolving by hand. A blank BASE is no common ancestor."""
    code, ours = _driver(tmp_path, base)
    output = capsys.readouterr()

    assert (code, output.err, output.out) == (0, "", "ratchet merge: 3 mark(s)\n")
    text = ours.read_text(encoding="utf-8")
    assert load_ratchet(text) == [RatchetEntry("a.py", "f( )", 8.0), RatchetEntry("b.py", "g( )", 9.0),
                                  RatchetEntry("c.py", "h( )", 10.0)]
    assert (read_stamp(text), read_key_version(text)) == (metric_version(), KEY_VERSION)


def test_an_add_add_merge_still_refuses_sides_of_different_key_versions(tmp_path, capsys):
    """Only BASE steps out of the check: OURS and THEIRS must still agree."""
    code, ours = _driver(tmp_path, b"", theirs_keys=0)
    before = ours.read_bytes()

    assert code == 3
    assert "ratchet key identity versions differ" in capsys.readouterr().err
    assert ours.read_bytes() == before
