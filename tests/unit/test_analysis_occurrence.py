"""Function positions survive callbacks sharing both signature and source line."""
from concurrent.futures import ProcessPoolExecutor

import pytest

from crapkit.analyze import analyze_one, analyze_source
from crapkit.snapshot import build_inventory_rows


CHAIN = "const a = values.map((x) => x > 0 ? x : 0).filter((x) => x > 1);"


def test_chain_keeps_both_positions_and_raw_reader_values():
    rows = analyze_source("sample.ts", CHAIN)
    assert [(r.long_name, r.start, r.end, r.ccn, r.occurrence) for r in rows] == [
        ("(anonymous)", 1, 1, 2, 1), ("(anonymous)", 1, 1, 1, 2)]


def test_nested_callbacks_number_creation_not_completion_order():
    source = "values.map((x) => { if (x) return other.map((y) => { return y; }); });"
    records = analyze_source("sample.ts", source)
    assert [r.ccn for r in records] == [1, 2]
    assert [r.occurrence for r in records] == [2, 1]
    rows = build_inventory_rows({"src": records})
    assert [(r.ccn, r.occurrence) for r in rows] == [(2, 1), (1, 2)]


def test_equal_records_and_scope_copies_keep_their_occurrences():
    source = "const f = [(x) => { return x + 1; }, (x) => { return x + 2; }];"
    rows = build_inventory_rows({scope: analyze_source("sample.ts", source)
                                 for scope in ("one", "two")})
    assert [(r.scope, r.occurrence) for r in rows] == [
        ("one", 1), ("one", 2), ("two", 1), ("two", 2)]
    assert len(set(rows)) == 4


@pytest.mark.parametrize("extension,source", [
    ("cpp", "int f(int x) { return x; } int f(int x) { return x + 1; }"),
    ("rs", "fn f(x: i32) -> i32 { x } fn g(x: i32) -> i32 { x + 1 }"),
    ("swift", "func f(x: Int) -> Int { return x }; func g(x: Int) -> Int { return x + 1 }"),
    ("kt", "fun f(x: Int): Int { return x }; fun g(x: Int): Int { return x + 1 }"),
    ("sh", "f() { echo x; }; g() { echo y; }"),
    ("ps1", "function f { Write-Output x }; function g { Write-Output y }"),
])
def test_reader_creation_order_is_per_file(extension, source):
    for _ in range(2):
        rows = analyze_source("sample." + extension, source)
        assert [(r.start, r.occurrence) for r in rows] == [(1, 1), (1, 2)]


def test_distinct_lines_restart_occurrence_count():
    rows = analyze_source("sample.py", "def f(x):\n    return x\ndef g(y):\n    return y\n")
    assert [(r.start, r.occurrence) for r in rows] == [(1, 1), (3, 1)]


def test_spawned_workers_keep_same_line_occurrences(tmp_path):
    path = tmp_path / "sample.ts"
    path.write_text(CHAIN, encoding="utf-8")
    with ProcessPoolExecutor(max_workers=1) as pool:
        _, actual = pool.submit(analyze_one, (str(path), "sample.ts")).result()
    assert actual == analyze_source("sample.ts", CHAIN)
    assert [r.occurrence for r in actual] == [1, 2]
