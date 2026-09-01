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

from conftest import run_cli

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


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


@pytest.fixture()
def pytest_repo(tmp_path: Path) -> Path:
    """A python repo with a pytest marker: init writes the py lane for it."""
    repo = _bare_git_repo(tmp_path, "pyrepo")
    (repo / "pylib").mkdir()
    (repo / "pylib" / "mod.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "pyrepo"\n', encoding="utf-8")
    _git_commit_all(repo, "init")
    return repo


def _without_pytest_cov(tmp_path: Path) -> dict:
    """Environment overrides whose python cannot import pytest_cov: a PYTHONPATH
    shim shadows the package, the same trick fixtures/shims plays on lizard."""
    shim_dir = tmp_path / "no-cov-shim"
    shim_dir.mkdir()
    (shim_dir / "pytest_cov.py").write_text(
        'raise ImportError("shimmed out for the init probe test")\n', encoding="utf-8")
    inherited = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join(p for p in (str(shim_dir), inherited) if p)}


def test_init_warns_when_the_lanes_python_lacks_pytest_cov(pytest_repo: Path, tmp_path: Path):
    """The first-run trap, caught at init instead of forty minutes into the
    first `crapkit coverage`: the py lane runs `pytest --cov`, and those flags
    come from pytest-cov — a package of the REPO's interpreter, which a
    dependency on crapkit itself could never guarantee."""
    res = run_cli(pytest_repo, "init", env_extra=_without_pytest_cov(tmp_path))
    assert res.returncode == 0, res.stderr
    assert "pytest_cov" in res.stderr and "pip install pytest-cov" in res.stderr
    assert '"crapkit[py]"' in res.stderr, (
        "the extra is the same-venv shortcut, and double quotes are the one form "
        "cmd.exe, PowerShell, bash and zsh all read the same way")
    assert (pytest_repo / "crapkit.toml").is_file(), "a warning must not stop the scaffold"


