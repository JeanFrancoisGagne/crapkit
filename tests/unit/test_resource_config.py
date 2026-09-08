"""Resource settings use the same admission contract as the CLI and editor."""
import pytest

from crapkit.config import Config, Lane, load_config_text
from crapkit.errors import ConfigError


SCOPE = '\n[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
LANE = '\n[[lane]]\nname="py"\ncommand="run"\nartifact="cov.json"\nparser="coveragepy"\nscopes=["src"]\n'
DEFAULTS = {"analysis_worker_budget": 0, "log_max_bytes": 16777216,
            "test_retention_days": 7, "test_retention_count": 10}


@pytest.mark.parametrize("key,value", DEFAULTS.items())
def test_resource_defaults_are_public_config_values(key, value):
    assert getattr(Config(), key) == value
    assert getattr(load_config_text(SCOPE), key) == value


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("value", [0, 3])
def test_explicit_resource_settings_survive_admission(key, value):
    config = load_config_text(f"[crapkit]\n{key}={value}\n" + SCOPE)
    assert getattr(config, key) == value


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("value", ["-1", "true", "1.5", '"2"'])
def test_invalid_resource_settings_refuse_before_execution(key, value):
    with pytest.raises(ConfigError, match=key):
        load_config_text(f"[crapkit]\n{key}={value}\n" + SCOPE)


@pytest.mark.parametrize("value", [0, 512])
def test_every_lane_carries_the_admitted_global_log_bound(value):
    config = load_config_text(f"[crapkit]\nlog_max_bytes={value}\n" + SCOPE + LANE)
    assert config.lanes[0].log_max_bytes == value


def test_direct_lane_construction_keeps_the_default_log_bound():
    assert Lane("py", "run", "cov.json", "coveragepy", ("src",)).log_max_bytes == 16777216
