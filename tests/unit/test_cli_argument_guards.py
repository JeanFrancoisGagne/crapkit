"""What the CLI refuses, and what it accepts, at the boundary where argv arrives.

Every test here drives `main(argv)` over the in-process repo. The bugs behind
them share one shape: an argument the parser typed but nobody checked reached a
slice, a path match or an `open()` and produced an answer instead of a refusal.
A `--top` of 0 handed out an item and locked it; the same 0 printed a clean bill
of health over three duplicate pairs; a file argument spelled `./src/a.ts` or as
the absolute path a shell just completed routed to no scope and scored nothing.
"""
import json

from cli_inproc_repo import (add_knotty, commit_all, repo,  # noqa: F401
                             seed_artifacts, template_repo)

import pytest

from crapkit.cli import main


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    """The command at its public seam: exit code, stdout, stderr."""
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def scored(repo, capsys):
    """A repo carrying one trusted coverage run: what the queue ranks."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


@pytest.fixture()
def queued(repo, capsys):
    """A scored repo whose queue actually holds something: a committed function
    over its ceiling that no lane artifact covers. Without one, every assertion
    about what `--top` hands out passes on an empty queue."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


# --- next-item --top ----------------------------------------------------------

@pytest.mark.parametrize("top", ["0", "-1"])
def test_next_item_refuses_a_top_below_one(queued, capsys, top):
    """`--top 0` used to hand out one item: `max(top, 1)` widened the slice back
    to one and the emit branch fell through to the single-item shape. An agent
    templating `--top {budget}` that computed 0 got work it did not ask for."""
    code, out, err = run(["next-item", "--top", top], queued, capsys)

    assert code == 3, out
    assert f"next-item --top must be >= 1, got {top}" in err, err
    assert out == ""


def test_next_item_top_zero_claims_nothing(queued, capsys):
    """The claim is the expensive half: it hides the function from every other
    session until something releases it."""
    run(["next-item", "--top", "0", "--claim"], queued, capsys)

    _, out, _ = run(["claims", "--json"], queued, capsys)

    assert json.loads(out)["claims"] == []


def test_next_item_still_hands_out_the_top_item(queued, capsys):
    code, out, _ = run(["next-item"], queued, capsys)

    assert code == 0
    assert json.loads(out)["empty"] is False


# --- duplication and coupling --top -------------------------------------------

@pytest.mark.parametrize("command", ["duplication", "coupling"])
@pytest.mark.parametrize("top", ["0", "-1"])
def test_the_analyses_refuse_a_top_below_one(scored, capsys, command, top):
    """`duplication --top 0` printed "no near-duplicate functions found" and
    exited 0 over a tree full of pairs: a clean bill of health that was false,
    and the thing a CI gate reads. `--top -1` dropped the last row silently."""
    code, out, err = run([command, "--top", top], scored, capsys)

    assert code == 3, out
    assert f"{command} --top must be >= 1, got {top}" in err, err


def test_duplication_at_its_default_top_still_reports(scored, capsys):
    code, _, err = run(["duplication"], scored, capsys)

    assert (code, err) == (0, "")


# --- bare `crapkit ratchet` ----------------------------------------------------

def test_bare_ratchet_does_not_demand_a_file(repo, capsys):
    """`report`, `seed` and `prune` take no file at all, so naming FILE in the
    missing-arguments line sends the reader hunting for an argument three of the
    five actions refuse to use."""
    with pytest.raises(SystemExit) as exc:
        main(["ratchet", "--repo", str(repo)])

    err = capsys.readouterr().err
    assert exc.value.code == 2
    assert "required: action" in err, err
    assert "FILE" not in err.split("required:")[1], err


def test_ratchet_report_still_runs_with_no_file(scored, capsys):
    code, out, _ = run(["ratchet", "report"], scored, capsys)

    assert code == 0
    assert "ratchet burn-down" in out, out


# --- `crapkit help [TOPIC]` ----------------------------------------------------

def test_help_prints_the_command_list(repo, capsys):
    """git, npm and docker all answer `help`. crapkit answered exit 2 and the
    invalid-choice dump, which never names `--help` as the way out."""
    code = main(["help"])
    out = capsys.readouterr().out

    assert code == 0
    assert "duplication" in out and "usage: crapkit" in out


