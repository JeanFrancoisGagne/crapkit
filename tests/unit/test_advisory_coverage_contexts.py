"""The advisory's store guard must not intercept coverage's SQLite database."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_advisory_and_following_test_pass_under_configured_contexts(tmp_path):
    config = tmp_path / ".coveragerc"
    config.write_text("[run]\nbranch = true\ndynamic_context = test_function\n"
                      "[json]\nshow_contexts = true\n")
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("COVERAGE_", "COV_CORE_")):
            environment.pop(key)
    environment["COVERAGE_FILE"] = str(tmp_path / ".coverage")
    evidence = tmp_path / "coverage.json"
    advisory = ROOT / "tests/unit/test_ratchet_reader_identity.py"
    following = ROOT / "tests/unit/test_ratchet_finite_admission.py"
    result = subprocess.run([
        sys.executable, "-B", "-m", "pytest",
        str(advisory) + "::test_advisory_warns_when_the_saved_mark_has_no_proved_callback_identity",
        str(following) + "::test_existing_finite_mark_representations_still_load[6.125]",
        "-o", "addopts=", "-q", "-p", "no:randomly", "--cov=crapkit",
        "--cov-config=" + str(config), "--cov-report=json:" + str(evidence)],
        cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout
    data = json.loads(evidence.read_text())
    contexts = {context for measured in data["files"].values()
                for lines in measured["contexts"].values() for context in lines}
    assert any("test_advisory_warns" in context for context in contexts)
    assert any("test_existing_finite_mark" in context for context in contexts)
