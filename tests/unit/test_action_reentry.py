"""Repeated composite action calls must not inherit earlier verdict evidence."""
import os
import subprocess
from pathlib import Path

from test_action_contract import _bash, _steps


def invocation_directory(tmp_path):
    state = next((step for step in _steps() if step.get("id") == "state"), None)
    if state is None:
        return tmp_path
    output = tmp_path / "outputs"
    output.write_text("", encoding="utf-8")
    env = {**os.environ, "RUNNER_TEMP": tmp_path.as_posix(), "GITHUB_OUTPUT": output.as_posix()}
    subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", state["run"]],
                   env=env, check=True, capture_output=True, text=True)
    return Path(output.read_text(encoding="utf-8").strip().split("=", 1)[1])


def test_a_second_action_call_cannot_reuse_a_previous_base_sha(tmp_path):
    first = invocation_directory(tmp_path)
    (first / "crapkit-base.sha").write_text("old-success", encoding="utf-8")

    second = invocation_directory(tmp_path)

    assert not (second / "crapkit-base.sha").exists()
    assert (first / "crapkit-base.sha").read_text(encoding="utf-8") == "old-success"


def test_every_state_consumer_uses_its_own_action_instance():
    consumers = [step for step in _steps() if "$CRAPKIT_STATE/" in step.get("run", "")]
    assert consumers
    for step in consumers:
        assert step["env"]["CRAPKIT_STATE"] == "${{ steps.state.outputs.directory }}"
