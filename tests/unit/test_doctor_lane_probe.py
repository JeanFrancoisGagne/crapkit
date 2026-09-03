"""`doctor` repeats init's first-run lane probe and judges the {files} template.

init printed a note when a lane's python could not import pytest_cov, and
doctor then called the same config clean. Doctor now FAILs with that sentence,
prints the interpreter and plugin versions a healthy lane resolves to, WARNs
when that interpreter is not the one running doctor, and FAILs a `{files}`
template on a scope that holds no test file, which is the template that hands
the runner a source path and collects nothing.
"""
import os
import sys
from pathlib import Path

import pytest

from crapkit.cli import admin
from crapkit.config import Config, Lane, Scope
from crapkit.doctor import files_template_gaps


def _lane(name: str = "py", command: str = "python -m pytest --cov", parser: str = "coveragepy") -> Lane:
    return Lane(name=name, command=command, artifact=f"{name}.json", parser=parser,
                scopes=("pkg",), results_artifact=f"{name}-junit.xml")


@pytest.fixture(autouse=True)
def _forget_probed_words():
    admin._runner_report.cache_clear()
    yield
    admin._runner_report.cache_clear()


# --- the {files} template on a scope holding no test file ----------------------

def test_a_files_template_on_a_scope_with_no_test_file_fails():
    gaps = files_template_gaps((("pkg", "python -m pytest {files} -q"),),
                               {"pkg": ("pkg",)}, ["pkg/x.py", "tests/test_x.py"])

    assert [f.level for f in gaps] == ["FAIL"]
    assert "'pkg'" in gaps[0].text and "{files}" in gaps[0].text
    assert "whole suite" in gaps[0].text, "the fix is the form without {files}"


def test_a_files_template_on_a_scope_holding_its_tests_is_fine():
    assert files_template_gaps((("pkg", "python -m pytest {files} -q"),),
                               {"pkg": ("pkg",)}, ["pkg/x.py", "pkg/test_x.py"]) == ()


def test_a_template_without_files_is_never_judged_here():
    assert files_template_gaps((("pkg", "python -m pytest -q"),),
                               {"pkg": ("pkg",)}, ["pkg/x.py"]) == ()


def test_a_template_for_an_undeclared_scope_is_not_this_check_s_business():
    assert files_template_gaps((("ghost", "a {files}"),), {"pkg": ("pkg",)}, ["pkg/x.py"]) == ()


def test_gaps_come_out_in_scope_name_order():
    templates = (("web", "b {files}"), ("api", "a {files}"))

    gaps = files_template_gaps(templates, {"api": ("api",), "web": ("web",)}, ["api/a.py"])

    assert [f.text.split("'")[1] for f in gaps] == ["api", "web"]


# --- init's note, repeated by doctor -------------------------------------------

def _cfg(*lanes: Lane) -> Config:
    return Config(target=6, scopes=(Scope(name="pkg", paths=("pkg",), languages=("python",)),),
                  exclude_globs=(), lanes=lanes)


def test_doctor_fails_with_inits_sentence_when_the_lane_cannot_import_pytest_cov(monkeypatch):
    monkeypatch.setattr(admin, "_runner_report", lambda word: None)
    monkeypatch.setattr(admin, "_lane_first_run_note",
                        lambda lane: "note: lane 'py' names `python`, which cannot import pytest_cov")

    findings = admin._doctor_lane_probes([_lane()])

    assert [(f.level, f.text) for f in findings] == [
        ("FAIL", "lane 'py' names `python`, which cannot import pytest_cov")]


def test_a_healthy_lane_costs_one_interpreter_start_not_two(monkeypatch):
    """The version report imports pytest_cov on its way, so it answers the
    first-run question too; asking init's probe as well started the same
    interpreter twice per lane per doctor."""
    def boom(lane):
        raise AssertionError("the first-run note must not be asked once the report answered")

    monkeypatch.setattr(admin, "_runner_report", lambda word: (sys.executable, "8.3.3", "7.1.0"))
    monkeypatch.setattr(admin, "_lane_first_run_note", boom)

    assert [f.level for f in admin._doctor_lane_probes([_lane()])] == ["ok"]


def test_a_healthy_lane_prints_the_interpreter_and_plugin_versions_it_resolves_to(monkeypatch):
    monkeypatch.setattr(admin, "_lane_first_run_note", lambda lane: None)
    monkeypatch.setattr(admin, "_runner_report", lambda word: (sys.executable, "8.3.3", "7.1.0"))

    findings = admin._doctor_lane_probes([_lane()])

    assert [f.level for f in findings] == ["ok"]
    assert findings[0].text == (f"lane 'py': python -> {sys.executable} "
                                "(pytest 8.3.3, pytest-cov 7.1.0)")


def test_a_lane_running_another_python_than_this_doctor_warns(monkeypatch):
    monkeypatch.setattr(admin, "_lane_first_run_note", lambda lane: None)
    monkeypatch.setattr(admin, "_runner_report",
                        lambda word: ("/srv/venv/bin/python", "8.3.3", "7.1.0"))

    findings = admin._doctor_lane_probes([_lane()])

    assert [f.level for f in findings] == ["ok", "WARN"]
    assert "/srv/venv/bin/python" in findings[1].text
    assert sys.executable in findings[1].text, "name both, so the reader knows which is which"


def test_only_a_cov_flagged_coveragepy_lane_is_probed(monkeypatch):
    def boom(lane):
        raise AssertionError("this lane must not be probed")

    monkeypatch.setattr(admin, "_lane_first_run_note", boom)

    assert admin._doctor_lane_probes([_lane(command="npx vitest run --coverage", parser="istanbul"),
                                      _lane(command="python -m pytest")]) == []


