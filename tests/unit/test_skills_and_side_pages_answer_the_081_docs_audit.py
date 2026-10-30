"""The plugin skills and the side pages describe what 0.8.1 does.

The 0.8.1 docs audit found the recover skill quoting the analysis 7 to 8
upgrade, passing `--baseline N` to seed alone, and giving one of the two causes
of doctor's `checking` line; the crapkit skill mixing a value into its list of
flags and routing only exits 5 to 9; cuts.md scoring its comprehension example
wrongly; the onboard skill putting the plugin before the CLI, printing a
settings fragment with no file named, and leaving out Copilot CLI; the Codex
upgrade row leaving a pinned marketplace behind; comparison.md saying crap4py
and wily gate nothing; adoption.md misreading doctor's output, the agents that
load the plugin and `coverage_optional`'s reason; and resources.md naming no
release for the lock move and spending a section on crapkit's own test runner.
Each test pins one finding to the page, and to the code where the claim is
checkable.
"""
import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

from crapkit.agent_fields import _PACKET_PROPERTIES
from crapkit.analyze import ANALYSIS_VERSION, analyze_source
from crapkit.cli.parser import build_parser
from crapkit.ratchet import stamp_conflict

ROOT = Path(__file__).resolve().parents[2]
RECOVER = "plugin/skills/crapkit-recover/SKILL.md"
SKILL = "plugin/skills/crapkit/SKILL.md"
ONBOARD = "plugin/skills/crapkit-onboard/SKILL.md"
COMPARISON = "docs/comparison.md"
ADOPTION = "docs/adoption.md"
RESOURCES = "docs/resources.md"


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _prose(rel: str) -> str:
    return " ".join(_doc(rel).split())


def _section(rel: str, heading: str) -> str:
    """The body under `heading`, up to the next heading of the same depth or higher."""
    level = heading.split(" ", 1)[0]
    body = _doc(rel).split(heading + "\n", 1)[1]
    return re.split(rf"^#{{1,{len(level)}}} ", body, maxsplit=1, flags=re.M)[0]


def _row(rel: str, key: str) -> str:
    return next(line for line in _doc(rel).splitlines() if line.startswith(f"| {key} |"))


# --- plugin/skills/crapkit-recover/SKILL.md -------------------------------------------

def test_the_recover_skill_quotes_the_upgrade_this_release_makes():
    recorded, current = "crapkit-analysis=11 lizard=1.24.0", f"crapkit-analysis={ANALYSIS_VERSION} lizard=1.24.0"
    head = stamp_conflict(recorded, current).split(" - ", 1)[0]

    assert f"`{head}` is the 0.8.1 upgrade" in _prose(RECOVER)
    assert "ccn did not move" not in _doc(RECOVER) and "crapkit-analysis=7" not in _doc(RECOVER)
    assert "upgrading.md#analysis-version-13" in _doc(RECOVER)


def _ratchet_baseline_help() -> str:
    subcommands = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return next(a.help for a in subcommands.choices["ratchet"]._actions if "--baseline" in a.option_strings)


def test_the_recover_skill_passes_the_newer_run_to_prune_and_seed():
    section = " ".join(_section(RECOVER, "## Three exit-3 signatures worth naming").split())

    assert _ratchet_baseline_help().startswith("seed and prune:")
    assert "plain prune and plain seed both read the pinned run" in section
    assert "`crapkit ratchet prune --baseline N`, then `crapkit ratchet seed --baseline N`" in section


def test_the_checking_row_gives_the_bare_flag_as_a_cause():
    """`_default_plugin_root` looks in two directories at once, so its root
    never equals where it looked and the bare flag always prints the line."""
    row = _row(RECOVER, '"crapkit doctor: checking PATH", then nothing')

    assert 'f"{plugins} or {codex}"' in _doc("src/crapkit/cli/admin.py")
    assert "You ran `--plugin-root` with no PATH, or named a directory above the plugin root" in row


