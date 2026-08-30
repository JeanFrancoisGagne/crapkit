"""Config seam: crapkit.toml in, validated Config out. Pure: bytes/str in, dataclass out."""
from pathlib import Path

import pytest

from crapkit.errors import ConfigError
from crapkit import config as config_module
from crapkit.config import Config, load_config_text, shell_words


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


def test_a_quoted_file_filter_beside_coverage_is_still_a_filter(monkeypatch):
    """A whitespace split leaves the closing quote on the token, the suffix
    check misses, and a quoted positional filter sails past the guard."""
    import pytest as _pytest
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", False)  # sh reads '...'
    with _pytest.raises(ConfigError, match="narrows"):
        load_config_text(
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
            "[[lane]]\nname = \"unit\"\ncommand = \"vitest run --coverage 'src/foo.test.ts'\"\n"
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')


def test_a_quoted_exclude_glob_value_is_not_a_file_filter(monkeypatch):
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", False)  # sh reads '...'
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
        "[[lane]]\nname = \"unit\"\ncommand = \"vitest run --coverage --exclude 'src/**/*.test.ts'\"\n"
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    assert cfg.lanes[0].name == "unit", "a value flag's quoted glob never narrows"


def test_quoted_values_of_the_vitest_flags_that_take_a_path_or_glob_are_not_filters():
    """`--coverage.exclude "**/*.d.ts"` and `--setupFiles "test/setup.ts"` are
    values; closing the quoted-positional hole must not refuse them."""
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
        '[[lane]]\nname = "unit"\n'
        "command = 'vitest run --coverage --coverage.exclude \"**/*.d.ts\" --setupFiles \"test/setup.ts\"'\n"
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    assert cfg.lanes[0].name == "unit"


def test_under_cmd_a_double_quoted_file_filter_is_still_a_filter(monkeypatch):
    import pytest as _pytest
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", True)
    with _pytest.raises(ConfigError, match="narrows"):
        load_config_text(
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
            "[[lane]]\nname = \"unit\"\ncommand = 'vitest run --coverage \"src/foo.test.ts\"'\n"
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')


def _istanbul_lane(command: str):
    """A TOML literal string, so a command's double quotes reach the guard."""
    return load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
        f"[[lane]]\nname = \"unit\"\ncommand = '{command}'\n"
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n').lanes[0]


def test_a_script_path_in_a_step_chained_after_the_run_is_not_a_file_filter():
    """`&&` starts a new process: vitest never sees `scripts/post.mjs`, so
    calling it a filter that narrows the coverage include set refused a lane
    that runs correctly, and full_suite = false does not clear this guard."""
    assert _istanbul_lane("vitest run --coverage && node scripts/post.mjs src/a.ts").name
    assert _istanbul_lane("vitest run --coverage | tee run.log").name


def test_a_run_chained_after_another_command_is_still_checked_for_a_filter():
    """The runner in the second segment is still the runner: stopping at the
    first operator would let a real filter through."""
    import pytest as _pytest
    with _pytest.raises(ConfigError, match="narrows"):
        _istanbul_lane("npm run build && vitest run --coverage src/a.ts")
    with _pytest.raises(ConfigError, match="narrows"):
        _istanbul_lane("vitest run --coverage && vitest run --coverage src/b.ts")


@pytest.mark.parametrize("shell_is_cmd", [True, False])
def test_a_quoted_operator_beside_coverage_starts_no_new_command(monkeypatch, shell_is_cmd):
    """`"&&"` is an argument vitest is handed, not a separator, so the filter
    behind it is still in the run's own argv."""
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", shell_is_cmd)
    with pytest.raises(ConfigError, match="narrows"):
        _istanbul_lane('npx vitest run --coverage "&&" src/a.ts')


def test_the_lanes_page_names_every_vitest_option_the_guard_licenses():
    """The guard works from a closed list, so the page has to print the list. It
    read as a rule about values while the frozenset grew from 11 names to 20
    behind it, and a reader whose flag was missing had nothing to grep for."""
    from crapkit.config import _VITEST_VALUE_FLAGS

    page = (Path(__file__).resolve().parents[2] / "docs" / "lanes.md").read_text(encoding="utf-8")
    assert [flag for flag in sorted(_VITEST_VALUE_FLAGS) if f"`{flag}" not in page] == []


def test_under_cmd_a_caret_escaped_file_filter_is_still_a_filter(monkeypatch):
    """`^"src/a.ts^"` reaches vitest as the filter `src/a.ts`: leaving the caret
    in the token hid the suffix and the narrowing lane loaded clean."""
    import pytest as _pytest
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", True)
    with _pytest.raises(ConfigError, match="narrows"):
        _istanbul_lane('npx vitest run --coverage ^"src/a.ts^"')


def test_shell_words_under_cmd_reads_a_caret_as_the_escape_cmd_reads():
    """Outside a quoted run cmd.exe drops `^` and hands the runner the character
    behind it, so `-k ^"not slow^"` is one value. Inside a quoted run cmd.exe
    leaves the caret alone, and the runner gets it."""
    assert shell_words('pytest --cov -k ^"not slow^"', cmd=True) == \
        ["pytest", "--cov", "-k", "not slow"]
    assert shell_words('pytest -k "a^b"', cmd=True) == ["pytest", "-k", "a^b"]
    assert shell_words('pytest -k "a^^b"', cmd=True) == ["pytest", "-k", "a^^b"]


def test_shell_words_keeps_the_empty_argument_a_pair_of_quotes_writes():
    """cmd.exe hands the program the empty argument `""` wrote (verified argv:
    `-k "" tests/unit` -> ["-k", "", "tests/unit"]). Dropping it moved every
    later token one place left, so the flag in front swallowed the wrong word
    and a narrowing lane loaded clean."""
    assert shell_words('pytest -k "" tests/unit', cmd=True) == \
        ["pytest", "-k", "", "tests/unit"]
    assert shell_words('pytest -k "" tests/unit', cmd=False) == \
        ["pytest", "-k", "", "tests/unit"]
    assert shell_words('pytest "" ""', cmd=True) == ["pytest", "", ""]


def test_an_empty_quoted_value_does_not_hand_the_next_path_to_the_flag(monkeypatch):
    """`--testNamePattern ""` takes the empty word, so `src/a.ts` behind it is a
    positional filter and still narrows the coverage include set."""
    import pytest as _pytest
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", True)
    with _pytest.raises(ConfigError, match="narrows"):
        _istanbul_lane('npx vitest run --coverage --testNamePattern "" src/a.ts')


def test_shell_words_under_sh_leaves_a_caret_alone():
    """sh has no caret escape: it is an ordinary character in the word."""
    assert shell_words('pytest -k ^"not slow^"', cmd=False) == ["pytest", "-k", "^not slow^"]


def test_shell_words_reads_double_quotes_under_both_shells():
    command = 'pytest -m "not live and not perf" --cov'
    expected = ["pytest", "-m", "not live and not perf", "--cov"]
    assert shell_words(command, cmd=True) == expected
    assert shell_words(command, cmd=False) == expected


def test_shell_words_under_cmd_keeps_backslashes_and_reads_a_single_quote_as_a_character():
    """cmd.exe: `\\` separates path components and `'` quotes nothing, so a
    single-quoted phrase reaches the runner as one word per space."""
    assert shell_words(r"pytest tests\unit --cov-report=json:.crapkit\cov\py.json", cmd=True) == \
        ["pytest", r"tests\unit", r"--cov-report=json:.crapkit\cov\py.json"]
    assert shell_words("pytest -m 'not live'", cmd=True) == ["pytest", "-m", "'not", "live'"]


def test_shell_words_reads_a_quote_that_opens_mid_token_under_both_shells():
    """Quoting the part that holds the space is how a Windows path gets written.
    cmd.exe closes the word on the quote wherever the quote sits, so
    `--cov-report=json:"a b\\py.json"` is one argument to the runner and one
    token here; splitting it named a positional the operator never wrote."""
    assert shell_words(r'pytest --cov-report=json:"a b\py.json"', cmd=True) == \
        ["pytest", r"--cov-report=json:a b\py.json"]
    assert shell_words('pytest tests/"a b"/test_x.py', cmd=True) == \
        ["pytest", "tests/a b/test_x.py"]
    assert shell_words('pytest tests/"a b"/test_x.py', cmd=False) == \
        ["pytest", "tests/a b/test_x.py"]


def test_shell_words_under_sh_reads_single_quotes():
    assert shell_words("pytest -m 'not live'", cmd=False) == ["pytest", "-m", "not live"]


def test_shell_words_falls_back_to_the_whitespace_read_on_an_unbalanced_quote():
    assert shell_words('pytest "unclosed --cov', cmd=True) == ["pytest", '"unclosed', "--cov"]
    assert shell_words("pytest 'unclosed --cov", cmd=False) == ["pytest", "'unclosed", "--cov"]


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
