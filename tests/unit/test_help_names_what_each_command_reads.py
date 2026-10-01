"""Help text and the editor schema say what the command reads, checked against it.

`init --help` described `--repo` with the shared "nearest crapkit.toml at or
above cwd" line, and init never walks up. Three help strings said "the latest
run" where the command reads the baseline run: verify's `--baseline` default,
`ratchet seed`, and `rescore`. rescore's then said "the baseline run", the run
`crapkit runs` marks, and rescore reads the newest trusted run with no
behind-HEAD rule: after a branch switch, the other branch's run. `clean --help`
listed `--json` with no words, and the root help named four `--version --json`
keys where the object holds five.
The editor schema called retest_command a `{tests}` template, and the lane fills
`{files}` and `{names}` too.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cli_inproc_repo import (add_knotty, commit_all, git, repo, seed_artifacts,  # noqa: F401
                            template_repo)
from crapkit.cli import main
from crapkit.cli.parser import _help_topics, build_parser

ROOT = Path(__file__).resolve().parents[2]


def _screen(topic: str) -> str:
    parser = build_parser()
    text = (parser if topic == "crapkit" else _help_topics(parser)[topic]).format_help()
    return " ".join(text.split())


# --- init never walks up ------------------------------------------------------

def test_init_help_says_where_init_writes():
    words = _screen("init")

    assert "nearest crapkit.toml" not in words, words
    assert "--repo REPO directory to write crapkit.toml in (default: the current directory; " \
           "init never walks up)" in words, words


def test_init_below_a_config_writes_nothing_and_adopts_nothing(repo, monkeypatch, capsys):  # noqa: F811
    """What the help now says: init reads the current directory, not the
    crapkit.toml above it."""
    docs = repo / "docs"
    docs.mkdir()
    monkeypatch.chdir(docs)

    assert main(["init"]) == 3
    assert "no source files found to scope" in capsys.readouterr().err
    assert not (docs / "crapkit.toml").exists()


# --- the baseline, not the latest run ------------------------------------------

@pytest.mark.parametrize("topic, said", [
    ("verify", "--baseline BASELINE baseline run id (default: the trusted run `crapkit runs` "
               "marks `baseline`)"),
    ("ratchet", "seed: mark over-target functions from the run verify would compare against;"),
])
def test_help_names_the_baseline_run_the_command_reads(topic, said):
    words = _screen(topic)

    assert said in words, words
    assert "latest run" not in words and "latest scored run" not in words, words


RESCORE_READS = ("rescore fresh complexity for named files overlaid on the newest trusted run's "
                 "coverage, from any branch")


def test_rescore_help_names_the_newest_trusted_run():
    words = _screen("crapkit")

    assert RESCORE_READS in words, words
    assert "the baseline run's coverage" not in words, words


def test_rescore_reads_the_other_branchs_run_after_a_branch_switch(repo, capsys):  # noqa: F811
    """`crapkit runs` marks run 1, the run behind HEAD, as baseline; rescore
    overlays run 2, the newest trusted run, as its help says."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "src" / "extra.ts").write_text("export const a = 1;\n", encoding="utf-8")
    commit_all(repo, "side work")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    git(repo, "checkout", "-q", "-")
    capsys.readouterr()

    assert main(["runs", "--json", "--repo", str(repo)]) == 0
    marked = [run["id"] for run in json.loads(capsys.readouterr().out)["runs"] if run["baseline"]]
    assert main(["rescore", "src/app.ts", "--repo", str(repo)]) == 0

    assert (marked, capsys.readouterr().out.split(" @ ")[0]) == ([1], "rescore vs run 2")
    assert RESCORE_READS in _screen("crapkit")


