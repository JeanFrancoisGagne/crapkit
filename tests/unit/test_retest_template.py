"""Placeholder values remain arguments, including placeholder-shaped values."""
import shlex
from types import SimpleNamespace

import pytest

from crapkit import procs
from crapkit.errors import ToolError


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


@pytest.mark.parametrize("arguments,escaped", [
    (["a&echo"], "^a^&^e^c^h^o"),
    (["x|y"], "^x^|^y"),
    (["r>file"], "^r^>^f^i^l^e"),
    (['a"b'], '^a^\\^"^b'),
    (["two words"], '^"^t^w^o^ ^w^o^r^d^s^"'),
    (["one", "two"], "^o^n^e^ ^t^w^o"),
    (["%VALUE%"], "^%^V^A^L^U^E^%"),
    (["!VALUE!"], "^!^V^A^L^U^E^!"),
    ([""], '^"^"'),
])
def test_windows_percent_arguments_keep_shell_characters_as_data(monkeypatch, arguments, escaped):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))

    assert procs.prepare_template('runner "{tests}"', {"tests": arguments}) == (
        "runner %CRAPKIT_LITERAL_0%", {"CRAPKIT_LITERAL_0": escaped})


def test_windows_empty_list_omits_the_argument_and_environment_value(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))

    assert procs.prepare_template("runner {tests}", {"tests": []}) == ("runner ", {})


def test_windows_single_line_values_preserve_static_expansion_and_operators(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))
    template = 'runner "!STATIC!" "%ROOT%" {tests} > output && echo done | sink'

    assert procs.prepare_template(template, {"tests": ["case"]}) == (
        'runner "!STATIC!" "%ROOT%" %CRAPKIT_LITERAL_0% > output && echo done | sink',
        {"CRAPKIT_LITERAL_0": "^c^a^s^e"})


@pytest.mark.parametrize("arguments,rendered", [
    (["line1\nline2", "two words"], 'line1\nline2 "two words"'),
    (["carriage\rreturn", "two words"], 'carriage\rreturn "two words"'),
])
def test_windows_multiline_values_protect_static_bangs_in_delayed_mode(monkeypatch, arguments, rendered):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))
    template = '"runner!literal!.exe" "!STATIC!" {tests} > output && echo done'

    assert procs.prepare_template(template, {"tests": arguments}) == (
        'cmd /D /V:ON /S /C ""runner!CRAPKIT_LITERAL_BANG!literal!CRAPKIT_LITERAL_BANG!.exe" '
        '"!CRAPKIT_LITERAL_BANG!STATIC!CRAPKIT_LITERAL_BANG!" !CRAPKIT_LITERAL_0! > output && echo done"',
        {"CRAPKIT_LITERAL_0": rendered, "CRAPKIT_LITERAL_BANG": "!"})


@pytest.mark.parametrize("argument", ["line1\nline2", "carriage\rreturn"])
def test_windows_multiline_values_refuse_static_percent_expansion(monkeypatch, argument):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))

    with pytest.raises(ToolError) as error:
        procs.prepare_template('"%RUNNER%" {tests}', {"tests": [argument]})

    assert str(error.value) == (
        "Windows templates cannot combine multiline arguments with percent environment expansion; "
        "use a literal command path for this template")


def test_unused_multiline_values_leave_windows_command_expansion_unchanged(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))
    template = 'runner "!STATIC!" "%ROOT%"'

    assert procs.prepare_template(template, {"unused": ["line1\nline2"]}) == (template, {})


def test_windows_placeholders_have_separate_values_without_recursive_replacement(monkeypatch):
    monkeypatch.setattr(procs, "os", SimpleNamespace(name="nt"))

    assert procs.prepare_template('runner "{tests}" --file \'{files}\'',
                                  {"tests": ["{files}"], "files": ["last"]}) == (
        "runner %CRAPKIT_LITERAL_0% --file %CRAPKIT_LITERAL_1%",
        {"CRAPKIT_LITERAL_0": "^{^f^i^l^e^s^}", "CRAPKIT_LITERAL_1": "^l^a^s^t"})
