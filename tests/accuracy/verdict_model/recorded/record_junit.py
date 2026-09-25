"""Regenerate the recorded JUnit reports test_junit_parse reads.

usage: python record_junit.py OUT PYTHON [PYTHON ...] [--node NODE_MODULES]

Each PYTHON must have pytest, pytest-xdist 3.8 and pytest-reportlog 1.0
installed; its suites land in OUT/pytest-<major.minor>/ as <suite>.xml (the
JUnit report) and <suite>.jsonl (pytest-reportlog's record of the same run,
the second reader test_junit_parse compares against). With --node, the vitest
and jest suites run from that node_modules directory into OUT/vitest-<version>/
and OUT/jest-junit-<version>/.

The suites are written fresh into a temporary directory named `suite`, so a
report carries no path of the machine that recorded it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

MIXED = '''\
import pytest


@pytest.fixture
def broken_setup():
    raise RuntimeError("setup broke")


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown broke")


def test_pass():
    pass


def test_fail():
    assert 1 == 2


def test_setup_error(broken_setup):
    pass


def test_teardown_error(broken_teardown):
    pass


def test_fail_and_teardown_error(broken_teardown):
    assert False


@pytest.mark.skip(reason="skipped on purpose")
def test_skip():
    pass


@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="passes anyway")
def test_xpass():
    pass


@pytest.mark.parametrize("value", ["a b", "x[1]", "caf\\u00e9"])
def test_param(value):
    assert value != "x[1]"


class TestGroup:
    def test_method(self):
        pass

    def test_method_fails(self):
        assert False
'''
# One teardown error per suite, so no other shape offsets its count: pytest
# before 9.1 declares one more test than it writes testcases after a pass, and
# one fewer after a failed call, whose teardown error opens a second testcase
# with the same id.
TEARDOWN = '''\
import pytest


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown broke")


def test_plain():
    pass


def test_then_teardown_error(broken_teardown):
    assert CALL_PASSES
'''
PYTEST_SUITES = {
    "mixed": ({"tests/__init__.py": "", "tests/test_mixed.py": MIXED}, []),
    "teardown_after_pass": ({"tests/__init__.py": "",
                             "tests/test_td.py": "CALL_PASSES = True\n\n\n" + TEARDOWN}, []),
    "teardown_after_fail": ({"tests/__init__.py": "",
                             "tests/test_td.py": "CALL_PASSES = False\n\n\n" + TEARDOWN}, []),
    "collection_error": ({"tests/__init__.py": "", "tests/test_ok.py": "def test_ok():\n    pass\n",
                          "tests/test_broken.py": "def test_broken(:\n    pass\n"},
                         ["--continue-on-collection-errors"]),
    "worker_crash": ({"tests/__init__.py": "",
                      "tests/test_crash.py": "import os\n\n\ndef test_dies():\n    os._exit(3)\n\n\n"
                                             "def test_lives():\n    pass\n"},
                     ["-n", "2"]),
    "zero_testcases": ({"tests/__init__.py": "", "tests/test_none.py": "VALUE = 1\n"}, []),
}
VITEST_TESTS = '''\
import { describe, expect, it } from "vitest";

describe("group", () => {
  it("passes", () => { expect(1).toBe(1); });
  it("fails", () => { expect(1).toBe(2); });
  it.skip("skipped", () => {});
  it.todo("todo");
});

it("top level passes", () => {});
it("top level fails", () => { throw new Error("broke"); });
'''
JEST_TESTS = '''\
describe("group", () => {
  it("passes", () => { expect(1).toBe(1); });
  it("fails", () => { expect(1).toBe(2); });
  it.skip("skipped", () => {});
});

it("top level passes", () => {});
it("top level fails", () => { throw new Error("broke"); });
'''


def _write(root: Path, files: dict) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _fresh(base: Path) -> Path:
    suite = base / "suite"
    shutil.rmtree(suite, ignore_errors=True)
    suite.mkdir()
    return suite


def _pytest_version(python: str) -> str:
    done = subprocess.run([python, "-c", "import pytest; print(pytest.__version__)"],
                          capture_output=True, text=True, check=True)
    return ".".join(done.stdout.strip().split(".")[:2])


def record_pytest(out: Path, python: str, base: Path) -> None:
    target = out / f"pytest-{_pytest_version(python)}"
    target.mkdir(parents=True, exist_ok=True)
    for name, (files, extra) in PYTEST_SUITES.items():
        suite = _fresh(base)
        _write(suite, files)
        subprocess.run([python, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly",
                        f"--junitxml={target / (name + '.xml')}",
                        f"--report-log={target / (name + '.jsonl')}", *extra, "tests"],
                       cwd=suite, capture_output=True, env={**os.environ, "PYTHONHASHSEED": "0"})


def _node_version(modules: Path, package: str) -> str:
    return json.loads((modules / package / "package.json").read_text(encoding="utf-8"))["version"]


def _linked(base: Path, modules: Path) -> Path:
    suite = _fresh(base)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(suite / "node_modules"), str(modules)],
                       capture_output=True, check=True)
    else:
        (suite / "node_modules").symlink_to(modules, target_is_directory=True)
    return suite


def record_vitest(out: Path, modules: Path, base: Path) -> None:
    target = out / f"vitest-{_node_version(modules, 'vitest')}"
    target.mkdir(parents=True, exist_ok=True)
    suite = _linked(base, modules)
    _write(suite, {"package.json": '{"type": "module"}\n', "suite.test.js": VITEST_TESTS})
    subprocess.run(["node", "node_modules/vitest/vitest.mjs", "run", "--reporter=junit",
                    f"--outputFile={target / 'mixed.xml'}"], cwd=suite, capture_output=True)


def record_jest(out: Path, modules: Path, base: Path) -> None:
    target = out / f"jest-junit-{_node_version(modules, 'jest-junit')}"
    target.mkdir(parents=True, exist_ok=True)
    suite = _linked(base, modules)
    _write(suite, {"package.json": "{}\n", "suite.test.js": JEST_TESTS})
    env = {**os.environ, "JEST_JUNIT_OUTPUT_DIR": str(target), "JEST_JUNIT_OUTPUT_NAME": "mixed.xml"}
    subprocess.run(["node", "node_modules/jest/bin/jest.js", "--ci", "--reporters=default",
                    "--reporters=jest-junit"], cwd=suite, capture_output=True, env=env)


# What a report may carry of the machine that wrote it: tracebacks, host and
# clock. Parsing reads none of it, so it is replaced; a crashed worker's line is
# the one body a reader matches, and it names only the test.
_BODY = re.compile(r"(<(failure|error|skipped)\b[^>]*?>)(.*?)(</\2>)", re.S)
_HOST = re.compile(r'(hostname|timestamp)="[^"]*"')
_KEEP = ("$report_type", "nodeid", "when", "outcome", "wasxfail")


def _body(match: re.Match) -> str:
    text = match.group(3)
    kept = text if "crashed while running" in text else "traceback removed by record_junit.py"
    return f"{match.group(1)}{kept}{match.group(4)}"


def scrub_xml(path: Path) -> None:
    text = _BODY.sub(_body, path.read_text(encoding="utf-8"))
    path.write_text(_HOST.sub(lambda m: f'{m.group(1)}="recorded"', text), encoding="utf-8")


def _kept(row: dict) -> dict:
    return {key: row[key] for key in _KEEP if key in row}


def scrub_jsonl(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    kept = [_kept(row) for row in rows if row.get("$report_type") in ("TestReport", "CollectReport")]
    path.write_text("".join(json.dumps(row) + "\n" for row in kept), encoding="utf-8")


def scrub(out: Path) -> None:
    for path in out.rglob("*.xml"):
        scrub_xml(path)
    for path in out.rglob("*.jsonl"):
        scrub_jsonl(path)


def _arguments(argv: list[str]) -> tuple[Path, list[str], Path | None]:
    """OUT, the pythons, and the node_modules directory --node names."""
    node = Path(argv[argv.index("--node") + 1]).resolve() if "--node" in argv else None
    rest = argv[1:argv.index("--node")] if node else argv[1:]
    return Path(argv[0]).resolve(), rest, node


def record_node(out: Path, node: Path | None, base: Path) -> None:
    if node:
        record_vitest(out, node, base)
        record_jest(out, node, base)


def main(argv: list[str]) -> None:
    out, pythons, node = _arguments(argv)
    with tempfile.TemporaryDirectory() as tmp:
        for python in pythons:
            record_pytest(out, python, Path(tmp))
        record_node(out, node, Path(tmp))
    scrub(out)


if __name__ == "__main__":
    main(sys.argv[1:])
