"""A cc-only Go scope that gains its coverprofile lane: the flip and the scoped reseed.

The repo holds a Python scope `calc`, measured by a live coveragepy lane, and a
Go scope `cmd` under a root go.mod, cc-only. It measures, seeds and commits.
The second commit uncomments the go stanza `crapkit init` writes for a root
go.mod, with its command swapped for a copy of the committed Go 1.26.8 profile
(CI has no Go), and deletes `coverage_optional`. verify refuses the flip once,
naming `cmd`, its lane and its scoped seed; `crapkit ratchet seed --scope cmd`
replaces cmd's marks with the run's values and counts the rises, verify
passes, and calc's stamp line and marks keep their bytes.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.cli import main
from crapkit.toolchain import TOOLCHAINS

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "tests" / "fixtures" / "coverage" / "coverprofile"
PROFILE = GO / "go-1.26.8-set.out"
MARKS = "crapkit-ratchet.tsv"
STANZA = TOOLCHAINS["go test"].stanza

PICK = ("def pick(a, b, c):\n    if a:\n        return 1\n    if b:\n        return 2\n"
        "    return 3\n")
# The calc lane: coverage.py's JSON report with pick fully run.
MAKE_COV = '''import json
import os

fn = {"start_line": 1, "executed_lines": [2, 3, 4, 5, 6], "missing_lines": [],
      "excluded_lines": [], "summary": {"covered_lines": 5, "num_statements": 5,
                                        "num_branches": 4, "covered_branches": 4,
                                        "excluded_lines": 0}}
report = {"meta": {"branch_coverage": True},
          "files": {"calc/ops.py": {"functions": {"pick": fn}, "missing_lines": []}}}
os.makedirs(os.path.join(".crapkit", "cov"), exist_ok=True)
with open(os.path.join(".crapkit", "cov", "calc.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, sort_keys=True)
'''
# The go lane's stand-in command: a copy of the profile the test names.
COPY_PROFILE = ("import pathlib, sys\n"
                "pathlib.Path('.crapkit/cov/go.out').write_bytes(pathlib.Path(sys.argv[1]).read_bytes())\n")
# Target 2 puts the Go functions at ccn 3 over the ceiling, so the cc-only seed
# marks them at their ccn.
HEAD = '[crapkit]\ntarget = 2\n\n[exclude]\nglobs = ["**/*_test.go"]\n\n'
CALC = ('[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n\n'
        '[[lane]]\nname = "calc"\ncommand = "python make_cov.py"\n'
        'artifact = ".crapkit/cov/calc.json"\nparser = "coveragepy"\nscopes = ["calc"]\n\n')
CMD = '[[scope]]\nname = "cmd"\npaths = ["shapes"]\nlanguages = ["go"]\n'
# The go stanza as init writes it for a root go.mod, commented.
GO_STANZA = "".join(f"# {line}\n" for line in (
    "[[lane]]", f'name = "{STANZA.name}"', f'command = "{STANZA.command}"',
    f'artifact = "{STANZA.artifact}"', f'parser = "{STANZA.parser}"', 'scopes = ["cmd"]'))
SCOPED = '\n[crapkit.scoped_tests]\ncalc = "python -c pass"\n'
CC_ONLY = HEAD + CALC + CMD + "coverage_optional = true\n\n" + GO_STANZA + SCOPED


def _measured(profile: Path) -> str:
    """The go stanza uncommented with its command swapped for the profile's
    copy, and coverage_optional deleted."""
    stanza = GO_STANZA.replace("# ", "").replace(
        STANZA.command, f"python copy_profile.py {profile.as_posix()}")
    return HEAD + CALC + CMD + "\n" + stanza + SCOPED


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                   check=True, capture_output=True, timeout=120)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    """The mixed repo: the Go fixture's module under shapes/, keyed by a root
    go.mod whose module path is its parent, and calc/ops.py."""
    monkeypatch.delenv("CRAPKIT_OVERRIDE_REASON", raising=False)
    root = tmp_path / "mixed"
    for source in (GO / "go-repo").rglob("*.go"):
        target = root / "shapes" / source.relative_to(GO / "go-repo")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (root / "calc").mkdir()
    files = {"go.mod": "module example.com\n\ngo 1.22\n", "calc/ops.py": PICK,
             ".gitignore": ".crapkit/\nmake_cov.py\ncopy_profile.py\nprofile.out\n",
             "crapkit.toml": CC_ONLY}
    for rel, body in files.items():
        (root / rel).write_text(body, encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")
    (root / "make_cov.py").write_text(MAKE_COV, encoding="utf-8", newline="\n")
    (root / "copy_profile.py").write_text(COPY_PROFILE, encoding="utf-8", newline="\n")
    # The recorded profile names module example.com/shapes, which sits under
    # this go.mod's example.com as shapes/.
    (root / "profile.out").write_bytes(PROFILE.read_bytes())
    return root


def cli(repo: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = main([*args, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def marks(repo: Path, scope_dir: str) -> dict[str, str]:
    rows = (line.split("\t") for line in (repo / MARKS).read_text(encoding="utf-8").splitlines()
            if line.startswith(scope_dir))
    return {re.split(r"[ (]", name, maxsplit=1)[0]: mark for _, name, mark in rows}


def lines_of(text: str, scope: str, scope_dir: str) -> list[str]:
    """A scope's `#scope` line and its marks, byte for byte."""
    return [line for line in text.splitlines(keepends=True)
            if line.startswith((f"#scope\t{scope}\t", scope_dir))]


