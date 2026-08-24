"""Staged-diff seam: git diff -U0 text in, per-file changed new-side line ranges out. Pure."""
from crapkit.diffparse import changed_ranges

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
