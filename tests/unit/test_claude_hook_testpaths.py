"""The advisory hook loads a lane whose positional is pytest's own `testpaths`.

The hook parses crapkit.toml on its own (`claude_hook._config`), not through
`cli._shared`, so the root it found has to reach the loader there too. Without
it the full-suite guard reads `pytest tests` beside `testpaths = ["tests"]` as
narrowing, the ConfigError lands in the hook's catch-all, and an over-ceiling
edit in that repo draws exit 0 and silence: the one repo shape every other
command accepts was the one the hook stayed quiet in.
"""
import argparse
import io
import json
from pathlib import Path

from crapkit.cli.claude_hook import cmd_claude_hook

PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
TOML = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest tests -q --cov=pylib --cov-report=json:.crapkit/cov/py.json"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["py"]
"""
# Seven ifs: ccn 8, over the ceiling of 6 the configuration sets.
SPRAWL = ("def sprawl(n):\n"
          + "".join(f"    if n > {i}:\n        return {i}\n" for i in range(1, 8))
          + "    return 0\n")


def _repo(root: Path, *, testpaths: bool) -> Path:
    """A measured repo with one edited, untracked file over the ceiling. No git
    is needed: the hook judges an untracked file in full."""
    (root / "pylib").mkdir()
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    if testpaths:
        (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    edited = root / "pylib" / "hot.py"
    edited.write_text(SPRAWL, encoding="utf-8")
    return edited


def _event(edited: Path) -> str:
    return json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                       "cwd": str(edited.parent),
                       "tool_input": {"file_path": str(edited)}})


def _hook(edited: Path, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(_event(edited)))
    return cmd_claude_hook(argparse.Namespace(protocol="1"))


def test_an_edit_beside_a_lane_naming_the_configured_testpaths_is_advised(
        tmp_path, monkeypatch, capsys):
    edited = _repo(tmp_path, testpaths=True)

    code = _hook(edited, monkeypatch)

    assert code == 2
    assert ("crapkit advisory: 1 function(s) over ceiling 6 in pylib/hot.py"
            in capsys.readouterr().err)


def test_the_same_edit_is_silent_where_no_testpaths_names_the_positional(
        tmp_path, monkeypatch, capsys):
    """The control, and the hook's standing contract: a configuration crapkit
    refuses is a rung that fails to advance, exit 0 and nothing said."""
    edited = _repo(tmp_path, testpaths=False)

    code = _hook(edited, monkeypatch)

    assert code == 0
    assert capsys.readouterr().err == ""
