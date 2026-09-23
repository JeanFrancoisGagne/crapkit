"""brief's twin list and duplication's pair list apply the threshold to the same number.

find_twins promises to score a function exactly as find_duplicates scores the
pair it would appear in. It rounded containment to four places before comparing
it with the threshold, and the pair scorer compared the raw value, so a pair at
containment 0.79996 was a twin in brief and absent from duplication.

Each function below is a run of distinct lines, so its shingles are its 4-line
windows: 25,003 lines give 25,000 shingles. The twin copies the first K lines
and then diverges, sharing K - 3 shingles.
"""
from crapkit.dup import find_duplicates, find_twins
from crapkit.snapshot import InventoryRow

LINES = 25_003


def _pair(shared_lines: int):
    first = [f"a{i} = {i}" for i in range(LINES)]
    second = first[:shared_lines] + [f"b{i} = {i}" for i in range(LINES - shared_lines)]
    sources = {"src/a.py": "\n".join(first) + "\n", "src/b.py": "\n".join(second) + "\n"}
    rows = [InventoryRow("src", path, name, 1, LINES, 1, 1, 1, LINES, 0, 0, 0, 1)
            for path, name in (("src/a.py", "f( )"), ("src/b.py", "g( )"))]
    return rows, sources


def test_containment_just_under_the_threshold_is_neither_a_twin_nor_a_pair():
    rows, sources = _pair(20_002)  # 19,999 of 25,000 shingles: 0.79996

    assert find_twins(rows[0], rows, sources) == []
    assert find_duplicates(rows, lambda: sources) == []


def test_containment_at_the_threshold_is_both_a_twin_and_a_pair():
    rows, sources = _pair(20_003)  # 20,000 of 25,000 shingles: 0.8

    twins = find_twins(rows[0], rows, sources)
    pairs = find_duplicates(rows, lambda: sources)

    assert [(t["path"], t["similarity"]) for t in twins] == [("src/b.py", 0.8)]
    assert [p["similarity"] for p in pairs] == [0.8]


def test_a_twin_reports_its_similarity_rounded_to_four_places():
    rows, sources = _pair(21_004)  # 21,001 of 25,000 shingles: 0.84004

    assert [t["similarity"] for t in find_twins(rows[0], rows, sources)] == [0.84]
