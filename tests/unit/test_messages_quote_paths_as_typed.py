r"""A message quotes a path the reader typed the way it was typed.

Ten messages quoted a path from the command line or crapkit.toml with `!r`, and
repr doubles every backslash: `crapkit .\mini` answered about `'.\\mini'`,
doctor named a lane's cwd `'sub\\dir'`, and a Windows reader copied back a
token they never typed. invocation.quoted_path now quotes the value as typed,
and still escapes a character that would break the line.

Each row drives the real command in-process with the Windows spelling and with
the POSIX one. The lane-command rows run on Windows only: on POSIX crapkit
splits a lane command with shell rules, where a backslash is an escape, so the
typed token never reaches the message with its backslashes.
"""
from __future__ import annotations

import os

import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.invocation import quoted_path

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="the typed backslash reaches this message on Windows only")


# --- the helper ----------------------------------------------------------------

@pytest.mark.parametrize("given, shown", [
    (".\\mini", "'.\\mini'"),
    ("./mini", "'./mini'"),
    ("C:\\Program Files\\x", "'C:\\Program Files\\x'"),
    ("pkg/café.py", "'pkg/café.py'"),
    ("a\nb", "'a\\nb'"),
    ("caf\udce9.py", "'caf\\udce9.py'"),
])
def test_a_typed_path_is_quoted_as_typed(given, shown):
    assert quoted_path(given) == shown


def test_a_path_object_is_quoted_as_its_text():
    assert quoted_path(os.path.join("a", "b")) == f"'{os.path.join('a', 'b')}'"


# --- the messages --------------------------------------------------------------

def _toml(root, old: str, new: str) -> None:
    path = root / "crapkit.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _toml_text(value: str) -> str:
    return value.replace("\\", "\\\\")


def _said(capsys, argv: list[str]) -> str:
    main(argv)
    out = capsys.readouterr()
    return out.out + out.err


def _first_argument(root, capsys, typed):
    return _said(capsys, [typed])


def _export(root, capsys, typed):
    return _said(capsys, ["inventory", "--export", typed, "--repo", str(root)])


def _scope_path(root, capsys, typed):
    _toml(root, 'paths = ["src"]', f'paths = ["{_toml_text(typed)}"]')
    return _said(capsys, ["doctor", "--repo", str(root)])


def _lane_cwd(root, capsys, typed):
    _toml(root, 'name = "unit"\n', f'name = "unit"\ncwd = "{_toml_text(typed)}"\n')
    return _said(capsys, ["doctor", "--repo", str(root)])


def _lane_executable(root, capsys, typed):
    _toml(root, 'command = "python -c pass"', f'command = "{_toml_text(typed)} -c pass"')
    return _said(capsys, ["doctor", "--repo", str(root)])


def _lane_script(root, capsys, typed):
    _toml(root, 'command = "python -c pass"', f'command = "python {_toml_text(typed)}"')
    return _said(capsys, ["doctor", "--repo", str(root)])


def _lane_cannot_run(root, capsys, typed):
    (root / "fail.bat").write_text("@exit /b 9009\r\n", encoding="ascii")
    _toml(root, 'command = "python -c pass"', f'command = "{_toml_text(typed)}"')
    return _said(capsys, ["doctor", "--repo", str(root)])


def _input_outside(root, capsys, typed):
    _toml(root, 'name = "unit"\n', f'name = "unit"\ninputs = ["{_toml_text(typed)}"]\n')
    return _said(capsys, ["doctor", "--repo", str(root)])


_input_glob = _input_outside


def _shared_artifact(root, capsys, typed):
    spelled = _toml_text(typed)
    _toml(root, 'artifact = "coverage/unit.json"', f'artifact = "{spelled}"')
    _toml(root, 'artifact = "coverage/ui.json"', f'artifact = "{spelled}"')
    return _said(capsys, ["doctor", "--repo", str(root)])


SITES = [
    # site, driver, Windows spelling, POSIX spelling, marks on the Windows row
    ("parser: a path as the first argument", _first_argument, ".\\mini", "./mini", [WINDOWS]),
    ("_shared: an --export path that climbs out", _export, "..\\out.tsv", "../out.tsv", [WINDOWS]),
    ("config: a scope path with a drive", _scope_path, "C:\\src", "C:/src", []),
    ("admin: a lane cwd that does not exist", _lane_cwd, "sub\\dir", "sub/dir", []),
    ("admin: a lane executable off PATH", _lane_executable, ".venv\\Scripts\\python.exe",
     ".venv/bin/python", [WINDOWS]),
    ("admin: a lane script that does not exist", _lane_script, "scripts\\run.py", "scripts/run.py",
     [WINDOWS]),
    ("admin: a lane the shell cannot run", _lane_cannot_run, ".\\fail.bat", None, [WINDOWS]),
    ("config: an input outside the root", _input_outside, "..\\other", "../other", []),
    ("config: an input that is a glob", _input_glob, "src\\*.ts", "src/*.ts", []),
    ("config: two lanes share an artifact", _shared_artifact, "coverage\\cov.json",
     "coverage/cov.json", []),
]


def _rows():
    for site, driver, windows, posix, marks in SITES:
        yield pytest.param(driver, windows, id=f"{site} (Windows spelling)", marks=marks)
        if posix is not None:
            yield pytest.param(driver, posix, id=f"{site} (POSIX spelling)")


@pytest.mark.parametrize("driver, typed", list(_rows()))
def test_the_message_quotes_the_path_as_typed(repo, capsys, driver, typed):
    said = driver(repo, capsys, typed)

    assert f"'{typed}'" in said, said
    if "\\" in typed:
        assert typed.replace("\\", "\\\\") not in said, said


# --- a path crapkit settled before the message -----------------------------------------

@pytest.mark.parametrize("typed", ["src" + chr(92) + "nothere", "src/nothere"],
                         ids=["Windows spelling", "POSIX spelling"])
def test_an_unmatched_input_is_quoted_the_way_git_reads_it(repo, capsys, typed):
    """config turns an inputs entry's backslash into `/` on every OS before git
    reads it (the scope-path spelling rule), so doctor's unmatched-input line
    names that one spelling and never a doubled backslash."""
    _toml(repo, 'name = "unit"\n', f'name = "unit"\ninputs = ["{_toml_text(typed)}"]\n')
    said = _said(capsys, ["doctor", "--repo", str(repo)])

    assert "inputs entry 'src/nothere' matches no file" in said, said
    assert chr(92) * 2 not in said, said
