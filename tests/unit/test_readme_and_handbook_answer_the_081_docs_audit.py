"""README and the handbook tell a 0.8.0 user what 0.8.1 asks of them.

The 0.8.1 docs audit found README's Upgrading section naming no release, a CI
job that pins nothing while its prose says to pin, an Action job whose one
permission leaves a private checkout with none, a 44-character commit id,
transcripts that drop lines the CLI prints, and clone sizes that go stale at
every release. The handbook named none of 0.8.1's upgrade steps, never set
`fetch-depth`, listed cache files 0.8.1 no longer writes, and titled sections
by the 0.4.x release that added them. Each test pins one finding, to the code
where the claim is checkable there.
"""
import re
import tomllib
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from crapkit import churn_cache, churn_commits, churn_log, coupling_cache
from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import _shared

ROOT = Path(__file__).resolve().parents[2]
README = "README.md"
HANDBOOK = "docs/handbook.html"
_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$", re.M)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(heading: str, rel: str = README) -> str:
    """The raw text under a Markdown `heading`, its subsections included; a
    `#` line inside a fence is code, not a heading."""
    text = _doc(rel)
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    blank = _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m[0]), text[start:])
    ends = [m.start() for m in _HEADING.finditer(blank) if len(m[1]) <= level]
    return text[start:start + ends[0] if ends else None]


def _prose(heading: str, rel: str = README) -> str:
    return " ".join(_section(heading, rel).split())


def _fences(body: str) -> list[str]:
    return re.findall(r"^```[^\n]*\n(.*?)^```", body, re.M | re.S)


def _version() -> str:
    return tomllib.loads(_doc("pyproject.toml"))["project"]["version"]


# --- README: Upgrading (da-47, da-56) ------------------------------------------------

def test_readme_upgrading_names_the_0_8_1_steps():
    steps = _section("### Upgrading from 0.8.0 to 0.8.1")

    for step in ('"coverage>=7.13.1"', "`crapkit ratchet prune`", "`--baseline N`", "pre-commit `rev`",
                 "`{python}`", "#080-to-081-in-order", "#analysis-version-13"):
        assert step in steps, step
    assert "#081)" not in _doc(README), "stage 1 dates the heading, so #081 never exists"


def test_readme_links_the_changelog_from_upgrading_and_the_documentation_table():
    assert "blob/main/CHANGELOG.md" in _section("## Upgrading")
    assert "| [CHANGELOG.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/CHANGELOG.md) |" in _doc(README)


def test_the_0_4_4_refusal_lives_in_the_upgrade_guide_and_exit_3_points_at_upgrading():
    guide = _section("### Analysis version 8", "docs/upgrading.md")

    assert "### Upgrading from 0.4.4" not in _doc(README)
    assert "[crapkit-analysis=7 lizard=1.24.0]" in _fences(guide)[0]
    assert "([Upgrading](#upgrading))" in _doc(README)


@pytest.mark.parametrize("page, stale", [
    ("docs/ratchet.md", "### Upgrading to 0.4.5"),
    ("docs/lanes.md", "since 0.4.5\n"),
    (HANDBOOK, "<h3>What changed in 0."),
    (HANDBOOK, "<h3>0.4.5 is one of those bumps</h3>"),
    (HANDBOOK, "What 0.4.5 caches"),
    (HANDBOOK, "ccn is untouched in every language"),
])
def test_reference_headings_say_what_they_describe_not_the_release_that_added_it(page, stale):
    assert stale not in _doc(page)


# --- README: CI and the Action (da-46, da-48, da-57, da-58) --------------------------

def test_the_route_4_job_pins_the_release_it_installs():
    job = next(f for f in _fences(_section("### Route 4: CI")) if "jobs:" in f)

    assert f'- run: pip install "crapkit=={_version()}"' in job
    assert "- run: pip install crapkit\n" not in job


def test_the_action_job_grants_the_checkout_its_read():
    job = next(f for f in _fences(_section("## The GitHub Action")) if "jobs:" in f)
    permissions = yaml.safe_load(job)["jobs"]["crapkit"]["permissions"]

    assert permissions == {"contents": "read", "pull-requests": "write"}
    assert "`contents: read` for the checkout" in _prose("## The GitHub Action")


def test_a_runner_without_gh_keeps_the_step_green_as_the_readme_says():
    steps = yaml.safe_load(_doc("action.yml"))["runs"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "post the comment")

    assert re.findall(r"\bexit\b[^\n]*", script) == ["exit 0"], "the step never exits with gh's code"
    assert "without the `gh` CLI on PATH posts nothing. In both cases the step stays green" in _prose(
        "## The GitHub Action")


def test_every_quoted_commit_id_is_a_whole_sha():
    ids = re.findall(r"git fetch origin ([0-9a-f]+)", _doc(README))

    assert ids and all(len(i) in (40, 64) for i in ids), ids


