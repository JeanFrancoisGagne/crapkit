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
def test_the_hint_binds_the_install_to_the_python_running_pytest(tmp_path, monkeypatch, command):
    """`python` resolves to whatever this machine's PATH holds, so its venv
    kind is pinned; a venv uv made gets the case below."""
    from crapkit import launchers
    monkeypatch.setattr(launchers, "_uv_made", lambda python: False)

    assert "the environment `python` runs in (`python -m pip install pytest-cov`)" in \
        _hint(tmp_path, command)


def test_a_manager_after_the_chain_still_gets_no_install_line(tmp_path):
    """The rule is the head of the pytest step, not whatever python appears in
    it: `uv run python -m pytest` heads on `uv`, and `uv -m pip` is no command."""
    message = _hint(tmp_path, "cd web && uv run python -m pytest --cov=src")

    assert "-m pip install" not in message
    assert "the environment the lane's suite runs in" in message


def test_in_a_venv_uv_made_the_hint_names_uv_pip(tmp_path):
    """uv installs no pip into a venv it makes, so the `.venv/bin/python -m pip
    install pytest-cov` this hint printed failed with "No module named pip" and
    the next `crapkit coverage` failed the same way. doctor's own note already
    names `uv pip install --python` there; the lane's refusal now agrees."""
    from crapkit.invocation import interpreter_word
    from test_doctor_uv_venv_remedy import PYTHON, venv

    python = venv(tmp_path, uv=True)
    word = PYTHON.as_posix()

    message = _hint(tmp_path, f"{word} -m pytest --cov=src")

    assert (f"the environment `{word}` runs in "
            f"(`uv pip install --python {interpreter_word(str(python))} pytest-cov`)"
            in message), message
