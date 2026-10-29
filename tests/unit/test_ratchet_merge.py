"""3-way ratchet merge (git merge driver semantics per key): marks only fall,
a deliberate drop beats an unchanged copy, and concurrent improvements meet at min.

The stamps merge three ways too, scope by scope: the side that moved a scope's
stamp wins, and only a scope both sides moved differently refuses. The driver
cannot place a mark in a scope without the config, so when one side moved a
scope's stamp and the other changed any mark it names the scope."""
import sys

import pytest

from crapkit import invocation
from crapkit.cli import main
from crapkit.ratchet import (KEY_VERSION, RatchetEntry, ScopeStamps, SingleNumberStamp, dump_ratchet,
                             load_ratchet, merge_ratchets, merge_stamps, metric_version,
                             read_key_version, read_stamp)
from merge_oracle import three_way_marks


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


# --- the stamps merge scope by scope -------------------------------------------------
# Every file below is written by hand, as docs/ratchet.md "What a mark is" lays
# one out, so no expected file comes from the dump the driver writes with.

LIZARD = "1.24.0"
API13 = "analysis python=13; coverage coveragepy=1"
API14 = "analysis python=14; coverage coveragepy=1"
API15 = "analysis python=15; coverage coveragepy=1"
OLD13 = "analysis python=13; coverage cc-only"
OLD14 = "analysis python=14; coverage cc-only"
WEB13 = "analysis typescript=13; coverage istanbul=1"
WEB14 = "analysis typescript=14; coverage istanbul=1"
TOOLS13 = "analysis python=13; coverage no-lane"
SCOPES = {"api": API13, "old": OLD13, "web": WEB13}


def scoped(lizard: str = LIZARD, **scopes: str) -> list[str]:
    """The stamp lines of a per-scope marks file, key stamp included."""
    head = (f"# crapkit-stamp=2 lizard={lizard}: crapkit-ratchet.tsv holds one stamp per scope, "
            "which crapkit 0.9.0 or later reads")
    return [head, "# crapkit-keys=1", *(f"#scope\t{name}\t{text}" for name, text in sorted(scopes.items()))]


def single(analysis: int, lizard: str = LIZARD) -> list[str]:
    return [f"# crapkit-analysis={analysis} lizard={lizard}", "# crapkit-keys=1"]


UNSTAMPED = ["# crapkit-keys=1"]
BASE = scoped(**SCOPES)


def marks_file(stamp_lines: list[str], rows: list[str] = ()) -> str:
    return "\n".join([*stamp_lines, "path\tlong_name\tcrap", *rows]) + "\n"


def stamp_of(stamp_lines: list[str]):
    return read_stamp(marks_file(stamp_lines))