def _seeded(repo: Path, capsys) -> str:
    """The cc-only start, measured, seeded and committed; the marks file."""
    for step in (["coverage"], ["ratchet", "seed"], ["verify"]):
        code, out, err = cli(repo, capsys, *step)
        assert code == 0, (step, out, err)
    _commit(repo, "seed")
    return (repo / MARKS).read_text(encoding="utf-8")


def _flipped(repo: Path, capsys) -> tuple[str, dict[str, str]]:
    """Seed cc-only, then commit the uncommented go lane; the marks file and
    cmd's cc-only marks."""
    before = _seeded(repo, capsys)
    cc_only = marks(repo, "shapes/")
    (repo / "crapkit.toml").write_text(_measured(repo / "profile.out"), encoding="utf-8",
                                       newline="\n")
    _commit(repo, "measure cmd")
    return before, cc_only


def test_the_cc_only_seed_marks_the_go_functions_at_their_ccn(repo, capsys):
    _seeded(repo, capsys)

    assert marks(repo, "shapes/") == {"Classify": "3.0000", "SumPositive": "3.0000",
                                      "Mul": "3.0000"}
    assert marks(repo, "calc/") == {"pick": "3.0000"}


def test_verify_refuses_the_flip_naming_the_scope_its_lane_and_its_scoped_seed(repo, capsys):
    _flipped(repo, capsys)

    code, out, err = cli(repo, capsys, "verify")

    assert code == 3, (out, err)
    assert "1 scope(s): cmd (coverage cc-only to coverprofile=1)" in err, err
    assert "cmd was cc-only and is now measured by its coverprofile lane(s)" in err
    assert "ratchet seed --scope cmd`" in err
    assert "Classify" not in out + err and "Mul" not in out + err


def test_the_scoped_seed_raises_cmds_marks_counts_them_and_leaves_calc_alone(repo, capsys):
    before, cc_only = _flipped(repo, capsys)
    assert cli(repo, capsys, "coverage")[0] == 0

    code, out, err = cli(repo, capsys, "ratchet", "seed", "--scope", "cmd")

    assert code == 0, (out, err)
    measured = marks(repo, "shapes/")
    # Classify runs 6 of its 8 lines: 3^2 * 0.25^3 + 3; Mul runs none: 3^2 + 3.
    assert {name: measured[name] for name in cc_only} == {
        "Classify": "3.1406", "SumPositive": "3.0000", "Mul": "12.0000"}
    risen = sum(float(measured[name]) > float(mark) for name, mark in cc_only.items())
    printed = re.search(r"scope cmd: added (\d+), tightened 0, raised (\d+)", out)
    assert printed and int(printed.group(2)) == risen == 2, out
    after = (repo / MARKS).read_text(encoding="utf-8")
    assert lines_of(after, "calc", "calc/") == lines_of(before, "calc", "calc/") != []
    assert cli(repo, capsys, "verify")[0] == 0


def test_a_reader_bump_after_the_flip_raises_no_mark(repo, capsys, monkeypatch):
    """Only the cc-only to measured flip raises a mark. A planted
    lower coverage for Classify after a coverprofile reader bump leaves its
    mark where the flip's seed put it."""
    _flipped(repo, capsys)
    assert cli(repo, capsys, "coverage")[0] == 0
    assert cli(repo, capsys, "ratchet", "seed", "--scope", "cmd")[0] == 0
    _commit(repo, "reseed cmd")
    seeded = marks(repo, "shapes/")
    planted = PROFILE.read_text(encoding="utf-8").replace(
        "example.com/shapes/shapes.go:8.29,9.11 1 1", "example.com/shapes/shapes.go:8.29,9.11 1 0")
    (repo / "profile.out").write_text(planted, encoding="utf-8", newline="\n")
    monkeypatch.setitem(coverage_format.READER_VERSIONS, "coverprofile", 2)
    assert cli(repo, capsys, "coverage")[0] == 0

    code, out, err = cli(repo, capsys, "ratchet", "seed", "--scope", "cmd")

    assert code == 0, (out, err)
    assert marks(repo, "shapes/")["Classify"] == seeded["Classify"] == "3.1406"
