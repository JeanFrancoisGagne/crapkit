"""The reference pages describe what 0.8.1's code does.

The 0.8.1 docs audit ran each JSON payload, lane rule and glossary entry against
the release tree and found pages that disagree with it: coverage's exit 5, the
`handle` of a twin, verify's `tool_versions`, doctor's `warnings` and example,
explain's keys, next-item's error, brief's `lane` and `scored`, inventory's keys,
`anchor_ts` before a commit, `analysis_workers`, a duplicated refusal, a lane's
required keys, init's vitest lane, two lock files, `retried_passes`, the ratchet
examples at analysis 12, which never shipped, and five glossary entries. Each
test pins one finding to the page, and to the code where the claim is checkable.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import config_contract, invocation, keys
from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import ratchet_cmds
from crapkit.ratchet import stamp_conflict

ROOT = Path(__file__).resolve().parents[2]
AGENT = "docs/agent-json.md"
LANES = "docs/lanes.md"


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _prose(rel: str) -> str:
    return " ".join(_doc(rel).split())


def _row(rel: str, key: str) -> str:
    """The table row whose first cell is `key`."""
    return next(line for line in _doc(rel).splitlines() if line.startswith(f"| {key} |"))


# --- docs/agent-json.md -------------------------------------------------------------

def test_coverage_exits_5_on_any_failed_lane_and_prints_the_partial_summary():
    row = _row(AGENT, "`lane_failures`")

    assert "return 5 if run.lane_errors else 0" in _doc("src/crapkit/cli/scoring.py")
    assert "`coverage` exits 5 whenever a lane failed" in row and "`kind` `partial`" in row
    assert "exits 5 only when every lane failed" not in _doc(AGENT)


def _twin(start: int, long_name: str = "__post_init__( self )") -> SimpleNamespace:
    return SimpleNamespace(path="a.py", long_name=long_name, start=start, end=start + 1, occurrence=1)


def test_the_handle_rule_the_pages_give_is_the_one_keys_computes():
    names = keys.handles([_twin(2), _twin(5), _twin(8, "f( x )"), _twin(11, "f( x , y )")])

    assert sorted(names.values()) == ["__post_init__#1", "__post_init__#2", "f( x )", "f( x , y )"]
    assert _doc(AGENT).count("`NAME#N` in file order when one file gives that long name to several "
                             "functions, starting at `NAME#1`") == 3
    assert "A handle is not a ratchet key" in _doc(AGENT)


def test_the_verify_receipt_lists_analysis_version_among_its_tool_versions():
    block = re.search(r'"tool_versions": (\{[^}]*\})', _doc(AGENT))[1]

    assert json.loads(block)["analysis_version"] == str(ANALYSIS_VERSION)
    assert '`{"analysis_version", "crapkit", "lizard"}`, all strings' in _doc(AGENT)
    assert '"analysis_version": str(ANALYSIS_VERSION)' in _doc("src/crapkit/cli/scoring.py")


def _doctor_example() -> dict:
    return json.loads(_doc(AGENT).split("$ crapkit doctor --json\n```\n\n```json\n", 1)[1].split("```", 1)[0])


def test_the_doctor_example_is_a_current_capture():
    example = _doctor_example()

    assert example["analysis_version"] == ANALYSIS_VERSION
    assert len(example["resources"]) == 18 and example["lanes"][0]["refusal"] is None
    assert "The captured example below uses analysis version" not in _doc(AGENT)


def test_the_doctor_warnings_row_is_not_a_closed_list():
    row = _row(AGENT, "`warnings`")

    assert "They include" in row and "`crapkit` launchers on PATH" in row


def test_the_runner_probe_is_memoized_per_directory_and_environment():
    assert "once per directory and environment it starts in, not once per lane" in _prose(AGENT)
    assert "@lru_cache" in _doc("src/crapkit/cli/admin.py").split("def _start_probe")[0][-200:]


def test_explain_json_lists_its_keys_and_ratchet_mark_note():
    row = _row(AGENT, "`explain`")

    for key in ("`ratchet_mark_note`", "`uncovered_lines_note`", "`commits_note`", "`tests_note`",
                "`get_function_history`", "{run_id, kind, commit, created_at, ccn, cov, crap, flag}"):
        assert key in row, key


def test_next_item_says_it_prints_no_error_object():
    assert "On an error it prints nothing on stdout and the reason on stderr, with no error object" in _prose(AGENT)
    assert "because it only ever emits JSON" not in _doc(AGENT)


def test_brief_lane_command_names_the_expanded_launcher_token():
    assert "with a `{python}` or `{python:DIR}` token expanded for this OS" in _row(AGENT, "`lane`")


def test_brief_scored_lists_its_17_keys():
    row = _row(AGENT, "`scored`")
    listed = re.search(r"17 keys: (.*?)\. `next-item`", row)[1]

    assert len(re.findall(r"`(\w+)`", listed)) == 17


def test_inventory_json_lists_the_keys_it_keeps():
    assert "`inventory --json` carries these keys of the summary and no others" in _prose(AGENT)
    assert "`unreadable_names`, `db` and `schema`" in _prose(AGENT)


def test_anchor_ts_is_zero_before_any_commit_touches_the_marks_file():
    assert "`0` when no commit has touched the file yet" in _row(AGENT, "`anchor_ts`")


# --- docs/lanes.md ------------------------------------------------------------------

def test_analysis_workers_zero_is_automatic_sizing():
    workers = config_contract.schema()["properties"]["crapkit"]["properties"]["analysis_workers"]

    assert "0 = automatic sizing" in workers["description"]
    assert "one process per core" not in _doc(LANES)
    assert "default `0`, automatic sizing within the CPU limit" in _prose(LANES)


def test_the_unreadable_stamp_refusal_is_described_once_with_the_message_crapkit_prints():
    assert _doc(LANES).count(".crapkit/artifacts.json cannot be read (it does not parse as JSON)") == 1
    assert "The refusal lives only in `.crapkit/artifacts.json`" not in _doc(LANES)
    assert 'f"the {artifact} on disk is the file a failed attempt left"' in _doc("src/crapkit/lane_stamps.py")
    assert "whether the .crapkit/cov/py.json on disk is the file a failed attempt left" in _doc(LANES)


def test_a_lane_names_every_required_key():
    required = config_contract.schema()["properties"]["lane"]["items"]["required"]

    assert "Five required keys, `name` plus the four below" in _prose(LANES)
    assert all(_row(LANES, f"`{key}`") for key in required)


def test_the_vitest_lane_init_writes_depends_on_the_test_script():
    prose = _prose(LANES)

    assert "and whose package.json has a `test` script" in prose
    assert "With no such script it writes `npx vitest run --coverage ...`" in prose


@pytest.mark.parametrize("lock", ["`<ratchet_file name>.lock`", "`mutate-pool.lock`"])
def test_the_crapkit_directory_table_lists_every_lock(lock):
    assert _row(LANES, lock)


# --- docs/portable-records.md, docs/ratchet.md --------------------------------------

def test_retried_passes_is_named_as_conditional():
    text = _prose("docs/portable-records.md")

    assert "plus `retried_passes` when a test passed its flake retry" in text
    assert 'return {**prov, "retried_passes": passed} if passed else prov' in _doc("src/crapkit/cli/verifying.py")


@pytest.fixture
def console_script(monkeypatch):
    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)


def test_the_newer_marks_example_quotes_a_stamp_pair_a_release_prints(console_script):
    printed = stamp_conflict(f"crapkit-analysis={ANALYSIS_VERSION + 1} lizard=1.24.0",
                             f"crapkit-analysis={ANALYSIS_VERSION} lizard=1.24.0")

    assert f"crapkit: {printed}" in _doc("docs/ratchet.md")
    assert "[crapkit-analysis=12 " not in _doc("docs/ratchet.md")
    assert "A 0.8.0 reader meeting 0.8.1's marks prints the older line" in _prose("docs/ratchet.md")


def test_the_merge_refusal_example_is_what_the_driver_prints(console_script):
    with pytest.raises(Exception) as refused:
        ratchet_cmds._merge_stamp(["", "# crapkit-analysis=11 lizard=1.24.0\n",
                                   f"# crapkit-analysis={ANALYSIS_VERSION} lizard=1.24.0\n"])

    assert f"crapkit: {refused.value}" in _doc("docs/ratchet.md")


def test_the_live_merge_shows_the_file_a_current_merge_writes():
    merged = f"# crapkit-analysis={ANALYSIS_VERSION} lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"

    assert merged in _doc("docs/ratchet.md")
    assert "# crapkit-analysis=8 lizard=1.24.0\npath" not in _doc("docs/ratchet.md")


# --- CONTEXT.md ---------------------------------------------------------------------

def test_the_glossary_counts_a_no_verdict_mutant_inside_killed():
    context = _prose("CONTEXT.md")

    assert "JSON schema 1 counts it inside `killed` and the kill rate" in context
    assert "it is in neither `killed` nor `survived`" not in context
    assert '"killed": len(verdicts) - count[V.SURVIVED]' in _doc("src/crapkit/cli/analyses.py")


def test_the_glossary_matches_the_code_on_packets_lanes_runs_and_placeholders():
    context = _prose("CONTEXT.md")

    assert "or `uvx crapkit` when uvx ran the command that built the packet" in context
    assert "writes one coverage artifact for the scopes it lists" in context
    assert "**Positionless run**" in context and "**Legacy run**" not in context
    assert "`{files}`, `{tests}` or `{names}`" in context
