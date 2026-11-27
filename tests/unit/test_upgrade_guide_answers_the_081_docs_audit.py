"""docs/upgrading.md tells a 0.8.0 user what 0.8.1 moves, in the order they do it.

The 0.8.1 docs audit read the guide against both releases and found it wrong in
places a user acts on: a link to a heading that does not exist, exit codes and
output moves no table named, a CI pin moved before the re-seed that turns CI red,
a Docker row that builds main's tip, a downgrade two releases back, a hook body
0.8.0's README wrote that the guide never mentions, and no ordered list of the
steps at all. Each test pins one finding to the page, and to the code where the
claim is checkable there.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit import sarif

ROOT = Path(__file__).resolve().parents[2]
GUIDE = "docs/upgrading.md"
STEPS = "## 0.8.0 to 0.8.1, in order"
_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$", re.M)


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(heading: str, rel: str = GUIDE) -> str:
    """The body under `heading`, its subsections included, joined as prose."""
    text = _doc(rel)
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    ends = [m.start() for m in _HEADING.finditer(text[start:]) if len(m[1]) <= level]
    return " ".join(text[start:start + ends[0] if ends else None].split())


def _headings() -> list[str]:
    return [f"{hashes} {title}" for hashes, title in _HEADING.findall(_doc(GUIDE))]


def _slug(title: str) -> str:
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


# --- the order of the page (da-33) -------------------------------------------------

def test_the_steps_are_the_first_section_after_the_installer_table():
    """While the next release is built, its section and subsections may sit above the steps."""
    heads = _headings()[1:]
    if re.fullmatch(r"## Upgrading to \d+\.\d+\.\d+", heads[0]):
        heads = [head for head in heads[1:] if not head.startswith("###")]

    assert heads[0] == STEPS


def test_each_step_links_a_heading_on_the_page_and_holds_no_fence():
    body = _doc(GUIDE).split(f"\n{STEPS}\n", 1)[1].split("\n## ", 1)[0]
    anchors = {_slug(h.split(" ", 1)[1]) for h in _headings()}
    steps = re.findall(r"^\d+\. ", body, re.M)
    linked = re.findall(r"\]\(#([\w-]+)\)", body)

    assert len(steps) == 13 and "```" not in body
    assert linked and set(linked) <= anchors, set(linked) - anchors


def test_downgrading_follows_removing_crapkit():
    heads = _headings()

    assert heads.index("## Downgrading") > heads.index("## Removing crapkit")


def test_the_version_13_sections_sit_in_one_appendix_after_the_steps_a_user_takes():
    heads = _headings()
    appendix = heads.index("## What analysis version 13 moves, section by section")

    assert heads.index("### Rust rows") > appendix > heads.index("## Library callers")
    assert not [h for h in heads if h.startswith("#### ")]


def test_saved_state_keeps_what_0_8_1_moved_under_its_own_heading():
    moved = _section("### What 0.8.1 changes in saved state")

    assert "the crapkit version changed" in moved and "full-suite guard" in moved
    assert "full-suite guard" not in _section("## Saved state and command behavior").split(
        "What 0.8.1 changes in saved state")[0]


# --- links and pins (da-27, da-32, da-42, da-45) -----------------------------------

def test_the_config_paths_section_links_the_analysis_version_the_reader_runs():
    from crapkit.analyze import ANALYSIS_VERSION

    body = _section("## Config paths that 0.8.1 reads on every OS")

    assert f"(#analysis-version-{ANALYSIS_VERSION})" in body.replace(" ", "")
    assert "#analysis-version-12" not in _doc(GUIDE)


def test_a_team_commits_every_pin_with_the_re_seed():
    team = _section("## A team upgrades every reader before the re-seed lands")

    assert "Upgrade every clone first." in team
    assert "the CI install pin, the Action's `uses:` pin and the pre-commit `rev`" in team
    assert "the CI pin and the pre-commit `rev` before you commit marks" not in team


def test_the_docker_row_builds_the_release_tag():
    """The row names the tag as vX.Y.Z: no release surface bumps a literal version
    there, so `v0.8.0` would have told a 0.8.1 reader to build 0.8.0."""
    row = next(line for line in _doc(GUIDE).splitlines() if line.startswith("| the Docker image |"))

    assert "`git fetch --tags`, then `git checkout vX.Y.Z` for the release you move to" in row
    assert "`git pull`" not in row


def test_downgrading_goes_back_one_release_and_the_deploy_cell_installs_it():
    lines = re.findall(r'crapkit==([\d.]+)', _section("## Downgrading"))
    cell = re.search(r'^DOWNGRADE_TO = "([\d.]+)"', _doc("tests/deploy/test_docs_install_paths.py"), re.M)

    assert lines == ["0.8.0"] * 3
    assert cell[1] == "0.8.0"


# --- exit codes and output (da-28, da-29, da-30, da-43) ----------------------------

# (the row, what it quotes, and the source file that prints the quote, when it quotes one)
LOAD_REFUSALS = [
    ("`bash -c` or `sh -c` payload", "narrows a full-suite coverage run", "src/crapkit/config.py"),
    ("differ only in case", "`unit?`", None),
    ('`results_artifact` is `"."`', "Name the report file", None),
    ("upper-case extension (`src/MAIN.CPP`, `src/Tool.PY`)", "commit hook", None),
    ("outside every scope and outside a `test`", "belongs to no declared scope", "src/crapkit/cli/verifying.py"),
    ("the Action's `top` input", "::warning", "tools/action/comment.py"),
    ("more than four decimals", "29.99996", None),
]


@pytest.mark.parametrize(("row", "quote", "source"), LOAD_REFUSALS, ids=[r[0][:24] for r in LOAD_REFUSALS])
def test_the_rest_of_the_exit_codes_have_their_rows(row, quote, source):
    rest = _section("## Other exit codes that move in 0.8.1")

    assert row in rest and quote in rest
    assert source is None or quote in _doc(source)


def test_the_changelog_upgrading_list_names_the_extension_and_test_scoped_moves():
    upgrading = _section("### Upgrading from 0.8.0", "CHANGELOG.md")

    assert "`src/Tool.PY`" in upgrading and "`test-scoped` refuses" in upgrading


VALUE_MOVES = ["`CRAP load 0.0`", "`-> next:`", "prints `-` in its cov column", sarif.SCHEMA_URI, "`is_test`",
               "`explain --history --json`"]


@pytest.mark.parametrize("move", VALUE_MOVES)
def test_every_output_move_without_an_exit_code_is_listed(move):
    values = _section("## Values that move without an exit code")

    assert move in values
    assert "Three changes move text" not in values


# --- the re-seed and its review (da-35 to da-40) -----------------------------------

def test_the_cp1252_rule_is_for_a_source_that_is_not_utf8():
    body = _section("### Analysis version 13")

    assert "In a source that is not UTF-8" in body
    assert "In Python, TypeScript and C, 0.8.0 keyed such a function" not in body


def test_the_coverage_install_lines_upgrade_what_is_installed():
    body = _section("### 0.8.1 on coverage 7.6 to 7.13.0")

    assert '`pip install -U "coverage>=7.13.1"`' in body and '`pip install -U "crapkit[py]"`' in body


def test_every_coverage_export_names_the_file_it_writes():
    text = _doc(GUIDE) + _section("### Upgrading from 0.8.0", "CHANGELOG.md")
    paths = re.findall(r"crapkit coverage --export( [^\s`]+)?", text)

    assert paths and all(paths), "`coverage --export` with no path exits 2"
    assert "a `crapkit coverage --export`;" not in _doc(GUIDE)
    assert "`crapkit coverage --export before.tsv`" in _section("## Config paths that 0.8.1 reads on every OS")


def test_the_marks_review_is_verify_after_the_seed():
    measure = _section("## Measure before changing marks")

    assert "Keep a copy of the committed ratchet" not in measure
    assert "Compare the export above with the marks before you seed" not in measure
    assert "each `RATCHET` line names a mark whose function now scores higher" in measure


def test_the_pinned_seed_line_is_named_by_verifys_warning_not_its_refusal():
    from crapkit.cli import verifying

    body = _section("### Analysis version 13")

    assert not re.search(r"\d|\{", verifying._PINNED_SEED), "the refusal names no run id"
    assert "and the warning verify prints above its refusal, name it" in body


@pytest.mark.parametrize("section", ["#rust-rows", "#c-c-objective-c-and-java-rows", "#the-swift-and-rust-readers",
                                     "#shell-and-powershell-rows"])
def test_prune_names_every_key_move_version_13_makes(section):
    assert f"({section})" in _section("### Analysis version 13").replace(" ", "")


def test_the_inputs_lane_rerun_line_is_named():
    moved = _section("### What 0.8.1 changes in saved state")

    assert "a lane that lists `inputs` prints `its lane table or env differs" in moved


# --- the hook 0.8.0's README wrote, and the release evidence (da-41, da-44) --------

def test_the_0_8_0_hook_body_is_named_where_a_team_rewrites_or_removes_it():
    assert "`exec python -m crapkit hook-precommit` alone" in _section("## Teammates' clones")
    assert "A hook written from the 0.8.0 README has no uvx line" in _section("## Removing crapkit")
    assert "(one in a hook written from the 0.8.0 README)" in _section("## Removing crapkit")


@pytest.mark.parametrize("page", [GUIDE, "README.md"])
def test_the_release_evidence_is_the_suites_this_release_passed(page):
    text = _doc(page)

    assert "2026-09-07-implementation/REPORT.md" not in text
    assert "accuracy.md" in text and "tools/deploy/README.md" in text
