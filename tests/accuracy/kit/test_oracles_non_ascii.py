"""Every oracle reads a source file under a non-ASCII path, once, in the image.

A packet's adapter hands its oracle paths from the corpus, and the corpus has
non-ASCII names (a history-oracles fixture, a small-corpus file). An oracle
that mangles such a path answers for no function, which reads as a mismatch
in the packet's test instead of the tool problem it is. Each row runs one
oracle on a small file under `ñandú-データ/` and names what its answer must
hold. The binaries live in the accuracy image, so this runs on Linux nightly.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys

import pytest

import hang_guard
from accuracy.kit import oracles, repos

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
PLACE = "ñandú-データ"
SOURCES = {
    "py": "def fünf(x):\n    if x:\n        return 1\n    return 0\n",
    "js": "function fuenf(x) {\n  if (x) { return 1; }\n  return 0;\n}\n",
    "ts": "export function fuenf(x: number): number {\n  if (x) { return 1; }\n  return 0;\n}\n",
    "go": "package p\n\nfunc Fuenf(x int) int {\n\tif x > 0 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n",
    "rs": "pub fn fuenf(x: i32) -> i32 {\n    if x > 0 { 1 } else { 0 }\n}\n",
    "java": "class Fuenf {\n  int fuenf(int x) {\n    if (x > 0) { return 1; }\n    return 0;\n  }\n}\n",
    "c": "int fuenf(int x) {\n  if (x > 0) { return 1; }\n  return 0;\n}\n",
    "swift": "func fuenf(_ x: Int) -> Int {\n  if x > 0 { return 1 }\n  return 0\n}\n",
    "sh": "fuenf() {\n  if [ \"$1\" ]; then echo 1; fi\n}\n",
    "ps1": "function Fuenf($x) {\n  if ($x) { return 1 }\n  return 0\n}\nWrite-Output 'ran'\n",
}
CHECKSTYLE = ('<?xml version="1.0"?>\n<!DOCTYPE module PUBLIC "-//Checkstyle//DTD Checkstyle '
              'Configuration 1.3//EN" "https://checkstyle.org/dtds/configuration_1_3.dtd">\n'
              '<module name="Checker"><property name="severity" value="warning"/>'
              '<module name="TreeWalker"><module name="CyclomaticComplexity">'
              '<property name="max" value="0"/></module></module></module>\n')


@dataclass(frozen=True)
class Case:
    oracle: str
    language: str
    argv: tuple  # {file}, {dir}, {python}, {node_modules} and {config} are filled in
    says: str
    codes: tuple = (0,)
    env: dict = field(default_factory=dict)


CASES = (
    Case("lizard", "py", ("{python}", "-m", "lizard", "{file}"), "fünf@"),
    Case("radon", "py", ("{python}", "-m", "radon", "cc", "-s", "{file}"), "F 1:0 fünf"),
    Case("mccabe", "py", ("{python}", "-m", "mccabe", "--min", "1", "{file}"), "fünf"),
    Case("complexipy", "py", ("{python}", "-m", "complexipy", "{file}"), "fünf"),
    Case("pylint", "py", ("{python}", "-m", "pylint", "--disable=all",
                          "--enable=too-many-nested-blocks", "{file}"), "", (0,)),
    Case("typescript", "ts", ("node", "-e", "const ts=require(process.argv[1]);"
                              "const f=process.argv[2];const s=ts.createSourceFile(f,"
                              "require('fs').readFileSync(f,'utf8'),99);"
                              "console.log(s.statements.length)",
                              "{node_modules}/typescript", "{file}"), "1"),
    Case("eslint", "js", ("node", "{node_modules}/eslint/bin/eslint.js", "--no-config-lookup",
                          "--rule", '{"complexity": ["warn", 0]}', "{file}"), "complexity of 2"),
    Case("jscpd", "js", ("node", "{node_modules}/jscpd/bin/jscpd", "--silent", "{dir}"), ""),
    Case("gocyclo", "go", ("gocyclo", "{file}"), "Fuenf"),
    Case("gocognit", "go", ("gocognit", "{file}"), "Fuenf"),
    Case("revive", "go", ("revive", "{file}"), ""),
    Case("rust-code-analysis-cli", "rs", ("rust-code-analysis-cli", "-m", "-O", "json", "-p",
                                          "{file}"), "cyclomatic"),
    Case("checkstyle", "java", ("checkstyle", "-c", "{config}", "{file}"),
         "Cyclomatic Complexity is 2"),
    Case("pmd", "java", ("pmd", "check", "--no-cache", "-f", "text", "-R",
                         "category/java/design.xml/CyclomaticComplexity", "-d", "{file}"),
         "", (0, 4)),
    Case("clang-tidy", "c", ("clang-tidy", "{file}",
                             "--checks=-*,readability-function-cognitive-complexity",
                             "--config={CheckOptions: {readability-function-cognitive-complexity"
                             ".Threshold: 0}}", "--"), "cognitive complexity of 1"),
    Case("oclint", "c", ("oclint", "{file}", "--", "-c"), "Summary"),
    Case("swiftlint", "swift", ("swiftlint", "lint", "--quiet", "{file}"), "", (0, 2)),
    Case("shellmetrics", "sh", ("shellmetrics", "{file}"), "fuenf"),
    Case("pwsh", "ps1", ("pwsh", "-NoProfile", "-File", "{file}"), "ran"),
)


def _fill(argv: tuple, values: dict) -> list[str]:
    return [part.format(**values) for part in argv]


@pytest.fixture(scope="module")
def place(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("oracles") / PLACE
    root.mkdir()
    for language, text in SOURCES.items():
        (root / f"fünf.{language}").write_text(text, encoding="utf-8")
    (root / "checkstyle.xml").write_text(CHECKSTYLE, encoding="utf-8")
    return root


def _values(place: Path, case: Case) -> dict:
    return {"file": str(place / f"fünf.{case.language}"), "dir": str(place),
            "python": sys.executable, "config": str(place / "checkstyle.xml"),
            "node_modules": str(oracles.node_modules("push"))}


@pytest.mark.parametrize("case", CASES, ids=[case.oracle for case in CASES])
def test_the_oracle_reads_a_file_under_a_non_ascii_path(case, place, oracle):
    oracle(case.oracle)

    done = hang_guard.run(_fill(case.argv, _values(place, case)), cwd=place, text=True,
                          encoding="utf-8", errors="replace", env={**os.environ, **case.env})

    said = done.stdout + done.stderr
    assert done.returncode in case.codes, said
    assert case.says in said
    assert "Traceback" not in said and "Exception" not in said, said


def test_bugspots_walks_a_repo_under_a_non_ascii_path(tmp_path, oracle):
    oracle("bugspots")
    spec = repos.Spec(steps=(repos.Commit(files={"fünf.py": SOURCES["py"]}, message="fix: fünf"),))
    built = repos.build(spec, tmp_path / PLACE)

    done = hang_guard.run(["bugspots", str(built.top)], cwd=built.top, text=True,
                          encoding="utf-8", errors="replace")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "fünf.py" in done.stdout
