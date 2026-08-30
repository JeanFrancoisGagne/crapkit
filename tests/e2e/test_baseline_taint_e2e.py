"""A failed verify's findings survive any number of `coverage` runs.

The audit's repro, end to end: land debt past an unarmed hook, watch `verify`
refuse it, run `coverage` (a dashboard cron, or anyone), commit something
unrelated, and verify again. Before the taint rule that second verify said OK
and the ccn-8 function was never flagged again. A hermetic istanbul generator
stands in for a coverage tool so the lanes are fast and exact.
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
    """One decision: ccn 2, comfortably under the target of 6."""
    return f"def {name}(n):\n    if n > 1:\n        n = n + 1\n    return n\n"


def tangled_src(name: str) -> str:
    """Seven decisions: ccn 8, which no coverage can clear against a target of 6."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def config(*sources: str) -> str:
    command = "'\"" + PY + "\" " + GEN + " cov/unit.json " + " ".join(sources) + "'"
    return ("[crapkit]\ntarget = 6\n\n"
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
            f'[[lane]]\nname = "unit"\ncommand = {command}\nartifact = "cov/unit.json"\n'
            'parser = "istanbul"\nscopes = ["src"]\n')


@pytest.fixture()
def laundered(tmp_path: Path) -> Path:
    """run 1 coverage, run 2 verify FAILED on a ccn-8 function, run 3 coverage.

    Run 3 is the laundering run: it scores the very tree run 2 refused, and
    picking it as the baseline would retire run 2's finding.
    """
    repo = tmp_path / "laundered"
    repo.mkdir(parents=True)
    write(repo, ".gitignore", ".crapkit/\ncov/\n__pycache__/\n")
    write(repo, GEN, GEN_COV)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")

    write(repo, "src/mod.py", clean_src("alpha"))
    write(repo, "crapkit.toml", config("src/mod.py"))
    commit_all(repo, "adopt crapkit")
    assert run_cli(repo, "coverage", "--json").returncode == 0, "run 1"

    write(repo, "src/legacy.py", tangled_src("legacy_router"))
    write(repo, "crapkit.toml", config("src/mod.py", "src/legacy.py"))
    commit_all(repo, "land legacy_router without the hook armed")
    assert run_cli(repo, "verify", "--json").returncode == 6, "run 2 records the gate finding"

    assert run_cli(repo, "coverage", "--json").returncode == 0, "run 3"
    write(repo, "src/other.py", clean_src("beta"))
    write(repo, "crapkit.toml", config("src/mod.py", "src/legacy.py", "src/other.py"))
    commit_all(repo, "unrelated edit")
    return repo


def test_a_coverage_run_cannot_retire_a_failed_verifys_finding(laundered: Path):
    res = run_cli(laundered, "verify", "--json")

    assert res.returncode == 6, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["baseline_run"] == 1, "run 3 scored the tree run 2 refused"
    assert [g["long_name"] for g in payload["gate_violations"]] == \
        ["legacy_router( n )"], "the finding run 3 would have buried"


def test_the_refusal_names_the_failed_run_its_findings_and_both_escapes(laundered: Path):
    res = run_cli(laundered, "verify", "--json")

    assert "run 3 is not the baseline" in res.stderr, res.stderr
    assert "verify run 2 FAILED with 1 finding(s)" in res.stderr
    assert "--baseline 3" in res.stderr, "the deliberate-acceptance escape"
    assert "Fix" in res.stderr, "the other escape is to answer the findings"


def test_naming_the_newer_run_explicitly_accepts_it(laundered: Path):
    res = run_cli(laundered, "verify", "--baseline", "3", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["baseline_run"] == 3
    assert "not the baseline" not in res.stderr, "an explicit pick is not refused"


def test_fixing_the_function_is_the_other_way_through(laundered: Path):
    write(laundered, "src/legacy.py", clean_src("legacy_router"))
    commit_all(laundered, "decompose legacy_router")

    res = run_cli(laundered, "verify", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["baseline_run"] == 1


def test_runs_list_marks_the_run_verify_would_compare_against(laundered: Path):
    res = run_cli(laundered, "runs", "list")

    assert res.returncode == 0, res.stdout + res.stderr
    marked = [ln for ln in res.stdout.splitlines() if ln.endswith("baseline")]
    assert len(marked) == 1, res.stdout
    assert marked[0].startswith("run   1 "), "run 3 is trusted but tainted"


def test_runs_list_json_carries_the_same_marker(laundered: Path):
    res = run_cli(laundered, "runs", "list", "--json")

    runs = json.loads(res.stdout)["runs"]
    assert [r["id"] for r in runs if r["baseline"]] == [1]
    assert [r["findings"] for r in runs if r["id"] == 2] == [1]
