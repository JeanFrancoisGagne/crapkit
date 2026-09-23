"""Which test file doctor names beside "N function(s) all flagged untested".

Measured on a 20k-function TypeScript+Python repo: the WARN for a directory
holding handler.test.ts beside handler.ts named a handler.test.ts twenty
directories away (first of twenty alphabetically), a Python directory was
paired with a .ts test, and a docs directory with a Markdown file named
_mermaid_test.md. The example is meant to be the test the reader opens next,
so the nearest one wins, and a file no lizard reader parses is not a test.
"""
from path_counts import path_counts

from crapkit import doctor
from crapkit.score import ScoredRow


def _row(path: str, flag: str = "untested", scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, path, "f( )", 1, 9, 3, 3, 3, 5, 1, 1,
                     0.0 if flag != "measured" else 1.0, flag, 3.0, "ok", 2)


def unmeasured_directories(rows, tracked):
    """The rule over these rows, grouped per path the way the store groups them."""
    return doctor.unmeasured_directories(path_counts(rows), tracked)


def _example(rows, tracked) -> str | None:
    found = unmeasured_directories(rows, tracked)
    return found[0].example_test if found else None


# --- slice 1: a sibling beats an alphabetically earlier same-stem test -------

def test_a_test_beside_the_source_beats_an_earlier_same_stem_test_elsewhere():
    rows = [_row("src/hooks/bundled/boot-md/handler.ts")]
    tracked = ["extensions/admin-http-rpc/src/handler.test.ts",
               "extensions/admin-http-rpc/src/handler.ts",
               "src/hooks/bundled/boot-md/handler.test.ts",
               "src/hooks/bundled/boot-md/handler.ts"]
    assert _example(rows, tracked) == "src/hooks/bundled/boot-md/handler.test.ts"


def test_a_test_below_the_directory_beats_a_same_stem_test_elsewhere():
    rows = [_row("src/api/parser.py")]
    tracked = ["lib/test_parser.py", "lib/parser.py",
               "src/api/parser.py", "src/api/tests/test_other.py"]
    assert _example(rows, tracked) == "src/api/tests/test_other.py"


# --- slice 2: a same-stem match elsewhere has to be the same language ---------

def test_a_python_directory_is_never_paired_with_a_typescript_test():
    rows = [_row("scripts/nlp_pipeline/config.py")]
    tracked = ["extensions/acpx/src/config.test.ts", "extensions/acpx/src/config.ts",
               "scripts/nlp_pipeline/config.py"]
    assert unmeasured_directories(rows, tracked) == ()


def test_a_python_directory_pairs_with_a_python_test_of_the_same_stem():
    rows = [_row("scripts/nlp_pipeline/config.py")]
    tracked = ["extensions/acpx/src/config.test.ts", "extensions/acpx/src/config.ts",
               "scripts/nlp_pipeline/config.py", "tests/test_config.py"]
    assert _example(rows, tracked) == "tests/test_config.py"


def test_the_javascript_family_is_one_language():
    rows = [_row("src/kg/store.js")]
    tracked = ["src/kg/store.js", "test/store.test.ts"]
    assert _example(rows, tracked) == "test/store.test.ts"


def test_a_markdown_file_named_like_a_test_is_not_a_test():
    rows = [_row("scripts/docs/mermaid.py")]
    tracked = ["docs/_mermaid_test.md", "scripts/docs/mermaid.py"]
    assert unmeasured_directories(rows, tracked) == ()


def test_a_markdown_file_named_like_a_test_never_wins_over_a_real_one():
    rows = [_row("scripts/docs/mermaid.py")]
    tracked = ["docs/_mermaid_test.md", "scripts/docs/mermaid.py", "tests/test_mermaid.py"]
    assert _example(rows, tracked) == "tests/test_mermaid.py"


# --- slice 3: nothing qualifies, so the directory is not reported at all -----

def test_a_directory_with_no_sibling_no_mirror_and_no_same_language_stem_is_silent():
    """The caller reads None as "no test exists for this code", which is a
    testing gap and not a tooling one: no finding, the way an untested
    directory with no test anywhere has always been left alone."""
    rows = [_row("scripts/nlp_pipeline/config.py"), _row("scripts/nlp_pipeline/run.py")]
    tracked = ["extensions/acpx/src/config.test.ts", "extensions/acpx/src/config.ts",
               "scripts/nlp_pipeline/config.py", "scripts/nlp_pipeline/run.py",
               "tests/test_something_else.py"]
    assert unmeasured_directories(rows, tracked) == ()


# --- slice 4: with no sibling, the tests/ mirror still beats a far stem -------

