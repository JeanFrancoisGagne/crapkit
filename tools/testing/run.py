"""Run serial in-process tests and parallel CLI tests with one evidence owner."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
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
        environment["COVERAGE_FILE"] = str(scratch / (".coverage." + name))
        command += ["--cov=crapkit", "--cov-branch", "--cov-context=test", "--cov-report="]
    return subprocess.run(command, cwd=root, env=environment).returncode


def _junit(scratch: Path, output: Path) -> None:
    combined = ET.Element("testsuites")
    for name in SUITES:
        suite = ET.parse(scratch / (name + ".xml")).getroot()
        combined.extend(list(suite) if suite.tag == "testsuites" else [suite])
    ET.ElementTree(combined).write(output, encoding="utf-8", xml_declaration=True)


def _coverage(root: Path, scratch: Path, output: Path) -> None:
    environment = dict(os.environ, COVERAGE_FILE=str(scratch / ".coverage"))
    sources = [str(scratch / (".coverage." + name)) for name in SUITES]
    command = [sys.executable, "-m", "coverage"]
    subprocess.run([*command, "combine", *sources], cwd=root, env=environment, check=True)
    subprocess.run([*command, "json", "--show-contexts", "-o", str(output)],
                   cwd=root, env=environment, check=True)


def run_suites(root: Path, *, coverage: bool = False, workers: int = E2E_WORKERS) -> int:
    """Always run both suites; preserve either failure and combine their evidence."""
    root = root.resolve()
    output = root / ".crapkit/cov"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="suites-", dir=output) as directory:
        scratch = Path(directory)
        results = [_suite(command, root, scratch, name, coverage)
                   for name, command in zip(SUITES, test_commands(sys.executable, workers))]
        _junit(scratch, output / "junit.xml")
        if coverage:
            _coverage(root, scratch, output / "py.json")
    return int(any(results))


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
