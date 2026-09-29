"""The commit gate's refusal names the ceiling it judged each function against.

A scope may set its own `target` below the repo's. The hook judged a staged
ccn-6 function in a target-5 scope against 5, and its head line printed the
repo target: `exceed the complexity ceiling of 6:` over a `ccn   6` row, which
reads as no breach at all. The head line now names the ceiling the breaches
share, and when staged breaches fall under scopes with different ceilings,
each row names its own (`ccn   6 > 5`).
"""
from __future__ import annotations

from pathlib import Path

from cli_inproc_repo import add_knotty, git, repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.cli.verifying import _print_breaches
from crapkit.hook import Violation

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
    assert out[:2] == ["crapkit gate: 1 staged function(s) exceed the complexity ceiling of 5:",
                       "  ccn   6  web/ui.ts:5  six ( n )"], out


def test_breaches_under_different_ceilings_name_each_on_its_row(repo, capsys):  # noqa: F811
    _strict_web(repo)
    _stage_six(repo)
    add_knotty(repo)
    git(repo, "add", "-A")

    code, out = _hook(repo, capsys)

    assert code == 6, out
    assert out[:3] == ["crapkit gate: 2 staged function(s) exceed the complexity ceiling of "
                       "their scope:",
                       "  ccn   8 > 6  src/app.ts:20  knotty ( n )",
                       "  ccn   6 > 5  web/ui.ts:5  six ( n )"], out


def test_one_repo_target_keeps_the_head_line_every_page_quotes(repo, capsys):  # noqa: F811
    add_knotty(repo)
    git(repo, "add", "-A")

    code, out = _hook(repo, capsys)

    assert code == 6, out
    assert out[:2] == ["crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:",
                       "  ccn   8  src/app.ts:20  knotty ( n )"], out


def test_a_violation_built_without_a_ceiling_reads_the_target(capsys):
    _print_breaches([Violation("app/m.py", "route( a )", 1, 9)], 6, "", "staged")

    assert capsys.readouterr().out.splitlines() == [
        "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:",
        "  ccn   9  app/m.py:1  route( a )"]