def test_help_on_a_topic_prints_that_subcommands_help(repo, capsys):
    code = main(["help", "coverage"])
    out = capsys.readouterr().out

    assert code == 0
    assert "usage: crapkit coverage" in out, out
    assert "--reuse-artifacts" in out, out


def test_help_names_the_topics_when_the_topic_is_not_one(repo, capsys):
    code = main(["help", "nonesuch"])
    err = capsys.readouterr().err

    assert code == 3
    assert "no subcommand 'nonesuch'" in err, err


# --- file arguments: ./, backslashes, absolute --------------------------------

def test_the_gate_reads_an_absolute_path_the_way_it_reads_a_relative_one(scored, capsys):
    """A wrapper hands crapkit the absolute path it already has. That path
    matched no scope prefix, so the rescore scored nothing and `--gate` had no
    row to fail on: exit 0 on code that violates the ceiling."""
    add_knotty(scored)
    absolute = str((scored / "src" / "app.ts").resolve())

    code, _, err = run(["rescore", absolute, "--gate"], scored, capsys)

    assert code == 6, err
    assert "knotty" in err, err


def test_a_file_argument_outside_the_repo_is_refused(scored, capsys, tmp_path):
    """Scoring nothing is not an answer to a path crapkit cannot place."""
    stranger = tmp_path / "elsewhere" / "app.ts"
    stranger.parent.mkdir(parents=True)
    stranger.write_text("export function f() { return 1; }\n", encoding="utf-8")

    code, _, err = run(["rescore", str(stranger)], scored, capsys)

    assert code == 3, err
    assert "outside the repo" in err, err


def test_rescore_takes_the_dot_slash_spelling(scored, capsys):
    """`./src/app.ts` is what tab completion, `find` and coding agents produce."""
    code, out, err = run(["rescore", "./src/app.ts"], scored, capsys)

    assert code == 0, err
    assert "dispatch" in out, out


def test_test_scoped_routes_the_dot_slash_spelling(scored, capsys):
    """It used to answer `./src/app.ts belongs to no declared scope`, which was
    false: the scope declaring it is right there in crapkit.toml."""
    code, _, err = run(["test-scoped", "./src/app.ts"], scored, capsys)

    assert code == 0, err


def test_test_scoped_routes_an_absolute_path(scored, capsys):
    absolute = str((scored / "src" / "app.ts").resolve())

    code, _, err = run(["test-scoped", absolute], scored, capsys)

    assert code == 0, err


# --- writers into a directory that does not exist yet -------------------------

def test_export_creates_the_directory_it_writes_into(scored, capsys):
    """`report --out` mkdirs. `--export` raised a raw FileNotFoundError and
    exited 1, a code crapkit's exit table does not define."""
    code, _, err = run(["inventory", "--export", "out/new/inv.tsv"], scored, capsys)

    assert (code, err) == (0, "")
    assert (scored / "out" / "new" / "inv.tsv").is_file()


def test_sarif_creates_the_directory_it_writes_into(scored, capsys):
    """The SARIF write is the last step of `coverage`, after the run is already
    committed to the store, so the crash landed on a run that had succeeded."""
    code, _, err = run(["coverage", "--reuse-artifacts", "--sarif", "out/new/x.sarif"],
                       scored, capsys)

    assert code == 0, err
    assert (scored / "out" / "new" / "x.sarif").is_file()


def test_emit_baseline_creates_the_directory_it_writes_into(scored, capsys):
    """The last writer still opening its path straight. `verify` writes the
    baseline before the verdict, so the traceback landed on a run whose lanes
    had already finished."""
    code, _, err = run(["verify", "--reuse-artifacts", "--emit-baseline", "out/new/b.tsv"],
                       scored, capsys)

    assert code == 0, err
    assert (scored / "out" / "new" / "b.tsv").is_file()


def test_a_relative_export_may_not_climb_out_of_the_repo(scored, capsys):
    code, _, err = run(["inventory", "--export", "../escaped.tsv"], scored, capsys)

    assert code == 3, err
    assert "climbs out of" in err, err


# --- a hand-edited ratchet line -----------------------------------------------

def _short_ratchet_line(root) -> None:
    """One mark with two fields where three belong: what a hand edit or a
    botched merge leaves behind."""
    path = root / "crapkit-ratchet.tsv"
    path.write_text("path\tlong_name\tcrap\nsrc/app.ts\tgnarly( a , b )\n",
                    encoding="utf-8")


