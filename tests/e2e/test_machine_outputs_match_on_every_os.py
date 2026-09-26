"""Every output a program reads is the same bytes on every OS.

CI, an agent and the pull-request comment read crapkit's JSON, its workflow
annotations, its SARIF and TSV files, and crapkit-ratchet.tsv, which is
committed. The same repo measured on Windows and on Linux has to hand those
readers the same bytes, or a diff flips with the OS of whoever ran crapkit
last, and a golden written on one OS fails on the other. A hunt ran 18 such
outputs over one repo on both OSes: 17 matched, and report.html's explain
command did not (tests/unit/test_report_commands_run_in_every_shell.py holds
that one now).

This test runs those 17 over a repo built the same on every OS, SHA for SHA,
with paths a separator or an accent could change (pkg/cé.py, pkg/deep/z.py),
and a lane artifact keyed the way coverage.py keys it on each OS: with that
OS's separator. It compares each output with its copy under
tests/goldens/machine_outputs/, and every CI leg (Ubuntu and Windows, 3.11 to
3.14) checks the same copies, so an output that one OS prints otherwise fails
that OS's legs. Masked first, as what legitimately differs: the repo's
absolute path, the run stamps, the artifact digests, the interpreter version,
crapkit's own version and how crapkit spells itself in a next step. On stdout
a CR before each LF is dropped, since a Windows text stream writes it and
every reader takes it (test_program_streams_carry_non_ascii.py); the files are
compared as written, since crapkit pins them to LF.

A change that alters one of these outputs on purpose rewrites the copies:

    CRAPKIT_WRITE_GOLDENS=1 python -m pytest tests/e2e/test_machine_outputs_match_on_every_os.py

and the diff under tests/goldens/machine_outputs/ shows what changed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import crapkit
from conftest import cli_runner

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="strict")

GOLDENS = Path(__file__).resolve().parents[1] / "goldens" / "machine_outputs"
NOW = "1835492400"  # 2028-03-01T03:00:00Z, the churn window's clock

GRADE = '''def grade(score):
    if score > 90:
        return "A"
    elif score > 80:
        return "B"
    elif score > 70:
        return "C"
    elif score > 60:
        return "D"
    return "F"


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''
ACCENT = "def accent(v):\n    for a in v:\n        if a:\n            return a\n    return None\n"
# grade's twin sits in the accented file, so duplication names that path.
TWIN = GRADE.split("\n\n\n")[0].replace("def grade(", "def grade_twin(") + "\n"
SOURCES = {
    "pkg/calc.py": GRADE,
    "pkg/b.py": GRADE.split("\n\n\n")[1].replace("def clamp(", "def clamp_low("),
    "pkg/c\u00e9.py": ACCENT + "\n\n" + TWIN,
    "pkg/deep/z.py": "def z(a, b):\n    if a and b:\n        return 1\n    if a or b:\n        return 2\n"
                     "    return 3\n",
}
# (file, function): (start line, executed lines, missing lines, branches, covered branches)
COVERED = {
    ("pkg/calc.py", "grade"): (1, [1, 2, 3, 4, 10], [5, 6, 7, 8, 9], 8, 2),
    ("pkg/calc.py", "clamp"): (13, [13, 14, 15, 16, 17, 18], [], 4, 4),
    ("pkg/b.py", "clamp_low"): (1, [1, 2, 4, 6], [3, 5], 4, 1),
    ("pkg/c\u00e9.py", "accent"): (1, [1, 2, 3, 4], [5], 4, 2),
    ("pkg/c\u00e9.py", "grade_twin"): (8, [8, 9, 10, 17], [11, 12, 13, 14, 15, 16], 8, 2),
    ("pkg/deep/z.py", "z"): (1, [1, 2, 4, 6], [3, 5], 4, 1),
}
# The artifact keys each file with this OS's separator, as coverage.py does.
MAKE_COV = f'''import json, os

covered = {COVERED!r}
files = {{}}
for (path, name), (start, executed, missing, branches, taken) in covered.items():
    summary = {{"covered_lines": len(executed), "num_statements": len(executed) + len(missing),
               "num_branches": branches, "covered_branches": taken}}
    entry = files.setdefault(os.path.join(*path.split("/")), {{"functions": {{}}, "missing_lines": []}})
    entry["functions"][name] = {{"start_line": start, "executed_lines": executed,
                                "missing_lines": missing, "summary": summary}}
    entry["missing_lines"] += missing
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({{"meta": {{"branch_coverage": True}}, "files": files}}, fh)
'''
TOML = """[crapkit]
target = 6

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["pkg"]
"""
# (author, date, {path: (old, new)}): calc.py changes twice with z.py and twice
# with cé.py, so coupling at a support of 2 and churn have pairs and authors.
HISTORY = [
    ("bob", "2027-11-02T10:00:00+00:00", {"pkg/calc.py": ('return "F"', 'return "E"'),
                                          "pkg/deep/z.py": ("return 3", "return 4")}),
    ("carol", "2028-01-15T16:30:00+00:00", {"pkg/calc.py": ('return "E"', 'return "F"'),
                                            "pkg/c\u00e9.py": ("return None", "return v")}),
    ("dana", "2028-02-10T08:15:00+00:00", {"pkg/calc.py": ("return lo", "return int(lo)"),
                                           "pkg/deep/z.py": ("return 4", "return 5"),
                                           "pkg/c\u00e9.py": ("return v", "return list(v)")}),
]
EDIT = ('    return "F"', '    if score < 0:\n        return "?"\n    return "F"')

# The 17 outputs: each command's stdout, then the files named after it.
SCRIPT = [
    ("coverage", ["coverage", "--json", "--export", "out/scored.tsv", "--sarif", "out/coverage.sarif"]),
    ("coverage --github", ["coverage", "--github", "--reuse-artifacts"]),
    ("ratchet seed", ["ratchet", "seed"]),
    ("ratchet report", ["ratchet", "report", "--json"]),
    ("worklist", ["worklist", "--json"]),
    ("next-item", ["next-item", "--top", "5"]),
    ("brief", ["brief", "pkg/calc.py", "grade", "--json"]),
    ("explain", ["explain", "pkg/calc.py", "grade", "--json"]),
    ("duplication", ["duplication", "--json"]),
    ("coupling", ["coupling", "--min-support", "2", "--json"]),
    ("trend", ["trend", "--json"]),
    ("runs", ["runs", "--json"]),
    ("rescore", ["rescore", "pkg/calc.py", "--json"]),
    ("verify", ["verify", "--json", "--sarif", "out/verify.sarif"]),
]
# ratchet seed's stdout is a sentence for a person; the file it writes is the output.
UNREAD = {"ratchet seed"}
FILES = ["out/scored.tsv", "out/coverage.sarif", "crapkit-ratchet.tsv", "out/verify.sarif"]

STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?")
DIGEST = re.compile(r'"(\w*sha256\w*)": "[0-9a-f]{64}"')
PYTHON = re.compile(r'"python": "3\.\d+\.\d+"')
# lizard is a floor in pyproject, so CI may install a later release.
LIZARD = re.compile(r'(lizard=|"lizard": ")\d+(?:\.\d+)+')
# An absolute path under the repo: the separators inside it are the OS's own.
UNDER_REPO = re.compile(r"<REPO>[^\s\"'<>|,]*")


def _git(repo: Path, *args: str, author: str = "alice", date: str = "2027-09-20T09:00:00+00:00") -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.com",
           "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@example.com",
           "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   cwd=repo, check=True, capture_output=True, env=env)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    _write(path, text.replace(old, new, 1))


def _build(repo: Path) -> None:
    """The same commits, byte for byte and so SHA for SHA, on every OS."""
    for rel, text in {**SOURCES, "make_cov.py": MAKE_COV, "crapkit.toml": TOML,
                      ".gitignore": ".crapkit/\ncov.json\nout/\n"}.items():
        _write(repo / rel, text)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    for author, date, edits in HISTORY:
        for rel, (old, new) in edits.items():
            _edit(repo / rel, old, new)
        _git(repo, "commit", "-qam", f"{author} edits", author=author, date=date)
    (repo / "out").mkdir()


def _spellings(repo: Path) -> list[str]:
    """How an output can spell the repo's absolute path, longest first."""
    native = str(repo)
    forms = {native, native.replace("\\", "/"), json.dumps(native)[1:-1]}
    return sorted(forms, key=len, reverse=True)