def test_init_does_not_probe_a_lane_it_did_not_write(tmp_path: Path):
    """A TypeScript-only repo whose pyproject.toml only holds ruff config: the
    py lane is detected, has no scope, and goes back to being a template. Its
    probe must not tell the reader to install pytest-cov for a suite init never
    wrote a lane for."""
    repo = _bare_git_repo(tmp_path, "tsonly")
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text("export function f(a: number) { return a ? 1 : 2; }\n",
                                         encoding="utf-8")
    (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    _git_commit_all(repo, "init")
    res = run_cli(repo, "init", env_extra=_without_pytest_cov(tmp_path))
    assert res.returncode == 0, res.stderr
    assert "pytest_cov" not in res.stderr, "no lane was written for that python"


_DEAD_EXIT = 9009 if os.name == "nt" else 127


def _with_a_python_that_will_not_run(tmp_path: Path) -> dict:
    """Environment overrides whose `python` resolves and then refuses to run: the Windows
    Store alias in %LOCALAPPDATA%\\Microsoft\\WindowsApps that a stock Windows 11
    PATH carries with no Store app behind it. Prepended, not swapped in, so git
    still resolves — the shim is the only answer that comes from the fixture."""
    shim_dir = tmp_path / "dead-python"
    shim_dir.mkdir()
    if os.name == "nt":
        (shim_dir / "python.bat").write_text(f"@exit /b {_DEAD_EXIT}\n", encoding="utf-8")
    else:
        shim = shim_dir / "python"
        shim.write_text(f"#!/bin/sh\nexit {_DEAD_EXIT}\n", encoding="utf-8")
        shim.chmod(0o755)
    return {"PATH": os.pathsep.join([str(shim_dir), os.environ["PATH"]])}


def test_init_says_when_the_shell_cannot_run_the_lanes_interpreter(
        pytest_repo: Path, tmp_path: Path):
    """9009 is not an answer about pytest_cov, so that note rightly stopped
    firing on it — and nothing replaced it. The reader got a committed config
    whose only lane cannot start, and init printed not one word about it."""
    res = run_cli(pytest_repo, "init",
                  env_extra=_with_a_python_that_will_not_run(tmp_path))

    assert res.returncode == 0, res.stderr
    assert "cannot run it" in res.stderr and str(_DEAD_EXIT) in res.stderr
    assert "`python`" in res.stderr, "the note has to name the word the lane starts with"
    assert "pytest_cov" not in res.stderr, "nothing ran: pytest-cov is not the gap"
    assert (pytest_repo / "crapkit.toml").is_file(), "a note must not stop the scaffold"


def test_doctor_fails_a_lane_whose_first_word_will_not_run(
        pytest_repo: Path, tmp_path: Path):
    """Doctor asked which() whether the lane's runner resolves and never
    whether it starts, so the repo above — one lane, exit 9009, coverage
    exiting 5 — was reported as "1 lane(s) declared" and "no problems found"."""
    dead = _with_a_python_that_will_not_run(tmp_path)
    assert run_cli(pytest_repo, "init", env_extra=dead).returncode == 0

    res = run_cli(pytest_repo, "doctor", env_extra=dead)

    assert res.returncode == 1, res.stdout
    assert "cannot run" in res.stdout and str(_DEAD_EXIT) in res.stdout
    assert "'python'" in res.stdout, "name the word that has to change"
    assert "no problems found" not in res.stdout


def test_doctor_passes_a_lane_whose_interpreter_really_runs(pytest_repo: Path):
    """The other half: an interpreter that starts is not a finding, and the
    check must not turn every working repo's doctor red."""
    assert run_cli(pytest_repo, "init").returncode == 0

    res = run_cli(pytest_repo, "doctor")

    assert "cannot run" not in res.stdout, res.stdout
    assert res.returncode == 0, res.stdout


def test_init_stays_quiet_when_pytest_cov_is_importable(pytest_repo: Path):
    res = run_cli(pytest_repo, "init")
    assert res.returncode == 0, res.stderr
    assert "pytest_cov" not in res.stderr, "no warning when the probe answers yes"


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


@pytest.fixture()
def vitest_repo(tmp_path: Path) -> Path:
    """A JS repo naming exactly one runner, which is what lets init route both
    of that runner's output files under .crapkit/."""
    repo = _bare_git_repo(tmp_path, "vitest")
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text("export function f(a: number) { return a ? 1 : 2; }\n",
                                         encoding="utf-8")
    (repo / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^2.0.0"}}),
        encoding="utf-8")
    _git_commit_all(repo, "init")
    return repo


def test_init_on_a_vitest_repo_writes_a_lane_doctor_does_not_warn_about(vitest_repo: Path):
    """init wrote a JS lane with no results_artifact, so the first thing doctor
    said about a config init had just written was that its lane cannot run the
    crashed-worker check or no-new-failures. vitest ships the junit reporter, so
    the flags cost the reader nothing. The scoped_tests WARN is the one that
    stays: only the repo knows that command."""
    assert run_cli(vitest_repo, "init").returncode == 0

    res = run_cli(vitest_repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "results_artifact" not in res.stdout, res.stdout
    assert [line for line in res.stdout.splitlines() if line.startswith("WARN")] == [
        line for line in res.stdout.splitlines() if "scoped_tests" in line]


def test_the_vitest_lane_init_writes_still_parses_under_the_lane_guard(vitest_repo: Path):
    """The istanbul command validator refuses a file filter beside --coverage,
    and `--outputFile=.crapkit/cov/js/junit.xml` must not read as one. Loading
    the config is what runs that guard."""
    from crapkit.config import load_config_text

    assert run_cli(vitest_repo, "init").returncode == 0

    cfg = load_config_text((vitest_repo / "crapkit.toml").read_text(encoding="utf-8"))

    (lane,) = cfg.lanes
    assert lane.results_artifact == ".crapkit/cov/js/junit.xml"
    assert lane.command.endswith("--reporter=default --reporter=junit "
                                 "--outputFile=.crapkit/cov/js/junit.xml"), lane.command


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


# --- the environment the scaffolded lane binds to -----------------------------

@pytest.fixture()
def locked_repo(tmp_path: Path) -> Path:
    """A pytest project that pins its environment with uv, which is the shape
    two worktrees of one branch are usually checked out in."""
    repo = tmp_path / "locked"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "faro"\n', encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _git_commit_all(repo, "init")
    return repo


def test_init_binds_the_lane_to_the_environment_the_lockfile_pins(locked_repo: Path):
    """A bare `python` binds to whatever venv the shell has active. Run in one
    worktree with another worktree's venv active, that lane measures the other
    checkout — and when the two APIs agree it does so silently."""
    res = run_cli(locked_repo, "init")
    assert res.returncode == 0, res.stderr

    from crapkit.config import load_config_text
    cfg = load_config_text((locked_repo / "crapkit.toml").read_text(encoding="utf-8"))
    (lane,) = cfg.lanes
    assert lane.command.startswith("uv run python -m pytest ")
    assert dict(cfg.scoped_tests)["pylib"].startswith("uv run python -m pytest ")


def test_the_same_project_without_a_lockfile_keeps_the_bare_interpreter(locked_repo: Path):
    (locked_repo / "uv.lock").unlink()
    _git_commit_all(locked_repo, "drop the lockfile")

    assert run_cli(locked_repo, "init").returncode == 0

    from crapkit.config import load_config_text
    cfg = load_config_text((locked_repo / "crapkit.toml").read_text(encoding="utf-8"))
    # Any of the three names init falls back through, `py` included: which one
    # resolves is the machine's answer, and only the manager prefix is gone.
    assert re.match(r"(python3?|py) -m pytest ", cfg.lanes[0].command)


@pytest.fixture()
def unmarked_locked_repo(tmp_path: Path) -> Path:
    """uv pins the environment, and no pytest marker file names the runner, so
    init writes the coveragepy lane as a commented template rather than live."""
    repo = tmp_path / "unmarked"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _git_commit_all(repo, "init")
    return repo


def test_the_commented_lane_template_names_the_python_the_lockfile_pins(
        unmarked_locked_repo: Path):
    """The template is the command a reader uncomments. Written with a bare
    `python` on a uv repo it hands them the environment bug init exists to
    avoid, one uncomment later."""
    assert run_cli(unmarked_locked_repo, "init").returncode == 0

    text = (unmarked_locked_repo / "crapkit.toml").read_text(encoding="utf-8")

    assert '# command = "uv run python -m pytest --cov' in text
    assert '# pylib = "uv run python -m pytest {files}' in text, \
        "the commented scoped-tests entry reads the same launcher"


def test_without_a_lockfile_the_commented_template_keeps_the_bare_interpreter(
        unmarked_locked_repo: Path):
    (unmarked_locked_repo / "uv.lock").unlink()
    _git_commit_all(unmarked_locked_repo, "drop the lockfile")

    assert run_cli(unmarked_locked_repo, "init").returncode == 0

    text = (unmarked_locked_repo / "crapkit.toml").read_text(encoding="utf-8")

    assert re.search(r'# command = "(python3?|py) -m pytest --cov', text), text


# --- the virtualenv the repo carries ------------------------------------------
#
# A library whose own .venv holds pytest, checked out where the PATH python holds
# none: init wrote `python -m pytest --cov`, doctor called that config clean, and
# `crapkit coverage` exited 5 on "No module named pytest" with the right
# interpreter sitting in the tree the whole time.

_VENV_LAUNCHER = r".venv\Scripts\python.exe" if os.name == "nt" else ".venv/bin/python"


def _repo_venv(repo: Path) -> None:
    """A real virtualenv in the repo, with pytest importable through it.

    `--without-pip` keeps it under a second, and a stub on its own path answers
    the import: what this test is about is which interpreter init names, not
    what a package installer does.
    """
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(repo / ".venv")],
                   check=True, capture_output=True)
    (site,) = (repo / ".venv").glob("**/site-packages")
    for module in ("pytest.py", "pytest_cov.py"):
        (site / module).write_text("", encoding="utf-8")


