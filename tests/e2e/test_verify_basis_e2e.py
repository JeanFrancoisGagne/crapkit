"""What a verify is measured against, and what its verdict is worth as a record.

Four seams, all driven through the CLI against real git repos in tmp_path:
the diff basis (--base), a baseline that survives a fresh clone (--emit-baseline
/--baseline-tsv), attribution of findings a dirty tree produced, and the receipt
fields that let somebody re-check the verdict later. A hermetic istanbul
generator stands in for a coverage tool so the lanes are fast and exact.
"""
import hashlib
import json
import shutil
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


VERDICT_KEYS = ("ok", "gate_violations", "ratchet_regressions", "new_failures",
                "diff_uncovered", "diff_uncovered_count",
                "committed_findings", "dirty_findings", "dirty_failures")


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
    """Seven decisions: ccn 8. CRAP = 8**2 * (1 - cov)**3 + 8, so it is >= 8 at
    any coverage and can never clear a target of 6."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def config(*sources: str) -> str:
    command = "'\"" + PY + "\" " + GEN + " cov/unit.json " + " ".join(sources) + "'"
    return ("[crapkit]\ntarget = 6\n\n"
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
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


def verdict_of(res: subprocess.CompletedProcess) -> dict:
    payload = json.loads(res.stdout)
    return {k: payload[k] for k in VERDICT_KEYS}


@pytest.fixture()
def branch_repo(tmp_path: Path) -> Path:
    """main sits at the fork point. A feature branch adds ccn-8 debt in one
    commit, takes a fresh coverage run there, then makes a clean edit."""
    repo = new_repo(tmp_path, "branch")
    write(repo, "src/mod.py", clean_src("alpha"))
    write(repo, "src/debt.py", clean_src("beta"))
    write(repo, "crapkit.toml", config("src/mod.py", "src/debt.py"))
    commit_all(repo, "fork point")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "src/debt.py", tangled_src("tangled"))
    commit_all(repo, "add debt on the branch")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    write(repo, "src/mod.py", clean_src("alpha").replace("n = n + 1", "n = n + 2"))
    commit_all(repo, "a clean edit further up the branch")
    return repo


def test_a_mid_branch_coverage_run_shrinks_the_diff_basis(branch_repo: Path):
    res = run_cli(branch_repo, "verify", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["changed_files"] == 1, "only the last commit's file is in the diff"
    assert payload["gate_violations"] == [], "the branch's ccn-8 function is behind the baseline"


def test_base_drives_the_diff_from_the_fork_point(branch_repo: Path):
    assert run_cli(branch_repo, "verify", "--json").returncode == 0, "the shrunk basis passed"

    res = run_cli(branch_repo, "verify", "--base", "main", "--json")

    assert res.returncode == 6, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["changed_files"] == 2, "the whole branch is the diff, not the last commit"
    assert [(g["path"], g["ccn"]) for g in payload["gate_violations"]] == [("src/debt.py", 8)]


def test_base_picks_a_baseline_run_at_or_behind_the_fork_point(branch_repo: Path):
    res = run_cli(branch_repo, "verify", "--base", "main", "--json")

    payload = json.loads(res.stdout)
    assert payload["baseline_run"] == 1, "run 2 sits on the branch, past the fork point"
    assert payload["baseline_commit"] == git(branch_repo, "rev-parse", "main")


def test_base_without_a_run_at_the_fork_point_names_the_fix(tmp_path: Path):
    repo = new_repo(tmp_path, "nobase")
    write(repo, "src/mod.py", clean_src("alpha"))
    write(repo, "crapkit.toml", config("src/mod.py"))
    commit_all(repo, "fork point")
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "src/mod.py", clean_src("alpha").replace("n + 1", "n + 2"))
    commit_all(repo, "branch work")
    assert run_cli(repo, "coverage", "--json").returncode == 0, "the only run is on the branch"

    res = run_cli(repo, "verify", "--base", "main", "--json")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "at or behind" in res.stderr
    assert "crapkit coverage" in res.stderr


def test_two_ways_of_naming_a_baseline_at_once_is_refused(branch_repo: Path):
    res = run_cli(branch_repo, "verify", "--base", "main", "--baseline", "1")

    assert res.returncode == 2, res.stdout + res.stderr
    assert "not allowed with" in res.stderr


@pytest.fixture()
def portable_repo(tmp_path: Path) -> Path:
    """A baseline run, then a committed ccn-8 function the verdict must catch."""
    repo = new_repo(tmp_path, "portable")
    write(repo, "src/mod.py", clean_src("alpha"))
    write(repo, "crapkit.toml", config("src/mod.py"))
    commit_all(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    write(repo, "src/mod.py", tangled_src("tangled"))
    commit_all(repo, "add debt")
    return repo


def test_a_baseline_file_carries_the_verdict_across_a_wiped_store(portable_repo: Path):
    first = run_cli(portable_repo, "verify", "--emit-baseline", "base.tsv", "--json")
    assert first.returncode == 6, first.stdout + first.stderr
    emitted = (portable_repo / "base.tsv").read_bytes()
    assert emitted.startswith(b"# commit=")
    assert b"\r\n" not in emitted, "the baseline file must not carry the host's line separator"

    shutil.rmtree(portable_repo / ".crapkit")
    second = run_cli(portable_repo, "verify", "--baseline-tsv", "base.tsv",
                     "--emit-baseline", "again.tsv", "--json")

    assert second.returncode == 6, second.stdout + second.stderr
    assert verdict_of(second) == verdict_of(first)
    assert (portable_repo / "again.tsv").read_bytes() == emitted, "emit -> read -> emit is byte-stable"


def test_a_baseline_file_stamps_the_commit_the_diff_is_taken_from(portable_repo: Path):
    assert run_cli(portable_repo, "verify", "--emit-baseline", "base.tsv", "--json").returncode == 6
    stamp = (portable_repo / "base.tsv").read_text(encoding="utf-8").splitlines()[0]

    shutil.rmtree(portable_repo / ".crapkit")
    res = run_cli(portable_repo, "verify", "--baseline-tsv", "base.tsv", "--json")

    baseline_commit = json.loads(res.stdout)["baseline_commit"]
    assert stamp == f"# commit={baseline_commit} run_kind=coverage"
    assert baseline_commit == git(portable_repo, "rev-parse", "HEAD~1")


def test_without_a_baseline_file_a_storeless_repo_still_names_the_fix(portable_repo: Path):
    shutil.rmtree(portable_repo / ".crapkit")

    res = run_cli(portable_repo, "verify", "--json")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "no baseline snapshot in" in res.stderr


def test_a_corrupt_baseline_file_is_refused_rather_than_half_read(portable_repo: Path):
    write(portable_repo, "junk.tsv", "not a stamp at all\n")

    res = run_cli(portable_repo, "verify", "--baseline-tsv", "junk.tsv", "--json")

    assert res.returncode == 3, res.stdout + res.stderr
    assert "junk.tsv" in res.stderr


@pytest.fixture()
def dirty_repo(tmp_path: Path) -> Path:
    """One ccn-8 function committed, another sitting uncommitted in the tree."""
    repo = new_repo(tmp_path, "dirty")
    write(repo, "src/a.py", clean_src("alpha"))
    write(repo, "src/b.py", clean_src("beta"))
    write(repo, "crapkit.toml", config("src/a.py", "src/b.py"))
    commit_all(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    write(repo, "src/a.py", tangled_src("committed_debt"))
    commit_all(repo, "debt this session owns")
    write(repo, "src/b.py", tangled_src("someone_elses_debt"))
    return repo


def test_findings_from_uncommitted_edits_are_tagged_in_json(dirty_repo: Path):
    res = run_cli(dirty_repo, "verify", "--json")

    assert res.returncode == 6, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert {g["path"]: g["dirty"] for g in payload["gate_violations"]} == \
        {"src/a.py": False, "src/b.py": True}
    assert payload["committed_findings"] == 1
    assert payload["dirty_findings"] == 1


def test_the_text_report_splits_committed_from_dirty(dirty_repo: Path):
    res = run_cli(dirty_repo, "verify")

    assert res.returncode == 6, res.stdout + res.stderr
    lines = [ln for ln in res.stdout.splitlines() if "GATE" in ln]
    assert [("[dirty]" in ln) for ln in sorted(lines)] == [False, True]
    assert "findings: 1 committed / 1 dirty" in res.stdout


def test_a_dirty_tree_never_changes_the_exit_code(dirty_repo: Path):
    dirty = run_cli(dirty_repo, "verify", "--json")
    commit_all(dirty_repo, "commit the other session's edit")
    committed = run_cli(dirty_repo, "verify", "--json")

    assert dirty.returncode == committed.returncode == 6
    assert json.loads(committed.stdout)["dirty_findings"] == 0
    assert json.loads(committed.stdout)["committed_findings"] == 2


@pytest.fixture()
def receipt_repo(tmp_path: Path) -> Path:
    repo = new_repo(tmp_path, "receipt")
    write(repo, "src/mod.py", clean_src("alpha"))
    write(repo, "crapkit.toml", config("src/mod.py"))
    commit_all(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    return repo


def test_the_verdict_names_the_tools_that_produced_it(receipt_repo: Path):
    res = run_cli(receipt_repo, "verify", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    versions = json.loads(res.stdout)["tool_versions"]
    assert set(versions) == {"crapkit", "lizard"}
    assert all(v and isinstance(v, str) for v in versions.values())


def test_the_verdict_hashes_the_ratchet_it_was_decided_against(receipt_repo: Path):
    first = run_cli(receipt_repo, "verify", "--json")
    assert json.loads(first.stdout)["ratchet_sha256"] is None, "no ratchet file existed yet"

    second = run_cli(receipt_repo, "verify", "--json")

    ratchet = (receipt_repo / "crapkit-ratchet.tsv").read_bytes()
    # the metric stamp rides the file, so the receipt hashes stamped bytes
    assert ratchet.splitlines()[0].startswith(b"# crapkit-analysis=")
    assert ratchet.endswith(b"path\tlong_name\tcrap\n")
    assert json.loads(second.stdout)["ratchet_sha256"] == hashlib.sha256(ratchet).hexdigest()
