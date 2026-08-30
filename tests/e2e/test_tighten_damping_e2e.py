"""End-to-end: verify will not tighten a mark on a measurement that bounced.

The lane here scores the same bytes two ways, which is what the reporter's repo
did by accident: coverage attribution raced a subprocess data file, and one
function came back 20.0 on one run of a commit and 72.0 on the next. Tightening
on the lucky half of that and failing on the unlucky half turns a nondeterministic
input into a coin-flip gate, so verify holds the mark and says so.
"""
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
    'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n'
)

# The lane, with the race made explicit: cov_mode.txt decides whether the
# function reads as invoked. Same commit, same bytes, two answers.
MAKE_COV = (
    "import json, os\n"
    "hits = 1 if open('cov_mode.txt').read().strip() == 'lucky' else 0\n"
    "app = os.path.join(os.getcwd(), 'src', 'tangled.ts')\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'tangled', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 7}}}},\n"
    "             'f': {'0': hits}, 'branchMap': {}, 'b': {}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n"
)

# ccn 8. Invoked it scores CRAP 8.0, uninvoked 72.0: a 9x move on one commit.
TANGLED = (
    "export function tangled(a: number, b: number): number {\n"
    "  let r = 0;\n"
    "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
    "  if (a > 10 && b > 10) { r += 3; }\n"
    "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
    "  return r;\n}\n"
)

RATCHET = "crapkit-ratchet.tsv"
MARK = "src/tangled.ts\ttangled ( a , b )\t200.0000\n"


run_cli = cli_runner(timeout=180)


def set_mode(repo: Path, mode: str) -> None:
    (repo / "cov_mode.txt").write_text(mode + "\n", encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\ncov_mode.txt\n", encoding="utf-8")
    set_mode(tmp_path, "lucky")
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "init")
    return tmp_path


def loose_mark(repo: Path) -> None:
    """A mark well above either measurement, so the next green verify has a real
    tighten to make. Seeded first, so the file keeps the metric stamp verify checks."""
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    stamp = (repo / RATCHET).read_text(encoding="utf-8").splitlines()[0]
    (repo / RATCHET).write_text(f"{stamp}\npath\tlong_name\tcrap\n{MARK}", encoding="utf-8")


def marks(repo: Path) -> str:
    return (repo / RATCHET).read_text(encoding="utf-8")


def baseline(repo: Path) -> None:
    assert run_cli(repo, "coverage", "--json").returncode == 0, "baseline run"


def test_a_measurement_that_bounced_on_one_commit_leaves_the_mark_alone(repo: Path):
    baseline(repo)
    loose_mark(repo)
    set_mode(repo, "unlucky")

    res = run_cli(repo, "verify", "--json")

    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert "measurement moved 8.0 -> 72.0 on the same commit; not tightening" in res.stderr
    assert "tangled ( a , b )" in res.stderr, "the refusal has to name the function"
    assert MARK in marks(repo), "the mark held at 200.0"


def test_the_refusal_leaves_json_output_parseable(repo: Path):
    """The line goes to stderr, where every other verify warning goes: `--json`
    prints one object on stdout and a caller parses all of it."""
    import json

    baseline(repo)
    loose_mark(repo)
    set_mode(repo, "unlucky")

    res = run_cli(repo, "verify", "--json")

    assert json.loads(res.stdout)["ok"] is True
    assert "NO TIGHTEN" not in res.stdout


def test_a_stable_improvement_still_tightens(repo: Path):
    """Both runs of the commit measure 8.0, so nothing is in dispute and the
    whole gain from 200.0 lands."""
    baseline(repo)
    loose_mark(repo)

    res = run_cli(repo, "verify", "--json")

    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert "NO TIGHTEN" not in res.stderr
    assert "src/tangled.ts\ttangled ( a , b )\t8.0000" in marks(repo)


def test_no_tighten_never_rewrites_the_marks_file(repo: Path):
    """The blunt escape: the verdict still stands, the file is left as committed."""
    baseline(repo)
    loose_mark(repo)
    before = marks(repo)

    res = run_cli(repo, "verify", "--json", "--no-tighten")

    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert marks(repo) == before
