"""Snapshot seam: per-scope FunctionRecords in, canonical sorted rows and export text out. Pure."""
import random

from crapkit.merge import FunctionRecord
from crapkit.snapshot import build_inventory_rows, tsv_lines


def rec(path="src/a.ts", name="f( x )", start=1, end=9, ccn_std=7, ccn_mod=5, nloc=8, params=1, nesting=2):
    return FunctionRecord(path, name, start, end, ccn_std, ccn_mod, min(ccn_std, ccn_mod), nloc, params, nesting)


def export_tsv(rows):
    """The export document, joined here so these tests can compare whole text.
    Production never joins it: cli writes the lines straight to the file."""
    return "".join(tsv_lines(rows))


def test_rows_are_sorted_by_scope_path_start_and_carry_no_timestamp():
    rows = build_inventory_rows({"py": [rec(path="scripts/z.py")], "src": [rec(path="src/b.ts", start=5), rec(path="src/b.ts", start=1)]})
    assert [(r.scope, r.path, r.start) for r in rows] == [
        ("py", "scripts/z.py", 1),
        ("src", "src/b.ts", 1),
        ("src", "src/b.ts", 5),
    ]
    assert not any(hasattr(r, "ts") or hasattr(r, "timestamp") for r in rows)


def test_export_is_byte_identical_under_any_input_permutation():
    records = [rec(path=f"src/f{i}.ts", start=i, ccn_std=i + 1, ccn_mod=i) for i in range(1, 30)]
    baseline = export_tsv(build_inventory_rows({"src": list(records)}))
    for seed in range(5):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        assert export_tsv(build_inventory_rows({"src": shuffled})) == baseline


def test_export_carries_both_ccn_variants_and_the_min():
    text = export_tsv(build_inventory_rows({"src": [rec(ccn_std=7, ccn_mod=5)]}))
    header, row = text.strip().splitlines()
    cols = dict(zip(header.split("\t"), row.split("\t")))
    assert cols["ccn_std"] == "7"
    assert cols["ccn_mod"] == "5"
    assert cols["ccn"] == "5"
    assert cols["long_name"] == "f( x )"
