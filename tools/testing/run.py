"""Run isolated unit and CLI workers with one evidence owner."""
from __future__ import annotations

import argparse
from functools import lru_cache
import importlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


SUITES = ("unit", "e2e")
UNIT_WORKERS = 4
E2E_WORKERS = 8


@lru_cache(maxsize=1)
def _runtime():
    """Prefer the selected installation, including installed-wheel CI runs."""
    try:
        return importlib.import_module("crapkit.retention"), importlib.import_module("crapkit.procs")
    except ModuleNotFoundError as error:
        if error.name not in {"crapkit", "crapkit.retention", "crapkit.procs"}:
            raise
        if hasattr(sys.modules.get("crapkit"), "__version__"):
            raise ValueError("selected Crapkit installation lacks this test runner's lifecycle helpers; "
                             "install this checkout's dev extra") from error
    # Miniature runner fixtures deliberately put a different crapkit package on
    # PYTHONPATH. Keep that package selected for their tests, loading only the
    # runner's lifecycle helpers under a private name in this process.
    package = Path(__file__).resolve().parents[2] / "src/crapkit"
    spec = importlib.util.spec_from_file_location(
        "_crapkit_test_runner", package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return (importlib.import_module(spec.name + ".retention"),
            importlib.import_module(spec.name + ".procs"))


def _retention_limits(root: Path) -> dict:
    path = root / "crapkit.toml"
    if not path.is_file():
        return {}
    retention, _ = _runtime()
    config = importlib.import_module(retention.__package__ + ".config")
    cfg = config.load_config_text(path.read_text(encoding="utf-8"), root=root)
    return {"keep": cfg.test_retention_count, "days": cfg.test_retention_days}


def test_commands(python: str = "python", workers: int = E2E_WORKERS,
                  unit_workers: int = UNIT_WORKERS) -> list[list[str]]:
    """The development and CI schedule, also rendered in contributor guidance."""
    unit = [python, "-m", "pytest", "tests/unit", "-p", "no:randomly"]
    if unit_workers > 1:
        unit += ["-n", str(unit_workers), "--dist", "worksteal"]
    return [unit,
            [python, "-m", "pytest", "tests/e2e", "-n", str(workers), "-p", "no:randomly", "--dist", "worksteal"]]


def _suite(command: list[str], root: Path, scratch: Path, name: str, coverage: bool,
           owner=None) -> int:
    environment = dict(os.environ)
    command = [*command, f"--junitxml={scratch / (name + '.xml')}"]
    if coverage:
        # Nested pytest resolves this in its own cwd. Coverage's subprocess
        # startup keeps the absolute outer path for ordinary CLI children.
        environment["COVERAGE_FILE"] = str((scratch / (".coverage." + name)).relative_to(root))
        command += ["--cov=crapkit", "--cov-branch", "--cov-report="]
        config = environment.pop("COVERAGE_RCFILE", None)
        if config:
            command.append("--cov-config=" + config)
    return _runtime()[1].run_owned(command, owner=owner, cwd=root, env=environment).returncode


def _incomplete_suite(name: str, code: int, reason: str) -> ET.Element:
    suite = ET.Element("testsuite", name=name, tests="1", errors="1", failures="0")
    case = ET.SubElement(suite, "testcase", classname="test_runner", name=name + " evidence")
    ET.SubElement(case, "error", message=f"{name} exited {code}; {reason}")
    return suite


def _completed_pytest(suite: ET.Element, code: int) -> bool:
    if _unfinished_pytest(suite):
        return False
    return code == 0 or (code == 1 and any(
        node.tag in {"failure", "error"} for node in suite.iter()))


def _unfinished_pytest(suite: ET.Element) -> bool:
    return _session_error(suite) or any(_interrupted_error(error) for error in suite.iter("error"))


def _session_error(suite: ET.Element) -> bool:
    in_case = {id(error) for case in suite.iter("testcase") for error in case.iter("error")}
    return any(id(error) not in in_case for error in suite.iter("error"))


def _interrupted_error(error: ET.Element) -> bool:
    # These are pytest producer records, not failures of completed test cases.
    message = error.get("message", "")
    if message == "collection failure":
        return True
    text = message + " " + (error.text or "")
    return re.search(r"worker '[^']+' crashed while running '[^']+'", text) is not None


def _suite_xml(scratch: Path, name: str, code: int) -> tuple[list, bool]:
    try:
        suite = ET.parse(scratch / (name + ".xml")).getroot()
    except (OSError, ET.ParseError) as exc:
        return [_incomplete_suite(name, code, str(exc))], False
    parts = list(suite) if suite.tag == "testsuites" else [suite]
    if not _completed_pytest(suite, code):
        reason = "JUnit did not record a completed pytest run"
        return [*parts, _incomplete_suite(name, code, reason)], False
    return parts, True


def _junit(scratch: Path, output: Path, results: list[int]) -> bool:
    combined = ET.Element("testsuites")
    complete = []
    for name, code in zip(SUITES, results):
        parts, valid = _suite_xml(scratch, name, code)
        individual = ET.Element("testsuites")
        individual.extend(parts)
        ET.ElementTree(individual).write(output.with_name(name + ".xml"),
                                         encoding="utf-8", xml_declaration=True)
        combined.extend(parts)
        complete.append(valid)
    ET.ElementTree(combined).write(output, encoding="utf-8", xml_declaration=True)
    return all(complete)


def _coverage(root: Path, scratch: Path, output: Path, owner=None) -> None:
    environment = dict(os.environ, COVERAGE_FILE=str(scratch / ".coverage"))
    sources = [str(scratch / (".coverage." + name)) for name in SUITES]
    command = [sys.executable, "-m", "coverage"]
    run = _runtime()[1].run_owned
    run([*command, "combine", "--keep", *sources], cwd=root, env=environment,
        owner=owner).check_returncode()
    run([*command, "json", "--show-contexts", "-o", str(output)],
        cwd=root, env=environment, owner=owner).check_returncode()


def _retain_incomplete(scratch: Path, output: Path) -> None:
    retained = output / "incomplete" / scratch.name
    shutil.copytree(scratch, retained)
    print(f"incomplete test evidence retained at {retained}", file=sys.stderr)


def _retain_suite_data(scratch: Path, output: Path) -> None:
    for name in SUITES:
        source = scratch / (".coverage." + name)
        if source.is_file():
            shutil.copyfile(source, output / (name + ".coverage"))


def _collect(root: Path, scratch: Path, output: Path, results: list[int], coverage: bool,
             owner=None) -> bool:
    _retain_suite_data(scratch, output)
    complete = _junit(scratch, output / "junit.xml", results)
    if not complete:
        _retain_incomplete(scratch, output)
        return False
    try:
        if coverage:
            _coverage(root, scratch, output / "py.json", owner)
    except (OSError, subprocess.CalledProcessError):
        _retain_incomplete(scratch, output)
        raise
    return True


def _clear_outputs(output: Path) -> None:
    for name in ("junit.xml", "py.json", "unit.xml", "e2e.xml", "unit.coverage", "e2e.coverage"):
        (output / name).unlink(missing_ok=True)


def run_suites(root: Path, *, coverage: bool = False, workers: int = E2E_WORKERS,
               output: Path | None = None, unit_workers: int = UNIT_WORKERS) -> int:
    """Run both suites unless cancelled; stop descendants before releasing output."""
    root = root.resolve()
    retention, _ = _runtime()
    with retention.test_run_directory(root, selected=output, **_retention_limits(root)) as held:
        return _run_owned_suites(root, *held, coverage, workers, unit_workers)


def _run_owned_suites(root, output, owner, coverage, workers, unit_workers) -> int:
    print(f"test evidence: {output}", flush=True)
    _clear_outputs(output)
    with tempfile.TemporaryDirectory(prefix="suites-", dir=output) as directory:
        scratch = Path(directory)
        results = _execute_suites(root, scratch, output, owner, coverage, workers, unit_workers)
        complete = _collect(root, scratch, output, results, coverage, owner)
    return int(any(results) or not complete)


def _execute_suites(root, scratch, output, owner, coverage, workers, unit_workers) -> list[int]:
    try:
        return [_suite(command, root, scratch, name, coverage, owner)
                for name, command in zip(SUITES, test_commands(sys.executable, workers, unit_workers))]
    except BaseException:
        _retain_incomplete(scratch, output)
        raise


def _known_failures() -> tuple:
    failures = [OSError, ET.ParseError, subprocess.CalledProcessError, ValueError]
    for package in ("crapkit", "_crapkit_test_runner"):
        if errors := sys.modules.get(package + ".errors"):
            failures.append(errors.CrapkitError)
        if processes := sys.modules.get(package + ".procs"):
            failures.append(processes.CommandCancelled)
    return tuple(failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--workers", type=int, default=E2E_WORKERS)
    parser.add_argument("--unit-workers", type=int, default=UNIT_WORKERS,
                        help="unit processes (default: 4); use 1 for a serial reproduction")
    parser.add_argument("--output", type=Path,
                        help="replace evidence in this directory inside --repo; caller owns it "
                             "(default: a unique retained .crapkit/test-runs directory)")
    args = parser.parse_args(argv)
    try:
        return run_suites(args.repo, coverage=args.coverage, workers=args.workers,
                          output=args.output, unit_workers=args.unit_workers)
    except KeyboardInterrupt:
        print("test run cancelled; owned descendants stopped", file=sys.stderr)
        return 130
    except _known_failures() as exc:
        print(f"test evidence is incomplete: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
