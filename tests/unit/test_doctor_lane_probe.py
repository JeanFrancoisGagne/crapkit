"""`doctor` repeats init's first-run lane probe and judges the {files} template.

init printed a note when a lane's python could not import pytest_cov, and
doctor then called the same config clean. Doctor now FAILs with that sentence,
prints the interpreter and plugin versions a healthy lane resolves to, WARNs
when that interpreter is not the one running doctor, and FAILs a `{files}`
template on a scope that holds no test file, which is the template that hands
the runner a source path and collects nothing.
"""
import json
import os
import sys
from pathlib import Path

import coverage
import pytest

from cli_inproc_repo import commit_all, repo, template_repo  # noqa: F401

from crapkit.cli import admin, main
from crapkit.config import Config, Lane, Scope
from crapkit.lane_command import LaunchSpec
from crapkit.doctor import files_template_gaps
from crapkit.scaffold import npm_package


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
    monkeypatch.setattr(admin, "_runner_report", lambda word, spec: None)
    monkeypatch.setattr(admin, "_lane_first_run_note",
                        lambda spec, lane: "note: lane 'py' names `python`, which cannot import pytest_cov")

    findings = admin._doctor_lane_probes(Path.cwd(), [_lane()])

    assert [(f.level, f.text) for f in findings] == [
        ("FAIL", "lane 'py' names `python`, which cannot import pytest_cov")]


def test_a_healthy_lane_costs_one_interpreter_start_not_two(monkeypatch):
    """The version report imports pytest_cov on its way, so it answers the
    first-run question too; asking init's probe as well started the same
    interpreter twice per lane per doctor."""
    def boom(spec, lane):
        raise AssertionError("the first-run note must not be asked once the report answered")

    monkeypatch.setattr(admin, "_runner_report", lambda word, spec: (sys.executable, "8.3.3", "7.1.0", "7.16.0"))
    monkeypatch.setattr(admin, "_lane_first_run_note", boom)

    assert [f.level for f in admin._doctor_lane_probes(Path.cwd(), [_lane()])] == ["ok"]


def test_a_healthy_lane_prints_the_interpreter_and_plugin_versions_it_resolves_to(monkeypatch):
    monkeypatch.setattr(admin, "_lane_first_run_note", lambda spec, lane: None)
    monkeypatch.setattr(admin, "_runner_report", lambda word, spec: (sys.executable, "8.3.3", "7.1.0", "7.16.0"))

    findings = admin._doctor_lane_probes(Path.cwd(), [_lane()])

    assert [f.level for f in findings] == ["ok"]
    assert findings[0].text == (f"lane 'py': python -> {sys.executable} "
                                "(pytest 8.3.3, pytest-cov 7.1.0, coverage 7.16.0)")


# coverage.py writes each function's start_line from 7.13.1; 7.6.0 to 7.13.0
# write regions without it, and older releases write no regions at all.
_FLOOR_INSTALL = {False: '{python} -P -m pip install "coverage>=7.13.1"',
                  True: 'uv pip install --python {python} "coverage>=7.13.1"'}


@pytest.mark.parametrize("uv_made", [False, True], ids=["pip-venv", "uv-venv"])
@pytest.mark.parametrize("version", ["7.4.4", "7.5.4", "6.5.0", "7.6.0", "7.10.6", "7.13.0"])
def test_a_lane_whose_coverage_writes_no_function_regions_fails_naming_the_floor(monkeypatch,
                                                                                version, uv_made):
    """A repo that pins coverage 7.4 in its dev requirements passed doctor, and
    then `crapkit coverage` refused the lane's report with exit 5 and "needs
    coverage >= 7.6". The probe already starts that interpreter, so it asks
    coverage's version on the same start and FAILs below the floor. The venv
    kind is pinned: the install line differs in a venv uv made, and this test
    failed from a contributor's uv-made venv while it pinned the pip form."""
    from crapkit import launchers
    monkeypatch.setattr(launchers, "_uv_made", lambda python: uv_made)
    monkeypatch.setattr(admin, "_runner_report",
                        lambda word, spec: (sys.executable, "8.3.3", "5.0.0", version))

    findings = admin._doctor_lane_probes(Path.cwd(), [_lane()])

    assert [f.level for f in findings] == ["ok", "FAIL"]
    install = _FLOOR_INSTALL[uv_made].format(python=admin._shell_quote(sys.executable))
    assert findings[1].text == (
        f"lane 'py' runs coverage {version} ({sys.executable}), which writes no function "
        "start lines, so `crapkit coverage` refuses its report with exit 5 (needs coverage >= "
        f"7.13.1); install 7.13.1 or later there with `{install}` and raise any pin that holds it "
        "lower")


