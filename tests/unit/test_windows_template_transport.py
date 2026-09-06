"""Template values and existing command text keep their Windows argv meaning."""
import json
import os
from pathlib import Path
import sys

import pytest

from crapkit import procs
from crapkit.errors import ToolError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="cmd.exe argument transport")


def _capture(root, filename="capture.py"):
    script = root / filename
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path('arguments.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "print(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    return f'"{sys.executable}" -B "{script}"'


def _run(root, template, values):
    command, additions = procs.prepare_template(template, values)
    env = {**os.environ, "CRAPKIT_STATIC": "expanded static",
           "CRAPKIT_STATIC_WITH_BANG": "!CRAPKIT_STATIC!", **additions}
    assert procs.run_bounded(command, 5, cwd=root, env=env) == 0, command
    return json.loads((root / "arguments.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("argument,expected", [
    ('"!CRAPKIT_STATIC!"', "!CRAPKIT_STATIC!"),
    ('"unpaired!bang"', "unpaired!bang"),
    ('"%CRAPKIT_STATIC%"', "expanded static"),
    ('"%CRAPKIT_STATIC_WITH_BANG%"', "!CRAPKIT_STATIC!"),
    ('^!CRAPKIT_STATIC^!', "!CRAPKIT_STATIC!"),
])
def test_active_placeholder_preserves_static_command_argument(tmp_path, argument, expected):
    command = _capture(tmp_path)
    assert _run(tmp_path, command + " " + argument + " {tests}", {"tests": ["case"]}) == [expected, "case"]


def test_active_placeholder_preserves_literal_bang_in_command_path(tmp_path):
    command = _capture(tmp_path, "capture!CRAPKIT_STATIC!.py")
    assert _run(tmp_path, command + " {tests}", {"tests": ["case"]}) == ["case"]


@pytest.mark.parametrize("arguments", [[], [""]])
def test_empty_placeholder_and_empty_argument_remain_distinct(tmp_path, arguments):
    command = _capture(tmp_path)
    assert _run(tmp_path, command + " {tests}", {"tests": arguments}) == arguments


def test_multiline_argument_preserves_a_literal_bang_path(tmp_path):
    command = _capture(tmp_path, "capture!CRAPKIT_STATIC!.py")
    arguments = ["line1\nline2", "carriage\rreturn"]
    assert _run(tmp_path, command + ' "!CRAPKIT_STATIC!" {tests}', {"tests": arguments}) == ["!CRAPKIT_STATIC!", *arguments]


def test_multiline_arguments_with_static_percent_expansion_refuse_before_execution():
    with pytest.raises(ToolError, match="multiline arguments with percent"):
        procs.prepare_template('runner "%CRAPKIT_STATIC%" {tests}', {"tests": ["line1\nline2"]})


def test_placeholder_arguments_keep_shell_characters_as_data(tmp_path):
    command = _capture(tmp_path)
    arguments = ['!CRAPKIT_STATIC!', '%CRAPKIT_STATIC%', 'a" & echo wrong | ^ <>', '{files}', 'a b', 'trail\\',
                 '', 'Jos\u00e9', 'tab\there', 'line1\nline2', '\\"quoted\\"', '%%', '^!^!']
    assert _run(tmp_path, command + " {tests}", {"tests": arguments}) == arguments


def test_active_placeholder_preserves_command_chaining_and_redirection(tmp_path):
    command = _capture(tmp_path)
    template = command + ' "!CRAPKIT_STATIC!" {tests} > captured.txt && echo completed>marker.txt'
    assert _run(tmp_path, template, {"tests": ["case"]}) == ["!CRAPKIT_STATIC!", "case"]
    assert (tmp_path / "marker.txt").read_text().strip() == "completed"
    assert json.loads((tmp_path / "captured.txt").read_text()) == ["!CRAPKIT_STATIC!", "case"]


def test_active_placeholder_preserves_a_shell_pipe(tmp_path):
    command = _capture(tmp_path)
    receiver = tmp_path / "receiver.py"
    receiver.write_text("import sys\nfrom pathlib import Path\nPath('piped.txt').write_text(sys.stdin.read())\n", encoding="utf-8")
    template = command + ' "!CRAPKIT_STATIC!" {tests} | ' + f'"{sys.executable}" -B "{receiver}"'
    arguments = ['!CRAPKIT_STATIC!', '%CRAPKIT_STATIC%', 'a&echo', 'x|y', 'r>file', 'a"b']
    assert _run(tmp_path, template, {"tests": arguments}) == ["!CRAPKIT_STATIC!", *arguments]
    assert json.loads((tmp_path / "piped.txt").read_text()) == ["!CRAPKIT_STATIC!", *arguments]
