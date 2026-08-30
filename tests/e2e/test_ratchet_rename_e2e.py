"""End-to-end: marks follow code that moved.

Renaming a file used to cost its high-water mark — prune saw the old path absent
from the run and called the debt repaid, so the same function re-entered the
codebase unmarked. `ratchet move` does it by hand; prune now consults git.
"""
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

TANGLED = (
    "export function tangled(a: number, b: number): number {\n"
    "  let r = 0;\n"
    "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
    "  if (a > 10 && b > 10) { r += 3; }\n"
    "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
    "  return r;\n}\n"
)

RATCHET = "crapkit-ratchet.tsv"


run_cli = cli_runner(timeout=180)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def marks(repo: Path) -> dict[str, tuple[str, str]]:
    """path -> (long_name, crap) straight off the committed TSV."""
    text = (repo / RATCHET).read_text(encoding="utf-8")
    rows = [ln.split("\t") for ln in text.splitlines()
            if "\t" in ln and not ln.startswith(("#", "path\t"))]
    return {r[0]: (r[1], r[2]) for r in rows}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_move_repaths_marks_at_their_recorded_values(repo: Path):
    (repo / RATCHET).write_text(
        "path\tlong_name\tcrap\n"
        "src/deep/a.ts\tf( )\t42.0000\n"
        "src/other.ts\tg( )\t11.0000\n", encoding="utf-8")

    res = run_cli(repo, "ratchet", "move", "src/deep/", "lib/deep/")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert marks(repo) == {"lib/deep/a.ts": ("f( )", "42.0000"),
                           "src/other.ts": ("g( )", "11.0000")}
    assert "1 mark" in res.stdout


def test_move_that_matches_nothing_is_a_loud_config_error(repo: Path):
    (repo / RATCHET).write_text("path\tlong_name\tcrap\nsrc/a.ts\tf( )\t42.0000\n",
                                encoding="utf-8")

    res = run_cli(repo, "ratchet", "move", "src/nowhere.ts", "lib/nowhere.ts")
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "src/nowhere.ts" in res.stderr
    assert marks(repo) == {"src/a.ts": ("f( )", "42.0000")}, "a failed move rewrites nothing"


def test_prune_follows_a_git_rename_instead_of_repaying_the_debt(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    before = marks(repo)
    assert "src/tangled.ts" in before, before

    git(repo, "mv", "src/tangled.ts", "src/knots.ts")
    git(repo, "commit", "-q", "-m", "rename tangled")
    assert run_cli(repo, "coverage", "--json").returncode == 0, "re-score at the new path"

    res = run_cli(repo, "ratchet", "prune")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)

    after = marks(repo)
    assert "src/tangled.ts" not in after, "the old path must not linger"
    assert "src/knots.ts" in after, "a renamed file is a move, not repaid debt"
    assert after["src/knots.ts"] == before["src/tangled.ts"], \
        "the mark travels at its recorded value; a rename repays nothing"


def test_prune_still_drops_a_mark_whose_code_really_left(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0

    (repo / "src" / "tangled.ts").unlink()
    git(repo, "rm", "-q", "src/tangled.ts")
    git(repo, "commit", "-q", "-m", "delete tangled")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    res = run_cli(repo, "ratchet", "prune")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert marks(repo) == {}, "deleted code still forfeits its mark"
