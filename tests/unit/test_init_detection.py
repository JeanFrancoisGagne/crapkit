"""What `crapkit init` can know about a repo's test runners from files alone.

A starter config whose lanes are all commented out scores every function
no-lane, so the burn-down has nothing to rank. Detection is file presence and
package.json content only — nothing is executed, nothing is imported.
"""
import json

from crapkit.config import load_config_text
from crapkit.scaffold import (DEFAULT_EXCLUDES, LaneSpec, detect_lanes, gitignore_entries,
                              gitignore_update, live_lanes, lockfile_runner, pytest_testpaths,
                              python_launcher, sniff_scopes, source_candidates, starter_toml)

SCOPES = {"pylib": ("python",), "src": ("typescript",)}


def _package(**payload) -> str:
    return json.dumps(payload)


def test_a_pyproject_yields_a_live_pytest_lane():
    (lane,) = detect_lanes(frozenset({"pyproject.toml"}), "")
    assert lane.parser == "coveragepy"
    assert lane.command == ("python -m pytest --cov --cov-branch "
                            "--cov-report=json:.crapkit/cov/py.json "
                            "--junitxml=.crapkit/cov/junit-py.xml")
    assert lane.artifact == ".crapkit/cov/py.json"
    # The junit file feeds the crashed-worker and no-new-failures checks (#26).
    assert lane.results_artifact == ".crapkit/cov/junit-py.xml"


def test_pytest_ini_and_setup_cfg_count_as_the_same_signal():
    assert detect_lanes(frozenset({"pytest.ini"}), "")[0].parser == "coveragepy"
    assert detect_lanes(frozenset({"setup.cfg"}), "")[0].parser == "coveragepy"
    assert detect_lanes(frozenset({"Makefile"}), "") == ()


def test_the_interpreter_the_config_will_call_is_the_one_that_resolves():
    (lane,) = detect_lanes(frozenset({"pyproject.toml"}), "", interpreter="python3")
    assert lane.command.startswith("python3 -m pytest ")


def test_a_test_script_names_the_command_npm_would_run():
    (lane,) = detect_lanes(frozenset(), _package(scripts={"build": "tsc", "test": "vitest run"}))
    assert lane.command == "npm run test -- --coverage"
    assert lane.parser == "istanbul"
    assert lane.artifact == "coverage/coverage-final.json"


def test_the_first_test_prefixed_script_stands_in_for_a_missing_test_script():
    (lane,) = detect_lanes(frozenset(), _package(scripts={"test:unit": "vitest run",
                                                          "test:e2e": "playwright"}))
    assert lane.command == "npm run test:e2e -- --coverage", "sorted, so the choice is stable"


def test_a_dev_dependency_alone_is_enough_to_know_the_runner():
    (vitest,) = detect_lanes(frozenset(), _package(devDependencies={"vitest": "^2.0.0"}))
    (jest,) = detect_lanes(frozenset(), _package(devDependencies={"jest": "^29.0.0"}))
    assert vitest.command.startswith("npx vitest run --coverage")
    assert jest.command.startswith("npx jest --coverage")


def test_a_package_json_with_neither_signal_detects_nothing():
    assert detect_lanes(frozenset(), _package(dependencies={"react": "^19.0.0"})) == ()


def test_a_package_json_that_does_not_parse_detects_nothing():
    assert detect_lanes(frozenset(), "{ not json") == ()


def test_both_runners_detected_gives_two_lanes_in_a_fixed_order():
    lanes = detect_lanes(frozenset({"pyproject.toml"}), _package(scripts={"test": "vitest run"}))
    assert [lane.parser for lane in lanes] == ["coveragepy", "istanbul"]


def test_a_detected_lane_claims_the_scopes_that_speak_its_languages():
    lanes = detect_lanes(frozenset({"pyproject.toml"}), _package(scripts={"test": "vitest run"}))
    cfg = load_config_text(starter_toml(SCOPES, lanes))
    assert {lane.name: lane.scopes for lane in cfg.lanes} == {"py": ("pylib",), "js": ("src",)}


