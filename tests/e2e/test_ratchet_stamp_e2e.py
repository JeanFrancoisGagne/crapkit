"""End-to-end: the ratchet's metric stamp through the CLI seam.

A scoring change that leaves old marks in place is invisible — the marks still
parse, verify still runs, and every number it compares came from different rules.
The stamp turns that into an exit 3 instead of a green run.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def run_cli(repo: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=180, env=env)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=tmp_path, check=True, capture_output=True)
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


def test_merge_refuses_two_sides_measured_by_different_metrics(tmp_path: Path):
    base = _write(tmp_path / "base", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "# crapkit-analysis=3 lizard=1.24.0\n", "src/a.ts\tf( )\t20.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode != 0, (res.returncode, res.stdout)
    assert "crapkit-analysis=2 lizard=1.17.10" in res.stderr
    assert "crapkit-analysis=3 lizard=1.24.0" in res.stderr
    assert Path(ours).read_text(encoding="utf-8").endswith("src/a.ts\tf( )\t30.0000\n"), \
        "a refused merge must not rewrite ours"


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
