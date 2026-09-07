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