def test_init_binds_the_lane_to_the_venv_the_repo_carries(pytest_repo: Path):
    """The lane and step 4 both, because a lane measuring one environment while
    the scoped tests run in another is the same bug one command later."""
    _repo_venv(pytest_repo)

    res = run_cli(pytest_repo, "init")

    assert res.returncode == 0, res.stderr
    from crapkit.config import load_config_text
    cfg = load_config_text((pytest_repo / "crapkit.toml").read_text(encoding="utf-8"))
    (lane,) = cfg.lanes
    assert lane.command.startswith(f"{_VENV_LAUNCHER} -m pytest "), lane.command
    assert dict(cfg.scoped_tests)["pylib"].startswith(f"{_VENV_LAUNCHER} -m pytest ")
    assert "pytest_cov" not in res.stderr, "the venv carries the plugin: nothing to say"


def test_doctor_clears_the_venv_lane_init_just_wrote(pytest_repo: Path):
    """The config init writes has to survive its own doctor. This one names a
    path rather than a bare word, and doctor both resolves it and starts it.

    Asked twice, from two directories. `--repo` is how every caller that is not
    a shell reaches doctor — `mcp_server._run_cli` spawns
    `crapkit doctor --repo <repo>` with no cwd of its own — and a relative
    launcher read against the caller's directory instead of the repo's failed
    that call on every repo but the one the caller happened to be sitting in.
    """
    _repo_venv(pytest_repo)
    assert run_cli(pytest_repo, "init").returncode == 0

    inside = run_cli(pytest_repo, "doctor")
    outside = run_cli(pytest_repo.parent, "doctor", "--repo", str(pytest_repo))

    for res in (inside, outside):
        assert res.returncode == 0, res.stdout
        assert "does not resolve" not in res.stdout and "cannot run" not in res.stdout


