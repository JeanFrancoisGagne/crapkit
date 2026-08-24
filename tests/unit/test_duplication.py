"""Near-duplicate functions: normalized line shingles, containment scored, so a
copy-paste that then grew a few lines still surfaces. Tiny functions are noise
and stay out."""
import weakref

from crapkit.dup import find_duplicates, find_twins
from crapkit.snapshot import InventoryRow


def row(path, name, start, end):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1)


BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
A = "def alpha():\n" + BODY + "\n"
B = "def beta():\n" + BODY + "\n"
C = "def gamma():\n" + "\n".join(f"    other_{i} = load({i})" for i in range(10)) + "\n"


def test_identical_bodies_across_files_pair_up():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11)]
    (pair,) = find_duplicates(rows, lambda: {"src/a.py": A, "src/b.py": B})
    assert {f["path"] for f in pair["functions"]} == {"src/a.py", "src/b.py"}
    assert pair["similarity"] >= 0.85


def test_unrelated_bodies_do_not_pair():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/c.py", "gamma", 1, 11)]
    assert find_duplicates(rows, lambda: {"src/a.py": A, "src/c.py": C}) == []


def test_tiny_functions_are_skipped():
    tiny = "def t():\n    return 1\n"
    rows = [row("src/a.py", "t", 1, 2), row("src/b.py", "t", 1, 2)]
    assert find_duplicates(rows, lambda: {"src/a.py": tiny, "src/b.py": tiny}) == []


def test_comment_and_blank_lines_do_not_break_the_match():
    b_with_noise = "def beta():\n" + BODY.replace(
        "step_5", "step_5") + "\n"  # identical body...
    b_with_noise = b_with_noise.replace("    step_7", "    # a comment\n\n    step_7")
    rows = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 13)]
    (pair,) = find_duplicates(rows, lambda: {"src/a.py": A, "src/b.py": b_with_noise})
    assert pair["similarity"] >= 0.85


def test_rows_of_one_file_arriving_apart_find_the_same_pairs():
    """Sources are split once per file behind a one-entry cache. Rows normally
    arrive path-sorted, so the cache hits; rows that interleave two files must
    still read each function's OWN lines, not a neighbour's."""
    rows = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11),
            row("src/a.py", "alpha2", 12, 22), row("src/b.py", "beta2", 12, 22)]
    sources = {"src/a.py": A + A.replace("alpha", "alpha2"),
               "src/b.py": B + B.replace("beta", "beta2")}

    def spans(pairs):  # the sort tie among equal-similarity pairs is input order
        return sorted(tuple((f["path"], f["start"]) for f in p["functions"]) for p in pairs)

    interleaved = find_duplicates(rows, lambda: sources)
    by_path = find_duplicates(sorted(rows, key=lambda r: (r.path, r.start)), lambda: sources)
    assert spans(interleaved) == spans(by_path)
    assert len(interleaved) == 6, "four identical bodies pair every way"


def test_a_row_whose_file_was_never_loaded_is_skipped_not_scored_empty():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/gone.py", "ghost", 1, 11),
            row("src/b.py", "beta", 1, 11)]
    (pair,) = find_duplicates(rows, lambda: {"src/a.py": A, "src/b.py": B})
    assert {f["path"] for f in pair["functions"]} == {"src/a.py", "src/b.py"}


class _Weakable(dict):
    """A plain dict cannot be weak-referenced; this one can."""


class _ProbeRow:
    """Duck-types the five fields dup reads off a row. `long_name` is touched
    ONLY when a pair payload is built, which is after the index exists, so
    reading it is how a test observes what is still alive during scoring."""

    def __init__(self, path, start, end, on_payload):
        self.path, self.start, self.end = path, start, end
        self.nloc = end - start + 1
        self._on_payload = on_payload

    @property
    def long_name(self):
        self._on_payload()
        return "probe"


def test_the_source_texts_are_gone_before_any_pair_is_scored():
    """The shingles are ints, so a file's text is dead the moment its rows are
    indexed. Holding the texts across the pair counting cost 146 MB of peak on
    a 104 MB repo, which is why they arrive through a LOADER: no caller ever
    binds a name to them, and the index is the only thing that outlives them."""
    texts, freed_when_scored = [], []

    def load_sources():
        loaded = _Weakable({"src/a.py": A, "src/b.py": B})
        texts.append(weakref.ref(loaded))
        return loaded

    def note():
        freed_when_scored.append(texts[0]() is None)

    rows = [_ProbeRow("src/a.py", 1, 11, note), _ProbeRow("src/b.py", 1, 11, note)]

    (pair,) = find_duplicates(rows, load_sources)

    assert pair["similarity"] >= 0.85, "the pair still surfaces"
    assert freed_when_scored == [True, True], \
        "both payload reads happened with the texts already released"


# --- one function's twins: the same shingles, asked about a single row --------

def test_a_twin_scores_exactly_what_the_pair_it_belongs_to_scores():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11),
            row("src/c.py", "gamma", 1, 11)]
    sources = {"src/a.py": A, "src/b.py": B, "src/c.py": C}
    (pair,) = find_duplicates(rows, lambda: sources)

    (twin,) = find_twins(rows[0], rows, sources)

    assert twin["path"] == "src/b.py" and twin["long_name"] == "beta"
    assert twin["similarity"] == pair["similarity"] == 0.875, \
        "10 shared body lines of 11 give 7 shared shingles of 8"


def test_a_function_is_never_its_own_twin():
    rows = [row("src/a.py", "alpha", 1, 11)]
    assert find_twins(rows[0], rows, {"src/a.py": A}) == []


def test_a_function_too_small_to_shingle_has_no_twins():
    tiny = "def t():\n    return 1\n"
    rows = [row("src/a.py", "t", 1, 2), row("src/b.py", "t", 1, 2)]
    assert find_twins(rows[0], rows, {"src/a.py": tiny, "src/b.py": tiny}) == []


def test_a_function_below_the_threshold_is_not_a_twin():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/c.py", "gamma", 1, 11)]
    assert find_twins(rows[0], rows, {"src/a.py": A, "src/c.py": C}) == []


def test_equally_similar_twins_are_ordered_by_path_so_two_runs_agree():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/z.py", "zeta", 1, 11),
            row("src/b.py", "beta", 1, 11)]
    sources = {"src/a.py": A, "src/z.py": B.replace("beta", "zeta"), "src/b.py": B}
    assert [t["path"] for t in find_twins(rows[0], rows, sources)] == ["src/b.py", "src/z.py"]


def test_a_target_whose_file_was_never_loaded_has_no_twins():
    rows = [row("src/gone.py", "ghost", 1, 11), row("src/b.py", "beta", 1, 11)]
    assert find_twins(rows[0], rows, {"src/b.py": B}) == []