def test_a_detected_runner_with_no_scope_to_measure_stays_a_template():
    """pyproject.toml in a repo whose only source is TypeScript: a lane with an
    empty scopes list measures nothing and would hide the real gap."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "")
    text = starter_toml({"src": ("typescript",)}, lanes)
    assert load_config_text(text).lanes == ()
    assert '# parser = "coveragepy"' in text


def test_the_undetected_half_keeps_its_commented_template():
    text = starter_toml(SCOPES, detect_lanes(frozenset({"pyproject.toml"}), ""))
    assert '# parser = "istanbul"' in text
    assert '# parser = "coveragepy"' not in text
    # a placeholder, not a real scope: writing one pointed a TS lane template
    # at a python project's sources
    assert '# scopes = ["<your-scope>"]' in text


def test_with_no_runner_detected_both_templates_stay_commented():
    text = starter_toml(SCOPES)
    assert load_config_text(text).lanes == ()
    assert text.count("# [[lane]]") == 2


# --- what init tells you when there is nothing tracked to scope ---------------

def test_the_files_init_would_scope_are_counted_out_of_a_raw_path_list():
    files = ["app/m.py", "README.md", "app/m.test.py", "loose.py", "app\\win.py"]

    assert source_candidates(files) == ["app/m.py", "app/win.py"]


def test_a_tree_of_nothing_but_tests_and_docs_has_no_candidates():
    assert source_candidates(["docs/guide.md", "tests/test_m.py", "app/conftest.py"]) == []


# --- the artifacts init's own lanes will drop in the consumer's tree ----------

def test_the_ignore_list_covers_crapkits_store_and_each_written_lanes_artifact():
    lanes = detect_lanes(frozenset({"pyproject.toml"}), _package(scripts={"test": "vitest run"}))

    text, added = gitignore_update("", live_lanes(lanes, SCOPES))

    assert added == [".crapkit/", ".coverage", "__pycache__/", "coverage/"]
    assert text.endswith(".crapkit/\n.coverage\n__pycache__/\ncoverage/\n")


def test_an_artifact_inside_a_directory_ignores_the_directory():
    """istanbul writes a whole coverage/ tree beside coverage-final.json; ignoring
    the one file leaves the rest of it in `git status`."""
    lanes = detect_lanes(frozenset(), _package(scripts={"test": "vitest run"}))

    _, added = gitignore_update("", live_lanes(lanes, SCOPES))

    assert added == [".crapkit/", "coverage/"]


def test_entries_the_gitignore_already_carries_are_never_repeated():
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "")

    text, added = gitignore_update("node_modules/\n.crapkit/\n", live_lanes(lanes, SCOPES))

    assert added == [".coverage", "__pycache__/"]
    assert text.count(".crapkit/") == 1


def test_a_gitignore_that_already_covers_everything_is_left_byte_identical():
    current = "node_modules/\n.crapkit/\n.coverage\n__pycache__/\n"
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "")

    assert gitignore_update(current, live_lanes(lanes, SCOPES)) == (current, [])


def test_a_file_with_no_trailing_newline_is_not_merged_into_its_last_entry():
    text, _ = gitignore_update("node_modules/", ())

    assert text.splitlines()[0] == "node_modules/"
    assert ".crapkit/" in text.splitlines()


def test_a_lane_with_no_scope_to_measure_contributes_no_ignore_entry():
    """Same rule as the config: a lane init did not write cannot dirty the tree."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "")

    assert live_lanes(lanes, {"src": ("typescript",)}) == ()
    assert gitignore_update("", live_lanes(lanes, {"src": ("typescript",)}))[1] == [".crapkit/"]


# --- where the lanes init writes put their artifacts -------------------------
#
# A 14-lane consumer grew fifteen coverage-* directories and seven junit files
# at its root, one per lane, because every scaffolded lane wrote where its
# runner defaults to. .crapkit/ is ignored the moment init runs, so an artifact
# under it costs the consumer's tree nothing.

