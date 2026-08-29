"""Config seam: crapkit.toml in, validated Config out. Pure: bytes/str in, dataclass out."""
import pytest

from crapkit.errors import ConfigError
from crapkit.config import Config, load_config_text


MINIMAL = """
[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[scope]]
name = "py"
paths = ["scripts"]
languages = ["python"]

[exclude]
globs = ["**/node_modules/**", "**/dist/**", "deployed/**", "**/*.test.ts"]
"""


def test_minimal_config_parses_scopes_target_and_exclusions():
    cfg = load_config_text(MINIMAL)
    assert isinstance(cfg, Config)
    assert cfg.target == 6
    assert [s.name for s in cfg.scopes] == ["src", "py"]
    assert cfg.scopes[0].languages == ("typescript",)
    assert cfg.scopes[1].paths == ("scripts",)
    assert "**/node_modules/**" in cfg.exclude_globs


def test_target_defaults_to_6_when_absent():
    cfg = load_config_text('[[scope]]\nname = "a"\npaths = ["a"]\nlanguages = ["python"]\n')
    assert cfg.target == 6


def test_config_with_no_scope_is_rejected_loudly():
    with pytest.raises(ConfigError, match="scope"):
        load_config_text("[crapkit]\ntarget = 6\n")


def test_unknown_language_is_rejected_loudly():
    with pytest.raises(ConfigError, match="language"):
        load_config_text('[[scope]]\nname = "a"\npaths = ["a"]\nlanguages = ["cobol"]\n')


def test_worklist_knobs_default_and_parse():
    cfg = load_config_text(MINIMAL)
    assert cfg.churn_window_months == 12
    assert cfg.worklist_floor == 5
    assert cfg.worklist_top == 50
    cfg2 = load_config_text(MINIMAL.replace("target = 6", "target = 6\nchurn_window_months = 3\nworklist_floor = 2\nworklist_top = 10"))
    assert (cfg2.churn_window_months, cfg2.worklist_floor, cfg2.worklist_top) == (3, 2, 10)


LANE = MINIMAL + """
[[lane]]
name = "unit"
command = "node scripts/run-vitest.mjs run --config test/vitest/vitest.unit.config.ts --coverage --coverage.reporter=json"
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
"""


def test_lane_parses_with_all_fields():
    cfg = load_config_text(LANE)
    (lane,) = cfg.lanes
    assert lane.name == "unit"
    assert lane.parser == "istanbul"
    assert lane.scopes == ("src",)
    assert lane.artifact == "coverage/coverage-final.json"


def test_lane_with_unknown_parser_rejected():
    with pytest.raises(ConfigError, match="parser"):
        load_config_text(LANE.replace('parser = "istanbul"', 'parser = "gcov"'))


def test_lane_scope_must_reference_a_declared_scope():
    with pytest.raises(ConfigError, match="scope"):
        load_config_text(LANE.replace('scopes = ["src"]', 'scopes = ["nope"]'))


def test_istanbul_lane_command_with_coverage_and_file_filter_is_rejected():
    bad = LANE.replace(
        'run --config test/vitest/vitest.unit.config.ts --coverage --coverage.reporter=json',
        'run --config test/vitest/vitest.unit.config.ts --coverage src/thing.test.ts')
    with pytest.raises(ConfigError, match="file filter"):
        load_config_text(bad)


def test_lane_cwd_and_path_prefix_default_empty_and_parse():
    cfg = load_config_text(LANE)
    assert cfg.lanes[0].cwd == "" and cfg.lanes[0].path_prefix == ""
    with_extras = LANE.replace('scopes = ["src"]', 'scopes = ["src"]\ncwd = "scripts"\npath_prefix = "scripts"')
    lane = load_config_text(with_extras).lanes[0]
    assert lane.cwd == "scripts" and lane.path_prefix == "scripts"


def test_lane_env_parses_and_defaults_empty():
    assert load_config_text(LANE).lanes[0].env == ()
    with_env = LANE + '\n[lane.env]\nVITEST_NO_OUTPUT_TIMEOUT_MS = "900000"\n'
    lane = load_config_text(with_env).lanes[0]
    assert dict(lane.env)["VITEST_NO_OUTPUT_TIMEOUT_MS"] == "900000"


PYLANE = MINIMAL + """
[[lane]]
name = "py"
command = "python -m pytest"
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
"""


def test_coveragepy_full_suite_lane_rejects_path_arguments():
    narrowed = PYLANE.replace('command = "python -m pytest"', 'command = "python -m pytest tests/unit"')
    with pytest.raises(ConfigError, match="full-suite"):
        load_config_text(narrowed)


def test_coveragepy_lane_with_full_suite_false_allows_scoped_runs():
    scoped = PYLANE.replace('command = "python -m pytest"',
                            'command = "python -m pytest pylib"\nfull_suite = false')
    assert load_config_text(scoped).lanes[0].full_suite is False


def test_two_lanes_sharing_an_artifact_path_are_rejected():
    second_lane = LANE.replace(MINIMAL, "").replace('name = "unit"', 'name = "unit2"')
    with pytest.raises(ConfigError, match="artifact"):
        load_config_text(LANE + second_lane)


def test_coverage_exclude_flags_are_not_file_filters():
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
        '[[lane]]\nname = "unit"\n'
        'command = "vitest run --coverage --coverage.exclude=**/*.test.ts --outputFile.junit=x.xml"\n'
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    assert cfg.lanes[0].name == "unit", "an exclude FLAG never narrows the include set"


def test_positional_file_filter_still_rejected_beside_coverage():
    import pytest as _pytest
    with _pytest.raises(ConfigError, match="narrows"):
        load_config_text(
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
            '[[lane]]\nname = "unit"\ncommand = "vitest run src/foo.ts --coverage"\n'
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')


def test_a_quoted_file_filter_beside_coverage_is_still_a_filter():
    """A whitespace split leaves the closing quote on the token, the suffix
    check misses, and a quoted positional filter sails past the guard."""
    import pytest as _pytest
    with _pytest.raises(ConfigError, match="narrows"):
        load_config_text(
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
            "[[lane]]\nname = \"unit\"\ncommand = \"vitest run --coverage 'src/foo.test.ts'\"\n"
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')


def test_a_quoted_exclude_glob_value_is_not_a_file_filter():
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
        "[[lane]]\nname = \"unit\"\ncommand = \"vitest run --coverage --exclude 'src/**/*.test.ts'\"\n"
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    assert cfg.lanes[0].name == "unit", "a value flag's quoted glob never narrows"


def test_scope_target_overrides_the_repo_default():
    cfg = load_config_text(
        '[crapkit]\ntarget = 6\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
        '[[scope]]\nname = "legacy"\npaths = ["old"]\nlanguages = ["python"]\ntarget = 10\n')
    assert cfg.scope_targets == {"src": 6, "legacy": 10}


def test_scope_target_must_be_a_positive_int():
    with pytest.raises(ConfigError, match="target"):
        load_config_text(
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\ntarget = 0\n')


def test_mutation_workers_defaults_to_one_and_parses():
    assert load_config_text(MINIMAL).mutation_workers == 1
    cfg = load_config_text(MINIMAL.replace("target = 6", "target = 6\nmutation_workers = 4"))
    assert cfg.mutation_workers == 4


@pytest.mark.parametrize("value", ["0", "-2", "true", '"4"'])
def test_mutation_workers_below_one_or_not_an_int_is_rejected(value: str):
    with pytest.raises(ConfigError, match="mutation_workers"):
        load_config_text(MINIMAL.replace("target = 6", f"target = 6\nmutation_workers = {value}"))
