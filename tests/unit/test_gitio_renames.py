"""`git diff --name-status -z` framing: a rename record carries TWO paths.

Pure seam. Walking the NUL-separated fields one at a time would read the new
path of every rename as the next record's status and shift the rest of the diff.
"""
from crapkit.gitio import _rename_pairs


def test_a_rename_record_consumes_its_two_paths():
    fields = "M\0other.py\0R100\0src/a.py\0src/b.py\0M\0last.py\0".split("\0")
    assert _rename_pairs(fields) == {"src/a.py": "src/b.py"}


def test_a_status_after_a_rename_is_still_read_as_a_status():
    fields = "R100\0src/a.py\0src/b.py\0D\0src/c.py\0R084\0src/d.py\0src/e.py\0".split("\0")
    assert _rename_pairs(fields) == {"src/a.py": "src/b.py", "src/d.py": "src/e.py"}


def test_a_copy_carries_two_paths_but_is_not_a_move():
    fields = "C100\0src/a.py\0src/copy.py\0M\0src/z.py\0".split("\0")
    assert _rename_pairs(fields) == {}, "the source still exists; its mark must not travel"


def test_a_diff_with_no_renames_is_an_empty_map():
    assert _rename_pairs("M\0a.py\0A\0b.py\0D\0c.py\0".split("\0")) == {}


def test_windows_separators_are_normalized_to_the_path_form_marks_use():
    assert _rename_pairs("R100\0src\\a.py\0lib\\a.py\0".split("\0")) == {"src/a.py": "lib/a.py"}
