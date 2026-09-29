"""init's scaffold helpers, each on the input its docstring names.

scaffold.py: a new block goes under its own `# crapkit` heading after what a
file held; a lane's artifact inside a directory ignores the directory; a
testpath becomes a lane name a filename can hold; a dot-directory is never a
scope; a scoped-test entry says which form it chose and why, live only where
the repo's files prove the command; and a package.json field that is not the
shape npm reads names nothing. init parses each package.json once (cli.admin)
into those fields, and refuses a root one that is not an object.
"""
import json

import pytest

from crapkit import scaffold
from crapkit.scaffold import NO_PACKAGE, ScopedEntry, _ScopedFacts, npm_package


def packages(raw: dict[str, str]) -> dict:
    """package.json text by directory, read into the fields init reads."""
    return {directory: npm_package(json.loads(text)) for directory, text in raw.items()}


@pytest.mark.parametrize("current, written", [
    ("", "# crapkit\n.crapkit/\n"),
    ("dist/\n", "dist/\n\n# crapkit\n.crapkit/\n"),
    ("dist/", "dist/\n\n# crapkit\n.crapkit/\n"),
])
def test_new_entries_go_under_their_own_heading_after_what_was_there(current, written):
    assert scaffold._appended(current, [".crapkit/"]) == written


def test_an_artifact_inside_a_directory_ignores_the_directory():
    assert (scaffold._artifact_ignore("coverage/coverage-final.json"),
            scaffold._artifact_ignore("coverage.xml")) == ("coverage/", "coverage.xml")


@pytest.mark.parametrize("has_live, header", [
    (False, ["# Uncomment what fits:", "# [crapkit.scoped_tests]"]),
    (True, ["# Uncomment what fits:"]),
])
def test_commented_entries_name_their_table_only_when_no_live_one_did(has_live, header):
    assert scaffold._commented_block(['# web = "x"'], has_live) == header + ['# web = "x"']


@pytest.mark.parametrize("test_dir, testpaths", [
    ("tests", ("./tests/",)), ("tests", ("tests\\unit",)), ("specX", ("specX",))])
def test_testpaths_collect_the_test_directory_however_they_spell_it(test_dir, testpaths):
    assert scaffold._covered_by_testpaths(test_dir, testpaths)


def test_a_testpath_becomes_a_lane_name_a_filename_can_hold():
    assert scaffold._testpath_slug("./Xtests\\unit/a_b c\\") == "Xtests-unit-a_b-c"


@pytest.mark.parametrize("data", [{"scripts": [1], "devDependencies": ""}, {"scripts": None}, {"a": 1}])
def test_a_package_json_field_that_is_no_object_reads_empty(data):
    assert npm_package(data) == NO_PACKAGE


def test_a_workspace_with_no_dev_dependencies_names_no_runner():
    assert scaffold._runner_workspaces(packages({"": "{}", "web": "{}",
                                                 "app": '{"devDependencies": {"vitest": "1"}}'})) == [
        ("app", "vitest")]


def test_a_dot_directory_is_no_scope():
    assert (scaffold._scoped_source(".github/x.py"), scaffold._scoped_source("src/a.py")) \
        == (None, "src")


def test_a_test_file_s_top_directory_is_its_first_path_part():
    assert scaffold._test_top("tests/unit/test_a.py") == "tests"


def facts(raw: dict, tested=frozenset(), confirmed=frozenset()) -> _ScopedFacts:
    return _ScopedFacts("python", frozenset(confirmed), frozenset(tested), "", False, packages(raw))


@pytest.mark.parametrize("packages, entry", [
    ({"": "{}", "web": '{"scripts": {"test": "vitest"}}'},
     ScopedEntry("npm run test -w web",
                 "web/ is an npm workspace with its own test script, run from the root with -w", True)),
    ({"": '{"devDependencies": {"vitest": "1"}}'},
     ScopedEntry("npx vitest related --run {files}",
                 "vitest's related-tests mode, keyed by the runner package.json names", False)),
    ({"": "{}"},
     ScopedEntry("<your test command> {files}",
                 "package.json names no single runner; replace the placeholder", False)),
])
def test_a_js_scope_s_entry_says_which_form_it_chose_and_why(packages, entry):
    assert scaffold._scoped_entry("web", ("typescript",), facts(packages)) == entry


def test_a_scope_with_its_own_tests_narrows_to_its_files():
    assert scaffold._scoped_entry("app", ("python",), facts({}, tested={"app"})) == ScopedEntry(
        "python -m pytest {files} -q -p no:cacheprovider",
        "app/ holds its own tests, so {files} narrows the run to the files named", False)


def test_a_scope_no_runner_knows_names_every_language_it_holds():
    assert scaffold._scoped_entry("lib", ("go", "rust"), facts({})) == ScopedEntry(
        "<your test command> {files}", "no runner known for go, rust; replace the placeholder",
        False)


def test_live_entries_open_the_table_and_commented_ones_follow_without_repeating_it():
    stub = scaffold._scoped_tests_stub({"app": ("python",), "web": ("typescript",)},
                                       facts({"": "{}"}, tested={"app"}, confirmed={"python"}))

    assert stub[3:] == [
        "[crapkit.scoped_tests]",
        "# app: app/ holds its own tests, so {files} narrows the run to the files named",
        'app = "python -m pytest {files} -q -p no:cacheprovider"',
        "# Uncomment what fits:",
        "# web: package.json names no single runner; replace the placeholder",
        '# web = "<your test command> {files}"',
        ""]