def test_the_pytest_lane_reports_under_the_crapkit_directory():
    (lane,) = detect_lanes(frozenset({"pyproject.toml"}), "")

    assert lane.artifact == ".crapkit/cov/py.json"
    assert "--cov-report=json:.crapkit/cov/py.json" in lane.command


def test_vitest_is_routed_with_the_flag_vitest_spells_it_with():
    """Both of vitest's reports, because vitest ships the junit reporter: a lane
    with no results_artifact runs with the crashed-worker and no-new-failures
    checks off, and doctor WARNed about the config init had just written."""
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"vitest": "^2.0.0"}))

    assert lane.command == ("npx vitest run --coverage "
                            "--coverage.reportsDirectory=.crapkit/cov/js "
                            "--coverage.reportOnFailure "
                            "--reporter=default --reporter=junit "
                            "--outputFile=.crapkit/cov/js/junit.xml")
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json"
    assert lane.results_artifact == ".crapkit/cov/js/junit.xml"


def test_jest_is_routed_with_the_flag_jest_spells_it_with():
    """jest's junit reporter is a package of its own. Absent, the coverage flag
    is all init may write: `--reporters=jest-junit` jest cannot resolve fails
    the run outright, which is worse than the WARN it would silence."""
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"jest": "^29.0.0"}))

    assert lane.command == "npx jest --coverage --coverageDirectory=.crapkit/cov/js"
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json"
    assert lane.results_artifact == ""
    assert lane.env == ()


def test_jest_junit_installed_earns_the_reporter_and_the_env_that_places_it():
    """jest-junit reads no path off the command line, so the two variables are
    what keep its report out of the repo root."""
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"jest": "^29.0.0",
                                                                 "jest-junit": "^16.0.0"}))

    assert lane.command == ("npx jest --coverage --coverageDirectory=.crapkit/cov/js "
                            "--reporters=default --reporters=jest-junit")
    assert lane.results_artifact == ".crapkit/cov/js/junit.xml"
    assert lane.env == (("JEST_JUNIT_OUTPUT_DIR", ".crapkit/cov/js"),
                        ("JEST_JUNIT_OUTPUT_NAME", "junit.xml"))


def test_a_test_script_is_routed_when_devdependencies_name_one_runner():
    (lane,) = detect_lanes(frozenset(), _package(scripts={"test": "vitest run"},
                                                 devDependencies={"vitest": "^2.0.0"}))

    assert lane.command == ("npm run test -- --coverage "
                            "--coverage.reportsDirectory=.crapkit/cov/js "
                            "--coverage.reportOnFailure "
                            "--reporter=default --reporter=junit "
                            "--outputFile=.crapkit/cov/js/junit.xml")
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json"


def test_a_package_json_naming_no_runner_keeps_the_default_directory():
    """`npm test` can run anything. The two flags are not interchangeable, so an
    unnamed runner keeps the coverage directory its own default puts it in
    rather than getting a flag that would turn a working lane into exit 1."""
    (lane,) = detect_lanes(frozenset(), _package(scripts={"test": "vitest run"}))

    assert lane.command == "npm run test -- --coverage"
    assert lane.artifact == "coverage/coverage-final.json"


def test_a_package_json_naming_both_runners_keeps_the_default_directory():
    both = _package(scripts={"test": "node run.js"},
                    devDependencies={"jest": "^29.0.0", "vitest": "^2.0.0"})

    (lane,) = detect_lanes(frozenset(), both)

    assert lane.command == "npm run test -- --coverage"
    assert lane.artifact == "coverage/coverage-final.json"


def test_both_runners_installed_still_detect_the_lane_they_always_did():
    """The fallback is about the coverage directory only. Dropping the lane
    would leave the repo scoring every function no-lane instead."""
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"jest": "^29.0.0",
                                                                 "vitest": "^2.0.0"}))

    assert lane.command == "npx jest --coverage"


