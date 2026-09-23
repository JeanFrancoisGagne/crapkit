"""Directories whose functions are all untested while tests for them exist.

The measured failure: a unit lane's config excluded five core directories, so
six giant functions scored cov 0 for weeks with 450 passing tests sitting next
to them. Every score was honest and every one of them was about tooling.
"""
from path_counts import path_counts

from crapkit import doctor
from crapkit.score import ScoredRow


def _row(path: str, flag: str, scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, path, "f( )", 1, 9, 3, 3, 3, 5, 1, 1,
                     0.0 if flag != "measured" else 1.0, flag, 3.0, "ok", 2)


TRACKED = ["src/measured.py", "src/quiet/mod.py", "src/quiet/other.py", "tests/test_mod.py"]


def unmeasured_directories(rows, tracked):
    """The rule over these rows, grouped per path the way the store groups them."""
    return doctor.unmeasured_directories(path_counts(rows), tracked)


def test_a_directory_of_untested_code_with_a_same_stem_test_is_reported():
    rows = [_row("src/measured.py", "measured"),
            _row("src/quiet/mod.py", "untested"),
            _row("src/quiet/mod.py", "untested"),
            _row("src/quiet/other.py", "untested")]

    (gap,) = unmeasured_directories(rows, TRACKED)

    assert gap.directory == "src/quiet"
    assert gap.functions == 3
    assert gap.example_test == "tests/test_mod.py"


def test_one_measured_function_clears_the_whole_directory():
    rows = [_row("src/quiet/mod.py", "measured"), _row("src/quiet/other.py", "untested")]
    assert unmeasured_directories(rows, TRACKED) == ()


def test_untested_code_nobody_wrote_a_test_for_is_not_a_tooling_gap():
    rows = [_row("src/quiet/mod.py", "untested")]
    assert unmeasured_directories(rows, ["src/quiet/mod.py"]) == ()


def test_a_tests_mirror_counts_even_when_no_stem_matches():
    rows = [_row("src/kg/alpha.ts", "untested")]
    tracked = ["src/kg/alpha.ts", "tests/kg/beta.test.ts"]
    assert unmeasured_directories(rows, tracked)[0].example_test == "tests/kg/beta.test.ts"


def test_a_flat_tests_directory_does_not_mirror_every_directory():
    rows = [_row("src/kg/alpha.ts", "untested")]
    assert unmeasured_directories(rows, ["src/kg/alpha.ts", "tests/beta.test.ts"]) == ()


def test_the_four_test_naming_conventions_all_count():
    """Each test is paired with source in its own language: a .test.ts beside
    a Python module is not that module's test."""
    for source, test_path in (("src/quiet/mod.py", "tests/test_mod.py"),
                              ("src/quiet/mod.py", "tests/mod_test.py"),
                              ("src/quiet/mod.ts", "src/quiet/mod.test.ts"),
                              ("src/quiet/mod.js", "src/quiet/mod.spec.js")):
        rows = [_row(source, "untested")]
        assert unmeasured_directories(rows, [source, test_path])[0].example_test \
            == test_path


def test_no_rows_is_not_a_finding():
    assert unmeasured_directories([], TRACKED) == ()


def test_directories_are_reported_in_path_order():
    rows = [_row("src/zeta/z.py", "untested"), _row("src/alpha/a.py", "untested")]
    tracked = ["src/zeta/z.py", "src/alpha/a.py", "tests/test_z.py", "tests/test_a.py"]
    assert [g.directory for g in unmeasured_directories(rows, tracked)] == \
        ["src/alpha", "src/zeta"]
