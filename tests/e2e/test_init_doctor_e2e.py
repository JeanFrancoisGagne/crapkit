"""End-to-end: `crapkit init` sniffs a starter config; `crapkit doctor` checks that
config and repo still agree; `crapkit ratchet seed/prune` manage marks first-class."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=120, env=dict(os.environ))


def _git_commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def bare_repo(tmp_path: Path) -> Path:
    """A source tree with NO crapkit.toml: what init has to work with."""
    repo = tmp_path / "bare"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.ts").write_text("export function f(a: number) { return a ? 1 : 2; }\n",
                                         encoding="utf-8")
    (repo / "pylib").mkdir()
    (repo / "pylib" / "mod.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _git_commit_all(repo, "init")
    return repo


def test_init_sniffs_scopes_and_writes_a_parseable_config(bare_repo: Path):
    res = run_cli(bare_repo, "init")
    assert res.returncode == 0, res.stderr
    from crapkit.config import load_config_text
    cfg = load_config_text((bare_repo / "crapkit.toml").read_text(encoding="utf-8"))
    by_name = {s.name: s for s in cfg.scopes}
    assert by_name["src"].languages == ("typescript",)
    assert by_name["pylib"].languages == ("python",)
    assert any("node_modules" in g for g in cfg.exclude_globs)
    assert "lane" in res.stdout.lower(), "init must say lanes still need writing"


def test_init_refuses_to_clobber_an_existing_config(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    res = run_cli(bare_repo, "init")
    assert res.returncode == 3
    assert "already exists" in res.stderr


def _bare_git_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    return repo


def test_init_blames_untracked_source_instead_of_the_working_directory(tmp_path: Path):
    """crapkit reads `git ls-files`, so source nobody added is source it cannot
    see. Blaming the directory sends a first-time user to the wrong place."""
    repo = _bare_git_repo(tmp_path, "untracked")
    (repo / "app").mkdir()
    (repo / "app" / "m.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    (repo / "app" / "n.py").write_text("def h(x):\n    return x and 1\n", encoding="utf-8")

    res = run_cli(repo, "init")

    assert res.returncode == 3
    assert "git-tracked" in res.stderr and "git add" in res.stderr
    assert "2 untracked source file(s)" in res.stderr
    assert "is this the repo root" not in res.stderr


def test_git_add_alone_is_enough_to_make_the_same_repo_scopeable(tmp_path: Path):
    repo = _bare_git_repo(tmp_path, "addable")
    (repo / "app").mkdir()
    (repo / "app" / "m.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)

    res = run_cli(repo, "init")

    assert res.returncode == 0, res.stderr
    assert "1 scope(s): app" in res.stdout


def test_a_repo_with_no_source_at_all_still_blames_the_directory(tmp_path: Path):
    repo = _bare_git_repo(tmp_path, "docsonly")
    (repo / "notes.md").write_text("hi\n", encoding="utf-8")

    res = run_cli(repo, "init")

    assert res.returncode == 3
    assert "is this the repo root" in res.stderr


def test_init_ignores_its_own_store_in_the_consumers_gitignore(bare_repo: Path):
    res = run_cli(bare_repo, "init")

    assert res.returncode == 0, res.stderr
    assert ".crapkit/" in (bare_repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "added to .gitignore" in res.stdout and ".crapkit/" in res.stdout


def test_the_gitignore_covers_what_the_lanes_init_wrote_will_drop(bare_repo: Path):
    """The lane's own artifact needs no entry any more — it lands under the
    already-ignored .crapkit/. What the pytest runner drops elsewhere does."""
    (bare_repo / "pyproject.toml").write_text("[project]\nname = \"x\"\n", encoding="utf-8")
    _git_commit_all(bare_repo, "add pyproject")

    res = run_cli(bare_repo, "init")

    assert res.returncode == 0, res.stderr
    ignored = (bare_repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert [ln for ln in ignored if not ln.startswith("#")] == [".crapkit/", ".coverage",
                                                                "__pycache__/"], res.stdout


def test_running_the_lane_init_scaffolded_leaves_git_status_empty(tmp_path: Path):
    """The class this closes: a consumer adopts crapkit, runs the lane it was
    handed, and its root grows a coverage file per lane. Real pytest, real
    coverage report, real `git status`."""
    repo = _bare_git_repo(tmp_path, "clean")
    (repo / "calc").mkdir()
    (repo / "calc" / "grade.py").write_text("def classify(n):\n    return 1 if n else 0\n",
                                            encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_grade.py").write_text(
        "from calc.grade import classify\n\n\ndef test_classify():\n    assert classify(1) == 1\n",
        encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n',
                                         encoding="utf-8")
    _git_commit_all(repo, "sources")
    assert run_cli(repo, "init").returncode == 0
    _git_commit_all(repo, "adopt crapkit")

    res = run_cli(repo, "coverage")

    assert res.returncode == 0, res.stdout + res.stderr
    assert (repo / ".crapkit" / "cov" / "py.json").is_file()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            check=True, capture_output=True, text=True).stdout
    assert status.strip() == "", status


def test_init_appends_to_an_existing_gitignore_and_keeps_what_was_there(bare_repo: Path):
    (bare_repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git_commit_all(bare_repo, "ignore node_modules")

    assert run_cli(bare_repo, "init").returncode == 0

    text = (bare_repo / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\n")
    assert ".crapkit/" in text.splitlines()


def test_git_never_sees_the_store_init_just_ignored(bare_repo: Path):
    """The whole point of the entries: crapkit's own writes must not show up as
    untracked files in the consumer's next `git status`."""
    assert run_cli(bare_repo, "init").returncode == 0
    _git_commit_all(bare_repo, "adopt crapkit")
    (bare_repo / ".crapkit").mkdir(exist_ok=True)
    (bare_repo / ".crapkit" / "cache.json").write_text("{}", encoding="utf-8")

    status = subprocess.run(["git", "status", "--porcelain"], cwd=bare_repo,
                            check=True, capture_output=True, text=True).stdout

    assert status.strip() == "", status


