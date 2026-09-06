"""Placeholder values remain arguments, including placeholder-shaped values."""
import shlex
from types import SimpleNamespace

from crapkit import procs


def test_a_command_without_placeholders_keeps_its_shell_behavior(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))
    command = 'runner "!literal!" "{}"'

    assert procs.prepare_template(command, {"files": ["unused"]}) == (command, {})


def test_posix_placeholders_preserve_literals_without_recursive_replacement(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="posix"))
    arguments = ["{files}", 'a" b', "$(echo nope)", "$HOME", "a'b"]
    command, env = procs.prepare_template("runner {tests} {files}",
                                         {"tests": arguments, "files": ["last"]})
    assert shlex.split(command) == ["runner", *arguments, "last"]
    assert env == {}


def test_quoted_placeholder_is_one_literal_argument(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="posix"))
    command, _ = procs.prepare_template('runner -t "{names}"',
                                       {"names": ["a|b (edge)"]})
    assert shlex.split(command) == ["runner", "-t", "a|b (edge)"]


def test_empty_file_list_adds_no_argument(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="posix"))
    command, _ = procs.prepare_template("runner {files}", {"files": []})
    assert shlex.split(command) == ["runner"]