def _interpreter_spellings() -> list[str]:
    """How a next step names crapkit when PATH finds no console script of this
    interpreter (crapkit.invocation), inside a JSON string and as printed."""
    interpreter = sys.executable.replace(os.sep, "/")
    quoted = f'"{interpreter}"' if " " in interpreter else interpreter
    spelled = f"{quoted} -m crapkit"
    return [json.dumps(spelled)[1:-1], spelled]


def _masked(text: str, repo: Path) -> str:
    """`text` with what legitimately differs by machine and release masked."""
    for spelling in _spellings(repo):
        text = text.replace(spelling, "<REPO>")
    for spelling in _interpreter_spellings():
        text = text.replace(spelling, "crapkit")
    text = UNDER_REPO.sub(lambda path: path.group(0).replace("\\\\", "/").replace("\\", "/"), text)
    text = text.replace(f'"{crapkit.__version__}"', '"<VERSION>"')
    text = DIGEST.sub(r'"\1": "<SHA256>"', STAMP.sub("<STAMP>", text))
    return LIZARD.sub(r"\1<LIZARD>", PYTHON.sub('"python": "<PYTHON>"', text))


def _outputs(repo: Path) -> dict[str, str]:
    """Each command's exit code and stdout, then each file, masked."""
    printed = {}
    for name, argv in SCRIPT:
        if name == "rescore":
            _edit(repo / "pkg" / "calc.py", *EDIT)
        done = run_cli(repo, *argv, env_extra={"GIT_TEST_DATE_NOW": NOW})
        if name not in UNREAD:
            printed[name] = _masked(f"exit {done.returncode}\n{done.stdout}", repo)
    for rel in FILES:
        printed[rel] = _masked(_written(repo / rel), repo)
    return printed


