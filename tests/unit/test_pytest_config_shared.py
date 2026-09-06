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
