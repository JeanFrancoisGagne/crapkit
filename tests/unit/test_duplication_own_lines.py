"""A function is shingled from its own lines. The lines of a function nested in
it, past that function's first line, belong to the nested function, which pairs
on them itself.

This is how crapkit already counts nloc: lizard gives `factory` (lines 1..5 of
`def factory` / `def closure` / body / `return closure`) nloc 3, its def line,
the nested def line and its return, and the closure nloc 3, its def line and
body. Shingled whole instead, a factory carried its closure's lines, so every
clone of a closure was reported twice: once for the closure, and once for a
factory whose own lines are under --min-lines.
"""
from crapkit.dup import find_duplicates, find_twins, function_index, twins_in
from crapkit.snapshot import InventoryRow


def row(path, name, start, end, occurrence=1):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1,
                        occurrence=occurrence)


BODY = ["total = 0", "for item in items:", "    if item > limit:", "        total += item * 2",
        "    elif item < 0:", "        total -= item", "    else:", "        total += 1",
        "count = len(items)", "return total / max(count, 1)"]


def indented(lines, pad):
    return "".join(pad + line + "\n" for line in lines)


COPY = "def copy_a(items, limit):\n" + indented(BODY, "    ")
# factory 1..13 holds closure 2..12: three lines of its own around ten copied ones
FACTORY = ("def factory(limit):\n    def closure(items):\n" + indented(BODY, "        ")
           + "    return closure\n")
ROWS = [row("a.py", "copy_a( items , limit )", 1, 11),
        row("c.py", "factory( limit )", 1, 13),
        row("c.py", "factory.closure( items )", 2, 12)]
SOURCES = {"a.py": COPY, "c.py": FACTORY}


def names(pairs):
    return [sorted(f["long_name"] for f in pair["functions"]) for pair in pairs]


def test_a_closure_clone_is_reported_once_and_never_through_its_factory():
    pairs = find_duplicates(ROWS, lambda: SOURCES)

    assert names(pairs) == [["copy_a( items , limit )", "factory.closure( items )"]]
    assert pairs[0]["similarity"] == 0.875


def test_a_factory_long_enough_to_pair_still_pairs_only_on_its_own_lines():
    """Nine unrelated lines of its own put the factory over --min-lines. Its
    shingles still leave out the closure's body, so it shares none with copy_a."""
    own = [f"setting_{i} = configure({i}, limit)" for i in range(9)]
    text = ("def factory(limit):\n" + indented(own, "    ") + "    def closure(items):\n"
            + indented(BODY, "        ") + "    return closure\n")
    rows = [ROWS[0], row("c.py", "factory( limit )", 1, 22),
            row("c.py", "factory.closure( items )", 11, 21)]

    pairs = find_duplicates(rows, lambda: {"a.py": COPY, "c.py": text})

    assert names(pairs) == [["copy_a( items , limit )", "factory.closure( items )"]]


def test_a_brief_on_the_copy_names_the_closure_and_not_the_factory():
    twins = find_twins(ROWS[0], ROWS, SOURCES)

    assert [t["long_name"] for t in twins] == ["factory.closure( items )"]


def test_a_brief_on_the_factory_finds_it_too_short_to_have_twins():
    assert find_twins(ROWS[1], ROWS, SOURCES) == []


def test_the_stored_index_reader_leaves_the_nested_lines_out_of_its_target():
    """brief shingles its target from the file as it reads now, against the
    run's index. The target's own lines need the rows of its file."""
    index = function_index(ROWS, SOURCES)
    file_rows = ROWS[1:]

    assert twins_in(index, ROWS[1], FACTORY, file_rows) == []
    assert [t["long_name"] for t in twins_in(index, ROWS[2], FACTORY, file_rows)] == \
        ["copy_a( items , limit )"]


# `export const load = (id) => async (dispatch) => {`: lizard gives the outer
# arrow and the inner one the same span, the outer first on the start line.
THUNK_BODY = ["  let total = 0;", "  for (const item of items) {", "    if (item > id) {",
              "      total += item * 2;", "    } else {", "      total -= item;", "    }",
              "  }", "  dispatch(total);", "  return total;"]
THUNK = "export const load = (id) => async (dispatch) => {\n" + indented(THUNK_BODY, "") + "}\n"
CLONE = "export async function reload(id, dispatch) {\n" + indented(THUNK_BODY, "") + "}\n"


def test_an_arrow_returning_an_arrow_on_one_span_pairs_once():
    rows = [row("t.js", "load", 1, 12, occurrence=1),
            row("t.js", "(anonymous)", 1, 12, occurrence=2),
            row("u.js", "reload", 1, 12)]

    pairs = find_duplicates(rows, lambda: {"t.js": THUNK, "u.js": CLONE})

    assert names(pairs) == [["(anonymous)", "reload"]]


def test_scope_copies_of_one_function_are_not_nested_in_each_other():
    """Two scopes can score one file: the same span and occurrence twice is one
    function, and each copy keeps every line."""
    twin = ROWS[0]._replace(scope="lib")
    rows = [ROWS[0], twin, row("b.py", "copy_b( items , limit )", 1, 11)]
    sources = {"a.py": COPY, "b.py": COPY.replace("copy_a", "copy_b")}

    pairs = find_duplicates(rows, lambda: sources)

    assert names(pairs) == [["copy_a( items , limit )", "copy_b( items , limit )"]] * 2


# --- brief, through the CLI: the packet hands the twin reader its file's rows ---

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["."]
languages = ["python"]
coverage_optional = true
"""


def scored(path: str, name: str, start: int, end: int):
    from crapkit.score import ScoredRow

    return ScoredRow("src", path, name, start, end, 3, 3, 3, end - start + 1, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, 1)


def test_brief_on_a_factory_lists_no_twin_and_on_its_closure_lists_the_copy(tmp_path, capsys):
    import json
    import subprocess

    from crapkit.cli import main
    from crapkit.store import SnapshotStore

    root = tmp_path / "repo"
    root.mkdir()
    for path, text in {"crapkit.toml": TOML, **SOURCES}.items():
        (root / path).write_bytes(text.encode("utf-8"))
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                       capture_output=True)
    (root / ".crapkit").mkdir()
    SnapshotStore(root / ".crapkit" / "crap.sqlite").write_run(
        commit="a" * 40, tool_versions={}, rows=[scored(r.path, r.long_name, r.start, r.end)
                                                 for r in ROWS])

    def twins(name: str) -> list:
        assert main(["brief", "c.py", name, "--json", "--repo", str(root)]) == 0
        return [t["long_name"] for t in json.loads(capsys.readouterr().out)["duplication_twins"]]

    assert twins("factory( limit )") == []
    assert twins("factory.closure( items )") == ["copy_a( items , limit )"]
