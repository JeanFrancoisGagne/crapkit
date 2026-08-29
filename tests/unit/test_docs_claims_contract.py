"""The docs make claims a reader copies and runs. These pin the checkable ones.

A transcript nobody can reproduce, an install route pinned to a ref that does
not exist, a flag whose position is never shown: each one costs a reader a
failed command and a hunt. Every assertion here compares a documented string
against the code, the git history or the scaffolder that produces it.
"""
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit.churn import parse_git_log
from crapkit.cli import build_parser
from crapkit.scaffold import detect_lanes, gitignore_entries, live_lanes, starter_toml
from crapkit.score import grade

ROOT = Path(__file__).resolve().parent.parent.parent
PY_SCOPES = {"calc": ("python",)}
JS_SCOPES = {"src": ("typescript",)}
# The package.json the TypeScript quickstart's repo carries: a `test` script and
# vitest in devDependencies is what makes it "a vitest repo".
TS_PACKAGE = json.dumps({"scripts": {"test": "vitest run"},
                         "devDependencies": {"vitest": "^2.0.0"}})
_SUMMARY = re.compile(
    r"(\d+) functions scored — (\d+) measured / (\d+) untested / (\d+) no-lane"
    r" / (\d+) cc-only, (\d+) over target \d+, CRAP load [\d.]+, grade (\S+)")


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    """One read per page for the whole module. Every assertion below reaches for
    a page, most of them several times; uncached that is ~120 filesystem reads of
    the same six files. The page name is the only thing that invalidates it: the
    docs cannot change while the suite runs."""
    return (ROOT / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body under one heading, down to the next heading of the same depth."""
    lines = text.splitlines()
    assert heading in lines, f"the docs lost their {heading!r} heading"
    depth = heading.split(" ", 1)[0] + " "
    rest = lines[lines.index(heading) + 1:]
    end = next((i for i, ln in enumerate(rest) if ln.startswith(depth)), len(rest))
    return "\n".join(rest[:end])


def _table_row(text: str, first_cell: str) -> str:
    rows = [ln for ln in text.splitlines() if ln.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"expected one row for {first_cell!r}, found {len(rows)}"
    return rows[0]


def _py_lanes():
    return detect_lanes(frozenset({"pyproject.toml"}), "")


def _js_lanes():
    return detect_lanes(frozenset(), TS_PACKAGE)


def test_the_precommit_rev_the_readme_prints_resolves_as_a_commit():
    """Route 3 is copy-pasted into .pre-commit-config.yaml, where `rev` is a git
    ref. A ref that does not exist fails at install time, not at gate time."""
    (rev,) = re.findall(r"^\s+rev: (\S+)", _doc("README.md"), re.M)
    resolved = subprocess.run(["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
                              cwd=ROOT, capture_output=True, text=True)
    assert resolved.returncode == 0, f"README pins rev {rev!r}, which resolves to nothing"


def test_the_install_section_shows_a_local_clone_install():
    """A reader who cloned the repo needs a printed install that works from the
    clone root; the old `pip install ./crapkit` form failed there (round 4)."""
    section = _section(_doc("README.md"), "## Install")
    assert "pip install ." in section
    assert "clone root" in section


def test_the_parser_takes_repo_only_after_the_subcommand():
    args = build_parser().parse_args(["worklist", "--repo", "/tmp/x"])
    assert args.repo == "/tmp/x"
    with pytest.raises(SystemExit) as exit_code:
        build_parser().parse_args(["--repo", "/tmp/x", "worklist"])
    assert exit_code.value.code == 2, "the intuitive order is an argparse usage error"


def test_the_subcommands_section_shows_where_repo_goes():
    section = _section(_doc("README.md"), "## Subcommands")
    assert "worklist --repo" in section, "no worked example of the flag's position"


def test_the_typescript_quickstart_starts_with_init():
    """The section is a runnable sequence. Its first crapkit call cannot be one
    that needs the config a later step writes."""
    calls = re.findall(r"^\$ crapkit (\S+)", _section(_doc("README.md"),
                                                      "## Quickstart: TypeScript"), re.M)
    assert calls[0] == "init", f"the TS quickstart opens with `crapkit {calls[0]}`"


def test_the_typescript_quickstart_shows_the_fix_that_earns_its_last_transcript():
    """Its closing transcript is grade A+. Something has to remove the debt
    between the first coverage line and that one."""
    section = _section(_doc("README.md"), "## Quickstart: TypeScript")
    assert re.search(r"^### \d+\. Fix it$", section, re.M), "no fix step, but a fixed score"


def test_the_python_quickstart_prints_the_gitignore_line_init_writes():
    entries = gitignore_entries(live_lanes(_py_lanes(), PY_SCOPES))
    assert f"added to .gitignore: {', '.join(entries)}" in _doc("README.md")


def test_the_python_quickstart_prints_the_config_init_writes():
    """Including the commented lane template, which is what the prose above the
    block promises init leaves behind."""
    assert starter_toml(PY_SCOPES, _py_lanes()) in _doc("README.md")


def test_the_typescript_quickstart_prints_the_gitignore_line_init_writes():
    """One line, `.crapkit/`: a routed js lane leaves nothing else behind. A
    reader who sees a second entry here is reading a stale transcript."""
    entries = gitignore_entries(live_lanes(_js_lanes(), JS_SCOPES))
    assert f"added to .gitignore: {', '.join(entries)}" in _doc("README.md")


def test_the_typescript_quickstart_prints_the_lane_init_writes():
    """Both halves: the command, whose reportsDirectory flag is the mechanism the
    section explains, and the artifact path the failure transcript names."""
    (lane,) = _js_lanes()
    readme = _doc("README.md")
    assert lane.command in readme
    assert lane.artifact in readme


def _stop_sections() -> tuple[str, str]:
    return (_section(_doc("AGENTS.md"), "## The termination rule"),
            _section(_doc("docs/agent-json.md"), "### `reasons`, and the stop condition"))


def test_the_stop_condition_reads_the_same_in_agents_and_agent_json():
    """Two files tell an agent when to stop looping, and the rule has three
    clauses. A queue that hands back nothing is a finished burn-down only when no
    claim is hiding a row and no lane-less scope is holding debt."""
    for section in _stop_sections():
        assert "empty" in section
        assert "skipped_claimed" in section
        assert "no_lane_over_target" in section


def test_the_all_ok_count_does_not_carry_a_stop_instruction_of_its_own():
    """It used to end in a bolded "The work is done. Stop looping.", which fired
    while a ccn-9 no-lane function sat over its ceiling."""
    rule, _ = _stop_sections()

    paragraph = next(p for p in rule.split("\n\n")
                     if "all_remaining_at_or_under_target: N" in p)

    assert "stop" not in paragraph.lower(), "one stop instruction, with every clause on it"


def test_the_json_pages_stop_sentence_carries_every_clause():
    _, stop = _stop_sections()

    paragraph = next(p for p in stop.split("\n\n") if "stop condition" in p)

    assert "skipped_claimed" in paragraph and "no_lane_over_target" in paragraph


def test_the_below_floor_move_says_an_over_target_row_is_never_stuck_there():
    row = _table_row(_section(_doc("AGENTS.md"), "## The termination rule"), "`below_floor`")
    assert "over target" in row, "the move cell must say why ignoring these rows is safe"


def test_the_untested_note_sends_an_agent_to_write_a_test():
    """`uncovered_lines: null` on a clean tree is normally flag untested: no test
    imports the file, so no artifact ever mentions it. Committing changes nothing."""
    payloads = _section(_doc("AGENTS.md"), "## Picking an item")
    assert "untested" in payloads
    assert "first test" in payloads


def test_the_multi_scope_recipe_is_a_template_with_no_files_placeholder():
    """With several templated scopes, a test file outside every scope routes
    nowhere and a source file collects no tests. A template that names its own
    suite is the way out."""
    step = _section(_doc("AGENTS.md"), "## 4. Run the owning scope's tests")
    templates = re.findall(r"^    \w+ = \"(.+)\"$", step, re.M)
    assert templates, "step 5 shows no scoped_tests template at all"
    assert any("{files}" not in t for t in templates)


def test_no_doc_still_calls_a_files_less_template_a_config_error():
    row = _table_row(_doc("docs/configuration.md"), "`scoped_tests`")
    assert "config error" not in row
    assert "whole suite" in row


def test_a_one_timestamp_log_is_the_only_weightless_one():
    """What the README's `risk 0.0` line actually measures. Age is not it."""
    minute = 60
    same = parse_git_log(_git_log([1_787_000_000] * 3))["util/stats.py"]
    apart = parse_git_log(_git_log([1_787_000_000 + n * minute for n in (0, 12, 25)]))
    assert same.weight == 0.0
    assert apart["util/stats.py"].weight > 0.5, "commits minutes apart already rank"


def _git_log(stamps: list[int]) -> str:
    """One `git log --format=%x01%an%x02%at --name-only` block per commit."""
    return "".join(f"\x01ann\x02{ts}\nutil/stats.py\n" for ts in stamps)


@pytest.mark.parametrize("page", ["README.md", "AGENTS.md", "docs/lanes.md",
                                  "docs/ratchet.md", "docs/agent-json.md"])
def test_every_coverage_summary_in_the_docs_adds_up(page: str):
    """A pasted summary line is arithmetic: the four flags sum to the corpus and
    the letter follows from the over-target share. A hand-edited number breaks one."""
    for total, measured, untested, no_lane, cc_only, over, letter in _SUMMARY.findall(_doc(page)):
        counted = int(measured) + int(untested) + int(no_lane) + int(cc_only)
        assert counted == int(total), f"{page}: flags sum to {counted}, not {total}"
        assert grade(int(over), int(total)) == letter, f"{page}: {over}/{total} is not {letter}"


# --- the gate a reader installs from Route 2 ---------------------------------

def _route_two() -> str:
    return _section(_doc("README.md"), "### Route 2: a committed hooks directory")


def test_route_two_marks_the_hook_executable_before_the_commit_that_carries_it():
    """Run in the printed order, the old block left HEAD at 100644: `--chmod`
    writes the index, and the commit had already happened."""
    block = _route_two()
    order = [block.index(cmd) for cmd in
             ("git add ", "git update-index --chmod=+x githooks/pre-commit",
              'git commit -m "add crapkit gate hook"', "git config core.hooksPath githooks")]

    assert order == sorted(order), "the printed order has to be a working order"


def test_route_two_pins_the_hooks_line_endings():
    """A shebang line ending in CR is a bad-interpreter error on Linux and
    macOS, and Windows' default `core.autocrlf` produces one."""
    assert "githooks/pre-commit text eol=lf" in _route_two()


def test_route_two_creates_the_directory_it_writes_into():
    """`.git/hooks/` already exists, so Route 1 works without a mkdir. Route 2's
    directory does not, and the block was copy-pasteable only in Route 1's repo."""
    block = _route_two()
    assert "mkdir -p githooks" in block
    assert block.index("mkdir -p githooks") < block.index("githooks/pre-commit <<")
    assert "exec python -m crapkit hook-precommit" in block, "no script body to write"


# --- the full-suite rule -----------------------------------------------------

def _full_suite_refusal(command: str) -> str:
    """The guard's own words for one lane command, or "" when it allows it."""
    from crapkit.config import load_config_text
    from crapkit.errors import ConfigError

    toml = ('[[scope]]\nname = "py"\npaths = ["pylib"]\nlanguages = ["python"]\n'
            f'[[lane]]\nname = "py"\ncommand = "{command}"\n'
            'artifact = "cov.json"\nparser = "coveragepy"\nscopes = ["py"]\n')
    try:
        load_config_text(toml)
    except ConfigError as exc:
        return str(exc)
    return ""


def test_the_lanes_page_prints_the_full_suite_refusal_the_guard_produces():
    """Both transcripts under the rule are captured output. Reword the refusal
    and this pins the page to the new text instead of leaving a reader chasing
    a remedy the tool no longer offers."""
    page = _doc("docs/lanes.md")
    printed = _full_suite_refusal("python -m pytest -q pylib/unit")

    assert printed, "the guard stopped refusing the command the page prints"
    assert printed.replace("'py'", "'api'").replace("'pylib/unit'", "'api'") in page
    assert printed in page, "the -q transcript goes stale silently"


@pytest.mark.parametrize("command", ["python -m pytest -n 8",
                                     "python -m pytest -o timeout=300",
                                     "python -m pytest -p no:randomly",
                                     "python -m pytest --deselect tests/test_x.py::test_slow"])
def test_every_flag_value_the_lanes_page_calls_allowed_is_allowed(command: str):
    """The page lists four commands as passing the guard. Each one runs here."""
    assert command.split("pytest ", 1)[1] in _doc("docs/lanes.md")
    assert _full_suite_refusal(command) == ""


@pytest.mark.parametrize("flag_and_value", ["--config vitest.ci.ts",
                                            "--exclude src/legacy.cjs",
                                            "--reporter ./tools/my-reporter.ts"])
def test_every_vitest_option_value_the_lanes_page_calls_allowed_is_allowed(flag_and_value: str):
    """Same claim on the istanbul side: a source path that is an option's value
    is not the file filter the coverage guard refuses."""
    from crapkit.config import load_config_text

    assert flag_and_value in _doc("docs/lanes.md")
    toml = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n'
            '[[lane]]\nname = "unit"\n'
            f'command = "vitest run --coverage {flag_and_value}"\n'
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')

    assert load_config_text(toml).lanes[0].name == "unit"


# --- the trusted baseline ----------------------------------------------------

def test_the_readme_prints_the_taint_warning_the_code_produces():
    """The subsection quotes a captured warning. Reword the message and this
    pins the doc to the new text rather than leaving a transcript nobody can
    reproduce."""
    from crapkit.cli import _taint_note
    from crapkit.store import BaselinePick

    pick = BaselinePick(run={"id": 1, "commit": "88012a148f6d0a1b2c3d4e5f60718293a4b5c6d7"},
                        skipped={"id": 3}, blocker={"id": 2, "findings": 1})

    assert f"warning: {_taint_note(pick)}" in _doc("README.md")


def test_the_ratchet_page_prints_the_refusal_seed_raises_on_an_untrusted_store(tmp_path):
    """docs/ratchet.md quotes the refusal a store with no trusted run produces."""
    from crapkit.cli.ratchet_cmds import _latest_full_run
    from crapkit.errors import CrapkitError
    from crapkit.snapshot import InventoryRow
    from crapkit.store import SnapshotStore

    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.write_run(commit="a" * 40, tool_versions={},
                    rows=[InventoryRow("src", "src/a.py", "f( )", 1, 9, 7, 5, 5, 8, 1, 2)],
                    kind="inventory")
    with pytest.raises(CrapkitError) as exc:
        _latest_full_run(store)

    assert f"crapkit: {exc.value}" in _doc("docs/ratchet.md")


def test_the_ratchet_page_prints_the_skip_clause_the_seed_line_appends():
    from crapkit.cli.ratchet_cmds import _skip_note

    assert _skip_note([{"id": 2}]) in _doc("docs/ratchet.md")
    assert _skip_note([]) == "", "the ordinary line stays as it was"


def test_the_lanes_page_prints_the_crashed_worker_refusal_the_parser_raises():
    """The lane transcript is a captured refusal. Reword the message and this
    pins the page to the new text instead of leaving a line nobody reproduces."""
    from crapkit.errors import ToolError
    from crapkit.junitparse import suite_summary

    crashed = (ROOT / "tests" / "fixtures" / "recorded"
               / "junit_xdist_worker_crash.xml").read_text(encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        suite_summary(crashed)

    assert f"crapkit: lane 'py' FAILED: {exc.value}" in _doc("docs/lanes.md")


def test_the_lanes_page_quotes_the_drop_threshold_the_code_warns_at():
    from crapkit.lanes import SUITE_DROP_FRACTION, suite_drops

    assert f"**{SUITE_DROP_FRACTION:.0%}**" in _doc("docs/lanes.md")
    (note,) = suite_drops({"py": {"tests_total": 20}}, {"py": {"tests_total": 12}})
    assert f"crapkit: {note}" in _doc("docs/lanes.md")


def test_the_trusted_baseline_subsection_carries_every_clause():
    section = _section(_doc("README.md"), "### The trusted baseline")

    assert "verdict=-" in section, "the marker `runs list` prints goes unexplained"
    assert "--baseline 3" in section, "the deliberate-acceptance escape"
    assert "baseline" in _section(_doc("README.md"), "## Subcommands")


# --- two views, one run ------------------------------------------------------

def test_no_page_still_calls_the_two_views_one_queue():
    """worklist ranks by risk and lists rows next-item never offers; next-item
    ranks by crap. Four sentences used to say they were the same queue."""
    for page in ("README.md", "AGENTS.md", "docs/agent-json.md"):
        text = " ".join(_doc(page).lower().split())
        assert "the same queue" not in text, page
        assert "ranks the same set" not in text, page
        assert "never disagree" not in text, page


def test_both_pages_state_next_items_ordering_rule():
    for page, heading in (("README.md", "## Subcommands"),
                          ("docs/agent-json.md", "## `next-item`")):
        assert "crap` descending" in _section(_doc(page), heading), page


def test_the_readme_names_the_markers_the_worklist_row_prints():
    from crapkit.cli import _row_marker

    assert [_row_marker(_marked(f, r)) for f, r in
            (("measured", "ok"), ("no-lane", "decompose"), ("measured", "add-tests"),
             (None, None))] == ["  ok", "  no-lane", "", ""]
    risk = _section(_doc("README.md"), "### Risk: what ranks the worklist")
    assert any(ln.startswith("  risk") and ln.endswith("  ok") for ln in risk.splitlines()), \
        "the captured transcript no longer shows the marker the prose explains"


def _marked(flag, remedy):
    from crapkit.worklist import WorklistEntry

    return WorklistEntry("util", "util/stats.py", "spread( values , cap )", 13, 21,
                         4, 4, 9, 5, 1, 1.08, 4.3, flag, remedy)


def test_the_typescript_quickstart_covers_the_split_before_it_verifies():
    """Step 5 ends at `rescore --gate` exit 0, which judges complexity alone.
    Run straight from there into verify and it exits 6: three of the new
    functions have no test behind them yet."""
    steps = [title for _, title in re.findall(
        r"^### (\d+)\. (.+)$", _section(_doc("README.md"), "## Quickstart: TypeScript"), re.M)]

    assert steps.index("Fix it") + 1 == steps.index("Cover the new pieces")
    assert steps.index("Cover the new pieces") + 1 == steps.index("Verify")


def test_the_test_writing_step_names_a_command_that_runs_the_suite():
    step = _section(_doc("README.md"), "### 6. Cover the new pieces")
    assert "npx vitest run" in step


def test_both_agent_surfaces_say_the_scope_and_exclude_flags_repeat():
    """The MCP tool types `exclude` as an array, so a reader who only sees the
    singular CLI flag concludes repetition is an MCP-only affordance."""
    table = _section(_doc("README.md"), "## Subcommands")
    assert "`--scope NAME` (repeatable)" in table
    assert "`--exclude FRAG` (repeatable)" in table
    assert "(repeatable)" in _section(_doc("AGENTS.md"), "## Picking an item")


# --- one formula, three places -----------------------------------------------

def test_est_splits_reads_the_same_in_both_agent_pages():
    formula = "`0` when `ccn <= target`, else `ceil(ccn / target)`"

    assert formula in _doc("AGENTS.md")
    assert formula in _doc("docs/agent-json.md")


def test_est_splits_in_the_tool_matches_the_formula_both_pages_print():
    """AGENTS.md printed a bare `ceil(ccn / target)`, which is 1 for every row at
    or under its ceiling. The payload answers 0 for all of them."""
    assert [_payload_splits(ccn) for ccn in (1, 6, 7, 13)] == [0, 0, 2, 3]


def _payload_splits(ccn: int) -> int:
    from types import SimpleNamespace

    from crapkit.cli import _next_item_payload
    from crapkit.score import ScoredRow
    from crapkit.uncovered import MissingLines
    from crapkit.worklist import admission

    row = ScoredRow("calc", "calc/grade.py", "f( )", 1, 9, ccn, ccn, ccn, 8, 0, 1,
                    0.0, "measured", float(ccn), "decompose")
    payload = _next_item_payload(row, admission({}, 5),
                                 SimpleNamespace(target=6, scope_targets={}),
                                 MissingLines({}, "no lanes here"))
    return payload["est_splits"]


# --- the stale-artifact move -------------------------------------------------

def test_no_page_says_committing_alone_clears_a_stale_artifact():
    """It does not: nothing rereads the artifact until a run does. The runtime
    note has always said so; the prose on both pages did not."""
    for page in ("AGENTS.md", "docs/agent-json.md"):
        text = " ".join(_doc(page).lower().split())
        assert "commit or revert the edits, then rerun `crapkit coverage`" in text, \
            f"{page} never names the working move"
        assert "committing (or the verify at the end of the loop) clears it" not in text


# --- the MCP argument a client validates against -----------------------------

def test_both_pages_type_the_mcp_exclude_argument_the_way_the_server_serves_it():
    from crapkit.mcp_server import tool_listing

    (served,) = [t for t in tool_listing() if t["name"] == "next_item"]
    assert served["inputSchema"]["properties"]["exclude"]["type"] == "array"
    for page in ("AGENTS.md", "docs/agent-json.md"):
        row = _table_row(_doc(page), "`next_item`")
        assert "array of strings" in row, f"{page} still types exclude as one string"


# --- setup steps a contributor follows ---------------------------------------

def _commands(text: str, heading: str) -> list[str]:
    """The command lines under one heading. AGENTS.md indents its blocks by four
    spaces, which is what tells a command from the prose around it."""
    return [ln.strip() for ln in _section(text, heading).splitlines()
            if ln.startswith("    ") and ln.strip()]


def test_contributing_setup_carries_every_step_agents_calls_mandatory():
    steps = _commands(_doc("AGENTS.md"), "## Setup")
    contributing = _doc("CONTRIBUTING.md")

    assert steps, "AGENTS.md lost its Setup commands"
    for step in steps:
        assert step in contributing, f"CONTRIBUTING.md is missing {step!r}"


# --- the start-editing packet ------------------------------------------------
#
# `brief --json` is step one of the burn-down loop: an agent reads the payload
# instead of exploring the repo. A field the page never names is a field nobody
# reads, which puts the agent straight back into grep.

PACKET_FIELDS = ("source", "params", "notes", "file_functions", "file_totals",
                 "gate_rule", "lane", "stale", "versions", "commands", "attempts",
                 "regrowth")
GATE_RULE_KEYS = ("ceiling", "binds", "ratchet_mark", "mark_age_days",
                  "diff_uncovered_max")
COMMAND_KEYS = ("gate", "scoped_tests", "verify", "refresh")
ROW_SUBFIELDS = ("is_test", "contained")


def _brief_page() -> str:
    return _section(_doc("docs/agent-json.md"), "## `brief`")


def _brief_sub(heading: str) -> str:
    """A subsection of the brief section. Narrowed to it first: `_section` stops
    at the next heading of the same depth, and the page's next `###` after
    `--batch N` sits under `worklist`."""
    return _section(_brief_page(), heading)


def _cells(text: str) -> list[str]:
    """The first cell of every table row in a chunk of markdown."""
    return [ln.split("|")[1].strip() for ln in text.splitlines() if ln.startswith("| `")]


def _subcommand_row(name: str) -> str:
    rows = [ln for ln in _section(_doc("README.md"), "## Subcommands").splitlines()
            if ln.startswith(f"| `{name}")]
    assert len(rows) == 1, f"expected one Subcommands row for {name!r}, found {len(rows)}"
    return rows[0]


@pytest.mark.parametrize("field", PACKET_FIELDS + ROW_SUBFIELDS)
def test_the_json_page_gives_every_packet_field_a_row_of_its_own(field: str):
    """Not a bare field list: each one is a documented row with its meaning."""
    assert any(f"`{field}`" in cell for cell in _cells(_brief_page())), \
        f"docs/agent-json.md's brief section documents no `{field}`"


@pytest.mark.parametrize("key", GATE_RULE_KEYS + COMMAND_KEYS)
def test_the_json_page_documents_every_nested_packet_key(key: str):
    assert f"`{key}`" in _brief_page(), f"the brief section never names `{key}`"


def test_the_batch_call_documents_its_envelope():
    """One call, N packets, one run. A caller that cannot see `packets` in the
    envelope parses the single-function shape and gets a KeyError."""
    section = _brief_sub("### `--batch N`")
    for key in ("schema", "run_id", "commit", "stale", "packets"):
        assert key in section, f"the --batch section never names {key!r}"


def test_the_batch_call_says_it_reads_the_store_once():
    """The reason to prefer it over N `brief` calls."""
    assert "once" in _brief_sub("### `--batch N`")


# --- the name forms `brief` resolves -----------------------------------------

def test_no_page_still_says_brief_cannot_resolve_an_anonymous_function():
    """It can, since NAME takes the function's start line. The old line sent an
    agent away from the one command that would have answered."""
    for page in ("AGENTS.md", "README.md", "docs/agent-json.md"):
        text = " ".join(_doc(page).lower().split())
        assert "cannot resolve" not in text, page


def test_every_agent_page_names_the_start_line_as_a_name_form():
    for page in ("AGENTS.md", "README.md", "docs/agent-json.md"):
        assert "start line" in _doc(page), page


def test_agents_tells_a_session_how_to_open_an_anonymous_function():
    section = _section(_doc("AGENTS.md"), "## 1. The packet")
    assert "(anonymous)" in section
    assert "start line" in section


# --- the loop opens on the packet --------------------------------------------

def _loop_block() -> list[str]:
    """The command block above the first numbered step of the burn-down section."""
    body = _section(_doc("AGENTS.md"), "# Burning down debt in your repo")
    head = body.split("\n## ")[0]
    return [ln.strip() for ln in head.splitlines() if ln.startswith("    ") and ln.strip()]


def test_the_burn_down_loop_opens_on_the_packet():
    """Step one is a payload, not repo exploration."""
    first = _loop_block()[0]
    assert first.startswith("crapkit brief "), f"the loop still opens on {first!r}"
    assert "--json" in first, "step one is the packet, so it is a --json call"


def test_the_loop_runs_the_commands_the_packet_carries():
    """Steps 3 to 5 are strings the payload fills in for this file and this
    scope. Retyped by hand they lose the lane flag or the scoped_tests template."""
    block = "\n".join(_loop_block())
    for key in ("gate", "scoped_tests", "verify"):
        assert f"commands.{key}" in block, f"the loop retypes step `{key}`"


def _loop_intro() -> str:
    """Everything above the first numbered step, block included."""
    return _section(_doc("AGENTS.md"),
                    "# Burning down debt in your repo").split("\n## ")[0]


def test_the_loop_says_where_its_two_arguments_come_from():
    intro = _loop_intro()
    assert "next-item --claim" in intro, "nothing says where PATH and FUNCTION come from"
    assert "--batch" in intro, "an orchestrator-fed packet is the other source"


# --- fields the other read commands gained -----------------------------------

def test_the_json_page_documents_next_items_stale_field():
    assert "`stale`" in _section(_doc("docs/agent-json.md"), "### Envelope")


def test_the_other_payloads_row_gives_explain_a_json_form():
    row = _table_row(_section(_doc("docs/agent-json.md"), "## Other payloads"), "`explain`")
    assert "Never JSON" not in row, "explain has a --json form now"
    assert "--json" in row
    assert "body" in row, "`--history` commits carry their message body"


def test_digest_is_the_only_never_json_command_left():
    section = _section(_doc("docs/agent-json.md"), "## Other payloads")
    assert "Never JSON" in _table_row(section, "`digest`")
    assert section.count("Never JSON") == 1


# --- config keys the packet reads --------------------------------------------

def test_the_config_page_documents_notes_at_both_levels():
    """Repo-wide house rules and scope-local ones. `brief` carries both into the
    packet, so an undocumented key is guidance no session ever sees."""
    page = _doc("docs/configuration.md")
    for heading in ("## `[crapkit]`", "## `[[scope]]`"):
        row = _table_row(_section(page, heading), "`notes`")
        assert "brief" in row, f"{heading}: nothing says the packet carries them"


def test_both_surfaces_warn_about_a_lane_with_no_scoped_tests_template():
    """Step 4 of the loop is `commands.scoped_tests`, which is null without a
    template. doctor is where that gap gets named."""
    assert "scoped_tests" in _subcommand_row("doctor")
    assert "doctor warns" in _table_row(_doc("docs/configuration.md"), "`scoped_tests`")


def test_the_doctor_transcript_lists_the_keys_doctor_accepts():
    """The page prints a captured FAIL whose tail is the accepted spellings. A
    key added to the loader and not to that line reads as rejected."""
    from crapkit.doctor import valid_keys

    assert f"[crapkit] accepts these keys: {', '.join(valid_keys('crapkit'))}" \
        in _doc("docs/configuration.md")


# --- the README rows an agent picks a command from ---------------------------

def test_the_brief_row_documents_the_packet_and_its_batch_form():
    row = _subcommand_row("brief")
    for token in ("--batch", "packets", "start line", "source"):
        assert token in row, f"the brief row never mentions {token!r}"


def test_the_next_item_row_documents_stale():
    assert "stale" in _subcommand_row("next-item")


def test_the_explain_row_documents_its_json_form():
    row = _subcommand_row("explain")
    assert "--json" in row
    assert "body" in row


def test_the_changelog_records_the_packet_release():
    unreleased = _doc("CHANGELOG.md")
    assert "### The start-editing packet" in unreleased
    for token in ("brief --batch", "explain --json", "notes"):
        assert token in unreleased, f"the packet entry never mentions {token!r}"


# --- one read per page -------------------------------------------------------

def test_each_doc_page_is_read_from_disk_once(monkeypatch):
    """Pins the `_doc` cache. Without it every assertion above pays a fresh read
    of the same page, and the key is the page name, so two pages are two reads."""
    _doc.cache_clear()
    reads: list[str] = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text",
                        lambda self, **kw: (reads.append(self.name), real(self, **kw))[1])

    for _ in range(3):
        _doc("README.md")
        _doc("AGENTS.md")

    assert reads == ["README.md", "AGENTS.md"]
    _doc.cache_clear()