def test_the_routed_config_init_writes_parses_back():
    """The istanbul command validator rejects a file filter beside --coverage.
    A dotted coverage flag is not one, and this is where that stays true."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}),
                         _package(devDependencies={"vitest": "^2.0.0"}))

    cfg = load_config_text(starter_toml(SCOPES, lanes))

    assert {lane.name: lane.artifact for lane in cfg.lanes} == {
        "py": ".crapkit/cov/py.json", "js": ".crapkit/cov/js/coverage-final.json"}
    assert {lane.name: lane.results_artifact for lane in cfg.lanes} == {
        "py": ".crapkit/cov/junit-py.xml",
        "js": ".crapkit/cov/js/junit.xml"}, "both junit paths survive the round trip"


def test_the_commented_templates_point_under_the_crapkit_directory_too():
    """A reader who uncomments a template must not reintroduce the litter."""
    text = starter_toml(SCOPES)

    assert '# artifact = ".crapkit/cov/py.json"' in text
    assert '# artifact = ".crapkit/cov/js/coverage-final.json"' in text
    assert "coverage/coverage-final.json" not in text


def test_routed_lanes_add_nothing_to_gitignore_beyond_the_store():
    """.crapkit/ already covers every artifact under it. What survives is what
    the pytest runner drops elsewhere in the tree."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}),
                         _package(devDependencies={"vitest": "^2.0.0"}))

    assert gitignore_entries(live_lanes(lanes, SCOPES)) == [".crapkit/", ".coverage",
                                                            "__pycache__/"]


# --- the scoped_tests stub ----------------------------------------------------

def test_init_writes_live_entries_for_the_runner_it_detected():
    """AGENTS.md's loop step 5 is `crapkit test-scoped FILES`, which needs one
    template per scope. The pytest lane's presence signal makes the python
    command known-good, so it lands live; the js runner stays a commented
    template because presence cannot pick the right vitest config."""
    text = starter_toml(SCOPES, detect_lanes(frozenset({"pyproject.toml"}), ""))

    assert "[crapkit.scoped_tests]" in text
    assert 'pylib = "python -m pytest {files} -q -p no:cacheprovider"' in text
    assert '# src = "npx vitest run {files}"' in text


def test_the_stub_stays_commented_so_no_scope_gets_a_command_it_cannot_run():
    cfg = load_config_text(starter_toml(SCOPES))

    assert cfg.scoped_tests == ()


def test_the_stub_uncomments_into_a_config_crapkit_reads_back():
    """A stub a reader cannot uncomment is decoration. Everything from the table
    header down is one paste."""
    text = starter_toml(SCOPES, detect_lanes(frozenset({"pyproject.toml"}), ""))
    live = "\n".join(ln.removeprefix("# ") for ln in text.splitlines()
                     if ln.startswith("# src = "))

    cfg = load_config_text(text + "\n" + live)

    assert dict(cfg.scoped_tests) == {
        "pylib": "python -m pytest {files} -q -p no:cacheprovider",
        "src": "npx vitest run {files}"}


def test_a_scope_in_a_language_with_no_known_runner_gets_a_placeholder():
    text = starter_toml({"cmd": ("go",)})

    assert '# cmd = "<your test command> {files}"' in text