# id, BASE, OURS, THEIRS, the merged stamp lines, and each (scope, side its stamp came from)
MERGED = [
    ("all-equal", BASE, BASE, BASE, BASE, ()),
    ("ours-moved-api", BASE, scoped(**{**SCOPES, "api": API14}), BASE,
     scoped(**{**SCOPES, "api": API14}), (("api", "OURS"),)),
    ("both-moved-api-alike", BASE, scoped(**{**SCOPES, "api": API14}), scoped(**{**SCOPES, "api": API14}),
     scoped(**{**SCOPES, "api": API14}), ()),
    ("ours-added-tools", BASE, scoped(**SCOPES, tools=TOOLS13), BASE, scoped(**SCOPES, tools=TOOLS13),
     (("tools", "OURS"),)),
    ("theirs-removed-old", BASE, BASE, scoped(api=API13, web=WEB13), scoped(api=API13, web=WEB13), ()),
    ("ours-removed-old-theirs-moved-it", BASE, scoped(api=API13, web=WEB13),
     scoped(**{**SCOPES, "old": OLD14}), scoped(**{**SCOPES, "old": OLD14}), (("old", "THEIRS"),)),
    ("ours-renamed-web-to-frontend", BASE, scoped(api=API13, old=OLD13, frontend=WEB13), BASE,
     scoped(api=API13, old=OLD13, frontend=WEB13), (("frontend", "OURS"),)),
    ("lizard-moved-on-theirs", BASE, BASE, scoped("1.25.0", **SCOPES), scoped("1.25.0", **SCOPES),
     (("api", "THEIRS"), ("old", "THEIRS"), ("web", "THEIRS"))),
    ("single-number-equal", single(13), single(13), single(13), single(13), ()),
    ("single-number-ours-moved", single(12), single(13), single(12), single(13), ()),
    ("single-number-base-and-theirs-at-ours-numbers", single(13), scoped(api=API13, web=WEB13), single(13),
     scoped(api=API13, web=WEB13), ()),
    ("single-number-theirs-against-ours-raised-web", single(13), scoped(api=API13, web=WEB14), single(13),
     scoped(api=API13, web=WEB14), (("web", "OURS"),)),
    # OURS has no per-scope line 1, so the merged file's line 1 names the marks
    # file THEIRS's line 1 named, never the temporary file git hands over as OURS.
    ("single-number-base-and-ours-against-theirs-raised-web", single(13), single(13),
     scoped(api=API13, web=WEB14), scoped(api=API13, web=WEB14), (("web", "THEIRS"),)),
    # THEIRS moved only the lizard version, which merges on its own, so web's
    # number is still BASE's and OURS's raise wins.
    ("single-number-theirs-moved-lizard-against-ours-raised-web", single(13), scoped(api=API13, web=WEB14),
     single(13, "1.25.0"), scoped("1.25.0", api=API13, web=WEB14),
     (("api", "THEIRS"), ("web", "THEIRS"), ("web", "OURS"))),
    ("per-scope-base-theirs-moved-lizard-against-ours-raised-web", scoped(api=API13, web=WEB13),
     scoped(api=API13, web=WEB14), single(13, "1.25.0"), scoped("1.25.0", api=API13, web=WEB14),
     (("api", "THEIRS"), ("web", "THEIRS"), ("web", "OURS"))),
    # A single-number side that wins a scope takes that scope's languages and
    # coverage mode from OURS, or from BASE where OURS removed the scope.
    ("single-number-theirs-moved-every-scope-ours-held", BASE, BASE, single(14),
     scoped(api=API14, old=OLD14, web=WEB14), (("api", "THEIRS"), ("old", "THEIRS"), ("web", "THEIRS"))),
    ("single-number-theirs-moved-every-scope-ours-removed-old", BASE, scoped(api=API13, web=WEB13), single(14),
     scoped(api=API14, old=OLD14, web=WEB14), (("api", "THEIRS"), ("old", "THEIRS"), ("web", "THEIRS"))),
    ("unstamped-and-unstamped", UNSTAMPED, UNSTAMPED, UNSTAMPED, UNSTAMPED, ()),
]

# id, BASE, OURS, THEIRS, the scopes the refusal names (none for a whole-file refusal)
REFUSED = [
    ("both-moved-api-apart", BASE, scoped(**{**SCOPES, "api": API14}), scoped(**{**SCOPES, "api": API15}),
     ["api"]),
    ("lizard-moved-apart", BASE, scoped("1.25.0", **SCOPES), scoped("1.26.0", **SCOPES), ["api", "old", "web"]),
    ("lizard-moved-apart-and-theirs-removed-old", BASE, scoped("1.25.0", **SCOPES),
     scoped("1.26.0", api=API13, web=WEB13), ["api", "old", "web"]),
    ("single-number-theirs-and-ours-moved-web-apart", single(12), scoped(api=API13, web=WEB14), single(13),
     ["web"]),
    ("single-number-apart", single(12), single(13), single(14), []),
    ("unstamped-against-a-stamp", UNSTAMPED, UNSTAMPED, single(13), []),
]

SWAP = {"OURS": "THEIRS", "THEIRS": "OURS"}


def _ids(rows) -> list[str]:
    return [row[0] for row in rows]


@pytest.mark.parametrize("base, ours, theirs, want, came_from", [row[1:] for row in MERGED], ids=_ids(MERGED))
def test_merge_stamps_merges_each_scope_three_ways(base, ours, theirs, want, came_from):
    merged = merge_stamps(stamp_of(base), stamp_of(ours), stamp_of(theirs))

    assert (merged.stamp, type(merged.stamp)) == (stamp_of(want), type(stamp_of(want)))
    assert merged.came_from == came_from


@pytest.mark.parametrize("base, ours, theirs, want, came_from", [row[1:] for row in MERGED], ids=_ids(MERGED))
def test_swapping_ours_and_theirs_keeps_the_stamp_and_swaps_only_the_side_names(base, ours, theirs, want,
                                                                                 came_from):
    """Accuracy check 3: the merge has no favorite side."""
    swapped = merge_stamps(stamp_of(base), stamp_of(theirs), stamp_of(ours))

    assert swapped.stamp == stamp_of(want)
    assert sorted(swapped.came_from) == sorted((scope, SWAP[side]) for scope, side in came_from)


