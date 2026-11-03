"""The gate's refusal names the CRAP ceiling it judged each function against.

A scope may set its own `target` below the repo's. A staged ccn-6 function in
a target-5 scope is judged against 5, so the head line names 5: the repo's 6
over a `ccn   6` row would read as no breach at all. The head line names the
ceiling the breaches share, and when staged breaches fall under scopes with
different ceilings, each row names its own (`ccn   6 > 5`). `rescore --gate`
prints the same head line; its clause on ccn shows only over a `cov -` row.
"""
from __future__ import annotations

from pathlib import Path

from cli_inproc_repo import (add_knotty, commit_all, git, repo,  # noqa: F401
                             seed_artifacts, template_repo)
from crapkit.cli import main
from crapkit.cli.verifying import _print_breaches
from crapkit.hook import Violation

WHY = " (with no coverage known, CRAP is at least ccn):"

SIX = """
export function six(n: number): number {
  if (n > 1) { return 1; }
  if (n > 2) { return 2; }
  if (n > 3) { return 3; }
  if (n > 4) { return 4; }
  if (n > 5) { return 5; }
  return 0;
}
"""

# The template's render, edited to ccn 6 under its own name.
RENDER_SIX = """export function render(n: number): string {
  if (n > 1) { return "a"; }
  if (n > 2) { return "b"; }
  if (n > 3) { return "c"; }
  if (n > 4) { return "d"; }
  if (n > 5) { return "e"; }
  return "f";
}
"""


def _strict_web(root: Path) -> None:
    """web keeps the repo's paths and gets its own ceiling of 5."""
    toml = root / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('paths = ["web"]\n', 'paths = ["web"]\ntarget = 5\n'),
                    encoding="utf-8", newline="\n")


def _stage_six(root: Path) -> None:
    with open(root / "web" / "ui.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(SIX)
    git(root, "add", "-A")


def _hook(root: Path, capsys) -> tuple[int, list[str]]:
    code = main(["hook-precommit", "--repo", str(root)])
    return code, capsys.readouterr().out.splitlines()


def test_a_breach_of_a_scope_target_names_that_target(repo, capsys):  # noqa: F811
    _strict_web(repo)
    _stage_six(repo)

    code, out = _hook(repo, capsys)

    assert code == 6, out
    assert out[:2] == [f"crapkit gate: 1 staged function(s) exceed the CRAP ceiling of 5{WHY}",
                       "  ccn   6  web/ui.ts:5  six ( n )"], out


def test_breaches_under_different_ceilings_name_each_on_its_row(repo, capsys):  # noqa: F811
    _strict_web(repo)
    _stage_six(repo)
    add_knotty(repo)
    git(repo, "add", "-A")

    code, out = _hook(repo, capsys)

    assert code == 6, out
    assert out[:3] == [f"crapkit gate: 2 staged function(s) exceed the CRAP ceiling of their scope{WHY}",
                       "  ccn   8 > 6  src/app.ts:20  knotty ( n )",
                       "  ccn   6 > 5  web/ui.ts:5  six ( n )"], out


def test_one_repo_target_keeps_the_head_line_every_page_quotes(repo, capsys):  # noqa: F811
    add_knotty(repo)
    git(repo, "add", "-A")

    code, out = _hook(repo, capsys)

    assert code == 6, out
    assert out[:2] == [f"crapkit gate: 1 staged function(s) exceed the CRAP ceiling of 6{WHY}",
                       "  ccn   8  src/app.ts:20  knotty ( n )"], out


def test_a_violation_built_without_a_ceiling_reads_the_target(capsys):
    _print_breaches([Violation("app/m.py", "route( a )", 1, 9)], 6, "", "staged")

    assert capsys.readouterr().out.splitlines() == [
        f"crapkit gate: 1 staged function(s) exceed the CRAP ceiling of 6{WHY}",
        "  ccn   9  app/m.py:1  route( a )"]


def _scored_strict_web(root: Path) -> None:
    """web at target 5, committed, and one coverage run over both lanes."""
    _strict_web(root)
    commit_all(root, "web gets its own ceiling")
    seed_artifacts(root)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(root)]) == 0


def _rescore_gate(root: Path, capsys) -> tuple[int, list[str]]:
    capsys.readouterr()
    code = main(["rescore", "web/ui.ts", "--gate", "--repo", str(root)])
    return code, capsys.readouterr().err.splitlines()


def test_rescore_gate_names_a_scope_target_with_the_hooks_head_line(repo, capsys):  # noqa: F811
    """six is new since the run: no coverage stands behind it, so the clause stays."""
    _scored_strict_web(repo)
    with open(repo / "web" / "ui.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(SIX)

    code, err = _rescore_gate(repo, capsys)

    assert code == 6, err
    assert err[0] == f"crapkit gate: 1 rescored function(s) exceed the CRAP ceiling of 5{WHY}", err
    assert "cov -" in err[1] and "six ( n )" in err[1], err


def test_rescore_gate_drops_the_clause_when_the_run_measured_the_function(repo, capsys):  # noqa: F811
    """render keeps its name, so it keeps the run's coverage: its CRAP is known."""
    _scored_strict_web(repo)
    (repo / "web" / "ui.ts").write_text(RENDER_SIX, encoding="utf-8", newline="\n")

    code, err = _rescore_gate(repo, capsys)

    assert code == 6, err
    assert err[0] == "crapkit gate: 1 rescored function(s) exceed the CRAP ceiling of 5:", err
    assert "cov -" not in err[1] and "render ( n )" in err[1], err