def test_the_upgrade_guide_names_the_exit_this_fail_moves():
    """0.8.0's doctor printed `doctor: no problems found` and exited 0 on a lane
    whose coverage predates 7.13.1. This FAIL makes it exit 1, so a CI step that
    runs `crapkit doctor` on such a lane goes red on the upgrade. The guide's
    section for that coverage names the line and the exit it moves."""
    from crapkit.doctor import _COVERAGE_FLOOR

    guide = (Path(__file__).resolve().parents[2] / "docs" / "upgrading.md").read_text(encoding="utf-8")
    section = guide.split("### 0.8.1 on coverage 7.6 to 7.13.0\n", 1)[1].split("\n### ", 1)[0]
    line = _COVERAGE_FLOOR.split(" ({executable})", 1)[0].format(name="py", version="7.10.0")

    assert f"`FAIL {line} (" in section
    assert "`doctor: no problems found`" in section and "exited 0" in section and "exits 1" in section


@pytest.mark.parametrize("version", ["7.13.1", "7.16.0", "8.0.0b1", "unknown"])
def test_coverage_at_or_past_the_floor_or_unreadable_adds_nothing(monkeypatch, version):
    monkeypatch.setattr(admin, "_runner_report",
                        lambda word, spec: (sys.executable, "8.3.3", "7.1.0", version))

    assert [f.level for f in admin._doctor_lane_probes(Path.cwd(), [_lane()])] == ["ok"]


def test_a_lane_running_another_python_than_this_doctor_warns(monkeypatch):
    monkeypatch.setattr(admin, "_lane_first_run_note", lambda spec, lane: None)
    monkeypatch.setattr(admin, "_runner_report",
                        lambda word, spec: ("/srv/venv/bin/python", "8.3.3", "7.1.0", "7.16.0"))

    findings = admin._doctor_lane_probes(Path.cwd(), [_lane()])

    assert [f.level for f in findings] == ["ok", "WARN"]
    assert "/srv/venv/bin/python" in findings[1].text
    assert sys.executable in findings[1].text, "name both, so the reader knows which is which"


def test_only_a_lane_that_spells_pytest_with_cov_is_probed(monkeypatch):
    def boom(spec, lane):
        raise AssertionError("this lane must not be probed")

    monkeypatch.setattr(admin, "_lane_first_run_note", boom)

    assert admin._doctor_lane_probes(Path.cwd(), [_lane(command="npx vitest run --coverage", parser="istanbul"),
                                      _lane(command="python -m pytest"),
                                      _lane(command="make cov")]) == []


@pytest.mark.parametrize("command", ["uv run pytest --cov", "python -m pytest --cov"])
def test_a_lane_that_spells_pytest_with_cov_is_probed_whatever_its_parser(command):
    lanes = [_lane(command=command, parser=parser) for parser in ("coveragepy", "istanbul")]

    assert admin._probed_lanes(tuple(lanes)) == lanes


def test_doctor_probes_a_pytest_its_package_json_script_names(monkeypatch):
    """doctor holds the package map, so a pytest spelled in the script the lane
    runs counts; the lane names no python, so the note says it was not asked."""
    monkeypatch.setattr(admin, "_runner_report", lambda word, spec: None)
    packages = admin.PackageMap({"": npm_package({"scripts": {"cov": "pytest --cov"}})}, {})

    (note,) = admin._doctor_lane_probes(Path.cwd(), [_lane(command="npm run cov")], packages)

    assert note.level == "note"
    assert note.text.startswith("lane 'py' runs pytest through `npm`"), note.text


