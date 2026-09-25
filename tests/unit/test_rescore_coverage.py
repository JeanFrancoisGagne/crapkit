"""`rescore --coverage PATH` scores the named files on an artifact the caller measured (#76).

Plain `rescore` overlays the latest run's coverage, which holds nothing for a
function the branch added, so its `--gate` reads ccn alone. A new ccn-5
function with an untested branch then passes every check a change goes through
and fails first at `verify`, after the full coverage run. An artifact from a
scoped run covers no more than the lane's whole suite, so the CRAP scored on it
is at or above the score verify computes: under `--coverage`, `--gate` holds
that CRAP to the ceiling, as verify does.
"""
import json

import pytest
from cli_inproc_repo import istanbul, repo, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.cli._shared import _load_repo_config
from crapkit.cli.scoring import _ceiling_breaches, _rescore_analyze
from crapkit.score import ScoredRow

# ccn 5, one under the ceiling of 6: the ccn-only gate passes it at any coverage.
FORKED = """
export function forked(n: number): number {
  if (n > 1) { n = n + 1; }
  if (n > 2) { n = n + 1; }
  if (n > 3) { n = n + 1; }
  if (n > 4) { n = n + 1; }
  return n;
}
"""
HEAD_LINE = "export function forked(n: number): number {"


def add_forked(repo, covered: int) -> None:
    """Append `forked` to the working tree, and write the scoped artifact a
    caller's own run would: `covered` of its two branch arms walked."""
    path = repo / "src" / "app.ts"
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(FORKED)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(HEAD_LINE) + 1
    istanbul(repo, "scoped.json", "src/app.ts", {"forked": (start, len(lines), covered)})


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def gate_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if "GATE" in ln]


# --- the join, at its seam ------------------------------------------------------

def test_the_artifact_is_read_by_the_lane_parser_and_joined_onto_fresh_rows(repo):
    from crapkit.cli.scoring import _rescore_on_artifact

    add_forked(repo, covered=1)
    cfg = _load_repo_config(repo)
    rows, _, _ = _rescore_analyze(repo, cfg, ["src/app.ts"])

    scored = {r.long_name.split("(")[0].strip(): r
              for r in _rescore_on_artifact(repo, cfg, rows, repo / "scoped.json")}

    forked = scored["forked"]
    assert (forked.ccn, forked.flag, forked.cov) == (5, "measured", 0.5)
    assert forked.crap == pytest.approx(5 ** 2 * 0.5 ** 3 + 5)
    assert scored["dispatch"].cov == 0.0, "a function the artifact never measured reads uncovered"


def row(name: str, ccn: int, cov: float, crap: float, start: int = 10) -> ScoredRow:
    return ScoredRow("src", "src/a.py", name, start, start + 5, ccn, ccn, ccn,
                     12, 2, 1, cov, "measured", crap, "add-tests")


def test_on_crap_a_function_under_the_ccn_ceiling_can_breach_it():
    rows = [row("forked( n )", 5, 0.5, 8.125), row("tidy( n )", 6, 1.0, 6.0, start=40)]

    assert _ceiling_breaches(rows, {"src/a.py": 6}) == [], "ccn alone passes both"
    breaches = _ceiling_breaches(rows, {"src/a.py": 6}, metric="crap")
    assert [(b.long_name, b.crap) for b in breaches] == [("forked( n )", 8.125)]


def test_on_crap_breaches_are_ordered_worst_crap_first():
    rows = [row("mild( n )", 5, 0.6, 6.6), row("worst( n )", 4, 0.0, 20.0, start=40)]

    breaches = _ceiling_breaches(rows, {"src/a.py": 6}, metric="crap")

    assert [b.long_name for b in breaches] == ["worst( n )", "mild( n )"]


# --- the command ----------------------------------------------------------------
#
# The template repo carries no snapshot: the artifact is the whole of the
# coverage, so the command needs no run behind it.

