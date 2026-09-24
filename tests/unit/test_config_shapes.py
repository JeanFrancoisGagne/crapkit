"""Malformed TOML must fail admission before changing the scored corpus."""
import json
from pathlib import Path

import pytest

from crapkit.config import load_config_text
from crapkit.errors import ConfigError


SCOPE = '\n[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
LANE = '\n[[lane]]\nname="py"\ncommand="run"\nartifact="cov.json"\nparser="coveragepy"\nscopes=["src"]\n'


@pytest.mark.parametrize("text,field", [
    ('[crapkit]\ntarget=6\n', "scope"),
    ('scope=[]\n', "scope"),
    (SCOPE.replace('paths=["src"]', 'paths="src"'), "paths"),
    (SCOPE.replace('languages=["python"]', 'languages="python"'), "languages"),
    (SCOPE.replace('paths=["src"]', 'paths=[]'), "paths"),
    (SCOPE.replace('name="src"', 'name=7'), "name"),
    ('[crapkit]\nratchet_file=false\n' + SCOPE, "ratchet_file"),
    ('[crapkit]\nalert_command=false\n' + SCOPE, "alert_command"),
    ('[crapkit]\nmutation_command=123\n' + SCOPE, "mutation_command"),
    ('[crapkit.scoped_tests]\nsrc=123\n' + SCOPE, "scoped_tests"),
    ('[exclude]\nglobs="tests/**"\n' + SCOPE, "globs"),
    (SCOPE + LANE.replace('scopes=["src"]', 'scopes="src"'), "scopes"),
    (SCOPE + LANE + '\n[lane.env]\nNUMBER=123\n', "env"),
    ('crapkit=17\n' + SCOPE, "crapkit"),
    ('scope=[17]\n', "scope"),
    ('lane="py"\n' + SCOPE, "lane"),
])
def test_shapes_are_rejected_before_configuration_is_constructed(text, field):
    with pytest.raises(ConfigError, match=field):
        load_config_text(text)


def test_editor_contract_is_generated_from_runtime_admission():
    from crapkit.config_contract import schema

    published = Path(__file__).resolve().parents[2] / "crapkit.schema.json"
    assert json.loads(published.read_text(encoding="utf-8")) == schema()


def test_unknown_keys_remain_doctor_findings_for_version_skew():
    config = load_config_text('[crapkit]\nfuture_setting={answer=42}\n' + SCOPE)
    assert config.scopes[0].paths == ("src",)


# --- the shapes the hunts tried beyond these ------------------------------------
#
# TOML has no null, so an absent key, an empty value, a table where an array of
# tables belongs, an out-of-range number and non-ASCII text stand in. Each is a
# ConfigError naming the key, or a configuration that loads as written.

@pytest.mark.parametrize("text,words", [
    (SCOPE + LANE.replace('artifact="cov.json"\n', ""), "missing a required key: ['artifact']"),
    (SCOPE.replace('languages=["python"]', "languages=[]"), "languages must contain at least 1"),
    (SCOPE.replace("[[scope]]", "[scope]"), "scope must be array"),
    ("[crapkit]\ntarget=0\n" + SCOPE, "target must be >= 1, got 0"),
    ("[crapkit]\nlog_max_bytes=-1\n" + SCOPE, "log_max_bytes must be >= 0, got -1"),
], ids=["lane-artifact-absent", "scope-languages-empty", "scope-a-table", "target-zero",
        "log_max_bytes-negative"])
def test_an_absent_empty_or_out_of_range_value_is_named(text, words):
    with pytest.raises(ConfigError, match=words.replace("[", r"\[").replace("]", r"\]")):
        load_config_text(text)


@pytest.mark.parametrize("text,read,value", [
    (SCOPE.replace('name="src"', 'name="pâquet"') + LANE.replace('scopes=["src"]', 'scopes=["pâquet"]'),
     lambda cfg: tuple(cfg.lanes[0].scopes), ("pâquet",)),
    (SCOPE + LANE.replace('command="run"', 'command="run -k café"'),
     lambda cfg: cfg.lanes[0].command, "run -k café"),
    ("[crapkit]\ntarget=9223372036854775807\n" + SCOPE, lambda cfg: cfg.target, 9223372036854775807),
    (SCOPE + LANE.replace('scopes=["src"]', "scopes=[]"), lambda cfg: tuple(cfg.lanes[0].scopes), ()),
    (SCOPE + LANE.replace('command="run"', 'command=""'), lambda cfg: cfg.lanes[0].command, ""),
], ids=["scope-name-non-ascii", "lane-command-non-ascii", "target-huge", "lane-scopes-empty",
        "lane-command-empty"])
def test_a_value_doctor_judges_rather_than_admission_loads_as_written(text, read, value):
    """A lane with no scope or no command is doctor's finding, not a parse error."""
    assert read(load_config_text(text)) == value
