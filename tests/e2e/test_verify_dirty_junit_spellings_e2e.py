r"""A new failure is tagged dirty whatever spelling the runner gave its test file.

A JUnit report names a test's file the way the runner was started: vitest
writes `web/src/app.test.ts`, jest-junit's `{filepath}` on Windows writes
`web\src\app.test.ts`, a runner started with `./` keeps it, and a runner told to
report absolute paths writes the checkout root in whatever spelling its shell
stood in, including a lower-case drive or the checkout's directory in another
letter case. `crapkit verify` compares the file part of each new failure with
the files the working tree changed, and before 0.8.1 it compared the text: a
failure in the test file under edit read as a committed finding, which moved
`committed_findings` and `dirty_findings`. `cli/verifying` now hands the verdict
each file part as git spells it, read off the disk before the verdict runs.

Each row drives the real command: a lane that writes an istanbul artifact and a
JUnit report whose one case fails once the test file holds FAIL, `crapkit
coverage` on the clean tree, an uncommitted edit that fails the test, then
`crapkit verify --json`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace")

WINDOWS = os.name == "nt"

APP = ("export function add(a: number, b: number): number {\n  if (a) {\n    return a + b;\n"
       "  }\n  return b;\n}\n")
TEST = "import { add } from './app';\ntest('adds', () => { expect(add(1, 2)).toBe(3); });\n"

# The lane: an istanbul artifact keyed by the absolute path of web/src/app.ts,
# and a JUnit report whose classname is whatever classname.txt holds.
WRITER = r'''
import json, os, sys
from xml.sax.saxutils import quoteattr
root = os.getcwd()
app = os.path.join(root, "web", "src", "app.ts")
cov = {app: {"path": app,
    "fnMap": {"0": {"name": "add", "decl": {"start": {"line": 1}},
                    "loc": {"start": {"line": 1}, "end": {"line": 6}}}},
    "f": {"0": 1},
    "branchMap": {"0": {"loc": {"start": {"line": 2}},
                        "locations": [{"start": {"line": 2}}, {"start": {"line": 5}}]}},
    "b": {"0": [1, 1]}}}
os.makedirs(".crapkit", exist_ok=True)
with open(os.path.join(".crapkit", "coverage-final.json"), "w", encoding="utf-8") as fh:
    json.dump(cov, fh)
with open("classname.txt", encoding="utf-8") as fh:
    classname = fh.read()
with open(os.path.join("web", "src", "app.test.ts"), encoding="utf-8") as fh:
    failing = "FAIL" in fh.read()
body = '<failure message="boom">boom</failure>' if failing else ""
xml = ('<?xml version="1.0" encoding="UTF-8"?><testsuites tests="1"><testsuite name="s" tests="1">'
       f'<testcase classname={quoteattr(classname)} name="adds">{body}</testcase>'
       '</testsuite></testsuites>')
with open(os.path.join(".crapkit", "junit.xml"), "w", encoding="utf-8") as fh:
    fh.write(xml)
sys.exit(1 if failing else 0)
'''

TOML = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "web"\npaths = ["web/src"]\n'
        'languages = ["typescript"]\n\n[exclude]\nglobs = ["**/*.test.*"]\n\n'
        '[[lane]]\nname = "js"\ncommand = "python write_cov.py"\n'
        'artifact = ".crapkit/coverage-final.json"\nresults_artifact = ".crapkit/junit.xml"\n'
        'parser = "istanbul"\nscopes = ["web"]\n')


def _case_insensitive(folder: Path) -> bool:
    probe = folder / "Case-Probe"
    probe.write_text("", encoding="utf-8")
    try:
        return (folder / "case-probe").exists()
    finally:
        probe.unlink()


def _test_file(repo: Path) -> Path:
    return (repo / "web" / "src" / "app.test.ts").resolve()


def _other_case_checkout(repo: Path) -> str:
    """The test file's absolute path with the checkout's own directory in
    another letter case: a shell that typed `cd REPO` reports it so."""
    resolved = repo.resolve()
    return str(resolved.parent / resolved.name.swapcase() / "web" / "src" / "app.test.ts")


# id -> (what the host needs, the JUnit classname for web/src/app.test.ts)
SPELLINGS = {
    "vitest": ("", lambda repo: "web/src/app.test.ts"),
    "jest-junit-backslash": ("", lambda repo: "web\\src\\app.test.ts"),
    "dot-slash": ("", lambda repo: "./web/src/app.test.ts"),
    "dot-backslash": ("", lambda repo: ".\\web\\src\\app.test.ts"),
    "absolute": ("", lambda repo: str(_test_file(repo))),
    "absolute-forward": ("windows", lambda repo: _test_file(repo).as_posix()),
    "absolute-lower-drive": ("windows",
                             lambda repo: str(_test_file(repo))[0].lower()
                             + str(_test_file(repo))[1:]),
    "relative-dir-case": ("case", lambda repo: "WEB/src/app.test.ts"),
    "relative-file-case": ("case", lambda repo: "web/src/App.test.ts"),
    "absolute-checkout-case": ("case", _other_case_checkout),
    "absolute-dir-case": ("case", lambda repo: str(repo.resolve() / "WEB" / "src" / "app.test.ts")),
}


def _need(spec: str, folder: Path) -> None:
    if "windows" in spec and not WINDOWS:
        pytest.skip("needs Windows path rules")
    if "case" in spec and not _case_insensitive(folder):
        pytest.skip("needs a case-insensitive filesystem (Windows NTFS, macOS APFS)")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    files = {"web/src/app.ts": APP, "web/src/app.test.ts": TEST, "write_cov.py": WRITER,
             "crapkit.toml": TOML, ".gitignore": ".crapkit/\nclassname.txt\n"}
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "init")
    return repo


def _verify_after_breaking_the_test(repo: Path, classname: str) -> dict:
    (repo / "classname.txt").write_text(classname, encoding="utf-8", newline="")
    base = run_cli(repo, "coverage")
    assert base.returncode == 0, base.stdout + base.stderr
    (repo / "web" / "src" / "app.test.ts").write_text(TEST + "// FAIL\n", encoding="utf-8",
                                                      newline="\n")
    done = run_cli(repo, "verify", "--json")
    assert done.returncode == 8, done.stdout + done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("spelling", list(SPELLINGS))
def test_a_new_failure_in_the_test_file_under_edit_is_dirty_in_every_spelling(tmp_path, spelling):
    spec, spell = SPELLINGS[spelling]
    _need(spec, tmp_path)
    repo = _repo(tmp_path)
    classname = spell(repo)

    verdict = _verify_after_breaking_the_test(repo, classname)

    assert verdict["new_failures"] == [f"{classname}::adds"], verdict
    assert verdict["dirty_failures"] == verdict["new_failures"], (
        f"{classname!r} names the edited web/src/app.test.ts; verify called it committed")
    assert (verdict["committed_findings"], verdict["dirty_findings"]) == (0, 1)


def test_a_relative_junit_file_in_another_case_stays_committed_on_a_case_sensitive_disk(
        tmp_path):
    """On ext4 `WEB/src/app.test.ts` names a file git does not hold, so the
    failure is not the edited one."""
    if _case_insensitive(tmp_path):
        pytest.skip("needs a case-sensitive filesystem (Linux ext4)")
    repo = _repo(tmp_path)

    verdict = _verify_after_breaking_the_test(repo, "WEB/src/app.test.ts")

    assert verdict["new_failures"] == ["WEB/src/app.test.ts::adds"]
    assert verdict["dirty_failures"] == []
