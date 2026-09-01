"""The spelling every next-step and refusal uses for crapkit itself.

README tells a reader working from a source checkout to run `python -m crapkit`,
and the pre-commit hook it documents spells the same form with an absolute
interpreter path because git runs hooks outside the activated venv. Neither of
those environments puts a `crapkit` on PATH. Both used to be answered with "run
`crapkit coverage`", so `init` finished by naming a command the shell it just
ran in exits 127 on.

The process knows how it was started. `sys.argv[0]` is the console script when
that is what launched it and the `__main__.py` inside the package when
`python -m` did, so the message can name the form that resolves.
"""
import sys
from pathlib import Path

import pytest

from crapkit.errors import CrapkitError
from crapkit.invocation import _self

MODULE_RUN = str(Path(sys.prefix) / "Lib" / "site-packages" / "crapkit" / "__main__.py")
CONSOLE_RUN = str(Path(sys.prefix) / "Scripts" / "crapkit.exe")


@pytest.fixture()
def as_module(monkeypatch):
    """argv as `python -m crapkit` leaves it: the package's own __main__.py."""
    monkeypatch.setattr(sys, "argv", [MODULE_RUN, "coverage"])


@pytest.fixture()
def as_console(monkeypatch):
    """argv as the installed console script leaves it."""
    monkeypatch.setattr(sys, "argv", [CONSOLE_RUN, "coverage"])


# --- the helper itself -------------------------------------------------------

def test_the_console_script_names_itself(as_console):
    assert _self() == "crapkit"


def test_a_module_run_names_the_interpreter_that_is_running_it(as_module):
    """Not bare `python`: the reader may have no activated venv (the hook case),
    and the interpreter running this process is the one crapkit is installed in."""
    assert _self().endswith(" -m crapkit")
    assert sys.executable in _self()


def test_an_interpreter_path_holding_a_space_is_quoted(monkeypatch):
    r"""`C:\Program Files\Python311\python.exe` is an ordinary Windows install,
    and unquoted it reaches cmd.exe as `C:\Program` plus two arguments."""
    monkeypatch.setattr(sys, "argv", [MODULE_RUN])
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Python311\python.exe")

    assert _self() == r'"C:\Program Files\Python311\python.exe" -m crapkit'


def test_an_interpreter_path_without_a_space_is_left_bare(monkeypatch):
    monkeypatch.setattr(sys, "argv", [MODULE_RUN])
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

    assert _self() == "/usr/bin/python3 -m crapkit"


def test_an_empty_argv_falls_back_to_the_module_form(monkeypatch):
    """An embedded interpreter leaves argv empty. Nothing put a console script
    on PATH there either, so the module form is the honest answer."""
    monkeypatch.setattr(sys, "argv", [])

    assert _self().endswith(" -m crapkit")


# --- the messages ------------------------------------------------------------

def _init_next_step(_tmp):
    from crapkit.cli.admin import _next_step
    return _next_step({"src": ("python",)}, ())


def _queue_refusal(tmp):
    from crapkit.cli.queue import _scored_store
    with pytest.raises(CrapkitError) as raised:
        _scored_store(tmp)
    return str(raised.value)


def _verify_refusal(tmp):
    from crapkit.cli.verifying import _no_baseline
    return _no_baseline(tmp)


MESSAGES = pytest.mark.parametrize("message", [_init_next_step, _queue_refusal, _verify_refusal],
                                   ids=["init-next-step", "queue-refusal", "verify-refusal"])


@MESSAGES
def test_the_console_script_run_prescribes_the_console_script(message, tmp_path, as_console):
    assert "`crapkit coverage`" in message(tmp_path)


@MESSAGES
def test_the_module_run_prescribes_the_interpreter_that_is_running_it(message, tmp_path, as_module):
    """The red loop: with the venv's Scripts dir off PATH, every one of these
    lines named `crapkit coverage`, and the shell answered 127."""
    text = message(tmp_path)

    assert f"`{_self()} coverage`" in text
    assert "`crapkit coverage`" not in text