def test_a_venv_without_pytest_leaves_the_bare_name_alone(pytest_repo: Path):
    """An empty environment is not the one the lane wants. Naming it would
    trade a lane that runs the wrong python for one that runs no pytest."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip",
                    str(pytest_repo / ".venv")], check=True, capture_output=True)

    assert run_cli(pytest_repo, "init").returncode == 0

    from crapkit.config import load_config_text
    cfg = load_config_text((pytest_repo / "crapkit.toml").read_text(encoding="utf-8"))
    assert re.match(r"(python3?|py) -m pytest ", cfg.lanes[0].command), cfg.lanes[0].command
# --- what init leaves outside every scope -------------------------------------
#
# init refuses to build a scope out of a root-level loose file or a
# dot-directory, and the excludes it wrote missed both, so doctor came back with
# a FAIL on files init itself had walked past: a root conftest.py, a .github
# helper, and a vendored tree the root form of `**/vendor/**` never reached.

@pytest.fixture()
def scattered_repo(tmp_path: Path) -> Path:
    """A python repo carrying a root conftest.py, a .github helper and a
    vendored tree, each written in a language one of its scopes declares."""
    repo = _bare_git_repo(tmp_path, "scattered")
    (repo / "pylib").mkdir()
    (repo / "pylib" / "mod.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "scattered"\n', encoding="utf-8")
    (repo / "conftest.py").write_text("import sys\n", encoding="utf-8")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "gen.py").write_text("def gen():\n    return 1\n",
                                                           encoding="utf-8")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "lib.py").write_text("def v():\n    return 1\n", encoding="utf-8")
    _git_commit_all(repo, "init")
    return repo


def test_init_scopes_only_the_source_and_doctor_agrees_with_it(scattered_repo: Path):
    res = run_cli(scattered_repo, "init")
    assert res.returncode == 0, res.stderr
    assert "1 scope(s): pylib" in res.stdout, res.stdout

    doctor = run_cli(scattered_repo, "doctor")

    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "no scope path" not in doctor.stdout, doctor.stdout


def test_a_repo_root_vendor_tree_never_becomes_a_lane_less_scope(scattered_repo: Path):
    """The scope doctor FAILed: init made one out of vendor/, and no lane
    measured it."""
    assert run_cli(scattered_repo, "init").returncode == 0

    config = (scattered_repo / "crapkit.toml").read_text(encoding="utf-8")

    assert 'name = "vendor"' not in config, config


# --- a workspace repo names its runner one level down -------------------------

@pytest.fixture()
def workspace_repo(tmp_path: Path) -> Path:
    """A monorepo whose root test script only chains the workspaces': vitest
    lives in web/, and so does the only package.json that names it."""
    repo = _bare_git_repo(tmp_path, "workspace")
    (repo / "web" / "src").mkdir(parents=True)
    (repo / "web" / "src" / "app.ts").write_text(
        "export function f(a: number) { return a ? 1 : 2; }\n", encoding="utf-8")
    (repo / "package.json").write_text(
        json.dumps({"private": True, "workspaces": ["web"],
                    "scripts": {"test": "npm run --workspaces test"}}), encoding="utf-8")
    (repo / "web" / "package.json").write_text(
        json.dumps({"name": "web", "scripts": {"test": "vitest run"},
                    "devDependencies": {"vitest": "^2.0.0"}}), encoding="utf-8")
    _git_commit_all(repo, "init")
    return repo


def test_init_runs_the_js_lane_where_the_runner_lives(workspace_repo: Path):
    from crapkit.config import load_config_text

    assert run_cli(workspace_repo, "init").returncode == 0

    cfg = load_config_text((workspace_repo / "crapkit.toml").read_text(encoding="utf-8"))

    (lane,) = cfg.lanes
    assert lane.cwd == "web"
    assert "--coverage.reportsDirectory=../.crapkit/cov/js" in lane.command
    assert lane.artifact == ".crapkit/cov/js/coverage-final.json", "resolved from the root"
    assert lane.results_artifact == ".crapkit/cov/js/junit.xml"


def test_doctor_has_nothing_to_say_about_the_workspace_lane(workspace_repo: Path):
    """Both WARNs the reporter got on this shape: no results_artifact, and a
    coverage report written at the repo root."""
    assert run_cli(workspace_repo, "init").returncode == 0

    res = run_cli(workspace_repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "results_artifact" not in res.stdout, res.stdout
    assert "at the repo root" not in res.stdout, res.stdout