def test_a_detected_pytest_lane_activates_the_python_scoped_tests_entry():
    """The presence signal that wrote the py coverage lane is the same signal
    that makes `python -m pytest {files}` known-good, so init writes it live:
    a fresh repo then has no scoped-tests gap for doctor to warn about."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "")
    cfg = load_config_text(starter_toml({"src": ("python",)}, lanes))

    assert dict(cfg.scoped_tests)["src"] == "python -m pytest {files} -q -p no:cacheprovider"


def test_an_undetected_runner_keeps_its_scoped_tests_entry_commented():
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "")
    text = starter_toml({"src": ("python",), "ui": ("typescript",)}, lanes)
    cfg = load_config_text(text)

    assert "src" in dict(cfg.scoped_tests)
    assert "ui" not in dict(cfg.scoped_tests)
    assert '# ui = "npx vitest run {files}"' in text


# --- the environment the scaffolded lane binds to -----------------------------
#
# `python -m pytest` binds to whatever venv the shell has active. In two
# worktrees of one branch that is how a lane run in checkout A imports checkout
# B's sources through B's editable install — which either dies in collection or,
# when the two APIs agree, measures B and scores A as untested. A lockfile is the
# repo saying which environment is the right one, and the manager's `run` is the
# only spelling that binds to it.

def test_a_uv_lock_pins_the_lane_to_the_projects_own_environment():
    """The manager prefixes the whole invocation and changes nothing else: the
    junit flag and results_artifact the crashed-worker and no-new-failures
    checks read still ride on the lane."""
    (lane,) = detect_lanes(frozenset({"pyproject.toml"}), "", interpreter="uv run python")

    assert lane.command == ("uv run python -m pytest --cov --cov-branch "
                            "--cov-report=json:.crapkit/cov/py.json "
                            "--junitxml=.crapkit/cov/junit-py.xml")
    assert lane.results_artifact == ".crapkit/cov/junit-py.xml"


def test_each_lockfile_names_the_manager_that_owns_the_environment():
    assert lockfile_runner(frozenset({"uv.lock"})) == "uv run"
    assert lockfile_runner(frozenset({"poetry.lock"})) == "poetry run"
    assert lockfile_runner(frozenset({"pdm.lock"})) == "pdm run"
    assert lockfile_runner(frozenset({"Pipfile.lock"})) == "pipenv run"


def test_no_lockfile_leaves_the_interpreter_alone():
    assert lockfile_runner(frozenset({"package-lock.json", "Cargo.lock"})) == ""


def test_two_lockfiles_resolve_the_same_way_every_time():
    """A repo mid-migration carries both. Declaration order decides, so the
    config init writes does not depend on which name a set iterated first."""
    assert lockfile_runner(frozenset({"poetry.lock", "uv.lock"})) == "uv run"


def test_the_scoped_tests_entry_runs_the_same_python_the_lane_does():
    """Step 3 measuring one environment and step 4 testing another is the same
    bug one command later."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "", interpreter="uv run python")

    cfg = load_config_text(starter_toml({"src": ("python",)}, lanes))

    assert dict(cfg.scoped_tests)["src"] == (
        "uv run python -m pytest {files} -q -p no:cacheprovider")


def test_the_launcher_is_read_back_off_the_lane_rather_than_guessed():
    assert python_launcher(detect_lanes(frozenset({"pytest.ini"}), "",
                                        interpreter="poetry run python")) == "poetry run python"
    assert python_launcher(()) == "python", "no pytest lane, no claim to make"
    assert python_launcher(detect_lanes(frozenset(), _package(scripts={"test": "vitest run"}))) \
        == "python", "a js lane says nothing about python"


