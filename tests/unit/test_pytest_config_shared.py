"""Setup and runtime follow the same installed-pytest configuration contract."""
import codecs
import subprocess
import sys

import pytest

from crapkit.cli import admin
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



# Each pytest config file saved with a byte-order mark, and the words pytest
# stops with when it refuses that file. pytest reads the four INI files through
# iniconfig, which reads past the mark from 2.3.1 on and stops on it before:
# the mark makes the first line no section header, and the colon in
# `[tool:pytest]` makes setup.cfg's a key outside any section. It reads
# pyproject.toml through tomllib, which stops on the mark in every release.
# The installed pytest is the oracle for each row: init and the lane reader
# must name the testpaths pytest runs whenever pytest reads the file.
INI = "[pytest]\ntestpaths = tests\n"
_CONFIGS = [("pytest.ini", INI, "unexpected line"),
            (".pytest.ini", INI, "unexpected line"),
            ("tox.ini", INI, "unexpected line"),
            ("setup.cfg", "[tool:pytest]\ntestpaths = tests\n", "no section header defined"),
            ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
             "Invalid statement")]
_CONFIG_IDS = ["pytest-ini", "dot-pytest-ini", "tox-ini", "setup-cfg", "pyproject"]


def _collect(root) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider",
                           "-p", "no:randomly"], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _suite(root, name: str, raw: bytes) -> None:
    """A test under tests/, which `testpaths = tests` names, and one under
    other/, which only a run that ignores testpaths collects."""
    for folder, test in (("tests", "test_a"), ("other", "test_b")):
        (root / folder).mkdir()
        (root / folder / f"{test}.py").write_text(f"def {test}():\n    pass\n", encoding="utf-8")
    (root / name).write_bytes(raw)


def _ran_testpaths(ran: subprocess.CompletedProcess) -> bool:
    """pytest read the file and collected what `testpaths = tests` names, and
    nothing else."""
    return (ran.returncode == 0 and "tests/test_a.py::test_a" in ran.stdout
            and "test_b" not in ran.stdout)


@pytest.mark.parametrize("name, text, refusal", _CONFIGS, ids=_CONFIG_IDS)
def test_a_bom_config_names_the_testpaths_pytest_runs_whenever_pytest_reads_it(tmp_path, name, text,
                                                                              refusal):
    _suite(tmp_path, name, codecs.BOM_UTF8 + text.encode("utf-8"))

    ran = _collect(tmp_path)

    if not _ran_testpaths(ran):
        assert ran.returncode != 0 and refusal in ran.stderr + ran.stdout, ran
        return
    assert pytest_testpaths_at(tmp_path) == ("tests",)
    assert pytest_testpaths(admin._marker_texts(tmp_path)) == ("tests",)


@pytest.mark.parametrize("name, text, refusal", _CONFIGS, ids=_CONFIG_IDS)
def test_the_same_config_without_the_bom_names_the_testpaths_pytest_runs(tmp_path, name, text,
                                                                        refusal):
    _suite(tmp_path, name, text.encode("utf-8"))

    ran = _collect(tmp_path)

    assert _ran_testpaths(ran), ran
    assert pytest_testpaths_at(tmp_path) == ("tests",)
    assert pytest_testpaths(admin._marker_texts(tmp_path)) == ("tests",)


PYTEST_FILES = {
    "pytest.ini": "[pytest]\n# café\ntestpaths = tests\n",
    ".pytest.ini": "[pytest]\n# café\ntestpaths = tests\n",
    "tox.ini": "[pytest]\n# café\ntestpaths = tests\n",
    "setup.cfg": "[tool:pytest]\n# café\ntestpaths = tests\n",
    "pyproject.toml": '[tool.pytest.ini_options]\n# café\ntestpaths = ["tests"]\n',
}
EVERY_FILE_READS_TESTS = dict.fromkeys(PYTEST_FILES, ("tests",))
# How each file's bytes were saved, and the testpaths crapkit reads from each
# file. A byte-order mark is read past in the four INI files, as pytest reads
# them with iniconfig 2.3.1 or newer, and keeps pyproject.toml from naming any:
# tomllib stops on it (`Invalid statement (at line 1, column 1)`), so pytest
# refuses that file and no lane collects by its testpaths. Under an older
# iniconfig pytest refuses all five, and a lane on one fails at pytest whatever
# crapkit read. A Latin-1 byte in a comment reads as U+FFFD.
SAVED = [
    ("utf8", lambda text: text.encode("utf-8"), EVERY_FILE_READS_TESTS),
    ("utf8-bom", lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"),
     {**EVERY_FILE_READS_TESTS, "pyproject.toml": ()}),
    ("latin1-comment", lambda text: text.encode("latin-1"), EVERY_FILE_READS_TESTS),
    ("crlf", lambda text: text.replace("\n", "\r\n").encode("utf-8"), EVERY_FILE_READS_TESTS),
]


@pytest.mark.parametrize("save, expected", [row[1:] for row in SAVED], ids=[row[0] for row in SAVED])
@pytest.mark.parametrize("name", list(PYTEST_FILES))
def test_init_and_the_lane_guard_read_a_pytest_file_in_any_encoding_alike(tmp_path, name, save, expected):
    """init (admin._marker_texts) and the lane guard (pytest_testpaths_at) read
    the same bytes through one decode, so they name the same testpaths."""
    from crapkit.cli.admin import _marker_texts

    (tmp_path / name).write_bytes(save(PYTEST_FILES[name]))

    assert pytest_testpaths_at(tmp_path) == expected[name]
    assert pytest_testpaths(_marker_texts(tmp_path)) == expected[name]
