"""Run serial in-process tests and parallel CLI tests with one evidence owner."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


SUITES = ("unit", "e2e")
E2E_WORKERS = 8


def test_commands(python: str = "python", workers: int = E2E_WORKERS) -> list[list[str]]:
    """The development and CI schedule, also rendered in contributor guidance."""
    return [[python, "-m", "pytest", "tests/unit", "-p", "no:randomly"],
            [python, "-m", "pytest", "tests/e2e", "-n", str(workers), "-p", "no:randomly"]]


def _suite(command: list[str], root: Path, scratch: Path, name: str, coverage: bool) -> int:
    environment = dict(os.environ)
    command = [*command, f"--junitxml={scratch / (name + '.xml')}"]
    if coverage:
        # Nested pytest resolves this in its own cwd. Coverage's subprocess
        # startup keeps the absolute outer path for ordinary CLI children.
        environment["COVERAGE_FILE"] = str((scratch / (".coverage." + name)).relative_to(root))
        command += ["--cov=crapkit", "--cov-branch", "--cov-report="]
    return subprocess.run(command, cwd=root, env=environment).returncode


def _incomplete_suite(name: str, code: int, reason: str) -> ET.Element:
    suite = ET.Element("testsuite", name=name, tests="1", errors="1", failures="0")
    case = ET.SubElement(suite, "testcase", classname="test_runner", name=name + " evidence")
    ET.SubElement(case, "error", message=f"{name} exited {code}; {reason}")
    return suite


def _suite_xml(scratch: Path, name: str, code: int) -> tuple[list, bool]:
    try:
        suite = ET.parse(scratch / (name + ".xml")).getroot()
    except (OSError, ET.ParseError) as exc:
        return [_incomplete_suite(name, code, str(exc))], False
    parts = list(suite) if suite.tag == "testsuites" else [suite]
    if code and not any(node.tag in {"failure", "error"} for node in suite.iter()):
        return [*parts, _incomplete_suite(name, code, "JUnit recorded no failure")], False
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


def run_suites(root: Path, *, coverage: bool = False, workers: int = E2E_WORKERS) -> int:
    """Always run both suites; preserve either failure and combine their evidence."""
    root = root.resolve()
    output = root / ".crapkit/cov"
    output.mkdir(parents=True, exist_ok=True)
    _clear_outputs(output)
    with tempfile.TemporaryDirectory(prefix="suites-", dir=output) as directory:
        scratch = Path(directory)
        results = [_suite(command, root, scratch, name, coverage)
                   for name, command in zip(SUITES, test_commands(sys.executable, workers))]
        complete = _collect(root, scratch, output, results, coverage)
    return int(any(results) or not complete)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--workers", type=int, default=E2E_WORKERS)
    args = parser.parse_args(argv)
    try:
        return run_suites(args.repo, coverage=args.coverage, workers=args.workers)
    except (OSError, ET.ParseError, subprocess.CalledProcessError) as exc:
        print(f"test evidence is incomplete: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
