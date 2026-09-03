"""Which scoped-test form `crapkit init` writes per scope, decided from the
tracked files and the package.json map alone. Pure: nothing runs.

A python scope whose paths hold no test file gets the whole-suite form, with
the repo's test directory named unless pytest's testpaths already covers it;
`{files}` stays only where the scope holds its own tests. A js scope is keyed
by the runner package.json names, never by the language, and an npm workspace
runs its own script. One comment line above each entry names the form chosen.
"""
import json

from crapkit.config import load_config_text
from crapkit.scaffold import LaneSpec, detect_lanes, runner_workspaces, starter_toml
from crapkit.universe import is_test_file, scopes_with_tests

PY = {"pkg": ("python",)}
PY_LANES = detect_lanes(frozenset({"pyproject.toml"}), "")
LAYOUT = ["pkg/x.py", "pkg/y.py", "tests/test_x.py", "pyproject.toml"]
SELF_TESTED = ["pkg/x.py", "pkg/test_x.py", "pyproject.toml"]


def _entry(text: str, scope: str) -> str:
    (line,) = [ln for ln in text.splitlines() if ln.lstrip("# ").startswith(f"{scope} = ")]
    return line


def _comment_above(text: str, scope: str) -> str:
    lines = text.splitlines()
    return lines[lines.index(_entry(text, scope)) - 1]


def _package(**payload) -> str:
    return json.dumps(payload)


# --- the predicate both init and doctor read -----------------------------------

def test_a_test_file_is_one_under_a_test_directory_or_named_like_one():
    assert is_test_file("tests/helpers.py")
    assert is_test_file("pkg/__tests__/x.ts")
    assert is_test_file("pkg/test_x.py")
    assert is_test_file("pkg/x_test.py")
    assert is_test_file("pkg/x_test.go")
    assert is_test_file("web/app.test.ts")
    assert is_test_file("web/app.spec.tsx")
    assert not is_test_file("pkg/x.py")
    assert not is_test_file("pkg/conftest.py"), "a conftest configures tests; it is not one"
    assert not is_test_file("pkg/testing_tools.py")


def test_scopes_with_tests_names_the_scopes_whose_paths_hold_one():
    tested = scopes_with_tests(["pkg/x.py", "pkg/test_x.py", "web/src/a.ts", "tests/test_a.py"],
                               {"pkg": ("pkg",), "web": ("web",)})

    assert tested == frozenset({"pkg"})


def test_scopes_with_tests_reads_backslash_paths_the_way_git_prints_them_on_windows():
    assert scopes_with_tests(["pkg\\test_x.py"], {"pkg": ("pkg",)}) == frozenset({"pkg"})


# --- the python form -----------------------------------------------------------

def test_a_scope_holding_no_test_file_gets_the_whole_suite_form_naming_the_test_dir():
    text = starter_toml(PY, PY_LANES, tracked=LAYOUT)

    assert _entry(text, "pkg") == 'pkg = "python -m pytest tests -q -p no:cacheprovider"'
    assert _comment_above(text, "pkg").startswith("# pkg:")
    assert "whole suite" in _comment_above(text, "pkg")


def test_the_positional_is_omitted_when_testpaths_already_covers_the_test_dir():
    text = starter_toml(PY, PY_LANES, tracked=LAYOUT, testpaths=("tests",))

    assert _entry(text, "pkg") == 'pkg = "python -m pytest -q -p no:cacheprovider"'
    assert "testpaths" in _comment_above(text, "pkg")


def test_a_testpath_under_the_test_dir_also_covers_it():
    text = starter_toml(PY, PY_LANES, tracked=LAYOUT, testpaths=("tests/unit", "tests/e2e"))

    assert _entry(text, "pkg") == 'pkg = "python -m pytest -q -p no:cacheprovider"'


def test_a_testpath_naming_somewhere_else_does_not_cover_it():
    text = starter_toml(PY, PY_LANES, tracked=LAYOUT, testpaths=("spec",))

    assert _entry(text, "pkg") == 'pkg = "python -m pytest tests -q -p no:cacheprovider"'


def test_with_no_tracked_files_known_the_whole_suite_form_names_no_directory():
    text = starter_toml(PY, PY_LANES)

    assert _entry(text, "pkg") == 'pkg = "python -m pytest -q -p no:cacheprovider"'


def test_two_test_directories_outside_the_scopes_name_neither():
    tracked = LAYOUT + ["integration/test_flow.py"]

    text = starter_toml(PY, PY_LANES, tracked=tracked)

    assert _entry(text, "pkg") == 'pkg = "python -m pytest -q -p no:cacheprovider"'


def test_a_scope_holding_its_own_tests_keeps_files():
    text = starter_toml(PY, PY_LANES, tracked=SELF_TESTED)

    assert _entry(text, "pkg") == 'pkg = "python -m pytest {files} -q -p no:cacheprovider"'
    assert "{files}" in _comment_above(text, "pkg")


