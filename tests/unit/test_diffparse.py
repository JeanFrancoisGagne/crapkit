"""Staged-diff seam: git diff -U0 text in, per-file changed new-side line ranges out. Pure."""
from crapkit.diffparse import changed_ranges, git_span, reader_ranges

DIFF = """\
diff --git a/src/app.ts b/src/app.ts
index 111..222 100644
--- a/src/app.ts
+++ b/src/app.ts
@@ -10,2 +12,3 @@ export function plain
+a
+b
+c
@@ -40,0 +50 @@ tail
+z
diff --git a/pylib/mod.py b/pylib/mod.py
--- a/pylib/mod.py
+++ b/pylib/mod.py
@@ -1 +1,2 @@
+x
"""


def test_new_side_ranges_per_file():
    ranges = changed_ranges(DIFF)
    assert ranges["src/app.ts"] == [(12, 14), (50, 50)]
    assert ranges["pylib/mod.py"] == [(1, 2)]


def test_deleted_only_hunks_still_mark_the_touch_point():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -5,3 +4,0 @@\n-gone\n"
    assert changed_ranges(diff)["x.py"] == [(4, 4)]


def test_new_and_renamed_files_use_the_new_path():
    diff = ("diff --git a/old.py b/new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1,2 @@\n+x\n")
    assert list(changed_ranges(diff)) == ["new.py"]


def test_quoted_new_paths_decode():
    diff = ('diff --git "a/docs/r\\303\\251s.md" "b/docs/r\\303\\251s.md"\n'
            '--- "a/docs/r\\303\\251s.md"\n+++ "b/docs/r\\303\\251s.md"\n@@ -1 +1,2 @@\n+x\n')
    assert "docs/rés.md" in changed_ranges(diff)


def test_empty_diff_gives_empty_mapping():
    assert changed_ranges("") == {}


def test_added_line_starting_with_plus_plus_is_not_a_file_header():
    # git emits an added source line "++ marker" as "+++ marker"; hunk body
    # lines must be consumed by count, never parsed as headers.
    diff = (
        "--- a/src/app.ts\n"
        "+++ b/src/app.ts\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+++ marker\n"
        "+line3\n"
        "@@ -10,0 +14 @@\n"
        "+later\n"
    )
    ranges = changed_ranges(diff)
    assert ranges == {"src/app.ts": [(1, 3), (14, 14)]}, \
        "the ++ marker line hijacked the current file and hid the later hunk"


# --- git's lines on the reader's lines ------------------------------------------------------
# git ends a line at LF only. The reader ends one at LF, CRLF and a lone CR, as
# Python's compiler, coverage.py and ECMAScript do.


def on_reader(ranges: list, raw: bytes | None) -> list:
    return reader_ranges({"m.py": ranges}, {"m.py": raw}.get)["m.py"]


def test_lf_and_crlf_lines_are_the_same_lines_to_both():
    for raw in (b"a\nb\nc\n", b"a\r\nb\r\nc\r\n"):
        assert on_reader([(2, 2), (3, 3)], raw) == [(2, 2), (3, 3)]


def test_each_lone_cr_above_a_line_moves_it_one_reader_line_down():
    raw = b"# a\rx = 1\ndef f():\n    return 1\n"
    assert on_reader([(2, 3)], raw) == [(3, 4)]


def test_a_git_line_holding_lone_crs_spans_every_reader_line_in_it():
    raw = b"def f(n):\r    if n:\r        return 1\r    return 2\r"
    assert on_reader([(1, 1)], raw) == [(1, 4)]


def test_a_last_line_with_no_line_end_spans_its_reader_lines():
    assert on_reader([(1, 1)], b"a\rb") == [(1, 2)]
    assert on_reader([(2, 2)], b"a\nb\rc") == [(2, 3)]


def test_a_crlf_after_a_lone_cr_is_one_line_end():
    assert on_reader([(2, 2)], b"a\r\r\nb\n") == [(3, 3)]


def test_a_line_past_the_end_reads_as_the_last_git_line():
    """Bytes read after the diff was taken can be shorter than its new side."""
    assert on_reader([(5, 9)], b"a\rb\n") == [(1, 2)]
    assert on_reader([(1, 9)], b"a\nb\rc\n") == [(1, 3)]


def test_a_file_with_no_new_side_keeps_gits_lines():
    """A deleted file has no bytes, and no reader line to move onto."""
    assert on_reader([(3, 4)], None) == [(3, 4)]


def test_every_file_is_placed_by_its_own_bytes():
    ranges = {"a.py": [(2, 2)], "b.py": [(2, 2)]}
    sides = {"a.py": b"x\ry\nz\n", "b.py": b"x\ny\n"}
    assert reader_ranges(ranges, sides.get) == {"a.py": [(3, 3)], "b.py": [(2, 2)]}


# --- the reader's lines on git's lines -------------------------------------------------------
# `git log -L` takes a span in git's numbers, and a span crapkit holds is the reader's.


def test_a_reader_span_below_a_lone_cr_is_one_git_line_higher():
    raw = b"# a\rx = 1\ndef f():\n    return 1\n"
    assert git_span(raw, 3, 4) == (2, 3)


def test_reader_lines_inside_one_git_line_are_that_git_line():
    raw = b"def f(n):\r    if n:\r        return 1\r    return 2\r"
    assert git_span(raw, 2, 4) == (1, 1)


def test_a_span_in_a_file_with_no_lone_cr_or_no_bytes_keeps_its_lines():
    assert git_span(b"a\r\nb\r\nc\r\n", 2, 3) == (2, 3)
    assert git_span(None, 2, 3) == (2, 3)