# --- README: first runs and transcripts (da-49 to da-52) -----------------------------

def test_the_python_install_line_brings_a_coverage_that_writes_start_lines():
    install = _fences(_section("## Quickstart: Python"))[0]

    assert install.strip() == 'pip install pytest-cov "coverage>=7.13.1"'
    assert "coverage.py 7.13.1 or newer" in _doc(README).split("## Quickstart: Python")[0]


def test_the_missing_provider_transcript_keeps_npms_banner():
    printed = _fences(_section("### 2. Install a coverage provider"))[0]

    assert "\n> app@1.0.0 test\n> vitest run --coverage " in printed


@pytest.mark.parametrize("page", [README, "docs/lanes.md"])
def test_the_provider_pin_reads_the_vitest_major_npm_installs(page):
    text = _doc(page)

    assert "`npm ls vitest`" in text and "@vitest/coverage-v8@5" in text
    assert "@vitest/coverage-v8@2" not in text and "@vitest/coverage-v8@3" not in text


def test_the_skipped_step_transcript_is_what_verify_prints_over_an_uncommitted_edit():
    run_1 = re.search(r"^run 1 @ ([0-9a-f]{11}):", _section("### 3. Score the repo"), re.M)[1]
    block = _fences(_section("### 6. Cover the new pieces"))[-1]
    demote = SimpleNamespace(crap=17.8, ccn=5, cov=0.2, path="src/grade.ts", start=38,
                             long_name="demote ( letter , row Row )", remedy="add-tests", dirty=True)

    assert f"vs baseline {run_1} (6 changed files)" in block
    assert _shared._gate_line(demote) in block.splitlines()
    assert "  findings: 0 committed / 3 dirty (uncommitted edits and untracked files)" in block.splitlines()


# --- clone sizes and the documentation table (da-54, da-55) --------------------------

@pytest.mark.parametrize("page", [README, "docs/adoption.md", "docs/upgrading.md", HANDBOOK])
def test_no_page_quotes_a_clone_size_that_moves_at_every_release(page):
    assert not re.search(r"\b\d+(?:\.\d+)?\s+MB\b", _doc(page)), page


def test_the_documentation_table_lists_harnesses_and_the_handbook_link_is_not_repeated():
    table = _section("## Documentation")

    assert "blob/main/docs/harnesses.md" in table
    assert table.count("https://www.jfgagne.com/crapkit/handbook.html") == 1
    assert "blob/main/docs/harnesses.md" in _doc(HANDBOOK)


# --- the handbook (da-59 to da-63) ----------------------------------------------------

def test_the_handbook_names_the_0_8_1_re_seed_and_the_coverage_floor():
    text = _doc(HANDBOOK)

    assert f"Analysis version {ANALYSIS_VERSION}, in 0.8.1, is another" in text
    assert "<code>crapkit ratchet prune --baseline N</code>" in text
    assert "coverage.py 7.13.1 or newer" in text and "configuration.md#the-launcher-token" in text


def test_the_handbook_ci_case_sets_fetch_depth_and_names_the_shallow_exit_4():
    text = _doc(HANDBOOK)
    exit_4 = re.search(r'<td class="mono">4</td>.*?</tr>', text, re.S)[0]

    assert "<code>fetch-depth: 0</code>" in text.split("<h3>4 · CI on a pull request</h3>")[1].split("<h3>")[0]
    assert "shallow clone" in exit_4 and "git fetch --unshallow" in exit_4


def test_the_handbook_cache_listing_is_what_the_code_writes():
    cache = _doc(HANDBOOK).split("<h3>What crapkit caches, and where it lives</h3>")[1].split("<h3>")[0]

    for name in (coupling_cache.CACHE_NAME, churn_cache.CACHE_NAME, churn_log.LOG_NAME, churn_commits.COMMITS_NAME):
        assert f".crapkit/{name}" in cache, name
    for retired in ("coupling-cache-v1.json", "churn-cache-v2.json", "churn-log-v2"):
        assert retired not in cache, retired


def test_the_polyglot_inventory_line_ends_with_the_store_it_wrote():
    assert "run 1 @ 8da79a04d51: 4 functions in 4 files (0 cached) -&gt; /repo/.crapkit/crap.sqlite" in _doc(HANDBOOK)
    assert "cached) -> {db_path}" in _doc("src/crapkit/cli/scoring.py")


def test_the_handbook_ci_case_offers_the_action():
    case = _doc(HANDBOOK).split("<h3>4 · CI on a pull request</h3>")[1].split("<h3>")[0]

    assert f"<code>uses: JeanFrancoisGagne/crapkit@v{_version()}</code>" in case
    assert "<code>working-directory</code>" in case
