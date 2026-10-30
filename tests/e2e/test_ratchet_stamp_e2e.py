"""End-to-end: the ratchet's metric stamp through the CLI seam.

A scoring change that leaves old marks in place is invisible — the marks still
parse, verify still runs, and every number it compares came from different rules.
The stamp turns that into an exit 3 instead of a green run.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from conftest import child_env, cli_runner, git_commit_all, git_init_repo

TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
    'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n'
)

MAKE_COV = (
    "import json, os\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'tiny', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 1}}}},\n"
    "             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n"
)

APP = "export function tiny(a: number) { return a; }\n"

TANGLED = (
    "export function tangled(a: number, b: number): number {\n"
    "  let r = 0;\n"
    "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
    "  if (a > 10 && b > 10) { r += 3; }\n"
    "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
    "  return r;\n}\n"
)

RATCHET = "crapkit-ratchet.tsv"


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


# PYTHONPATH shims reach only a new interpreter, so this file keeps the child.
run_cli = cli_runner(timeout=180, spawn=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "init")
    return tmp_path


def first_line(repo: Path) -> str:
    return (repo / RATCHET).read_text(encoding="utf-8").splitlines()[0]


def test_seed_stamps_the_marks_file_with_the_running_metric(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    seeded = run_cli(repo, "ratchet", "seed")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr

    stamp = first_line(repo)
    assert stamp.startswith("# crapkit-analysis=") and " lizard=" in stamp, stamp
    assert "tangled" in (repo / RATCHET).read_text(encoding="utf-8")


def test_a_stamped_ratchet_still_verifies_clean(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def test_verify_refuses_marks_recorded_under_another_metric(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    body = (repo / RATCHET).read_text(encoding="utf-8").split("\n", 1)[1]
    (repo / RATCHET).write_text("# crapkit-analysis=1 lizard=0.0.1\n" + body, encoding="utf-8")

    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "crapkit-analysis=1 lizard=0.0.1" in res.stderr, "the stale metric must be named"
    assert "crapkit-analysis=" in res.stderr.split("but this run measures", 1)[1]
    assert "ratchet seed" in res.stderr, "the message must carry the fix"


def test_verify_warns_once_and_proceeds_on_a_ratchet_written_before_stamping(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    (repo / RATCHET).write_text("path\tlong_name\tcrap\nsrc/gone.ts\tf( )\t9999.0000\n",
                                encoding="utf-8")

    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert res.stderr.count("no metric stamp") == 1, res.stderr


def _write(path: Path, stamp: str, rows: str) -> str:
    path.write_text(f"{stamp}path\tlong_name\tcrap\n{rows}", encoding="utf-8")
    return str(path)


def test_merge_refuses_two_sides_that_each_moved_the_stamp_differently(tmp_path: Path):
    base = _write(tmp_path / "base", "# crapkit-analysis=1 lizard=1.17.10\n", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "# crapkit-analysis=3 lizard=1.24.0\n", "src/a.ts\tf( )\t20.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 3, (res.returncode, res.stdout)
    assert "crapkit-analysis=2 lizard=1.17.10" in res.stderr
    assert "crapkit-analysis=3 lizard=1.24.0" in res.stderr
    assert Path(ours).read_text(encoding="utf-8").endswith("src/a.ts\tf( )\t30.0000\n"), \
        "a refused merge must not rewrite ours"


def test_merge_takes_the_stamp_of_the_one_side_that_moved_it(tmp_path: Path):
    base = _write(tmp_path / "base", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "# crapkit-analysis=3 lizard=1.24.0\n", "src/a.ts\tf( )\t50.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert Path(ours).read_text(encoding="utf-8") == (
        "# crapkit-analysis=3 lizard=1.24.0\npath\tlong_name\tcrap\nsrc/a.ts\tf( )\t30.0000\n")


def test_merge_of_matching_stamps_keeps_that_stamp_not_the_running_one(tmp_path: Path):
    stamp = "# crapkit-analysis=2 lizard=1.17.10\n"
    base = _write(tmp_path / "base", stamp, "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", stamp, "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", stamp, "src/a.ts\tf( )\t20.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    merged = Path(ours).read_text(encoding="utf-8")
    assert merged.splitlines()[0] == "# crapkit-analysis=2 lizard=1.17.10"
    assert merged.splitlines()[2] == "src/a.ts\tf( )\t20.0000", "both changed: the merge keeps the min"


def test_the_merge_driver_needs_no_analysis_stack_to_write_a_stamp(tmp_path: Path):
    # git runs the driver in its own temp dir. Stamping the result from the
    # RUNNING metric would import lizard to write three lines, and a driver that
    # can fail on an absent dependency turns every merge into a conflict.
    stamp = "# crapkit-analysis=2 lizard=1.17.10\n"
    base = _write(tmp_path / "base", stamp, "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", stamp, "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", stamp, "src/a.ts\tf( )\t20.0000\n")
    blind = os.pathsep.join([str(FIXTURES / "shims" / "lizard"), os.environ.get("PYTHONPATH", "")])

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs, env_extra={"PYTHONPATH": blind})
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert Path(ours).read_text(encoding="utf-8").splitlines()[0] == stamp.strip()


def test_merge_of_two_unstamped_sides_stays_unstamped(tmp_path: Path):
    base = _write(tmp_path / "base", "", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "", "src/a.ts\tf( )\t50.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert Path(ours).read_text(encoding="utf-8").splitlines()[0] == "path\tlong_name\tcrap", \
        "stamping legacy marks with the running metric would assert a measurement nobody made"


# --- a real `git merge` through the driver, installed as docs/ratchet.md says --------
# Branch A (main, OURS) holds the file `ratchet seed --scope web` writes after a
# typescript raise; branch B (feature, THEIRS) lowers marks as a verify tighten
# does. Both files are written by hand: seed --scope and the per-scope writers
# land later in this release.

DOC = Path(__file__).resolve().parents[2] / "docs" / "ratchet.md"
API = "analysis python=13; coverage coveragepy=1"
WEB13 = "analysis typescript=13; coverage istanbul=1"
WEB14 = "analysis typescript=14; coverage istanbul=1"
WEB15 = "analysis typescript=15; coverage istanbul=1"
ROWS = {"api/a.py\tf( )": "50.0000", "api/b.py\tg( )": "40.0000", "web/c.ts\th( )": "30.0000"}


def _driver_lines() -> tuple[str, list[list[str]]]:
    """The `.gitattributes` line and the `git config` commands of the page's
    first two fences, with the driver run by this interpreter."""
    section = DOC.read_text(encoding="utf-8").split("## The git merge driver\n", 1)[1]
    attributes, config = re.findall(r"^```\n(.*?)^```", section, re.M | re.S)[:2]
    here = f"{shlex.quote(Path(sys.executable).as_posix())} -m crapkit"
    commands = [shlex.split(line.replace('"crapkit ratchet merge', f'"{here} ratchet merge'))
                for line in config.splitlines()]
    return attributes, commands


def _marks_text(web: str, rows: dict) -> str:
    head = ("# crapkit-stamp=2 lizard=1.24.0: crapkit-ratchet.tsv holds one stamp per scope, which "
            "crapkit 0.9.0 or later reads")
    lines = [head, "# crapkit-keys=1", f"#scope\tapi\t{API}", f"#scope\tweb\t{web}", "path\tlong_name\tcrap"]
    return "\n".join([*lines, *(f"{key}\t{crap}" for key, crap in sorted(rows.items()))]) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          env=child_env(), capture_output=True, text=True, encoding="utf-8")


def _branches(tmp_path: Path, a: tuple[str, dict], b: tuple[str, dict]) -> Path:
    """main at the base file, then A committed on main and B on feature."""
    repo = git_init_repo(tmp_path)
    attributes, commands = _driver_lines()
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    (repo / RATCHET).write_text(_marks_text(WEB13, ROWS), encoding="utf-8", newline="\n")
    git_commit_all(repo, "base marks")
    for command in commands:
        assert subprocess.run(command, cwd=repo, capture_output=True).returncode == 0, command
    for checkout, (web, rows) in ((["-b", "feature"], b), (["main"], a)):
        _git(repo, "checkout", "-q", *checkout)
        (repo / RATCHET).write_text(_marks_text(web, {**ROWS, **rows}), encoding="utf-8", newline="\n")
        git_commit_all(repo, f"marks on {checkout[-1]}")
    return repo


def _merged(repo: Path) -> str:
    return (repo / RATCHET).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_a_reseeded_scope_and_a_tighten_elsewhere_merge_through_git(tmp_path: Path):
    lowered = {"api/a.py\tf( )": "30.0000", "api/b.py\tg( )": "35.0000"}
    repo = _branches(tmp_path, (WEB14, {}), (WEB13, lowered))

    done = _git(repo, "merge", "feature", "-m", "merge feature")

    assert done.returncode == 0, done.stdout + done.stderr
    assert _merged(repo) == _marks_text(WEB14, {**ROWS, **lowered})
    assert ("ratchet merge: scope web's stamp came from OURS and THEIRS changed 2 mark(s); "
            "if verify names web, run ") in done.stderr, done.stderr


def test_a_web_mark_both_branches_moved_merges_at_the_lower_value(tmp_path: Path):
    repo = _branches(tmp_path, (WEB14, {"web/c.ts\th( )": "28.0000"}),
                     (WEB13, {"api/a.py\tf( )": "30.0000", "web/c.ts\th( )": "25.0000"}))

    done = _git(repo, "merge", "feature", "-m", "merge feature")

    assert done.returncode == 0, done.stdout + done.stderr
    assert _merged(repo) == _marks_text(WEB14, {**ROWS, "api/a.py\tf( )": "30.0000", "web/c.ts\th( )": "25.0000"})
    assert "scope web's stamp came from OURS and THEIRS changed 2 mark(s)" in done.stderr, done.stderr


def test_two_branches_that_reseeded_web_differently_leave_a_conflict_naming_web(tmp_path: Path):
    repo = _branches(tmp_path, (WEB14, {}), (WEB15, {}))

    done = _git(repo, "merge", "feature", "-m", "merge feature")

    said = done.stdout + done.stderr
    assert done.returncode == 1, said
    assert "CONFLICT (content): Merge conflict in crapkit-ratchet.tsv" in said, said
    assert (f"ratchet merge refused: OURS and THEIRS moved a stamp differently - scope web: OURS records "
            f"[lizard=1.24.0 {WEB14}] and THEIRS records [lizard=1.24.0 {WEB15}]") in said, said
