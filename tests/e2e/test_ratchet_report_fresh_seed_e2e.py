"""End-to-end: `ratchet seed` then `ratchet report`, with nothing committed yet.

The burn-down timeline comes from the ratchet file's git history, so a mark that
exists only in the working tree contributes no commits and used to report as 0
open marks one second after the seed printed "added 1". Open counts read the
working tree here; ages, repayment and velocity still read committed history,
and the gap between the two is stated on its own line.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

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

# ccn 9 and no coverage row: over target, so `ratchet seed` records exactly one mark.
TANGLED = (
    "export function tangled(a: number, b: number): number {\n"
    "  let r = 0;\n"
    "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
    "  if (a > 10 && b > 10) { r += 3; }\n"
    "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
    "  return r;\n}\n"
)

RATCHET = "crapkit-ratchet.tsv"
NOTE = ("  1 uncommitted mark(s) in crapkit-ratchet.tsv: open reads the working tree, "
        "ages and repayment read committed history")


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def seeded_repo(tmp_path: Path) -> Path:
    """A repo whose ratchet file holds one mark and has never been committed."""
    write(tmp_path / "src" / "app.ts", APP)
    write(tmp_path / "src" / "tangled.ts", TANGLED)
    write(tmp_path / "crapkit.toml", TOML)
    write(tmp_path / "make_cov.py", MAKE_COV)
    write(tmp_path / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "init")
    assert run_cli(tmp_path, "coverage", "--json").returncode == 0
    seeded = run_cli(tmp_path, "ratchet", "seed")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert "added 1" in seeded.stdout, seeded.stdout
    return tmp_path


def marks(repo: Path) -> list[str]:
    body = (repo / RATCHET).read_text(encoding="utf-8").splitlines()
    return [ln for ln in body if not ln.startswith(("#", "path\t"))]


def test_a_seeded_mark_is_open_before_the_tsv_is_committed(seeded_repo: Path):
    assert len(marks(seeded_repo)) == 1, "the fixture seeds exactly one mark"

    res = run_cli(seeded_repo, "ratchet", "report")
    assert res.returncode == 0, res.stdout + res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == ("ratchet burn-down: 1 open mark(s), 0 repaid "
                        "(0 in the last 30d, 0 in 90d)")
    assert lines[1] == NOTE
    assert lines[2].endswith("src/tangled.ts  " + marks(seeded_repo)[0].split("\t")[1])
    assert lines[2].strip().startswith("0d")


def test_the_json_report_carries_the_uncommitted_count(seeded_repo: Path):
    res = run_cli(seeded_repo, "ratchet", "report", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    report = json.loads(res.stdout)
    assert (report["open"], report["uncommitted"]) == (1, 1)
    assert report["dropped_total"] == 0
    assert [e["path"] for e in report["oldest"]] == ["src/tangled.ts"]


def test_committing_the_tsv_changes_nothing_but_the_uncommitted_note(seeded_repo: Path):
    before = run_cli(seeded_repo, "ratchet", "report", "--json")
    commit(seeded_repo, "seed the ratchet")

    after = run_cli(seeded_repo, "ratchet", "report", "--json")
    assert after.returncode == 0, after.stdout + after.stderr
    committed = json.loads(after.stdout)
    assert committed["uncommitted"] == 0
    dropped_note = {**json.loads(before.stdout), "uncommitted": 0}
    # anchor_ts moves off 0 once a commit exists; nothing a reader counts changes.
    assert {k: v for k, v in committed.items() if k != "anchor_ts"} == \
           {k: v for k, v in dropped_note.items() if k != "anchor_ts"}
    assert committed["anchor_ts"] > 0

    plain = run_cli(seeded_repo, "ratchet", "report")
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert NOTE not in plain.stdout
    assert plain.stdout.splitlines()[0] == ("ratchet burn-down: 1 open mark(s), 0 repaid "
                                            "(0 in the last 30d, 0 in 90d)")
