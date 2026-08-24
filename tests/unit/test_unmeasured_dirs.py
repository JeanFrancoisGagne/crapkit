"""Directories whose functions are all untested while tests for them exist.

The measured failure: a unit lane's config excluded five core directories, so
six giant functions scored cov 0 for weeks with 450 passing tests sitting next
to them. Every score was honest and every one of them was about tooling.
"""
from crapkit.doctor import unmeasured_directories
from crapkit.score import ScoredRow


def _row(path: str, flag: str, scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, path, "f( )", 1, 9, 3, 3, 3, 5, 1, 1,
                     0.0 if flag != "measured" else 1.0, flag, 3.0, "ok", 2)


TRACKED = ["src/measured.py", "src/quiet/mod.py", "src/quiet/other.py", "tests/test_mod.py"]


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
    rows = [_row("src/quiet/mod.py", "untested")]
    for test_path in ("tests/test_mod.py", "tests/mod_test.py",
                      "src/quiet/mod.test.ts", "src/quiet/mod.spec.js"):
        assert unmeasured_directories(rows, ["src/quiet/mod.py", test_path])[0].example_test \
            == test_path


def test_a_scope_now_marked_coverage_optional_is_skipped_even_in_older_rows():
    """The store still holds the run that scored before coverage_optional was
    set; those rows say untested, and the check must not re-open the question."""
    rows = [_row("shims/mod.py", "untested", scope="shims")]
    tracked = ["shims/mod.py", "tests/test_mod.py"]
    assert unmeasured_directories(rows, tracked, skip_scopes=frozenset({"shims"})) == ()
    assert unmeasured_directories(rows, tracked)[0].directory == "shims"


def test_no_rows_is_not_a_finding():
    assert unmeasured_directories([], TRACKED) == ()


def test_directories_are_reported_in_path_order():
    rows = [_row("src/zeta/z.py", "untested"), _row("src/alpha/a.py", "untested")]
    tracked = ["src/zeta/z.py", "src/alpha/a.py", "tests/test_z.py", "tests/test_a.py"]
    assert [g.directory for g in unmeasured_directories(rows, tracked)] == \
        ["src/alpha", "src/zeta"]
