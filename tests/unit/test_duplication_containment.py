"""A function whose span holds another's, or sits inside it, is nesting rather
than a clone. Each is shingled from its own lines: the nested function's lines
past its first are its own, not its encloser's, so the two share shingles only
where the encloser's own lines copy the nested body.

When they do, the two readers answer differently. `find_twins` keeps the pair
and labels it, because a brief about one function wants to know that its twin
is the function around it or inside it. `find_duplicates` drops it: a function
and its nested closure never pair, and a factory paired with its own closure
is not work.
"""
from crapkit.dup import find_duplicates, find_twins
from crapkit.snapshot import InventoryRow


def row(path, name, start, end):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1)


BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
DEEPER = "\n".join(f"        step_{i} = compute({i}) + offset" for i in range(10))

# outer spans 1..22 and inner spans 12..22: one function, literally inside the
# other. outer's own lines are 1..12, its body and the `def inner` line.
# Whitespace never distinguishes a clone, so outer's body and inner's normalize
# the same: 7 of inner's 8 shingles are outer's too.
NESTED = "def outer():\n" + BODY + "\n    def inner():\n" + DEEPER + "\n"

A = "def alpha():\n" + BODY + "\n"
B = "def beta():\n" + BODY + "\n"
TWICE = A + "\n\n" + B


def test_the_function_that_encloses_the_target_is_labelled_contained():
    rows = [row("src/a.py", "outer", 1, 22), row("src/a.py", "inner", 12, 22)]

    (twin,) = find_twins(rows[1], rows, {"src/a.py": NESTED})

    assert twin["long_name"] == "outer"
    assert twin["similarity"] == 0.875, "outer's own body copies inner's, not its lines"
    assert twin["contained"] is True


def test_the_function_nested_inside_the_target_is_labelled_contained():
    rows = [row("src/a.py", "outer", 1, 22), row("src/a.py", "inner", 12, 22)]

    (twin,) = find_twins(rows[0], rows, {"src/a.py": NESTED})

    assert twin["long_name"] == "inner"
    assert twin["contained"] is True


def test_a_real_clone_in_another_file_is_not_contained():
    rows = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11)]

    (twin,) = find_twins(rows[0], rows, {"src/a.py": A, "src/b.py": B})

    assert twin["path"] == "src/b.py"
    assert twin["contained"] is False


def test_two_clones_in_one_file_with_separate_spans_are_not_contained():
    """Same file is not enough: alpha at 1..11 and beta at 14..24 are a genuine
    copy-paste, and the label has to leave that one alone."""
    rows = [row("src/a.py", "alpha", 1, 11), row("src/a.py", "beta", 14, 24)]

    (twin,) = find_twins(rows[0], rows, {"src/a.py": TWICE})

    assert twin["long_name"] == "beta"
    assert twin["contained"] is False


# --- the standalone report: nesting is dropped, not ranked --------------------
#
# Issue #1, measured on the Nera-CodingGraph pilot: 43 of 43 reported pairs were
# same-file and span-contained, and the closure-factory idiom produced every one
# of them. The shapes below are that repo's, spans included.

MCP = "adapters/mcp_server.py"
PAD = "\n".join(f"import mod_{i}" for i in range(1, 280))  # lines 1..279
CLOSURE_BODY = "\n".join(f"        step_{i} = probe({i}) + deps.offset" for i in range(15))

# _reach_tool opens at 280 and returns at 299; its closure `reach` opens at 281
# and returns at 297. lizard scores both. Shingled whole, the factory held the
# closure's lines and containment read 1.0; its own lines are 280, 281, 298 and
# 299.
FACTORY = (PAD + "\n"
           "def _reach_tool(deps: _Deps):\n"
           "    def reach(name: str, depth: int = 1) -> dict:\n"
           + CLOSURE_BODY + "\n"
           '        return {"name": name, "depth": depth}\n'
           "    deps.register(reach)\n"
           "    return reach\n")

FACTORY_ROWS = [row(MCP, "_reach_tool( deps : _Deps )", 280, 299),
                row(MCP, "_reach_tool.reach( name : str , depth : int )", 281, 297)]


def test_a_factory_paired_with_its_own_closure_is_not_reported():
    assert find_duplicates(FACTORY_ROWS, lambda: {MCP: FACTORY}) == []


def test_the_factory_keeps_only_its_own_four_lines_and_is_no_twin_of_its_closure():
    """The 1.0 that topped the pilot's report came from the factory carrying
    its closure's lines. Its own four are under --min-lines, so neither one
    names the other."""
    assert find_twins(FACTORY_ROWS[1], FACTORY_ROWS, {MCP: FACTORY}) == []
    assert find_twins(FACTORY_ROWS[0], FACTORY_ROWS, {MCP: FACTORY}) == []


def test_a_cross_file_clone_survives_alongside_the_dropped_nesting():
    rows = FACTORY_ROWS + [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11)]

    (pair,) = find_duplicates(rows, lambda: {MCP: FACTORY, "src/a.py": A, "src/b.py": B})

    assert [f["path"] for f in pair["functions"]] == ["src/a.py", "src/b.py"]
    assert pair["similarity"] == 0.875


def test_every_reported_pair_carries_the_containment_flag():
    """A consumer joining pairs to twins reads one shape. On a kept pair the
    answer is always False, and False is a claim, not a missing key."""
    rows = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11)]

    (pair,) = find_duplicates(rows, lambda: {"src/a.py": A, "src/b.py": B})

    assert pair["contained"] is False