def test_a_stub_interpreter_with_no_version_to_print_is_no_finding(monkeypatch):
    """A python that runs and answers neither probe is not a finding: nothing
    is known about it either way."""
    monkeypatch.setattr(admin, "_lane_first_run_note", lambda spec, lane: None)
    monkeypatch.setattr(admin, "_runner_report", lambda word, spec: None)

    assert admin._doctor_lane_probes(Path.cwd(), [_lane()]) == []


def test_a_manager_headed_lane_gets_a_note_that_it_was_not_probed(monkeypatch):
    """`uv run python -m pytest --cov` names no python doctor can ask without
    provisioning the environment. Silence there read the same as "probed and
    healthy"; the note says which one this is."""
    def boom(word, spec):
        raise AssertionError("a manager-headed lane must not be probed")

    monkeypatch.setattr(admin, "_runner_report", boom)

    findings = admin._doctor_lane_probes(Path.cwd(), [_lane(command="uv run python -m pytest --cov")])

    assert [f.level for f in findings] == ["note"]
    assert findings[0].text.startswith("lane 'py' runs pytest through `uv`"), findings[0].text
    assert "not probed" in findings[0].text
    assert "coverage" in findings[0].text, "name the command that will answer instead"


def test_the_note_names_the_head_of_the_segment_that_runs_pytest(monkeypatch):
    """A lane that chains steps runs pytest after `&&`; the word to name is
    the one in front of pytest, not the command's first word."""
    monkeypatch.setattr(admin, "_runner_report", lambda word, spec: None)

    (note,) = admin._doctor_lane_probes(Path.cwd(), [_lane(command="cd pkg && uv run python -m pytest --cov")])

    assert note.text.startswith("lane 'py' runs pytest through `uv`"), note.text


def test_the_real_probe_answers_for_this_interpreter():
    # The parsed word, never the shell's spelling of it: shell_words strips the quotes
    # a lane command carries, and _runner_report quotes for the shell it runs under.
    # Handing it a pre-quoted path passed under cmd.exe and failed under sh, where
    # shlex.quote wrapped the quotes into the program name (CI, 2026-09-03).
    report = admin._runner_report(sys.executable, LaunchSpec(Path.cwd()))

    assert report is not None
    assert report[0].lower() == sys.executable.lower()
    assert report[1] == pytest.__version__
    assert report[3] == coverage.__version__


def test_a_timed_out_probe_cannot_keep_running(monkeypatch, tmp_path):
    import time

    marker = tmp_path / "escaped-probe"
    monkeypatch.setenv("CRAPKIT_PROBE_MARKER", str(marker))
    monkeypatch.setattr(admin, "_PROBE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(admin, "_VERSION_PROBE", (
        '-c "import os, time; from pathlib import Path; time.sleep(3); '
        "Path(os.environ['CRAPKIT_PROBE_MARKER']).write_text('escaped')\""))

    assert admin._runner_report(sys.executable, LaunchSpec(Path.cwd())) is None
    time.sleep(3.1)
    assert not marker.exists(), "doctor returned while its interpreter could still run"


def test_interpreter_startup_warnings_do_not_change_its_report(monkeypatch, tmp_path):
    (tmp_path / 'sitecustomize.py').write_text(
        'import sys\nprint("BENIGN_STARTUP_WARNING", file=sys.stderr)\n', encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(tmp_path), os.environ.get('PYTHONPATH', '')]))

    report = admin._runner_report(sys.executable, LaunchSpec(Path.cwd()))

    assert report is not None
    assert report[0].lower() == sys.executable.lower()


