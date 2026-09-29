"""Help text and the editor schema say what the command reads, checked against it.

`init --help` described `--repo` with the shared "nearest crapkit.toml at or
above cwd" line, and init never walks up. Three help strings said "the latest
run" where the command reads the baseline run: verify's `--baseline` default,
`ratchet seed`, and `rescore`. `clean --help` listed `--json` with no words, and
the root help named four `--version --json` keys where the object holds five.
The editor schema called retest_command a `{tests}` template, and the lane fills
`{files}` and `{names}` too.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cli_inproc_repo import add_knotty, repo, seed_artifacts, template_repo  # noqa: F401
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
    ("crapkit", "rescore fresh complexity for named files overlaid on the baseline run's coverage"),
])
def test_help_names_the_baseline_run_the_command_reads(topic, said):
    words = _screen(topic)

    assert said in words, words
    assert "latest run" not in words and "latest scored run" not in words, words


@pytest.fixture()
def failed_verify_on_top(repo, capsys):  # noqa: F811
    """Run 1 is the coverage baseline; run 2, newer, is a verify that failed."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    assert main(["verify", "--reuse-artifacts", "--repo", str(repo)]) == 6
    capsys.readouterr()
    return repo


def test_rescore_and_seed_read_the_baseline_under_a_newer_failed_verify(failed_verify_on_top,
                                                                          capsys):
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
