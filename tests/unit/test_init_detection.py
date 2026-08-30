"""What `crapkit init` can know about a repo's test runners from files alone.

A starter config whose lanes are all commented out scores every function
no-lane, so the burn-down has nothing to rank. Detection is file presence and
package.json content only — nothing is executed, nothing is imported.
"""
import json

from crapkit.config import load_config_text
from crapkit.scaffold import (detect_lanes, gitignore_entries, gitignore_update, live_lanes,
                              source_candidates, starter_toml)

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
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"vitest": "^2.0.0"}))

    assert lane.command == ("npx vitest run --coverage "
                            "--coverage.reportsDirectory=.crapkit/cov/js")
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json"


def test_jest_is_routed_with_the_flag_jest_spells_it_with():
    (lane,) = detect_lanes(frozenset(), _package(devDependencies={"jest": "^29.0.0"}))

    assert lane.command == "npx jest --coverage --coverageDirectory=.crapkit/cov/js"
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json"


def test_a_test_script_is_routed_when_devdependencies_name_one_runner():
    (lane,) = detect_lanes(frozenset(), _package(scripts={"test": "vitest run"},
                                                 devDependencies={"vitest": "^2.0.0"}))

    assert lane.command == ("npm run test -- --coverage "
                            "--coverage.reportsDirectory=.crapkit/cov/js")
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
        "py": ".crapkit/cov/junit-py.xml", "js": ""}, "the py lane's junit survives the round trip"


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