def test_the_probe_controls_its_output_encoding(monkeypatch, tmp_path, dependency_venv):
    environment = tmp_path / 'Jos\u00e9'
    executable, _ = dependency_venv(environment)
    monkeypatch.setenv('PYTHONIOENCODING', 'cp1252')

    report = admin._runner_report(str(executable), LaunchSpec(Path.cwd()))

    assert report is not None
    assert report[0] == str(executable)


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
                        lambda spec, lane: "note: lane 'py' names `python`, and the shell cannot run it")

    findings = admin._doctor_lanes(tmp_path, _cfg(_lane(command="no-such-runner-7f3a -m pytest --cov")))

    assert [f.level for f in findings] == ["FAIL"], findings
    assert "does not resolve" in findings[0].text


# --- init's summary names the workspaces it could not route ----------------------

def test_init_says_when_two_workspaces_name_a_runner_and_no_js_lane_was_written(capsys):
    packages = {"": npm_package({"private": True}),
                "api": npm_package({"devDependencies": {"jest": "1"}}),
                "web": npm_package({"devDependencies": {"vitest": "1"}})}

    admin._print_init_summary({"api": ("typescript",), "web": ("typescript",)}, (), packages)

    out = capsys.readouterr().out
    assert "2 workspaces name a runner (api: jest, web: vitest)" in out
    assert "no js lane was written" in out
    assert "[[lane]]" in out, "the fix is one lane per workspace"


def test_the_summary_is_silent_about_workspaces_once_a_js_lane_was_written(capsys):
    from crapkit.scaffold import LaneSpec

    js = LaneSpec("js", "npx vitest run --coverage", "coverage/coverage-final.json",
                  "istanbul", ("typescript",))
    packages = {"api": npm_package({"devDependencies": {"jest": "1"}}),
                "web": npm_package({"devDependencies": {"vitest": "1"}})}

    admin._print_init_summary({"api": ("typescript",)}, (js,), packages)

    assert "workspaces name a runner" not in capsys.readouterr().out


def test_a_root_lane_that_runs_no_js_runner_still_gets_the_note(capsys):
    """The question is whether init wrote a lane that runs a JS runner, not
    whether it wrote an istanbul lane: a root script that only chains the
    workspaces runs none of them itself."""
    from crapkit.scaffold import LaneSpec

    js = LaneSpec("js", "npm run test -- --coverage", "coverage/coverage-final.json",
                  "istanbul", ("typescript",))
    packages = {"": npm_package({"scripts": {"test": "pnpm -r test"}}),
                "api": npm_package({"devDependencies": {"jest": "1"}}),
                "web": npm_package({"devDependencies": {"vitest": "1"}})}

    admin._print_init_summary({"api": ("typescript",)}, (js,), packages)

    out = capsys.readouterr().out
    assert "2 workspaces name a runner (api: jest, web: vitest)" in out
    assert "no js lane was written" not in out, "init wrote lane 'js'"


def test_init_names_the_lane_it_wrote_when_the_root_script_only_runs_the_workspaces(tmp_path,
                                                                                    capsys):
    """The root's test script fans out (`npm test --workspaces`, web on
    vitest, api on jest), so init writes the root lane over it, whose runner
    nothing names. The note said "no js lane was written" one line under init's
    own "detected 1 lane(s) ...: js", and sent the reader to a commented js
    template init had not written."""
    from cli_inproc_repo import git

    root = tmp_path / "repo"
    files = {"package.json": {"private": True, "workspaces": ["web", "api"],
                              "scripts": {"test": "npm test --workspaces"}},
             "web/package.json": {"scripts": {"test": "vitest run"},
                                  "devDependencies": {"vitest": "^2.0.0"}},
             "api/package.json": {"scripts": {"test": "jest"},
                                  "devDependencies": {"jest": "^29.0.0", "jest-junit": "^16.0.0"}},
             "web/src/a.ts": "export const f = (a: number) => (a ? 1 : 2);\n",
             "api/src/b.ts": "export const g = (a: number) => (a ? 2 : 1);\n"}
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "fixture")

    assert main(["init", "--repo", str(root)]) == 0
    out = capsys.readouterr().out.splitlines()

    assert '\ncommand = "npm run test -- --coverage"\n' in (root / "crapkit.toml").read_text(
        encoding="utf-8")
    assert out[1].startswith("detected 1 lane(s) from this repo's own files: js - "), out
    assert out[2] == ("2 workspaces name a runner (api: jest, web: vitest) and the root names none, "
                      "so the runner of lane 'js' is unknown (npm run test -- --coverage names none "
                      "crapkit knows): replace it with one [[lane]] per workspace, each with its own "
                      "cwd and artifact"), out


