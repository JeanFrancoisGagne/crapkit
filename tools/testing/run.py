"""Run isolated unit and CLI workers with one evidence owner."""
from __future__ import annotations

import argparse
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


def test_commands(python: str = "python", workers: int = E2E_WORKERS,
                  unit_workers: int = UNIT_WORKERS) -> list[list[str]]:
    """The development and CI schedule, also rendered in contributor guidance."""
    unit = [python, "-m", "pytest", "tests/unit", "-p", "no:randomly"]
    if unit_workers > 1:
        unit += ["-n", str(unit_workers)]
    return [unit,
            [python, "-m", "pytest", "tests/e2e", "-n", str(workers), "-p", "no:randomly"]]


def _suite(command: list[str], root: Path, scratch: Path, name: str, coverage: bool) -> int:
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
    return subprocess.run(command, cwd=root, env=environment).returncode


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


def _coverage(root: Path, scratch: Path, output: Path) -> None:
    environment = dict(os.environ, COVERAGE_FILE=str(scratch / ".coverage"))
    sources = [str(scratch / (".coverage." + name)) for name in SUITES]
    command = [sys.executable, "-m", "coverage"]
    subprocess.run([*command, "combine", "--keep", *sources], cwd=root, env=environment, check=True)
    subprocess.run([*command, "json", "--show-contexts", "-o", str(output)],
                   cwd=root, env=environment, check=True)


def _retain_incomplete(scratch: Path, output: Path) -> None:
    retained = output / "incomplete" / scratch.name
    shutil.copytree(scratch, retained)
    print(f"incomplete test evidence retained at {retained}", file=sys.stderr)


def _retain_suite_data(scratch: Path, output: Path) -> None:
    for name in SUITES:
        source = scratch / (".coverage." + name)
        if source.is_file():
            shutil.copyfile(source, output / (name + ".coverage"))


def _collect(root: Path, scratch: Path, output: Path, results: list[int], coverage: bool) -> bool:
    _retain_suite_data(scratch, output)
    complete = _junit(scratch, output / "junit.xml", results)
    if not complete:
        _retain_incomplete(scratch, output)
        return False
    try:
        if coverage:
            _coverage(root, scratch, output / "py.json")
    except (OSError, subprocess.CalledProcessError):
        _retain_incomplete(scratch, output)
        raise
    return True


def _clear_outputs(output: Path) -> None:
    for name in ("junit.xml", "py.json", "unit.xml", "e2e.xml", "unit.coverage", "e2e.coverage"):
        (output / name).unlink(missing_ok=True)


def _output_directory(root: Path, selected: Path | None) -> Path:
    if selected is None:
        parent = root / ".crapkit/test-runs"
        parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    output = (root / selected).resolve()
    if not output.is_relative_to(root):
        raise ValueError(f"test output must be inside {root}: {output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def run_suites(root: Path, *, coverage: bool = False, workers: int = E2E_WORKERS,
               output: Path | None = None, unit_workers: int = UNIT_WORKERS) -> int:
    """Always run both suites; preserve either failure and combine their evidence."""
    root = root.resolve()
    output = _output_directory(root, output)
    print(f"test evidence: {output}", flush=True)
    _clear_outputs(output)
    with tempfile.TemporaryDirectory(prefix="suites-", dir=output) as directory:
        scratch = Path(directory)
        results = [_suite(command, root, scratch, name, coverage)
                   for name, command in zip(SUITES, test_commands(sys.executable, workers, unit_workers))]
        complete = _collect(root, scratch, output, results, coverage)
    return int(any(results) or not complete)


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
    except (OSError, ET.ParseError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"test evidence is incomplete: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
