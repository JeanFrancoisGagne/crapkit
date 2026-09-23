"""doctor.unmeasured_directories reads the per-path counts production feeds it.

The store groups a run per path, (path, functions, functions flagged anything
but untested), and leaves the coverage_optional scopes out in its WHERE. doctor
used to hold a row-reading copy of the rule that only tests called, while
cli/admin.py kept a second, count-reading copy for production. One rule now
takes the counts, so the tests and doctor read the same code.
"""
from crapkit.doctor import UnmeasuredDir, unmeasured_directories

TRACKED = ["src/measured.py", "src/quiet/mod.py", "src/quiet/other.py", "tests/test_mod.py"]


def test_the_rule_folds_path_counts_into_their_directories():
    counts = [("src/measured.py", 1, 1), ("src/quiet/mod.py", 2, 0), ("src/quiet/other.py", 1, 0)]

    assert unmeasured_directories(counts, TRACKED) == (
        UnmeasuredDir("src/quiet", 3, "tests/test_mod.py"),)


def test_one_function_with_another_verdict_clears_the_directory():
    counts = [("src/quiet/mod.py", 2, 1), ("src/quiet/other.py", 1, 0)]

    assert unmeasured_directories(counts, TRACKED) == ()