def test_a_stub_interpreter_with_no_version_to_print_is_no_finding(monkeypatch):
    """A python that runs and answers neither probe is not a finding: nothing
    is known about it either way."""
    monkeypatch.setattr(admin, "_lane_first_run_note", lambda lane: None)
    monkeypatch.setattr(admin, "_runner_report", lambda word: None)

    assert admin._doctor_lane_probes([_lane()]) == []


def test_a_manager_headed_lane_gets_a_note_that_it_was_not_probed(monkeypatch):
    """`uv run python -m pytest --cov` names no python doctor can ask without
    provisioning the environment. Silence there read the same as "probed and
    healthy"; the note says which one this is."""
    def boom(word):
        raise AssertionError("a manager-headed lane must not be probed")

    monkeypatch.setattr(admin, "_runner_report", boom)

    findings = admin._doctor_lane_probes([_lane(command="uv run python -m pytest --cov")])

    assert [f.level for f in findings] == ["note"]
    assert findings[0].text.startswith("lane 'py' runs pytest through `uv`"), findings[0].text
    assert "not probed" in findings[0].text
    assert "coverage" in findings[0].text, "name the command that will answer instead"


def test_the_note_names_the_head_of_the_segment_that_runs_pytest(monkeypatch):
    """A lane that chains steps runs pytest after `&&`; the word to name is
    the one in front of pytest, not the command's first word."""
    monkeypatch.setattr(admin, "_runner_report", lambda word: None)

    (note,) = admin._doctor_lane_probes([_lane(command="cd pkg && uv run python -m pytest --cov")])

    assert note.text.startswith("lane 'py' runs pytest through `uv`"), note.text


def test_the_real_probe_answers_for_this_interpreter():
    # The parsed word, never the shell's spelling of it: shell_words strips the quotes
    # a lane command carries, and _runner_report quotes for the shell it runs under.
    # Handing it a pre-quoted path passed under cmd.exe and failed under sh, where
    # shlex.quote wrapped the quotes into the program name (CI, 2026-09-03).
    report = admin._runner_report(sys.executable)

    assert report is not None
    assert report[0].lower() == sys.executable.lower()
    assert report[1] == pytest.__version__


def _linked(base: Path, target: Path) -> str:
    """A second name for the same file: what a venv's bin/python is to the base
    interpreter on POSIX, without needing a symlink privilege here."""
    base.parent.mkdir(parents=True, exist_ok=True)
    os.link(target, base)
    return str(base)


def test_a_venv_whose_python_links_to_the_doctors_binary_is_another_environment(monkeypatch, tmp_path):
    """On POSIX `python -m venv` symlinks bin/python to the base interpreter,
    so the two executables are one file and two environments: a package
    installed in the venv is invisible to the base python."""
    base = tmp_path / "base" / "python"
    base.parent.mkdir()
    base.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(base))
    venv_python = _linked(tmp_path / "venv" / "bin" / "python", base)

    findings = admin._foreign_interpreter("py", venv_python)

    assert [f.level for f in findings] == ["WARN"], findings
    assert findings[0].text.startswith(f"lane 'py' runs {venv_python}, not the python running this doctor")


def test_a_second_name_beside_the_doctors_binary_is_the_same_environment(monkeypatch, tmp_path):
    """`python3.12` next to `python` in one bin is one install, link or not."""
    base = tmp_path / "base" / "python"
    base.parent.mkdir()
    base.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(base))
    sibling = _linked(tmp_path / "base" / "python3.12", base)

    assert admin._foreign_interpreter("py", sibling) == []


def test_a_lane_with_a_problem_of_its_own_is_not_probed_twice(monkeypatch, tmp_path):
    """The dead-interpreter FAIL already names the word; the first-run note
    would say it again one line down."""
    monkeypatch.setattr(admin, "_lane_first_run_note",
                        lambda lane: "note: lane 'py' names `python`, and the shell cannot run it")

    findings = admin._doctor_lanes(tmp_path, _cfg(_lane(command="no-such-runner-7f3a -m pytest --cov")))

    assert [f.level for f in findings] == ["FAIL"], findings
    assert "does not resolve" in findings[0].text


# --- init's summary names the workspaces it could not route ----------------------

def test_init_says_when_two_workspaces_name_a_runner_and_no_js_lane_was_written(capsys):
    packages = {"": '{"private": true}',
                "api": '{"devDependencies": {"jest": "1"}}',
                "web": '{"devDependencies": {"vitest": "1"}}'}

    admin._print_init_summary({"api": ("typescript",), "web": ("typescript",)}, (), packages)

    out = capsys.readouterr().out
    assert "2 workspaces name a runner (api: jest, web: vitest)" in out
    assert "no js lane was written" in out
    assert "[[lane]]" in out, "the fix is one lane per workspace"


def test_the_summary_is_silent_about_workspaces_once_a_js_lane_was_written(capsys):
    from crapkit.scaffold import LaneSpec

    js = LaneSpec("js", "npm run test -- --coverage", "coverage/coverage-final.json",
                  "istanbul", ("typescript",))
    packages = {"api": '{"devDependencies": {"jest": "1"}}',
                "web": '{"devDependencies": {"vitest": "1"}}'}

    admin._print_init_summary({"api": ("typescript",)}, (js,), packages)

    assert "workspaces name a runner" not in capsys.readouterr().out