# --- the runner each lane runs ----------------------------------------------------
#
# One line per lane, read by toolchain.infer from the lane's command, the
# package.json script it runs, or devDependencies. The package map is read once
# per doctor, and a package.json doctor cannot read is a WARN, never a stop.

def _js(name: str = "js", command: str = "npm run cov", cwd: str = "") -> Lane:
    return Lane(name=name, command=command, artifact=f"{name}.json", parser="istanbul",
                scopes=("pkg",), results_artifact=f"{name}-junit.xml", cwd=cwd)


def _map(packages: dict, unreadable: dict | None = None) -> admin.PackageMap:
    return admin.PackageMap(packages, unreadable or {})


def _runner_lines(*lanes: Lane, packages: admin.PackageMap = admin.NO_PACKAGES) -> list[tuple]:
    return [(f.level, f.text) for f in admin._doctor_runners(_cfg(*lanes), packages)]


@pytest.mark.parametrize("lane, packages, line", [
    (_lane(command="python -m pytest --cov"), {},
     ("ok", "lane 'py': runs pytest (named in its command)")),
    (_js(command="npm test"), {"": npm_package({"scripts": {"test": "vitest run"}})},
     ("ok", "lane 'js': runs vitest (named in package.json script \"test\")")),
    (_js(command="make cov"), {"": npm_package({"devDependencies": {"vitest": "1"}})},
     ("ok", "lane 'js': runs vitest (package.json devDependencies; the command names no runner)")),
    (_js(command="npm run cov"), {"": npm_package({"scripts": {"cov": "node cov.js"}})},
     ("note", "lane 'js': runner unknown (npm run cov names none crapkit knows); "
              "runner-specific hints and refusals are off for it")),
    (_js(command="npx vitest run && npx jest"), {},
     ("note", "lane 'js': runner unknown (it runs more than one: vitest, jest); "
              "runner-specific hints and refusals are off for it")),
], ids=["command", "script", "devdependencies", "unknown", "two-runners"])
def test_doctor_prints_one_runner_line_per_lane(lane, packages, line):
    assert _runner_lines(lane, packages=_map(packages)) == [line]


def test_every_lane_gets_its_line_in_declared_order():
    lines = _runner_lines(_lane(), _js(command="npx jest"), _js(name="ui", command="make cov"))

    assert [text.split(":")[0] for _, text in lines] == ["lane 'py'", "lane 'js'", "lane 'ui'"]


@pytest.mark.parametrize("cwd, expected", [
    ("web", "web"),          # the lane's own directory
    ("web/src/deep", "web"),  # the nearest above it
    ("api", ""),             # the root's when nothing nearer has one
    ("", ""),
])
def test_the_lane_reads_the_package_at_its_cwd_or_the_nearest_above(cwd, expected):
    web, root = npm_package({"scripts": {"test": "web"}}), npm_package({"scripts": {"test": "root"}})
    packages = _map({"": root, "web": web})

    nearest, at_root = admin._lane_packages(packages, cwd)

    assert (nearest, at_root) == ({"web": web, "": root}[expected], root)


def test_an_unreadable_nearest_package_leaves_the_command_alone():
    packages = _map({"": npm_package({"scripts": {"test": "jest"}})},
                    {"web/package.json": "it is not UTF-8"})

    assert admin._lane_packages(packages, "web/src") == (None, None)