@pytest.mark.parametrize("base, ours, theirs, names", [row[1:] for row in REFUSED], ids=_ids(REFUSED))
def test_a_scope_both_sides_moved_differently_refuses_in_either_order(base, ours, theirs, names):
    for o, t in ((ours, theirs), (theirs, ours)):
        with pytest.raises(ValueError) as refused:
            merge_stamps(stamp_of(base), stamp_of(o), stamp_of(t))
        said = str(refused.value)
        assert said.startswith("ratchet merge refused: ") and said.endswith("OURS was left unchanged"), said
        assert [name for name in SCOPES if f"scope {name}: " in said] == names, said
        assert all(f"ratchet seed --scope {name}`" in said for name in names), said


def test_a_single_number_side_compares_on_its_number_and_lizard_only():
    """It records no coverage mode, so a per-scope side at its numbers has not
    moved, and a scope the per-scope side moved wins over it."""
    ours = stamp_of(scoped(api=API13, web=WEB14))

    for theirs in (SingleNumberStamp(13, LIZARD), stamp_of(single(13))):
        merged = merge_stamps(SingleNumberStamp(13, LIZARD), ours, theirs)
        assert (type(merged.stamp), merged.stamp.scopes) == (ScopeStamps, ours.scopes)


# --- the driver: the merged file, the marks, the note and the refusal ------------------

BASE_ROWS = ["a.py\tf( )\t50.0000", "b.py\tg( )\t40.0000", "c.py\th( )\t30.0000", "e.py\tk( )\t60.0000",
             "f.py\tm( )\t70.0000"]
# OURS lowers f and k and adds n; it prunes m.
OURS_ROWS = ["a.py\tf( )\t30.0000", "b.py\tg( )\t40.0000", "c.py\th( )\t30.0000", "d.py\tn( )\t20.0000",
             "e.py\tk( )\t45.0000"]
# THEIRS lowers g, h and k.
THEIRS_ROWS = ["a.py\tf( )\t50.0000", "b.py\tg( )\t25.0000", "c.py\th( )\t28.0000", "e.py\tk( )\t40.0000",
               "f.py\tm( )\t70.0000"]
MERGED_ROWS = ["a.py\tf( )\t30.0000", "b.py\tg( )\t25.0000", "c.py\th( )\t28.0000", "d.py\tn( )\t20.0000",
               "e.py\tk( )\t40.0000"]
CHANGED = {"OURS": 4, "THEIRS": 3}


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    """The lines name `crapkit` when PATH resolves it to this interpreter's
    console script, as it does where crapkit is installed."""
    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)


def write_sides(tmp_path, base, ours, theirs, rows=(BASE_ROWS, OURS_ROWS, THEIRS_ROWS)) -> list[str]:
    paths = []
    for name, lines, marks in zip(("base", "ours", "theirs"), (base, ours, theirs), rows):
        path = tmp_path / f"{name}.tsv"
        path.write_bytes(marks_file(lines, marks).encode("utf-8"))
        paths.append(str(path))
    return paths


def _marks_of(rows: list[str]) -> dict:
    return {(path, name): float(crap) for path, name, crap in (row.split("\t") for row in rows)}


def note(scope: str, side: str) -> str:
    other = SWAP[side]
    return (f"ratchet merge: scope {scope}'s stamp came from {side} and {other} changed "
            f"{CHANGED[other]} mark(s); if verify names {scope}, run crapkit ratchet seed --scope {scope}\n")


