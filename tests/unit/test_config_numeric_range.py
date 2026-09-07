"""Float consumers reject overflow without narrowing integer consumers."""
import json
import subprocess
import sys

import pytest

from crapkit.config import load_config_text
from crapkit.errors import ConfigError


SCOPE = '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
HUGE = "1" + "0" * 400


def test_a_factor_that_cannot_fit_a_finite_float_is_a_config_error():
    with pytest.raises(ConfigError, match="tighten_max_jump"):
        load_config_text(f"[crapkit]\ntighten_max_jump={HUGE}\n" + SCOPE)


def test_inventory_returns_config_error_json_before_writing_a_snapshot(tmp_path):
    (tmp_path / "crapkit.toml").write_text(
        f"[crapkit]\ntighten_max_jump={HUGE}\n" + SCOPE, encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "crapkit", "inventory", "--json"],
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "tighten_max_jump" in json.loads(result.stdout)["error"]["message"]
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".crapkit/crap.sqlite").exists()


def test_large_integer_file_limits_keep_their_exact_value():
    config = load_config_text(f"[exclude]\nmax_file_bytes={HUGE}\n" + SCOPE)
    assert type(config.max_file_bytes) is int
    assert config.max_file_bytes == int(HUGE)


@pytest.mark.parametrize("literal", ["1", "1.0", "1e308", repr(sys.float_info.max)])
def test_representable_factors_remain_supported(literal):
    config = load_config_text(f"[crapkit]\ntighten_max_jump={literal}\n" + SCOPE)
    assert config.tighten_max_jump == float(literal)
