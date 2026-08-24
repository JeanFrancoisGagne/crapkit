"""A twin whose span holds the target's, or sits inside it, is nesting rather
than a clone. It still scores 100%, because a nested body's shingles are a
subset of its enclosing body's, so only a label tells the two apart. Scores and
order are untouched: the flag says what the number cannot.
"""
from crapkit.dup import find_twins
from crapkit.snapshot import InventoryRow


def row(path, name, start, end):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1)


BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
DEEPER = "\n".join(f"        step_{i} = compute({i}) + offset" for i in range(10))

# outer spans 1..22 and inner spans 12..22: one function, literally inside the
# other. Whitespace never distinguishes a clone, so the two bodies normalize the
# same and inner's shingles are all outer's too.
NESTED = "def outer():\n" + BODY + "\n    def inner():\n" + DEEPER + "\n"

A = "def alpha():\n" + BODY + "\n"
B = "def beta():\n" + BODY + "\n"
TWICE = A + "\n\n" + B


def test_the_function_that_encloses_the_target_is_labelled_contained():
    rows = [row("src/a.py", "outer", 1, 22), row("src/a.py", "inner", 12, 22)]

    (twin,) = find_twins(rows[1], rows, {"src/a.py": NESTED})

    assert twin["long_name"] == "outer"
    assert twin["similarity"] == 1.0, "a nested body is a subset of its enclosing one"
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