@pytest.mark.parametrize("base, ours, theirs, want, came_from", [row[1:] for row in MERGED], ids=_ids(MERGED))
def test_the_driver_writes_the_hand_built_file_and_names_each_scope_a_side_moved(
        tmp_path, capsys, base, ours, theirs, want, came_from):
    """Accuracy checks 1 and 2: the marks are merge_oracle's per-key rule over
    the three sides, and the whole file is the one written by hand."""
    files = write_sides(tmp_path, base, ours, theirs)

    code = main(["ratchet", "merge", *files])
    output = capsys.readouterr()

    assert (code, output.out) == (0, "ratchet merge: 5 mark(s)\n"), output.err
    text = (tmp_path / "ours.tsv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == want[0], "line 1 names the marks file a side's line 1 named"
    assert text == marks_file(want, MERGED_ROWS)
    assert {(e.path, e.long_name): e.crap for e in load_ratchet(text)} == three_way_marks(
        _marks_of(BASE_ROWS), _marks_of(OURS_ROWS), _marks_of(THEIRS_ROWS))
    assert output.err == "".join(note(scope, side) for scope, side in came_from)


def test_no_note_when_the_other_side_changed_no_mark(tmp_path, capsys):
    files = write_sides(tmp_path, BASE, scoped(**{**SCOPES, "api": API14}), BASE,
                        rows=(BASE_ROWS, OURS_ROWS, BASE_ROWS))

    assert main(["ratchet", "merge", *files]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("base, ours, theirs, names", [row[1:] for row in REFUSED], ids=_ids(REFUSED))
def test_a_refused_merge_exits_3_and_leaves_ours_as_it_was(tmp_path, capsys, base, ours, theirs, names):
    files = write_sides(tmp_path, base, ours, theirs)
    before = (tmp_path / "ours.tsv").read_bytes()

    assert main(["ratchet", "merge", *files]) == 3

    output = capsys.readouterr()
    assert output.out == "" and output.err.startswith("crapkit: ratchet merge refused: "), output.err
    assert (tmp_path / "ours.tsv").read_bytes() == before


def test_the_refusal_names_each_scope_what_each_side_recorded_and_the_fix(tmp_path, capsys):
    files = write_sides(tmp_path, BASE, scoped(**{**SCOPES, "api": API14, "web": WEB14}),
                        scoped(**{**SCOPES, "api": API15, "web": WEB13.replace("istanbul=1", "istanbul=2")}))

    assert main(["ratchet", "merge", *files]) == 3

    assert capsys.readouterr().err == (
        "crapkit: ratchet merge refused: OURS and THEIRS moved a stamp differently - scope api: OURS "
        f"records [lizard=1.24.0 {API14}] and THEIRS records [lizard=1.24.0 {API15}]; scope web: OURS "
        f"records [lizard=1.24.0 {WEB14}] and THEIRS records [lizard=1.24.0 analysis typescript=13; "
        "coverage istanbul=2] - run `crapkit coverage`, `crapkit ratchet seed --scope api` and "
        "`crapkit ratchet seed --scope web` on one branch, then merge again; OURS was left unchanged\n")


def test_a_single_number_refusal_names_the_newer_side_and_the_crapkit_to_reseed_with(tmp_path, capsys):
    files = write_sides(tmp_path, single(12), single(13), single(14))

    assert main(["ratchet", "merge", *files]) == 3

    assert capsys.readouterr().err == (
        "crapkit: ratchet merge refused: OURS records [crapkit-analysis=13 lizard=1.24.0] and THEIRS "
        "records [crapkit-analysis=14 lizard=1.24.0], and each side moved the stamp away from BASE's; "
        "THEIRS is newer, so on this branch, with a crapkit that measures [crapkit-analysis=14 "
        "lizard=1.24.0], run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline with "
        "`crapkit ratchet seed`, then merge again; OURS was left unchanged\n")


def test_the_driver_merges_stamps_with_no_analysis_stack_and_no_config(tmp_path, capsys, monkeypatch):
    """git runs the driver in a temp dir with no crapkit.toml, so it reads no
    config and imports no analysis code to merge the stamps."""
    files = write_sides(tmp_path, BASE, scoped(**{**SCOPES, "web": WEB14}), BASE)
    monkeypatch.chdir(tmp_path)
    for name in ("lizard", "crapkit.analyze", "crapkit.config"):
        monkeypatch.setitem(sys.modules, name, None)

    assert main(["ratchet", "merge", *files]) == 0

    assert (tmp_path / "ours.tsv").read_text(encoding="utf-8") == marks_file(
        scoped(**{**SCOPES, "web": WEB14}), MERGED_ROWS)
    assert not (tmp_path / "crapkit.toml").exists()


@pytest.mark.parametrize("bom, codec", [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")])
def test_utf16_crlf_sides_merge_and_ours_keeps_its_encoding_and_line_endings(tmp_path, capsys, bom, codec):
    """THEIRS's moved stamp reaches OURS, which a PowerShell saved as UTF-16 with CRLF."""
    sides = ((BASE, BASE_ROWS), (BASE, OURS_ROWS), (scoped(**{**SCOPES, "web": WEB14}), THEIRS_ROWS))
    files = []
    for name, (lines, rows) in zip(("base", "ours", "theirs"), sides):
        path = tmp_path / f"{name}.tsv"
        path.write_bytes(bom + marks_file(lines, rows).replace("\n", "\r\n").encode(codec))
        files.append(str(path))

    assert main(["ratchet", "merge", *files]) == 0

    want = marks_file(scoped(**{**SCOPES, "web": WEB14}), MERGED_ROWS).replace("\n", "\r\n")
    assert (tmp_path / "ours.tsv").read_bytes() == bom + want.encode(codec)
    assert capsys.readouterr().err == note("web", "THEIRS")
