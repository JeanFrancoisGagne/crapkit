"""Invalid configuration cannot silently change ownership or escape as a traceback."""
import json
import subprocess
import sys

import pytest

from crapkit.config import load_config_text
from crapkit.errors import ConfigError


SCOPE = '[[scope]]\nname="app"\npaths=["src"]\nlanguages=["python"]\n'
POSITIVE = ("target", "churn_window_months", "worklist_floor", "worklist_top",
            "mutation_timeout_seconds")
LANE = ('[[lane]]\nname="unit"\ncommand="echo suite"\nartifact="coverage.json"\n'
        'parser="coveragepy"\nscopes=["app"]\n')


def test_duplicate_scope_names_cannot_transfer_a_ceiling_or_coverage_exemption():
    strict = SCOPE.replace('["src"]', '["strict"]') + "target=1\n"
    loose = SCOPE.replace('["src"]', '["loose"]') + "target=100\ncoverage_optional=true\n"
    with pytest.raises(ConfigError, match="duplicate scope name 'app'"):
        load_config_text(strict + loose)


@pytest.mark.parametrize("field", POSITIVE)
@pytest.mark.parametrize("value", ['"six"', '"6"', "true", "1.5", "0", "-1"])
def test_positive_config_numbers_reject_wrong_types_and_ranges(field, value):
    with pytest.raises(ConfigError, match=field):
        load_config_text(f"[crapkit]\n{field}={value}\n" + SCOPE)


@pytest.mark.parametrize("field", POSITIVE)
def test_positive_config_numbers_accept_the_smallest_valid_value(field):
    assert getattr(load_config_text(f"[crapkit]\n{field}=1\n" + SCOPE), field) == 1


@pytest.mark.parametrize("value", ["true", '"6"', "1.5", "0", "-1"])
def test_scope_ceiling_requires_a_positive_integer(value):
    with pytest.raises(ConfigError, match="target"):
        load_config_text(SCOPE + f"target={value}\n")


@pytest.mark.parametrize("value", ["nan", "+inf", "-inf"])
def test_tightening_ratio_requires_a_finite_number(value):
    with pytest.raises(ConfigError, match="tighten_max_jump"):
        load_config_text(f"[crapkit]\ntighten_max_jump={value}\n" + SCOPE)


def test_cli_returns_the_config_error_json_for_an_invalid_numeric_value(tmp_path):
    (tmp_path / "crapkit.toml").write_text('[crapkit]\ntarget="six"\n' + SCOPE)
    result = subprocess.run([sys.executable, "-B", "-m", "crapkit", "inventory", "--json"],
                            cwd=tmp_path, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 3
    assert json.loads(result.stdout)["error"]["exit"] == 3
    assert "target" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("value", ['"false"', '"true"', "0", "1", "[]"])
def test_coverage_exemption_requires_an_actual_boolean(value):
    with pytest.raises(ConfigError, match="coverage_optional"):
        load_config_text(SCOPE + f"coverage_optional={value}\n")


@pytest.mark.parametrize("field", ["full_suite", "container_ok"])
@pytest.mark.parametrize("value", ['"false"', '"true"', "0", "1", "[]"])
def test_lane_boolean_options_reject_coercion(field, value):
    with pytest.raises(ConfigError, match=field):
        load_config_text(SCOPE + LANE + f"{field}={value}\n")


@pytest.mark.parametrize("value", [False, True])
def test_scope_and_lane_booleans_preserve_the_declared_value(value):
    literal = str(value).lower()
    cfg = load_config_text(SCOPE + f"coverage_optional={literal}\n" + LANE
                           + f"full_suite={literal}\ncontainer_ok={literal}\n")
    assert cfg.scopes[0].coverage_optional is value
    assert cfg.lanes[0].full_suite is value
    assert cfg.lanes[0].container_ok is value


def test_quoted_false_cannot_create_a_trusted_coverage_exemption(tmp_path):
    (tmp_path / "crapkit.toml").write_text(SCOPE + 'coverage_optional="false"\n')
    result = subprocess.run([sys.executable, "-B", "-m", "crapkit", "coverage", "--json"],
                            cwd=tmp_path, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 3
    assert "coverage_optional" in json.loads(result.stdout)["error"]["message"]
    assert not (tmp_path / ".crapkit" / "crap.sqlite").exists()