def test_the_recover_skill_writes_no_em_dash_no_shouted_word_and_a_blank_line_before_each_heading():
    text = _doc(RECOVER)
    shouted = {"NO", "FIRST", "LAST", "SUITE", "EVERY", "OUTSIDE", "UNDER", "PREPENDS"}

    assert "—" not in text
    assert shouted.isdisjoint(re.findall(r"\b[A-Z]{2,}\b", text))
    assert re.findall(r"[^\n]\n## ", text) == []


# --- plugin/skills/crapkit/SKILL.md and cuts.md ---------------------------------------

def test_the_flag_list_names_every_flag_and_no_value():
    listed = re.findall(r"^- `([^`]+)`:", _section(SKILL, "## Two fields decide whether a number is worth reading"),
                        re.M)
    flags = set(_PACKET_PROPERTIES["flag"]["enum"])

    assert "[]" not in listed
    assert set(listed) | {"excluded"} == flags
    assert "An `excluded`\nfunction also reads `[]`" in _doc(SKILL)


def test_the_routing_line_sends_exits_3_and_4_to_the_recover_skill():
    routing = next(line for line in _doc(SKILL).splitlines() if "the `crapkit-recover` skill" in line)

    assert "exits 3/4/5/6/7/8/9" in _doc(RECOVER)
    assert "exit 3 to 9" in routing


def _cut_blocks() -> tuple[str, str]:
    after_heading = _doc("plugin/skills/crapkit/cuts.md").split("## 5. Comprehension splitting\n", 1)[1]
    block = re.search(r"```python\n(.*?)```", after_heading, re.S)[1]
    before, after = block.split("\n\n# after", 1)
    return before, "# after" + after


def _scores(code: str) -> dict[str, int]:
    return {r.long_name.split("(")[0]: r.ccn for r in analyze_source("m.py", code)}


def test_the_comprehension_example_states_the_ccn_crapkit_gives_it():
    before, after = _cut_blocks()
    wrapped_before = "def build(groups):\n" + "".join(f"    {line}\n" for line in before.splitlines()[1:])
    helper, assign = after.split("\n\nrows = ", 1)
    wrapped_after = helper.split("\n", 1)[1] + "\n\ndef build(groups):\n    rows = " + assign

    assert _scores(wrapped_before) == {"build": 5}
    assert _scores(wrapped_after) == {"is_live": 2, "build": 3}
    assert "the function scores ccn 5" in before.splitlines()[0]
    assert "the function scores ccn 3 and is_live 2" in after.splitlines()[0]


# --- plugin/skills/crapkit-onboard/SKILL.md -------------------------------------------

def test_the_onboard_skill_puts_the_repo_first_and_the_adoption_pointer_at_its_top():
    headings = re.findall(r"^## (.+)$", _doc(ONBOARD), re.M)
    repo = _section(ONBOARD, "## The repo").strip()

    assert headings.index("The repo") < headings.index("The plugin")
    assert repo.startswith("Read [docs: adoption]")


def _indented_blocks(text: str) -> list[str]:
    return ["\n".join(line[4:] for line in block.splitlines())
            for block in re.findall(r"(?:^ {4}.*\n)+", text, re.M)]


def _readme_bash_entry() -> list:
    block = next(b for b in re.findall(r"```json\n(.*?)```", _doc("README.md"), re.S) if '"matcher": "Bash"' in b)
    return json.loads(block)["hooks"]["PostToolUse"]


def test_the_bash_hook_block_is_a_whole_settings_object_and_names_its_file():
    block = next(b for b in _indented_blocks(_doc(ONBOARD)) if '"matcher": "Bash"' in b)

    assert json.loads(block)["hooks"]["PostToolUse"] == _readme_bash_entry()
    assert "`~/.claude/settings.json`, or to `.claude/settings.json` for one repo" in _prose(ONBOARD)