def test_version_names_the_program_that_produced_the_number(tmp_path: Path):
    res = run_cli(tmp_path, "--version")

    assert res.returncode == 0, res.stderr
    assert re.fullmatch(r"crapkit \d+\.\d+\.\d+", res.stdout.strip()), res.stdout


def test_doctor_passes_on_a_healthy_config(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    res = run_cli(bare_repo, "doctor")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok" in res.stdout


def test_doctor_flags_unknown_keys_with_their_names(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    with open(bare_repo / "crapkit.toml", "a", encoding="utf-8") as f:
        f.write('\n[[scope]]\nname = "typo"\npaths = ["src"]\nlanguages = ["typescript"]\ntarrget = 5\n')
    res = run_cli(bare_repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "tarrget" in res.stdout


def test_doctor_names_the_keys_the_table_would_have_accepted(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    toml = bare_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        "target = 6", "target = 6\nchurn_windo_months = 3"), encoding="utf-8")

    res = run_cli(bare_repo, "doctor")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "churn_windo_months" in res.stdout
    assert "[crapkit] accepts these keys:" in res.stdout
    assert "churn_window_months" in res.stdout, "the real spelling rides the rejection"


def test_doctor_flags_a_scope_that_matches_no_files(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    with open(bare_repo / "crapkit.toml", "a", encoding="utf-8") as f:
        f.write('\n[[scope]]\nname = "ghost"\npaths = ["nonexistent"]\nlanguages = ["python"]\n')
    res = run_cli(bare_repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "ghost" in res.stdout and "0 files" in res.stdout


def test_doctor_flags_a_lane_whose_cwd_is_missing(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    with open(bare_repo / "crapkit.toml", "a", encoding="utf-8") as f:
        f.write('\n[[lane]]\nname = "unit"\ncommand = "npx vitest run --coverage"\n'
                'artifact = "coverage/coverage-final.json"\nparser = "istanbul"\n'
                'scopes = ["src"]\ncwd = "nowhere"\n')
    res = run_cli(bare_repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "nowhere" in res.stdout


def test_doctor_show_files_lists_scope_members(bare_repo: Path):
    assert run_cli(bare_repo, "init").returncode == 0
    res = run_cli(bare_repo, "doctor", "--show-files")
    assert "src/app.ts" in res.stdout


@pytest.fixture()
def tight_repo(tmp_path: Path) -> Path:
    """mini_repo sources with target = 1 and only the hermetic unit lane, so its
    real functions all sit over the ceiling and seed has something to mark."""
    repo = tmp_path / "tight"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 1\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
        '[exclude]\nglobs = ["**/node_modules/**", "**/*.test.ts"]\n\n'
        '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
        'artifact = "coverage/coverage-final.json"\nparser = "istanbul"\nscopes = ["src"]\n',
        encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncoverage/\n__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _git_commit_all(repo, "init")
    return repo


def test_ratchet_report_burn_down_from_history(tight_repo: Path):
    assert run_cli(tight_repo, "coverage", "--json").returncode == 0
    assert run_cli(tight_repo, "ratchet", "seed").returncode == 0
    # mark rows only: the file opens with a metric stamp comment, then the header
    marks = len([ln for ln in (tight_repo / "crapkit-ratchet.tsv").read_text(
        encoding="utf-8").strip().splitlines() if not ln.startswith(("#", "path\t"))])
    assert marks >= 1
    _git_commit_all(tight_repo, "seed the debt")

    (tight_repo / "src" / "app.ts").unlink()
    _git_commit_all(tight_repo, "drop app.ts")
    assert run_cli(tight_repo, "coverage", "--json").returncode == 0
    assert run_cli(tight_repo, "ratchet", "prune").returncode == 0
    _git_commit_all(tight_repo, "repay the debt")

    res = run_cli(tight_repo, "ratchet", "report", "--json")
    assert res.returncode == 0, res.stderr
    rep = json.loads(res.stdout)
    assert rep["open"] == 0
    assert rep["dropped_total"] == marks
    assert rep["dropped_last_30d"] == marks, "both commits land inside the trailing window"


def test_ratchet_report_enforce_flags_stalled_repayment(tight_repo: Path):
    assert run_cli(tight_repo, "coverage", "--json").returncode == 0
    assert run_cli(tight_repo, "ratchet", "seed").returncode == 0
    _git_commit_all(tight_repo, "seed the debt")
    toml = (tight_repo / "crapkit.toml").read_text(encoding="utf-8")
    (tight_repo / "crapkit.toml").write_text(
        toml.replace("target = 1", "target = 1\nrepayment_min_per_30d = 1"), encoding="utf-8")

    res = run_cli(tight_repo, "ratchet", "report", "--enforce")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "POLICY" in res.stdout and "stalled" in res.stdout

    plain = run_cli(tight_repo, "ratchet", "report")
    assert plain.returncode == 0, "without --enforce the report only informs"


def test_ratchet_merge_driver_contract(tmp_path: Path):
    """git calls the driver as `crapkit ratchet merge %O %A %B` and expects the
    result in %A — with no crapkit.toml in sight (git temp dirs have none)."""
    header = "path\tlong_name\tcrap\n"
    base, ours, theirs = tmp_path / "b.tsv", tmp_path / "o.tsv", tmp_path / "t.tsv"
    base.write_text(header + "src/a.ts\tf( )\t50.0000\n", encoding="utf-8")
    ours.write_text(header + "src/a.ts\tf( )\t30.0000\n", encoding="utf-8")
    theirs.write_text(header + "src/a.ts\tf( )\t20.0000\n", encoding="utf-8")
    res = run_cli(tmp_path, "ratchet", "merge", str(base), str(ours), str(theirs))
    assert res.returncode == 0, res.stderr
    assert "20.0000" in ours.read_text(encoding="utf-8")


def test_sarif_export_and_github_annotations(tight_repo: Path):
    res = run_cli(tight_repo, "coverage", "--sarif", "out.sarif", "--github")
    assert res.returncode == 0, res.stderr
    doc = json.loads((tight_repo / "out.sarif").read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"
    results = doc["runs"][0]["results"]
    uris = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results}
    assert "src/app.ts" in uris, "target 1 puts the fixture functions over the ceiling"
    assert all(r["ruleId"] == "crapkit/over-target" for r in results)
    assert "::warning file=src/app.ts,line=" in res.stdout


def test_ratchet_seed_then_prune_lifecycle(tight_repo: Path):
    assert run_cli(tight_repo, "coverage", "--json").returncode == 0

    seeded = run_cli(tight_repo, "ratchet", "seed")
    assert seeded.returncode == 0, seeded.stderr
    ratchet_path = tight_repo / "crapkit-ratchet.tsv"
    first = ratchet_path.read_text(encoding="utf-8")
    assert len(first.strip().splitlines()) > 1, "seed must write marks for over-target functions"

    again = run_cli(tight_repo, "ratchet", "seed")
    assert again.returncode == 0
    assert ratchet_path.read_text(encoding="utf-8") == first, "re-seeding must change nothing"
    assert "added 0" in again.stdout

    (tight_repo / "src" / "app.ts").unlink()
    _git_commit_all(tight_repo, "drop app.ts")
    assert run_cli(tight_repo, "coverage", "--json").returncode == 0

    pruned = run_cli(tight_repo, "ratchet", "prune")
    assert pruned.returncode == 0, pruned.stderr
    remaining = [ln for ln in ratchet_path.read_text(encoding="utf-8").strip().splitlines()
                 if not ln.startswith("#")]  # the metric stamp survives an emptied ratchet
    assert remaining == ["path\tlong_name\tcrap"]
    assert "pruned" in pruned.stdout
