"""`uvx crapkit score` on the candidate wheel: one command over a coverage
file CI already wrote, with no crapkit.toml.

The repo is the Rust crate and the tracefile cargo-llvm-cov 0.9.1 wrote for it
(tests/fixtures/coverage/lcov), committed as lcov.info. `--fail-above 5` puts
`untouched` (ccn 2, cov 0, CRAP 6) over the limit: the command exits 6, prints
its line and the total, and leaves the work tree as git had it.
"""
from __future__ import annotations

from pathlib import Path

from kit import installers, profiles, state
from kit.cells import cell
from kit.installers import said

PACKET = "m1-readers-11"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"
SCENARIO = "fresh: `uvx crapkit score rust --coverage lcov.info --fail-above 5` on a Rust crate, no config"
USE_CASES = "score an existing coverage file, fail above N"
OVER = "crap     6.00  ccn   2  cov   0%  rust/shapes.rs:32  untouched x : i32"


def _crate(box) -> Path:
    repo = box.root / "repo"
    files = {f"rust/{name}": (FIXTURE / "rust" / name).read_text(encoding="utf-8")
             for name in ("Cargo.toml", "drive.rs", "shapes.rs")}
    files["lcov.info"] = (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_text(encoding="utf-8")
    (repo / "rust").mkdir(parents=True)
    state.write(repo, files)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "a rust crate and the tracefile CI wrote")
    return repo


def _scores_and_fails_above_n(box, candidate) -> None:
    install = installers.uvx(box)
    assert candidate.version in said(install.steps[0])
    repo = _crate(box)
    before = box.run(["git", "status", "--porcelain", "--ignored"], cwd=repo, expect=0).stdout
    step = box.run([*installers.uvx_prefix(), "score", "rust", "--coverage", "lcov.info",
                    "--fail-above", "5"], cwd=repo, expect=6)
    assert step.stdout.splitlines() == [OVER, "9 functions, 1 over 5"], box.transcript.text()
    after = box.run(["git", "status", "--porcelain", "--ignored"], cwd=repo, expect=0).stdout
    assert after == before == ""


@cell("lin-score-uvx", channel="uvx", harness="none", scenario=SCENARIO, use_cases=USE_CASES,
      os="linux", image="core", cadence="push")
def test_uvx_score_exits_6_above_n_on_linux(box, candidate):
    _scores_and_fails_above_n(box, candidate)


@cell("win-score-uvx", channel="uvx", harness="none", scenario=SCENARIO, use_cases=USE_CASES,
      os="windows", image=None, cadence="push")
def test_uvx_score_exits_6_above_n_on_windows(box, candidate):
    _scores_and_fails_above_n(box, candidate)
