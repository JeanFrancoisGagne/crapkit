"""Generated facts stay attached to the code that owns them."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_generated_guidance_and_configuration_schema_are_current():
    result = subprocess.run([sys.executable, str(ROOT / "tools/docs/generate.py"), "--check"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
