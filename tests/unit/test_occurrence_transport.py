"""Cache and exported baselines retain within-line function positions."""
import json

import pytest

from crapkit.analyze import analyze_files, fingerprint, load_cache, save_cache
from crapkit.score import parse_scored_tsv, score_rows, scored_tsv_lines
from crapkit.snapshot import InventoryRow
from crapkit.verify import baseline_tsv_lines, parse_baseline_tsv


OLD_HEADER = ("scope\tpath\tlong_name\tstart\tend\tccn_std\tccn_mod\tccn\tnloc\tparams\t"
              "nesting\tcov\tflag\tcrap\tremedy\tcognitive")
OLD_LINE = "src\tsrc/a.ts\t(anonymous)\t1\t1\t2\t2\t2\t1\t0\t0\t0.0\tuntested\t6.0\tok\t0"


def test_score_and_portable_baseline_keep_occurrence():
    inventory = [InventoryRow("src", "src/a.ts", "(anonymous)", 1, 1, 2, 2, 2,
                              1, 0, 0, occurrence=number) for number in (1, 2)]
    rows = score_rows(inventory, {}, lane_scopes={"src"})
    assert [r.occurrence for r in rows] == [1, 2]
    text = "".join(baseline_tsv_lines("abc", "coverage", rows))
    parsed = parse_baseline_tsv(text)
    assert parsed.rows == rows
    assert "".join(baseline_tsv_lines(parsed.commit, parsed.kind, parsed.rows)) == text
    assert text.splitlines()[1] == OLD_HEADER + "\toccurrence"


def test_legacy_scored_header_and_row_read_with_unknown_occurrence():
    rows = parse_scored_tsv(OLD_HEADER + "\n" + OLD_LINE + "\n")
    assert len(rows) == 1
    assert rows[0].occurrence == 0
    assert "".join(scored_tsv_lines(rows)).splitlines()[1] == OLD_LINE + "\t0"


@pytest.mark.parametrize("suffix", ["-1", "bad", "1.0", ""])
def test_invalid_occurrence_is_refused(suffix):
    with pytest.raises(ValueError):
        parse_scored_tsv(OLD_HEADER + "\toccurrence\n" + OLD_LINE + "\t" + suffix)


@pytest.mark.parametrize("header,line", [
    (OLD_HEADER + "\twrong", OLD_LINE + "\t1"),
    (OLD_HEADER + "\toccurrence", OLD_LINE),
    (OLD_HEADER, OLD_LINE + "\t1"),
])
def test_header_and_row_shape_must_agree(header, line):
    with pytest.raises(ValueError):
        parse_scored_tsv(header + "\n" + line)


def test_cache_reloads_occurrences_and_same_reader_rename(tmp_path):
    path = tmp_path / "a.ts"
    path.write_text("values.map((x) => x > 1).filter((x) => x > 2);", encoding="utf-8")
    cold, _, cache = analyze_files(tmp_path, ["a.ts"], cache={})
    destination = tmp_path / "cache.json"
    save_cache(destination, cache)
    restored = load_cache(destination)
    warm, hits, _ = analyze_files(tmp_path, ["a.ts"], cache=restored)
    assert (warm, hits) == (cold, 1)
    path.rename(tmp_path / "b.ts")
    renamed, hits, _ = analyze_files(tmp_path, ["b.ts"], cache=restored)
    assert hits == 1
    assert [r.occurrence for r in renamed["b.ts"]] == [1, 2]


def test_old_cache_fingerprint_cannot_supply_unknown_positions(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _, _, old = analyze_files(tmp_path, ["a.py"], cache={})
    old["fp"] = fingerprint().replace("cache=3", "cache=2")
    _, hits, _ = analyze_files(tmp_path, ["a.py"], cache=old)
    assert hits == 0


def test_negative_cached_occurrence_reads_cold(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"fp": fingerprint(), "entries": {
        "bad": [["a.py", "f()", 1, 2, 1, 1, 1, 1, 0, 0, 0, -1]]}}), encoding="utf-8")
    assert load_cache(path) == {}
