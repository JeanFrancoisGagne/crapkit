"""Setup and runtime follow the same installed-pytest configuration contract."""
import pytest

from crapkit.config import pytest_testpaths_at
from crapkit.scaffold import pytest_testpaths


@pytest.mark.parametrize(('files', 'expected'), [
    ({'pytest.ini': '', 'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["later"]'}, ()),
    ({'.pytest.ini': '', 'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["later"]'}, ('later',)),
    ({'.pytest.ini': '[pytest]\ntestpaths = actual', 'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["later"]'}, ('actual',)),
    ({'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = "one two"'}, ('one', 'two')),
    ({'pytest.ini': '[pytest]\ntestpaths = tests_100%'}, ('tests_100%',)),
    ({'tox.ini': '[pytest]\ntestpaths = tests'}, ('tests',)),
])
def test_setup_and_runtime_select_the_same_testpaths(tmp_path, files, expected):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding='utf-8')

    assert pytest_testpaths_at(tmp_path) == expected
    assert pytest_testpaths(files) == expected


PYTEST_FILES = {
    "pytest.ini": "[pytest]\n# café\ntestpaths = tests\n",
    "tox.ini": "[pytest]\n# café\ntestpaths = tests\n",
    "setup.cfg": "[tool:pytest]\n# café\ntestpaths = tests\n",
    "pyproject.toml": '[tool.pytest.ini_options]\n# café\ntestpaths = ["tests"]\n',
}
# How each file's bytes were saved, and the testpaths pytest 8.3 reads from them.
# A byte-order mark reads as none: pytest itself stops on it (`unexpected line:
# '\ufeff[pytest]'`, `Invalid statement (at line 1, column 1)`), so no lane can
# collect by those testpaths. A Latin-1 byte in a comment reads as U+FFFD there.
SAVED = [
    ("utf8", lambda text: text.encode("utf-8"), ("tests",)),
    ("utf8-bom", lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"), ()),
    ("latin1-comment", lambda text: text.encode("latin-1"), ("tests",)),
    ("crlf", lambda text: text.replace("\n", "\r\n").encode("utf-8"), ("tests",)),
]


@pytest.mark.parametrize("save, expected", [row[1:] for row in SAVED], ids=[row[0] for row in SAVED])
@pytest.mark.parametrize("name", list(PYTEST_FILES))
def test_init_and_the_lane_guard_read_a_pytest_file_in_any_encoding_alike(tmp_path, name, save, expected):
    """init (admin._marker_texts) and the lane guard (pytest_testpaths_at) read
    the same bytes through one repotext kind, so they name the same testpaths."""
    from crapkit.cli.admin import _marker_texts

    (tmp_path / name).write_bytes(save(PYTEST_FILES[name]))

    assert pytest_testpaths_at(tmp_path) == expected
    assert pytest_testpaths(_marker_texts(tmp_path)) == expected
