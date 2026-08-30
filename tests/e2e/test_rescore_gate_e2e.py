"""`rescore --gate` is the commit gate, hours before the commit.

Mid-session an agent rescores to watch complexity move; without a verdict it
learns it broke the ceiling only when the pre-commit hook refuses. The flag
applies the hook's own ccn-only policy to the rescored functions and exits 6.
The hermetic istanbul generator stands in for a coverage tool.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import cli_runner

PY = sys.executable
GEN = "gen_cov.py"

GEN_COV = """\
# Fixture coverage generator: istanbul coverage-final.json for the named sources.
# argv: <artifact-path> <source> [<source> ...]
import json
import os
import sys

artifact, sources = sys.argv[1], sys.argv[2:]
out = {}
for rel in sources:
    with open(rel, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    key = os.path.join(os.getcwd(), rel.replace("/", os.sep))
    starts = [i + 1 for i, ln in enumerate(lines) if ln.startswith("def ")]
    fn_map, f_hits = {}, {}
    for n, start in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(lines)
        fn_map[str(n)] = {"name": lines[start - 1][4:].split("(")[0],
                          "decl": {"start": {"line": start}},
                          "loc": {"start": {"line": start}, "end": {"line": end}}}
        f_hits[str(n)] = 1
    stmt_map, s_hits = {}, {}
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.startswith("def "):
            continue
        stmt_map[str(i)] = {"start": {"line": i}, "end": {"line": i}}
        s_hits[str(i)] = 1
    out[key] = {"path": key, "fnMap": fn_map, "f": f_hits, "branchMap": {}, "b": {},
                "statementMap": stmt_map, "s": s_hits}
os.makedirs(os.path.dirname(artifact) or ".", exist_ok=True)
with open(artifact, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""


run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def clean_src(name: str) -> str:
    """One decision: ccn 2, under the target of 6."""
    return f"def {name}(n):\n    if n > 1:\n        n = n + 1\n    return n\n"


def tangled_src(name: str, decisions: int = 7) -> str:
    """`decisions` decisions: ccn is one more. Same signature as clean_src, so the
    overlay joins it by name and it carries the baseline's coverage of 100% — CRAP
    is then ccn**2 * (1 - 1.0)**3 + ccn = ccn, and only the ccn rule can catch it."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, decisions + 1))
    return f"def {name}(n):\n{body}    return n\n"


def config(*sources: str, scope_target: str = "") -> str:
    command = "'\"" + PY + "\" " + GEN + " cov/unit.json " + " ".join(sources) + "'"
    return ("[crapkit]\ntarget = 6\n\n"
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
            f"{scope_target}\n"
            f'[[lane]]\nname = "unit"\ncommand = {command}\nartifact = "cov/unit.json"\n'
            'parser = "istanbul"\nscopes = ["src"]\n')


def new_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    write(repo, ".gitignore", ".crapkit/\ncov/\n__pycache__/\n")
    write(repo, GEN, GEN_COV)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    return repo


def breached_repo(tmp_path: Path, name: str, scope_target: str = "") -> Path:
    """A scored run over a clean tree, then a ccn-8 body in the working tree."""
    repo = new_repo(tmp_path, name)
    write(repo, "src/mod.py", clean_src("alpha"))
    write(repo, "crapkit.toml", config("src/mod.py", scope_target=scope_target))
    commit_all(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    write(repo, "src/mod.py", tangled_src("alpha"))
    return repo


@pytest.fixture()
def breached(tmp_path: Path) -> Path:
    return breached_repo(tmp_path, "gate")


def test_the_gate_fails_at_the_edit_instead_of_at_the_commit(breached: Path):
    res = run_cli(breached, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 6, res.stdout + res.stderr
    gate = [ln for ln in res.stderr.splitlines() if "GATE" in ln]
    assert len(gate) == 1, res.stderr
    assert "crap      8.0" in gate[0] and "ccn   8" in gate[0] and "cov 100%" in gate[0]
    assert "src/mod.py:1" in gate[0] and "-> decompose" in gate[0]


def test_without_the_flag_the_same_breach_is_still_advisory(breached: Path):
    plain = run_cli(breached, "rescore", "src/mod.py")
    gated = run_cli(breached, "rescore", "--gate", "src/mod.py")

    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert plain.stderr == "", "the advisory rescore says nothing on stderr"
    assert plain.stdout == gated.stdout, "the flag adds a verdict, not a different table"


def test_a_scope_that_allows_ccn_ten_lets_the_same_function_through(tmp_path: Path):
    repo = breached_repo(tmp_path, "tolerant", scope_target="target = 10\n")

    res = run_cli(repo, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "GATE" not in res.stderr


def test_a_function_under_its_ceiling_gates_to_zero(tmp_path: Path):
    repo = breached_repo(tmp_path, "clean")
    write(repo, "src/mod.py", clean_src("alpha").replace("n + 1", "n + 2"))

    res = run_cli(repo, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stderr == ""


def test_json_stays_parseable_while_the_gate_fires(breached: Path):
    res = run_cli(breached, "rescore", "--gate", "--json", "src/mod.py")

    assert res.returncode == 6, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert [(f["path"], f["ccn"]) for f in payload["functions"]] == [("src/mod.py", 8)]
    assert "GATE" in res.stderr


# --- parity with the hook: the same functions, judged the same way -------------
#
# A repo that ran `ratchet seed` carries over-ceiling functions nobody is
# editing. The hook never looks at them, so neither may the flag that claims to
# be the hook's verdict hours early.

def legacy_repo(tmp_path: Path, name: str, *, seed: bool) -> Path:
    """Committed ccn-8 debt (`legacy`) beside a clean `fresh`, scored, optionally
    marked. `legacy` spans lines 1-16 and `fresh` lines 18-21."""
    repo = new_repo(tmp_path, name)
    write(repo, "src/mod.py", tangled_src("legacy") + "\n" + clean_src("fresh"))
    write(repo, "crapkit.toml", config("src/mod.py"))
    commit_all(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    if seed:
        assert run_cli(repo, "ratchet", "seed").returncode == 0
        commit_all(repo, "seed the debt")
    return repo


def edit_only_fresh(repo: Path) -> None:
    write(repo, "src/mod.py", tangled_src("legacy") + "\n"
          + clean_src("fresh").replace("n + 1", "n + 3"))


def test_marked_debt_nobody_touched_is_not_a_breach(tmp_path: Path):
    repo = legacy_repo(tmp_path, "seeded", seed=True)
    edit_only_fresh(repo)

    res = run_cli(repo, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "GATE" not in res.stderr


def test_the_gate_and_the_hook_agree_on_the_same_tree(tmp_path: Path):
    repo = legacy_repo(tmp_path, "parity", seed=True)
    edit_only_fresh(repo)
    gated = run_cli(repo, "rescore", "--gate", "src/mod.py")
    git(repo, "add", "-A")

    hook = run_cli(repo, "hook-precommit")

    assert (gated.returncode, hook.returncode) == (0, 0), gated.stderr + hook.stdout


def test_untouched_debt_carrying_no_mark_is_still_out_of_scope(tmp_path: Path):
    repo = legacy_repo(tmp_path, "unseeded", seed=False)
    edit_only_fresh(repo)

    res = run_cli(repo, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 0, res.stdout + res.stderr


def test_editing_a_marked_function_past_its_mark_gates(tmp_path: Path):
    repo = legacy_repo(tmp_path, "regressed", seed=True)
    write(repo, "src/mod.py", tangled_src("legacy", 11) + "\n" + clean_src("fresh"))

    res = run_cli(repo, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 6, res.stdout + res.stderr
    gate = [ln for ln in res.stderr.splitlines() if "GATE" in ln]
    assert len(gate) == 1 and "legacy( n )" in gate[0], res.stderr
    assert "ccn  12" in gate[0]


def test_a_new_breach_beside_untouched_debt_is_the_only_one_reported(tmp_path: Path):
    repo = legacy_repo(tmp_path, "mixed", seed=True)
    write(repo, "src/mod.py", tangled_src("legacy") + "\n" + tangled_src("fresh"))

    res = run_cli(repo, "rescore", "--gate", "src/mod.py")

    assert res.returncode == 6, res.stdout + res.stderr
    gate = [ln for ln in res.stderr.splitlines() if "GATE" in ln]
    assert len(gate) == 1 and "fresh( n )" in gate[0], res.stderr


def test_an_untracked_file_is_gated_not_silently_passed(tmp_path: Path):
    """The second onboarding audit: an untracked file's violation was printed
    but exited 0, because untracked content is invisible to git diff."""
    repo = legacy_repo(tmp_path, "untracked", seed=False)
    write(repo, "src/newfile.py", tangled_src("fresh"))  # never git-added

    res = run_cli(repo, "rescore", "--gate", "src/newfile.py")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "untracked" in res.stderr
    gate = [ln for ln in res.stderr.splitlines() if "GATE" in ln]
    assert len(gate) == 1 and "fresh( n )" in gate[0], res.stderr