def test_the_whole_suite_form_carries_the_lanes_launcher():
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "", interpreter="uv run python")

    text = starter_toml(PY, lanes, tracked=LAYOUT)

    assert _entry(text, "pkg") == 'pkg = "uv run python -m pytest tests -q -p no:cacheprovider"'


def test_an_unconfirmed_python_entry_stays_commented_in_its_new_form():
    text = starter_toml(PY, (), tracked=LAYOUT)

    assert _entry(text, "pkg") == '# pkg = "python -m pytest tests -q -p no:cacheprovider"'
    assert load_config_text(text).scoped_tests == ()


def test_every_form_loads_back_once_uncommented():
    text = starter_toml({"pkg": ("python",), "src": ("typescript",), "cmd": ("go",)},
                        (), tracked=LAYOUT)
    block = text.split("# `crapkit test-scoped FILES`", 1)[1]
    live = "\n".join(ln.removeprefix("# ") for ln in block.splitlines()
                     if ln.startswith("# ") and " = " in ln)

    cfg = load_config_text(text + "\n[crapkit.scoped_tests]\n" + live)

    assert set(dict(cfg.scoped_tests)) == {"pkg", "src", "cmd"}


# --- the js form is keyed by the runner ----------------------------------------

def test_a_jest_root_gets_jests_related_tests_mode():
    package = _package(scripts={"test": "jest"}, devDependencies={"jest": "^29.0.0"})

    text = starter_toml({"src": ("typescript",)}, detect_lanes(frozenset(), package),
                        package_json=package)

    assert _entry(text, "src") == '# src = "npx jest --findRelatedTests {files}"'
    assert "jest" in _comment_above(text, "src")


def test_a_vitest_root_gets_vitests_related_tests_mode():
    package = _package(devDependencies={"vitest": "^2.0.0"})

    text = starter_toml({"src": ("typescript",)}, detect_lanes(frozenset(), package),
                        package_json=package)

    assert _entry(text, "src") == '# src = "npx vitest related --run {files}"'


def test_a_root_naming_no_single_runner_gets_the_placeholder():
    both = _package(devDependencies={"vitest": "^2.0.0", "jest": "^29.0.0"})

    assert _entry(starter_toml({"src": ("typescript",)}), "src") == \
        '# src = "<your test command> {files}"'
    assert _entry(starter_toml({"src": ("javascript",), }, package_json=both), "src") == \
        '# src = "<your test command> {files}"'


def test_a_workspace_with_a_test_script_runs_it_from_the_root_live():
    packages = {"": _package(scripts={"test": "npm run --workspaces test"}),
                "web": _package(scripts={"test": "vitest run"},
                                devDependencies={"vitest": "^2.0.0"})}

    text = starter_toml({"web": ("typescript",)}, detect_lanes(frozenset(), packages),
                        package_json=packages)

    assert _entry(text, "web") == 'web = "npm run test -w web"'
    assert "workspace" in _comment_above(text, "web")
    assert dict(load_config_text(text).scoped_tests) == {"web": "npm run test -w web"}


def test_a_workspace_names_the_script_npm_would_run():
    packages = {"web": _package(scripts={"test:unit": "vitest run"},
                                devDependencies={"vitest": "^2.0.0"})}

    text = starter_toml({"web": ("typescript",)}, package_json=packages)

    assert _entry(text, "web") == 'web = "npm run test:unit -w web"'


def test_a_scope_that_is_not_a_workspace_falls_back_to_the_root_runner():
    packages = {"": _package(devDependencies={"jest": "^29.0.0"}),
                "web": _package(scripts={"test": "vitest run"},
                                devDependencies={"vitest": "^2.0.0"})}

    text = starter_toml({"shared": ("typescript",)}, package_json=packages)

    assert _entry(text, "shared") == '# shared = "npx jest --findRelatedTests {files}"'


def test_a_language_with_no_runner_keeps_the_placeholder_and_says_why():
    text = starter_toml({"cmd": ("go",)})

    assert _entry(text, "cmd") == '# cmd = "<your test command> {files}"'
    assert "go" in _comment_above(text, "cmd")


# --- what init's summary needs to know -----------------------------------------

def test_runner_workspaces_lists_every_workspace_naming_one_runner():
    packages = {"": _package(scripts={"test": "x"}),
                "api": _package(devDependencies={"jest": "^29.0.0"}),
                "web": _package(devDependencies={"vitest": "^2.0.0"}),
                "docs": _package(devDependencies={"typescript": "^5.0.0"})}

    assert runner_workspaces(packages) == [("api", "jest"), ("web", "vitest")]
    assert runner_workspaces(_package(devDependencies={"jest": "^29.0.0"})) == [], \
        "a bare string is the root, which is no workspace"


def test_the_detected_lane_still_decides_which_python_entries_are_live():
    lanes = (LaneSpec("py", "uv run python -m pytest --cov", ".crapkit/cov/py.json",
                      "coveragepy", ("python",)),)

    cfg = load_config_text(starter_toml(PY, lanes, tracked=LAYOUT))

    assert dict(cfg.scoped_tests) == {"pkg": "uv run python -m pytest tests -q -p no:cacheprovider"}