def test_the_gate_fails_a_changed_function_whose_crap_the_artifact_puts_over(repo, capsys):
    add_forked(repo, covered=1)

    code, _, err = run(["rescore", "src/app.ts", "--coverage", "scoped.json", "--gate"],
                       repo, capsys)

    assert code == 6, err
    (line,) = gate_lines(err)
    assert "forked" in line and "ccn   5" in line and "cov 50%" in line, line


def test_the_gate_passes_once_the_artifact_covers_every_branch(repo, capsys):
    add_forked(repo, covered=2)

    code, out, err = run(["rescore", "src/app.ts", "--coverage", "scoped.json", "--gate"],
                         repo, capsys)

    assert (code, err) == (0, "")
    last = out.splitlines()[-1]
    assert last.startswith("gate: ") and last.endswith(" 0 over ceiling 6"), out


def test_without_the_gate_the_table_names_the_artifact_it_joined(repo, capsys):
    add_forked(repo, covered=1)

    code, out, err = run(["rescore", "src/app.ts", "--coverage", "scoped.json"], repo, capsys)

    assert (code, err) == (0, "")
    first = out.splitlines()[0]
    assert str(repo / "scoped.json") in first and "STALE" not in first, first
    assert "forked" in out


def test_the_payload_names_the_artifact_and_calls_no_coverage_stale(repo, capsys):
    add_forked(repo, covered=1)

    code, out, _ = run(["rescore", "src/app.ts", "--coverage", "scoped.json", "--json"],
                       repo, capsys)
    payload = json.loads(out)

    assert code == 0
    assert payload["coverage_artifact"] == str(repo / "scoped.json")
    assert "baseline_run" not in payload, "no run was read"
    assert not any(f["stale_coverage"] for f in payload["functions"])


def test_a_missing_artifact_is_refused_as_an_argument(repo, capsys):
    code, _, err = run(["rescore", "src/app.ts", "--coverage", "nowhere.json"], repo, capsys)

    assert code == 3
    assert "nowhere.json" in err, err


def test_an_artifact_the_lane_parser_cannot_read_is_refused_as_a_tool_failure(repo, capsys):
    (repo / "scoped.json").write_text("not json", encoding="utf-8")

    code, _, err = run(["rescore", "src/app.ts", "--coverage", "scoped.json"], repo, capsys)

    assert code == 5
    assert "unparseable istanbul artifact" in err, err


def test_an_artifact_that_measured_another_checkout_is_refused_and_named(repo, capsys):
    """Every path in it resolves outside this repo, so the join would match
    nothing and the gate would fail `forked` as untested: a verdict built on
    the wrong tree. The lane run's own check refuses it instead, naming PATH."""
    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(FORKED)
    other = repo.parent / "other-checkout"
    istanbul(other, "scoped.json", "src/app.ts", {"forked": (20, 26, 2)})
    artifact = other / "scoped.json"

    code, _, err = run(["rescore", "src/app.ts", "--coverage", str(artifact), "--gate"],
                       repo, capsys)

    assert code == 5, err
    assert f"{artifact} describes a different tree" in err, err


def test_files_whose_lanes_read_two_formats_are_refused(repo, capsys):
    toml = repo / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    ui_lane = text.index('name = "ui"')
    toml.write_text(text[:ui_lane] + text[ui_lane:].replace('"istanbul"', '"coveragepy"'),
                    encoding="utf-8")
    istanbul(repo, "scoped.json", "src/app.ts", {"plain": (13, 18, 2)})

    code, _, err = run(["rescore", "src/app.ts", "web/ui.ts", "--coverage", "scoped.json"],
                       repo, capsys)

    assert code == 3
    assert "unit (istanbul)" in err and "ui (coveragepy)" in err, err


def test_a_file_no_lane_measures_is_refused(repo, capsys):
    toml = repo / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text[:text.rindex("[[lane]]")], encoding="utf-8")
    istanbul(repo, "scoped.json", "web/ui.ts", {"render": (1, 3, 2)})

    code, _, err = run(["rescore", "web/ui.ts", "--coverage", "scoped.json"], repo, capsys)

    assert code == 3
    assert "no lane measures web" in err, err
