"""How a lane command reads: the step that runs pytest, and the python heading it.

lanes.py names that python in its missing pytest-cov hint, and doctor asks it
whether pytest-cov imports. Both read the command through lane_command, so the
rules are pinned here once rather than through either caller's privates.
"""
import pytest

from crapkit.lane_command import first_word, is_python, pytest_head, pytest_python, pytest_step


# --- only a python running pytest can be asked about pytest_cov ---------------
#
# The probe assumed the first word is an interpreter. `coverage run -m pytest
# --cov=pylib && coverage json` starts with `coverage`, so it shelled
# `coverage -c "import pytest_cov"`, read coverage's own argument error as a
# missing package, and printed the pip note where pytest_cov imports fine.

@pytest.mark.parametrize("command, python", [
    ("python -m pytest --cov", "python"),
    ("python3 -m pytest --cov", "python3"),
    ("py -3 -m pytest --cov", "py"),
    ("/usr/bin/python3.12 -m pytest --cov", "/usr/bin/python3.12"),
    ('"C:/Program Files/Python311/python.exe" -m pytest --cov', "C:/Program Files/Python311/python.exe"),
    ("npm run build && python -m pytest --cov", "python"),
    ("cd web && python -m pytest --cov=src", "python"),
    ("set X=1 && python -m pytest --cov=src", "python"),
    ("coverage run -m pytest --cov=pylib && coverage json", None),
    ("tox -e py311 -- --cov", None),  # no pytest on the line at all
    ("npx vitest run --coverage", None),
    ("uv run python -m pytest --cov", None),
    ("pytest --cov=src", None),
])
def test_only_a_python_heading_the_pytest_step_is_named(command, python):
    assert pytest_python(command) == python


def test_the_pytest_step_is_the_segment_holding_pytest():
    assert pytest_step("npm run build && python -m pytest --cov && coverage json") == \
        ["python", "-m", "pytest", "--cov"]
    assert pytest_step("npx vitest run --coverage") == []


@pytest.mark.parametrize("command, head", [
    ("cd pkg && uv run python -m pytest --cov", "uv"),
    ("coverage run -m pytest --cov=pylib", "coverage"),
    ("npx vitest run --coverage", "npx"),
])
def test_the_head_is_the_word_in_front_of_pytest_or_else_the_first_word(command, head):
    assert pytest_head(command) == head


def test_a_quoted_interpreter_path_is_one_first_word():
    assert first_word('"C:/Program Files/py/python.exe" -m pytest') == "C:/Program Files/py/python.exe"
    assert first_word("") == ""


@pytest.mark.parametrize("word, python", [
    ("python", True), ("python3", True), ("py", True), ("python3.12", True),
    ("C:/Program Files/Python311/python.exe", True), (".venv/bin/python", True),
    ("pytest", False), ("uv", False), ("coverage", False), ("pypy", False),
])
def test_a_python_is_named_by_the_last_segment_of_the_word(word, python):
    assert is_python(word) is python
