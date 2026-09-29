"""config.py at its edges: the defaults an unset key reads as, the exact
refusals, and the shell words a lane command splits into.

Backslashes before a quote follow the Microsoft C runtime's rule for a
program's argv ("Parsing C++ command-line arguments"): 2n backslashes and a
quote write n backslashes and open or close a quoted run, 2n+1 write n and a
literal quote. A `#` starts a sh comment only where a word starts (POSIX Shell
Command Language 2.3, rule 10).
"""
import pytest

from crapkit import config
from crapkit.config import ConfigError, load_config_text, shell_segments, shell_words

SCOPE = '[[scope]]\nname = "s"\npaths = ["src"]\nlanguages = ["python", "typescript"]\n'


def _lane(name: str, command: str, parser: str = "coveragepy", artifact: str = "c.json",
          extra: str = "") -> str:
    return (f'[[lane]]\nname = "{name}"\nparser = "{parser}"\nartifact = "{artifact}"\n'
            f'command = "{command}"\nscopes = ["s"]\n{extra}')


def _refusal(text: str) -> str:
    with pytest.raises(ConfigError) as refused:
        load_config_text(text)
    return str(refused.value)


def test_an_unset_key_reads_as_its_documented_default():
    loaded = load_config_text(SCOPE + _lane("py", "pytest --cov"))

    assert (loaded.alert_command, loaded.mutation_command, loaded.mutation_timeout_seconds,
            loaded.ratchet_file) == ("", "", 300, "crapkit-ratchet.tsv")
    lane = loaded.lanes[0]
    assert (lane.container_ok, lane.results_artifact, lane.timeout_seconds,
            lane.no_progress_seconds, lane.retries, lane.retest_command,
            lane.log_max_bytes) == (False, "", 0, 0, 0, "", 16777216)


def test_the_ratchet_file_is_the_one_configured():
    loaded = load_config_text('[crapkit]\nratchet_file = "r.tsv"\n' + SCOPE)

    assert loaded.ratchet_file == "r.tsv"


def test_a_file_that_does_not_parse_is_refused_as_such():
    assert _refusal("x = [").startswith("crapkit.toml does not parse: ")


def test_a_lane_naming_an_undeclared_scope_is_refused_by_name():
    text = SCOPE + _lane("py", "pytest --cov").replace('scopes = ["s"]', 'scopes = ["t"]')

    assert _refusal(text) == "lane 'py' references undeclared scope(s) ['t']"


def test_two_lanes_sharing_an_artifact_are_both_named():
    text = SCOPE + _lane("a", "pytest --cov") + _lane("b", "pytest --cov")

    assert _refusal(text) == ("lanes 'a' and 'b' share the artifact path 'c.json'; reused paths "
                              "cross-attribute coverage under --reuse-artifacts")


@pytest.mark.parametrize("flag", ["--coverage", "--coverage.enabled", "--coverage.enabled=true"])
def test_every_spelling_of_the_coverage_flag_asks_for_coverage(flag):
    text = SCOPE + _lane("ts", f"npx vitest {flag} src/a.test.ts", parser="istanbul")

    assert _refusal(text) == (
        "lane 'ts': file filter 'src/a.test.ts' combined with --coverage silently narrows "
        "the coverage include set; drop the filter or use a dedicated config")


def test_every_shape_a_pytest_positional_takes_is_a_path():
    shapes = ["tests/unit", "tests\\unit", "t::x", "test_a.py"]

    assert [config._looks_like_a_test_path(tok) for tok in [*shapes, "x"]] == [
        True, True, True, True, False]


@pytest.mark.parametrize("shell_is_cmd, command, hint", [
    (False, "pytest -m 'a b'", ""),
    (True, "pytest -m 'a b'",
     " (cmd.exe does not treat ' as a quote: write the value in double quotes)"),
    (True, 'pytest -m "a b"', ""),
])
def test_the_single_quote_hint_is_for_cmd_exe_only(monkeypatch, shell_is_cmd, command, hint):
    monkeypatch.setattr(config, "SHELL_IS_CMD", shell_is_cmd)

    assert config._quote_hint(command) == hint


def test_a_narrowing_refusal_under_cmd_exe_carries_the_single_quote_hint(monkeypatch):
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)

    refusal = _refusal(SCOPE + _lane("py", "pytest --cov -m 'not live'"))

    assert "set full_suite = false deliberately (cmd.exe does not treat ' as a quote" in refusal


def test_a_testpath_is_spelled_with_slashes_and_no_trailing_one():
    assert [config._as_testpath(tok) for tok in ["tests\\unit\\", "./testsX/"]] == [
        "tests/unit", "testsX"]


def test_a_backslashed_climb_is_outside_the_root():
    assert config._outside_root("..\\shared") is True


def test_a_scope_path_keeps_every_character_but_its_separators():
    assert [config._unrooted(raw) for raw in ["srcX/", "/Xa"]] == ["srcX", "Xa"]


def test_an_input_that_names_the_root_is_the_root():
    assert config._lane_input("py", "./") == "."


def test_an_input_outside_the_root_and_a_scope_path_that_climbs_are_refused_in_words():
    with pytest.raises(ConfigError) as outside:
        config._lane_input("py", "../x")
    with pytest.raises(ConfigError) as climbing:
        config._scope_path("s", "../x")

    assert str(outside.value) == ("lane 'py': inputs entry '../x' is not a path inside the root; "
                                  "list paths relative to crapkit.toml, without '..'")
    assert str(climbing.value) == ("scope 's': path '../x' can never match a tracked file — "
                                   "scope paths are repo-relative, with no drive and no `..` "
                                   "(docs/configuration.md)")


def test_a_pytest_section_without_testpaths_names_none():
    assert config._ini_testpaths("[pytest]\naddopts = -q\n", "pytest", True) == ()


def test_a_pytest_file_that_is_not_utf8_is_read_with_replacements(tmp_path):
    (tmp_path / "pytest.ini").write_bytes(b"[pytest]\ntestpaths = t\xff \xc3\xa9\n")

    assert config._pytest_text(tmp_path, "pytest.ini") == "[pytest]\ntestpaths = t� é\n"


# --- the words a line splits into -----------------------------------------------------------

@pytest.mark.parametrize("command, words", [
    ('pytest a\\"b', ['a"b']),
    ('pytest a\\\\"b c"', ["a\\b c"]),
    ('pytest a\\\\\\"b', ['a\\"b']),
    ('pytest a\\\\\\\\"b c"', ["a\\\\b c"]),
    ('pytest "x\\"y z"', ['x"y z']),
])
def test_under_cmd_backslashes_before_a_quote_follow_the_c_runtime(command, words):
    assert shell_words(command, cmd=True) == ["pytest", *words]


def test_under_cmd_delimiters_before_a_parenthesis_leave_it_opening_a_block():
    assert shell_segments(";, ( pytest a ) & pytest b", cmd=True) == [["pytest", "a"],
                                                                       ["pytest", "b"]]


def test_under_sh_a_hash_inside_a_word_is_text_and_one_that_starts_a_word_is_a_comment():
    assert shell_words("pytest a#b c #d e", cmd=False) == ["pytest", "a#b", "c"]
