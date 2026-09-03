"""End-to-end: the `nesting` column a user reads off `inventory --export` is a depth.

Until 0.5.0 a Python row read lizard's ND count, which counts structures rather
than depth: a flat function of seven `if`s read `nesting: 7`, and two personas
in the 0.4.15 review took that for seven levels deep. Spec item 15 derives the
Python value from crapkit's own cognitive pass, whose per-function stack is a
depth, and leaves brace languages on lizard's column.
"""
from pathlib import Path

from conftest import git_commit_all, git_init_repo, run_cli

TOML = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["lib"]
languages = ["python"]
coverage_optional = true

[[scope]]
name = "ts"
paths = ["src"]
languages = ["typescript"]
coverage_optional = true
"""

FLAT = "def flat(n):\n" + "".join(
    f"    if n == {i}:\n        return {i}\n" for i in range(7)) + "    return -1\n"

DEEP = """def deep(a, b, c):
    if a:
        if b:
            if c:
                return 1
    return 0
"""

# lizard's ND reads 3 here: the `for`, the `if`, and the first `&&` of a
# condition each add a level. That is the number brace languages keep.
TS = """export function f(a: number, b: number) {
  for (const x of [a, b]) {
    if (x > 0 && x < 9) { return x; }
  }
  return 0;
}
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_init_repo(repo)
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (repo / "lib").mkdir()
    (repo / "lib" / "flat.py").write_text(FLAT, encoding="utf-8")
    (repo / "lib" / "deep.py").write_text(DEEP, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "f.ts").write_text(TS, encoding="utf-8")
    git_commit_all(repo, "init")
    return repo


def _nesting_by_name(repo: Path) -> dict[str, int]:
    res = run_cli(repo, "inventory", "--export", "inv.tsv", "--json")
    assert res.returncode == 0, res.stderr
    header, *rows = (repo / "inv.tsv").read_text(encoding="utf-8").strip().splitlines()
    cols = header.split("\t")
    out = {}
    for row in rows:
        vals = dict(zip(cols, row.split("\t")))
        out[vals["long_name"].split("(")[0].strip()] = int(vals["nesting"])
    return out


def test_a_flat_python_function_reads_depth_one_and_a_three_deep_one_reads_three(tmp_path):
    nesting = _nesting_by_name(_repo(tmp_path))

    assert nesting["flat"] == 1, f"seven flat ifs are one level deep, not {nesting['flat']}"
    assert nesting["deep"] == 3


def test_a_brace_language_row_keeps_lizards_depth(tmp_path):
    nesting = _nesting_by_name(_repo(tmp_path))

    assert nesting["f"] == 3