def test_the_onboard_skill_installs_the_plugin_in_copilot_cli_and_names_where_the_hook_runs():
    text = _prose(ONBOARD)

    assert "`copilot plugin marketplace add JeanFrancoisGagne/crapkit`" in text
    assert "`copilot plugin install crapkit@crapkit`" in text and "core.longpaths true" in text
    assert "in Claude Code, Cursor, Copilot CLI and VS Code it also carries the advisory hook" in text


# --- docs/harnesses.md ------------------------------------------------------------------

def test_the_harness_page_says_install_tests_where_it_said_cells():
    assert "cells" not in _doc("docs/harnesses.md")
    assert "crapkit's install tests run it on Claude Code 2.1.138" in _doc("docs/harnesses.md")


def test_the_changelog_moves_a_pinned_codex_marketplace_by_hand():
    """Read by heading: a newer release's section can sit above 0.8.1's."""
    notes = " ".join(_doc("CHANGELOG.md").split("\n## 0.8.1 ", 1)[1].split("\n## ", 1)[0].split())

    assert "Codex refreshes git marketplaces each time it starts" not in notes
    assert "a marketplace added at a tag stays at that tag" in notes
    assert "`codex plugin marketplace remove crapkit`" in notes


# --- docs/comparison.md -------------------------------------------------------------------

def _tool_row(tool: str) -> str:
    return next(line for line in _doc(COMPARISON).splitlines() if line.startswith(f"| [{tool}]"))


def test_crap4py_and_wily_each_carry_the_gate_they_ship():
    assert "`--max-crap N` exits non-zero when any function scores above N" in _tool_row("crap4py")
    assert "`wily rank --threshold N` exits non-zero" in _tool_row("wily")
    assert "an optional `--max-crap` gate" in _prose(COMPARISON)


def test_radon_is_per_function_only_for_cyclomatic_complexity():
    assert "maintainability index and raw metrics per file; Halstead per file, or per function with `-f`" \
        in _tool_row("radon")


def test_the_comparison_names_every_agent_the_advisory_reaches_and_drops_the_sales_copy():
    text = _prose(COMPARISON)

    assert "per-edit advisory in Claude Code, Cursor, GitHub Copilot CLI and VS Code" in text
    assert [p for p in ("actually sit", "whole reason crapkit exists", "small sharp") if p in text] == []


# --- docs/adoption.md -------------------------------------------------------------------

def test_adoption_says_doctor_names_the_install_it_found_below_the_path():
    text = _prose(ADOPTION)

    assert "prints nothing when they agree" not in text
    assert "exits 0 when they agree. When it found the install under PATH rather than at it, it first prints" in text


def test_adoption_gives_each_agent_its_own_way_to_load_the_plugin():
    text = _prose(ADOPTION)

    assert "`copilot plugin install crapkit@crapkit`" in text
    assert "(harnesses.md#vs-code-with-github-copilot)" in text
    assert "GitHub Copilot CLI and VS Code load them too" not in text


def test_adoption_restates_coverage_optional_as_code_no_test_can_reach():
    text = _prose(ADOPTION)

    assert "is the first reason: code no test can reach" in text
    assert "code a test could reach but never will" not in text


# --- docs/resources.md ------------------------------------------------------------------

def test_the_lock_paragraph_names_the_release_that_moved_the_lock():
    release_071 = _doc("CHANGELOG.md").split("\n## 0.7.1 ", 1)[1].split("\n## ", 1)[0]

    assert "Move measurement locks outside report directories" in release_071
    assert "Before 0.7.1 the lock sat beside the artifact" in _prose(RESOURCES)
    assert "The old adjacent locks" not in _doc(RESOURCES)


def test_the_runner_retention_detail_lives_in_contributing():
    evidence = _section(RESOURCES, "## Logs and retained evidence")

    assert "--preview-retention" not in evidence and "`--retention-days`" in evidence
    assert "A run the filesystem will not fully delete" in _prose("CONTRIBUTING.md")
    assert "(../CONTRIBUTING.md#tests)" in evidence