def _written(path: Path) -> str:
    """A file crapkit wrote, as its bytes, or a line saying it wrote none, so a
    command that failed shows as that output's diff."""
    return path.read_bytes().decode("utf-8") if path.is_file() else "(not written)\n"


def _golden(name: str) -> Path:
    return GOLDENS / (re.sub(r"[^\w.]+", "_", name) + ".txt")


@pytest.fixture(scope="module")
def outputs(tmp_path_factory) -> dict[str, str]:
    repo = tmp_path_factory.mktemp("os") / "repo"
    _build(repo)
    return _outputs(repo)


def test_the_script_names_seventeen_outputs(outputs):
    assert len(outputs) == 17, sorted(outputs)


@pytest.mark.parametrize("name", [name for name, _argv in SCRIPT if name not in UNREAD] + FILES)
def test_each_output_matches_its_copy_from_every_os(outputs, name):
    golden = _golden(name)
    if os.environ.get("CRAPKIT_WRITE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(outputs[name].encode("utf-8"))
    assert golden.is_file(), f"no copy at {golden}: run with CRAPKIT_WRITE_GOLDENS=1 to write it"

    assert outputs[name] == golden.read_bytes().decode("utf-8"), \
        f"{name} printed otherwise than {golden.name}; if that change is meant, rewrite the copies " \
        f"with CRAPKIT_WRITE_GOLDENS=1 and read the diff"