# The three faults the one reader refuses, as a nested package.json holds them.
_UNREADABLE = {
    "not-utf8": b'{"scripts": {"test": "caf\xe9"}}',
    "utf16": "﻿{}".encode("utf-16-le"),
    "not-an-object": b'["vitest"]',
}


def _packaged_repo(repo: Path, web: bytes) -> Path:
    """The in-process repo with a readable root package.json naming vitest in
    its test script, and `web/package.json` holding `web`."""
    (repo / "package.json").write_text('{"scripts": {"test": "vitest run"}}', encoding="utf-8")
    (repo / "web" / "package.json").write_bytes(web)
    commit_all(repo, "packages")
    return repo


@pytest.mark.parametrize("web", _UNREADABLE.values(), ids=_UNREADABLE)
def test_a_package_json_doctor_cannot_read_is_one_warn_and_the_lane_reads_its_command(repo, web):
    root = _packaged_repo(repo, web)
    packages = admin._doctor_packages(root)
    lanes = (_js("unit", "npm test"), _js("ui", "npm test", cwd="web"))

    lines = [(f.level, f.text) for f in admin._doctor_runners(_cfg(*lanes), packages)]

    (warn,) = [text for level, text in lines if level == "WARN"]
    assert warn.startswith("web/package.json: it ") and warn.endswith(
        "; doctor read the runner of each lane under it from the lane's command alone"), warn
    assert ("ok", "lane 'unit': runs vitest (named in package.json script \"test\")") in lines
    assert ("note", "lane 'ui': runner unknown (npm test names none crapkit knows); "
                    "runner-specific hints and refusals are off for it") in lines


def test_inits_reader_still_skips_a_nested_package_json_it_cannot_read(repo, capsys):
    root = _packaged_repo(repo, _UNREADABLE["not-an-object"])

    packages = admin._package_json(root)

    assert set(packages) == {""}
    assert capsys.readouterr().err.startswith("crapkit: init skipped web/package.json: it holds")


def test_doctor_reads_each_tracked_package_json_once(repo, monkeypatch, capsys):
    """The text lines and --json both need the map; one read serves both."""
    from crapkit import repotext

    root = _packaged_repo(repo, b'{"devDependencies": {"jest": "1"}}')
    real, reads = repotext.repo_json, []

    def counted(path, what):
        if path.name == "package.json":
            reads.append(path.relative_to(root).as_posix())
        return real(path, what)

    monkeypatch.setattr(repotext, "repo_json", counted)
    for argv in (["doctor"], ["doctor", "--json"]):
        reads.clear()
        main([*argv, "--repo", str(root)])
        assert sorted(reads) == ["package.json", "web/package.json"], argv
    capsys.readouterr()


def test_no_config_key_names_a_toolchain(repo, capsys):
    """The runner is read, never declared in crapkit.toml. Today's unknown-key text."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(text.replace('name = "unit"\n',
                                                    'name = "unit"\ntoolchain = "vitest"\n'),
                                       encoding="utf-8")

    main(["doctor", "--repo", str(repo)])

    assert ("FAIL unknown key lane 'unit'.toolchain - crapkit ignores it (typo?); [[lane]] "
            "accepts these keys: ") in capsys.readouterr().out


def test_the_json_lane_carries_the_toolchain_the_line_names(repo, capsys):
    """The ui lane runs `npm test` in web/, whose script runs jest; the unit
    lane's `python -c pass` names no runner."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    ui = 'name = "ui"\ncommand = "python -c pass"\n'
    (repo / "crapkit.toml").write_text(text.replace(ui, 'name = "ui"\ncommand = "npm test"\n'
                                                        'cwd = "web"\n'), encoding="utf-8")
    root = _packaged_repo(repo, b'{"scripts": {"test": "jest"}}')

    main(["doctor", "--json", "--repo", str(root)])

    lanes = json.loads(capsys.readouterr().out)["lanes"]
    assert [lane["toolchain"] for lane in lanes] == [{"name": None, "source": None},
                                                     {"name": "jest", "source": "script"}]