def test_a_managed_lane_still_reads_as_the_confirmed_pytest_runner():
    """The `-m pytest` shape is what marks the python scoped-tests entry live;
    a manager prefix in front of it must not retire that."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}), "", interpreter="pdm run python")

    assert "src" in dict(load_config_text(starter_toml({"src": ("python",)}, lanes)).scoped_tests)


def test_the_launcher_and_the_live_entry_read_the_same_lane():
    """One reading of which lane runs pytest, or none.

    A command ending at `-m pytest` was the pytest lane to `python_launcher`
    and not to the check that writes the scoped-tests entry live, so init wrote
    a commented template carrying a `uv run python` it had just refused to
    confirm.
    """
    lanes = (LaneSpec("py", "uv run python -m pytest", ".crapkit/cov/py.json",
                      "coveragepy", ("python",)),)

    live = "src" in dict(load_config_text(starter_toml({"src": ("python",)}, lanes)).scoped_tests)

    assert live == (python_launcher(lanes) != "python")


def test_the_commented_lane_template_carries_the_managers_python_too():
    """A repo that pins uv and ships no pytest marker gets the coveragepy lane
    as a commented template. Uncommenting it must hand the reader the command
    init would have written live, not a bare `python` bound to whichever venv
    the shell has active."""
    text = starter_toml({"pylib": ("python",)}, interpreter="uv run python")

    assert ('# command = "uv run python -m pytest --cov --cov-branch '
            '--cov-report=json:.crapkit/cov/py.json '
            '--junitxml=.crapkit/cov/junit-py.xml"') in text
    assert '# pylib = "uv run python -m pytest {files} -q -p no:cacheprovider"' in text, \
        "one launcher for every python line the file holds"
    assert '# command = "npx vitest run --coverage' in text, "the js template is untouched"


def test_with_no_lockfile_the_commented_template_keeps_the_bare_name():
    text = starter_toml({"pylib": ("python",)})

    assert '# command = "python -m pytest --cov --cov-branch' in text
    assert '# pylib = "python -m pytest {files} -q -p no:cacheprovider"' in text


# --- a red test must still leave a coverage report ----------------------------
#
# vitest writes no coverage report when a test fails, so the first `crapkit
# coverage` on a repo with one red test exited 5 naming a missing
# coverage-final.json: a message about a file, for a run that was really about a
# flag. The junit report landed either way, which made the run look half
# finished. jest writes its report on a red run already, and exits on a flag it
# does not know, so this flag is vitest's alone.

def test_the_vitest_lane_asks_for_a_report_even_when_a_test_fails():
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"vitest": "^2.0.0"}))

    assert "--coverage.reportOnFailure" in lane.command


def test_the_commented_vitest_template_carries_the_flag_the_live_lane_carries():
    """What a reader uncomments has to be what init would have written live."""
    assert "--coverage.reportOnFailure" in starter_toml({"web": ("typescript",)})


def test_jest_never_gets_the_flag_vitest_spells_it_with():
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"jest": "^29.0.0"}))

    assert "reportOnFailure" not in lane.command


# --- the excludes reach the repo root -----------------------------------------
#
# Globs are whole-path, so `**/vendor/**` needs a directory before `vendor` and
# never matched a repo-root vendor/. init turned that tree into a scope no lane
# measured and doctor FAILed it; the root conftest.py init left unscoped was the
# same glob missing the same file.

def _default_match():
    from crapkit.universe import exclude_matcher

    return exclude_matcher(DEFAULT_EXCLUDES)


def test_the_default_excludes_reach_a_repo_root_tree():
    from crapkit.universe import excluded

    match = _default_match()

    assert excluded("vendor/lib.js", match)
    assert excluded("dist/bundle.js", match)
    assert excluded("build/gen.py", match)
    assert excluded("node_modules/pkg/index.js", match)
    assert excluded("conftest.py", match)
    assert excluded("test_thing.py", match)
    assert excluded("main_test.go", match)


def test_the_default_excludes_still_leave_source_in_the_corpus():
    from crapkit.universe import excluded

    match = _default_match()

    assert not excluded("src/pkg/mod.py", match)
    assert not excluded("web/src/w.ts", match)
    assert not excluded("a/b.py", match)
    assert not excluded("build.sh", match), "a root build SCRIPT is production code"


def test_a_repo_root_vendor_tree_is_never_sniffed_as_a_scope():
    assert sniff_scopes(["vendor/lib.js", "web/app.ts"]) == {"web": ("typescript",)}


# --- a monorepo names its runner in the workspace that owns the tests ---------
#
# init read the root package.json and nothing else. In a workspace repo the root
# `test` script only chains the workspaces' own and the runner lives one level
# down, so init wrote a lane with no routing at all: doctor WARNed twice about
# the config init had just written, and the lane could not produce the artifact
# it was asked for.

def _workspace_packages(directory: str) -> dict[str, str]:
    return {"": _package(scripts={"test": "npm run --workspaces test"},
                         devDependencies={"typescript": "^5.0.0"}),
            directory: _package(scripts={"test": "vitest run"},
                                devDependencies={"vitest": "^2.0.0"})}


def test_the_one_workspace_that_names_a_runner_gets_the_lane():
    (lane,) = detect_lanes(frozenset(), _workspace_packages("web"))

    assert lane.cwd == "web"
    assert lane.command == ("npm run test -- --coverage "
                            "--coverage.reportsDirectory=../.crapkit/cov/js "
                            "--coverage.reportOnFailure "
                            "--reporter=default --reporter=junit "
                            "--outputFile=../.crapkit/cov/js/junit.xml")
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json", "resolved from the root"
    assert lane.results_artifact == ".crapkit/cov/js/junit.xml"


def test_a_nested_workspace_climbs_back_to_the_root_once_per_level():
    (lane,) = detect_lanes(frozenset(), _workspace_packages("packages/web"))

    assert lane.cwd == "packages/web"
    assert "--coverage.reportsDirectory=../../.crapkit/cov/js" in lane.command
    assert "--outputFile=../../.crapkit/cov/js/junit.xml" in lane.command


def test_a_workspace_with_no_test_script_runs_its_runner_directly():
    packages = {"": _package(scripts={"test": "npm run --workspaces test"}),
                "web": _package(devDependencies={"vitest": "^2.0.0"})}

    (lane,) = detect_lanes(frozenset(), packages)

    assert lane.command.startswith("npx vitest run --coverage ")
    assert lane.cwd == "web"


def test_two_workspaces_naming_a_runner_leave_the_root_lane_alone():
    """Which one measures the repo is exactly what file presence cannot say."""
    packages = {"": _package(scripts={"test": "npm run --workspaces test"}),
                "web": _package(devDependencies={"vitest": "^2.0.0"}),
                "api": _package(devDependencies={"jest": "^29.0.0"})}

    (lane,) = detect_lanes(frozenset(), packages)

    assert lane.command == "npm run test -- --coverage"
    assert lane.cwd == ""


def test_a_root_that_names_the_runner_itself_keeps_the_root_lane():
    packages = {"": _package(scripts={"test": "vitest run"},
                             devDependencies={"vitest": "^2.0.0"}),
                "web": _package(devDependencies={"vitest": "^2.0.0"})}

    (lane,) = detect_lanes(frozenset(), packages)

    assert lane.cwd == ""
    assert "--coverage.reportsDirectory=.crapkit/cov/js" in lane.command


def test_the_workspace_lanes_cwd_survives_into_the_config_init_writes():
    lanes = detect_lanes(frozenset(), _workspace_packages("web"))

    cfg = load_config_text(starter_toml({"web": ("typescript",)}, lanes))

    (lane,) = cfg.lanes
    assert lane.cwd == "web"
# --- a suite whose testpaths cannot be collected in one process ---------------
#
# The full-suite guard refuses a positional in a pytest lane command, and the
# honest way past it on such a suite is one lane per testpath. init cannot know
# whether the whole suite collects — that would mean running it — so it writes
# the pattern commented out whenever the repo's pytest config names more than
# one testpath, and the reader who hits the collection error uncomments it.

PYTEST_INI = "[pytest]\ntestpaths = conform impl tests\n"
SETUP_CFG = "[tool:pytest]\ntestpaths =\n    conform\n    impl\n"
PYPROJECT_TESTPATHS = '[tool.pytest.ini_options]\ntestpaths = ["conform", "impl"]\n'
IMPL_SCOPE = {"impl": ("python",)}


def _uncommented_lane(text: str, name: str) -> str:
    """The stub a reader uncomments, uncommented the way they would do it."""
    lines = text.splitlines()
    start = lines.index(f'# name = "{name}"') - 1
    block = []
    for line in lines[start:]:
        if not line.startswith("# "):
            break
        block.append(line[2:])
    return "\n".join(block) + "\n"


def test_testpaths_are_read_from_each_file_pytest_reads_them_from():
    assert pytest_testpaths({"pytest.ini": PYTEST_INI}) == ("conform", "impl", "tests")
    assert pytest_testpaths({"setup.cfg": SETUP_CFG}) == ("conform", "impl")
    assert pytest_testpaths({"pyproject.toml": PYPROJECT_TESTPATHS}) == ("conform", "impl")


def test_pytest_ini_outranks_the_other_two_the_way_pytest_ranks_them():
    every = {"pytest.ini": PYTEST_INI, "pyproject.toml": PYPROJECT_TESTPATHS,
             "setup.cfg": SETUP_CFG}

    assert pytest_testpaths(every) == ("conform", "impl", "tests")


def test_a_config_naming_no_testpaths_or_not_parsing_reads_as_none():
    assert pytest_testpaths({}) == ()
    assert pytest_testpaths({"setup.cfg": "[metadata]\nname = app\n"}) == ()
    assert pytest_testpaths({"pyproject.toml": "[project]\nname = 'app'\n"}) == ()
    assert pytest_testpaths({"pytest.ini": "]]] not ini", "pyproject.toml": "[[["}) == ()


def test_the_section_that_names_no_testpaths_still_ends_the_search():
    """pytest reads one inifile and never consults a lower-ranked one. A
    `pytest.ini` carrying `[pytest]` is that file even when it names no
    testpaths, so a bare `pytest` collects from the rootdir and pyproject's
    list is dead text. Reading it anyway would write sibling lane stubs for
    paths the repo's real run never collects."""
    assert pytest_testpaths({"pytest.ini": "[pytest]\naddopts = -q\n",
                             "pyproject.toml": PYPROJECT_TESTPATHS}) == ()
    assert pytest_testpaths({"pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n",
                             "setup.cfg": SETUP_CFG}) == ()