def test_a_tests_mirror_beats_a_far_same_stem_test_when_there_is_no_sibling():
    rows = [_row("src/api/parser.py")]
    tracked = ["lib/test_parser.py", "lib/parser.py", "src/api/parser.py",
               "tests/api/test_routes.py"]
    assert _example(rows, tracked) == "tests/api/test_routes.py"


def test_a_far_same_language_stem_match_is_still_found_when_nothing_nearer_exists():
    rows = [_row("src/api/parser.py")]
    tracked = ["lib/test_parser.py", "lib/parser.py", "src/api/parser.py"]
    assert _example(rows, tracked) == "lib/test_parser.py"


# --- review follow-ups: the sibling is the nearest, a mirror needs a tests/ tree

def test_a_sibling_beats_a_test_nested_deeper_that_sorts_first():
    """extensions/browser named chrome-extension/background.test.ts, which
    sorts before its own index.test.ts and belongs to another directory."""
    rows = [_row("extensions/browser/index.ts")]
    tracked = ["extensions/browser/a/x.test.ts", "extensions/browser/index.ts",
               "extensions/browser/y.test.ts"]
    assert _example(rows, tracked) == "extensions/browser/y.test.ts"


def test_a_test_directory_with_no_test_named_component_is_not_a_mirror():
    """extensions/diffs-language-pack/src was paired with a root src/ test, as
    if src/ were a tests/ tree for every directory that ends in src."""
    rows = [_row("extensions/diffs-language-pack/src/plugin.ts")]
    tracked = ["extensions/diffs-language-pack/src/plugin.ts",
               "extensions/diffs/src/plugin.test.ts",
               "src/browser-lifecycle-cleanup.test.ts"]
    assert _example(rows, tracked) == "extensions/diffs/src/plugin.test.ts"


# --- every tier is held to the language of the directory's SCORED files ------

def test_a_python_directory_whose_only_test_below_it_is_javascript_is_silent():
    """openclaw's scripts/sam_admin (Python) named
    scripts/sam_admin/static/__tests__/api.test.js."""
    rows = [_row("scripts/sam_admin/app.py")]
    tracked = ["scripts/sam_admin/app.py", "scripts/sam_admin/static/api.js",
               "scripts/sam_admin/static/__tests__/api.test.js"]
    assert unmeasured_directories(rows, tracked) == ()


def test_a_python_directory_skips_a_javascript_test_below_it_for_a_python_one():
    rows = [_row("scripts/sam_admin/app.py")]
    tracked = ["scripts/sam_admin/app.py",
               "scripts/sam_admin/static/__tests__/api.test.js",
               "scripts/tests/test_app.py"]
    assert _example(rows, tracked) == "scripts/tests/test_app.py"


def test_a_javascript_test_beside_scored_python_does_not_count():
    rows = [_row("scripts/tool/run.py")]
    tracked = ["scripts/tool/run.py", "scripts/tool/widget.js",
               "scripts/tool/widget.test.js"]
    assert unmeasured_directories(rows, tracked) == ()


def test_unscored_javascript_beside_scored_python_does_not_admit_a_javascript_stem_match():
    """The family comes from the paths the store scored, not every tracked
    file: run.js sits in the directory but no row carries it."""
    rows = [_row("scripts/tool/run.py")]
    tracked = ["lib/run.test.js", "scripts/tool/run.js", "scripts/tool/run.py"]
    assert unmeasured_directories(rows, tracked) == ()


def test_a_javascript_tests_mirror_of_a_python_directory_does_not_count():
    rows = [_row("src/api/parser.py")]
    tracked = ["src/api/parser.py", "tests/api/routes.test.ts"]
    assert unmeasured_directories(rows, tracked) == ()


def test_the_nearest_test_below_beats_a_deeper_one_that_sorts_first():
    rows = [_row("src/api/parser.py")]
    tracked = ["src/api/a/b/tests/test_aaa.py", "src/api/parser.py",
               "src/api/tests/test_zzz.py"]
    assert _example(rows, tracked) == "src/api/tests/test_zzz.py"


def test_tests_at_the_same_depth_below_are_taken_alphabetically():
    rows = [_row("src/api/parser.py")]
    tracked = ["src/api/parser.py", "src/api/zz/test_a.py", "src/api/aa/test_z.py"]
    assert _example(rows, tracked) == "src/api/aa/test_z.py"


def test_a_directory_with_no_test_named_component_is_no_mirror_even_alone():
    """A root src/zzz.test.ts would mirror every directory ending in src if a
    test directory needed no tests/-like component."""
    rows = [_row("extensions/pack/src/plugin.ts")]
    tracked = ["extensions/pack/src/plugin.ts", "src/zzz.test.ts"]
    assert unmeasured_directories(rows, tracked) == ()