def _unreadable_mark(root, mark: str) -> None:
    """Three fields, and the third is not a number: the trailing tab a hand edit
    leaves, or a word where the score belongs."""
    path = root / "crapkit-ratchet.tsv"
    path.write_text(f"path\tlong_name\tcrap\nsrc/app.ts\tgnarly( a , b )\t{mark}\n",
                    encoding="utf-8")


def test_explain_salvages_a_ratchet_file_with_a_short_line(scored, capsys):
    """The mark is one optional field of the packet. The trajectory the reader
    asked for is all there, so a broken line is dropped with a warning rather
    than answered with a ValueError traceback."""
    _short_ratchet_line(scored)

    code, out, err = run(["explain", "src/app.ts", "dispatch"], scored, capsys)

    assert code == 0, err
    assert "expected 3" in err, err
    assert "dispatch" in out, out


@pytest.mark.parametrize("mark", ["", "n/a"])
def test_explain_salvages_a_ratchet_line_whose_mark_is_not_a_number(scored, capsys, mark):
    """The lenient reader counted fields and nothing else, so `float(parts[2])`
    still raised out of it. explain and brief are MCP tools, and a trailing tab
    answered both with a ValueError traceback at exit 1."""
    _unreadable_mark(scored, mark)

    code, out, err = run(["explain", "src/app.ts", "dispatch"], scored, capsys)

    assert code == 0, err
    assert "unreadable mark" in err, err
    assert "dispatch" in out, out


def test_the_gate_salvages_a_ratchet_file_with_a_short_line(scored, capsys):
    """A dropped mark can only make the gate stricter, never let a regression
    through, so `rescore --gate` still answers 6 rather than 1 and a traceback."""
    _short_ratchet_line(scored)
    add_knotty(scored)

    code, _, err = run(["rescore", "src/app.ts", "--gate"], scored, capsys)

    assert code == 6, err


def test_ratchet_merge_names_the_unreadable_file(scored, capsys, tmp_path):
    """Where crapkit REWRITES the file, a skipped line would delete a mark, so
    the strict refusal stays — as a named crapkit error, not a stack trace."""
    sides = []
    for name in ("base", "ours", "theirs"):
        p = tmp_path / f"{name}.tsv"
        p.write_text("path\tlong_name\tcrap\nsrc/app.ts\tgnarly( a , b )\n",
                     encoding="utf-8")
        sides.append(str(p))

    code, _, err = run(["ratchet", "merge", *sides], scored, capsys)

    assert code == 3, err
    assert "expected 3" in err, err


def test_the_merge_refusal_names_the_line_whose_mark_is_unreadable(scored, capsys, tmp_path):
    """The strict side went through `float()` too, so it named a Python
    conversion instead of the line the operator has to open."""
    sides = []
    for name in ("base", "ours", "theirs"):
        p = tmp_path / f"{name}.tsv"
        p.write_text("path\tlong_name\tcrap\nsrc/app.ts\tgnarly( a , b )\t\n",
                     encoding="utf-8")
        sides.append(str(p))

    code, _, err = run(["ratchet", "merge", *sides], scored, capsys)

    assert code == 3, err
    assert "ratchet line 2 has an unreadable mark" in err, err


# --- an unknown --scope --------------------------------------------------------

@pytest.mark.parametrize("command", ["worklist", "next-item"])
def test_an_undeclared_scope_is_a_configuration_error_naming_the_declared_ones(scored, capsys,
                                                                                command):
    """`worklist --scope frontend` on a repo whose scopes are `src` and `web`
    printed `0 active, 0 dormant` at exit 0, which a CI step reads as a clean
    pass, and `next-item --scope biling` answered `empty: true` with every
    reason at 0, the payload an agent reads as a finished scope."""
    code, out, err = run([command, "--scope", "frontend"], scored, capsys)

    assert code == 3, out
    assert "no scope named 'frontend'" in err, err
    assert "declared: src, web" in err, err
    assert out == ""


def test_a_declared_scope_still_cuts_the_worklist(scored, capsys):
    code, out, _ = run(["worklist", "--scope", "web", "--json"], scored, capsys)

    assert code == 0
    assert {e["scope"] for e in json.loads(out)["active"]} <= {"web"}


def test_one_unknown_scope_among_declared_ones_is_still_refused(scored, capsys):
    code, _, err = run(["worklist", "--scope", "src", "--scope", "srv"], scored, capsys)

    assert code == 3
    assert "no scope named 'srv'" in err, err
