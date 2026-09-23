"""A lane may list the paths its command reads, as `inputs`.

`--reuse-unchanged` reuses such a lane while nothing under those paths changed,
instead of demanding the whole tree be untouched since the artifact's commit.
The list is read as git pathspecs from the root, so a path that climbs out of
the root or is spelled absolutely could name changes the check never sees.
"""
import json
from pathlib import Path

import pytest

from crapkit.config import load_config_text
from crapkit.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]


def _config(inputs: str | None) -> str:
    line = f"inputs = {inputs}\n" if inputs is not None else ""
    return ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
            '[[lane]]\nname = "py"\ncommand = "x"\nartifact = "cov.json"\n'
            f'parser = "coveragepy"\nscopes = ["src"]\n{line}')


def test_a_lane_reads_its_declared_inputs_in_order():
    lane = load_config_text(_config('["src", "tests", "pyproject.toml"]')).lanes[0]
    assert lane.inputs == ("src", "tests", "pyproject.toml")


def test_a_lane_that_declares_none_keeps_the_strict_rule():
    assert load_config_text(_config(None)).lanes[0].inputs == ()


@pytest.mark.parametrize("entry", ["../shared", "src/../../x", "/abs/src", "C:/repo/src", ""])
def test_an_input_outside_the_root_is_refused_at_load(entry):
    with pytest.raises(ConfigError) as raised:
        load_config_text(_config(json.dumps([entry])))
    message = str(raised.value)
    assert "lane 'py'" in message and repr(entry) in message


def test_inputs_must_be_a_list_of_strings():
    with pytest.raises(ConfigError, match="inputs"):
        load_config_text(_config('"src"'))


def test_the_published_schema_names_the_key():
    schema = json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))
    rule = schema["properties"]["lane"]["items"]["properties"]["inputs"]
    assert rule["type"] == "array" and rule["items"] == {"type": "string"}
