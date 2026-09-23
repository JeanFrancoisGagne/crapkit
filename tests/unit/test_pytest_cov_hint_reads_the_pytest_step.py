"""The missing pytest-cov hint names the python heading the step that runs pytest.

lanes.py and doctor each kept a copy of "which python runs pytest". doctor's
took the head of the step that runs pytest; the lane's also demanded that the
head be the command's first word. So for `cd web && python -m pytest --cov=src`
doctor named `python`, and the hint after the lane failed named no interpreter
at all: "the environment the lane's suite runs in", with no install line.
"""
import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane

_NO_COV = "pytest: error: unrecognized arguments: --cov=src --cov-branch"


def _hint(tmp_path, command: str) -> str:
    """The refusal a reused lane raises over a log whose run pytest rejected."""
    log = tmp_path / ".crapkit" / "lane-py.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"$ {command}\n{_NO_COV}\n(exit 4)\n", encoding="utf-8")
    lane = Lane(name="py", command=command, artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("src",))
    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, lane, reuse_artifact=True)
    return str(raised.value)


@pytest.mark.parametrize("command", ["cd web && python -m pytest --cov=src",
                                     "set X=1 && python -m pytest --cov=src",
                                     "python -m pytest --cov=src"])
def test_the_hint_binds_the_install_to_the_python_running_pytest(tmp_path, command):
    assert "the environment `python` runs in (`python -m pip install pytest-cov`)" in \
        _hint(tmp_path, command)


def test_a_manager_after_the_chain_still_gets_no_install_line(tmp_path):
    """The rule is the head of the pytest step, not whatever python appears in
    it: `uv run python -m pytest` heads on `uv`, and `uv -m pip` is no command."""
    message = _hint(tmp_path, "cd web && uv run python -m pytest --cov=src")

    assert "-m pip install" not in message
    assert "the environment the lane's suite runs in" in message