@pytest.fixture()
def failed_verify_on_top(repo, capsys):  # noqa: F811
    """Run 1 is the coverage baseline; run 2, newer, is a verify that failed."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    assert main(["verify", "--reuse-artifacts", "--repo", str(repo)]) == 6
    capsys.readouterr()
    return repo


def test_rescore_and_seed_pass_over_a_newer_failed_verify(failed_verify_on_top, capsys):
    root = str(failed_verify_on_top)
    assert main(["rescore", "src/app.ts", "--repo", root]) == 0
    assert capsys.readouterr().out.startswith("rescore vs run 1 ")

    assert main(["ratchet", "seed", "--repo", root]) == 0
    assert " vs run 1 " in capsys.readouterr().out


# --- clean --json and --version --json -------------------------------------------

def test_clean_help_says_what_json_prints():
    assert "--json machine output" in _screen("clean")


def test_the_version_json_keys_are_the_ones_the_help_names(capsys):
    named = re.search(r"--version --json prints one object: ([^.]+?)$", _screen("crapkit"))
    assert named, _screen("crapkit")
    keys = set(re.split(r", | and ", named.group(1)))

    assert main(["--version", "--json"]) == 0
    assert keys == set(json.loads(capsys.readouterr().out)), keys


# --- retest_command's placeholders ------------------------------------------------

def test_the_schema_names_every_placeholder_the_retest_fills():
    from crapkit.config_contract import schema
    from crapkit.lanes import _retest_template

    filled, _ = _retest_template("run {tests} {files} {names}", {"tests/test_a.py::test_b"})
    assert "{" not in filled, filled
    shipped = json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))
    for contract in (schema(), shipped):
        described = contract["properties"]["lane"]["items"]["properties"]["retest_command"]
        for placeholder in ("{tests} (ids)", "{files} (vitest)", "{names} (pytest -k)"):
            assert placeholder in described["description"], described


# --- the marks file in the working tree -------------------------------------------
# hook-precommit's help said "A function the committed ratchet marks passes".
# The hook loads the marks file in the working tree, so a mark nobody staged or
# committed pardons a staged function. The ratchet help called the file it
# manages "the committed marks file", and seed writes the working tree's file
# and commits nothing. It said report is a burn-down "from the marks file's git
# history", and report's open marks are the file's rows, committed or not.

MARKS = "crapkit-ratchet.tsv"
HOOK_MARKS = ("A function with a mark in the working tree's marks file passes, whether or not "
              "the mark is staged.")
RATCHET_MANAGES = "ratchet manage the marks file: seed new debt, prune gone code"
REPORT_READS = ("report: the burn-down: open marks are the file's rows, committed or not, or the "
                "rows its history last held when it is missing or blank, and ages and repayments "
                "come from its git history")


def test_a_mark_nobody_staged_pardons_a_staged_function(repo, capsys):  # noqa: F811
    from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

    add_knotty(repo)
    git(repo, "add", "src/app.ts")
    (repo / MARKS).write_text(dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 72.0)],
                                           stamp=metric_version()), encoding="utf-8", newline="\n")

    assert main(["hook-precommit", "--repo", str(repo)]) == 0
    assert "1 staged function(s) carry a ratchet mark" in capsys.readouterr().err
    assert git(repo, "status", "--porcelain", "--", MARKS).startswith("??")
    words = _screen("hook-precommit")
    assert "committed ratchet marks" not in words, words
    assert HOOK_MARKS in words, words


def test_seed_writes_marks_that_report_reads_before_any_commit(repo, capsys):  # noqa: F811
    seed_artifacts(repo)
    add_knotty(repo)
    commit_all(repo, "knotty")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    head = git(repo, "rev-parse", "HEAD")

    assert main(["ratchet", "seed", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert main(["ratchet", "report", "--json", "--repo", str(repo)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert git(repo, "rev-parse", "HEAD") == head, "seed commits nothing"
    assert git(repo, "status", "--porcelain", "--", MARKS).startswith("??")
    assert (report["open"], report["uncommitted"]) == (1, 1), report
    assert "committed marks file" not in _screen("crapkit")
    assert RATCHET_MANAGES in _screen("crapkit")
    words = _screen("ratchet")
    assert "report: burn-down from the marks file's git history" not in words, words
    assert REPORT_READS in words, words