def test_a_single_testpath_leaves_the_detected_lane_alone():
    """One testpath collects in one process by definition, so there is nothing
    to split and no reason to put a second pattern in front of the reader."""
    text = starter_toml(IMPL_SCOPE, detect_lanes(frozenset({"pytest.ini"}), ""),
                        testpaths=("tests",))

    assert "full_suite" not in text


def test_several_testpaths_add_one_commented_lane_each_at_full_suite_false():
    lanes = detect_lanes(frozenset({"pytest.ini"}), "")

    text = starter_toml(IMPL_SCOPE, lanes, testpaths=("conform", "impl"))

    assert '# name = "py-conform"' in text
    assert '# name = "py-impl"' in text
    assert '# artifact = ".crapkit/cov/py-conform.json"' in text
    assert text.count("# full_suite = false") == 2
    assert load_config_text(text).lanes[0].name == "py", "the detected lane still runs"


def test_an_uncommented_sibling_lane_parses_and_clears_the_full_suite_guard():
    """What a reader uncomments has to load. A pytest lane carrying a positional
    is exactly what the full-suite guard refuses, so a stub without its own
    `full_suite = false` would be a config crapkit cannot read back."""
    text = starter_toml(IMPL_SCOPE, detect_lanes(frozenset({"pytest.ini"}), ""),
                        testpaths=("conform", "impl"))

    lane = load_config_text('[[scope]]\nname = "impl"\npaths = ["impl"]\n'
                            'languages = ["python"]\n'
                            + _uncommented_lane(text, "py-conform")).lanes[0]

    assert lane.full_suite is False
    assert lane.command.startswith("python -m pytest conform --cov")
    assert lane.results_artifact == ".crapkit/cov/junit-py-conform.xml"
    assert lane.scopes == ("impl",)


def test_a_repo_with_no_pytest_lane_gets_no_sibling_lanes():
    """testpaths without a detected pytest lane names no lane to split: the js
    lane measures none of those paths."""
    lanes = detect_lanes(frozenset(), _package(scripts={"test": "vitest run"}))

    text = starter_toml({"src": ("typescript",)}, lanes, testpaths=("a", "b"))

    assert "full_suite" not in text
