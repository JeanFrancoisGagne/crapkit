"""dup's twins and pairs at the edges of their thresholds, cuts and order.

dup.py: a function is shingled when it holds at least min_lines normalized
lines (8 by default), in windows of 4; containment is shared shingles over
the smaller set; twins list best first and cut at `top` (10 by default), pairs
cut at 50; pairs rank at the 4 places their similarity prints, a tie going to
the file order; and a line whose bytes did not decode (a lone surrogate) is
shingled as it stands.
"""
from crapkit.dup import find_duplicates, find_twins, function_index, twins_in
from crapkit.snapshot import InventoryRow


def build(files: dict[str, list[list[str]]]):
    """Rows and sources for files given as lists of function bodies."""
    rows, sources = [], {}
    for path, bodies in files.items():
        lines, start = [], 1
        for n, body in enumerate(bodies):
            text = ["def f():"] + [f"    {line}" for line in body]
            rows.append(InventoryRow("src", path, f"f{n}( )", start, start + len(text) - 1,
                                     1, 1, 1, len(text), 0, 1))
            lines += text
            start += len(text)
        sources[path] = "\n".join(lines) + "\n"
    return rows, sources


def body(tag: str, count: int, first: int = 0) -> list[str]:
    return [f"{tag}{i} = {i}" for i in range(first, first + count)]


def twins_of(rows, sources, **kw):
    return find_twins(rows[0], rows, sources, **kw)


def test_twins_list_the_closest_first():
    """Each function is 11 normalized lines, so 8 shingles: f2 holds all of f0's,
    f1 the 7 that end before its last line."""
    rows, sources = build({"a.py": [body("x", 10), body("x", 9) + ["y = 0"], body("x", 10)]})

    assert [(t["long_name"], t["similarity"]) for t in twins_of(rows, sources)] == [
        ("f2( )", 1.0), ("f1( )", 0.875)]


def test_a_function_of_exactly_min_lines_lines_is_shingled():
    """7 lines of body and the def line: 8 normalized lines."""
    rows, sources = build({"a.py": [body("x", 7), body("x", 7)]})

    assert [t["long_name"] for t in twins_of(rows, sources)] == ["f1( )"]


def test_twins_cut_at_10_by_default_and_at_top():
    rows, sources = build({"a.py": [body("x", 10)] * 12})
    index = function_index(rows, sources)

    assert (len(twins_of(rows, sources)), len(twins_of(rows, sources, top=11)),
            len(twins_in(index, rows[0], sources["a.py"]))) == (10, 11, 10)


def test_a_threshold_other_than_the_index_s_rebuilds_it():
    """3 lines of body and the def line: one shingle at min_lines 4, none at 8."""
    rows, sources = build({"a.py": [body("x", 3), body("x", 3)]})

    assert [p["similarity"] for p in find_duplicates(rows, lambda: sources, min_lines=4)] == [1.0]


def test_pairs_cut_at_50_by_default():
    rows, sources = build({f"m{n:02}.py": [body(f"x{n}_", 10), body(f"x{n}_", 10)]
                           for n in range(51)})

    assert len(find_duplicates(rows, lambda: sources)) == 50


def test_pairs_tied_at_4_places_list_in_file_order():
    """a.py's pair shares 50 of 91 shingles (0.549450...), b.py's 61 of 111
    (0.549549...): both print 0.5495, so the file order decides."""
    rows, sources = build({"a.py": [body("a", 93), body("a", 52) + body("c", 41)],
                           "b.py": [body("b", 113), body("b", 63) + body("d", 50)]})

    pairs = find_duplicates(rows, lambda: sources, similarity=0.5)

    assert [(p["functions"][0]["path"], p["similarity"]) for p in pairs] == [
        ("a.py", 0.5495), ("b.py", 0.5495)]


def test_a_line_that_did_not_decode_is_shingled_as_it_stands():
    rows, sources = build({"a.py": [body("x", 9) + ["s = '\udcff'"], body("x", 9) + ["s = '\udcff'"]]})

    assert [t["similarity"] for t in twins_of(rows, sources)] == [1.0]
