"""What a fresh reader copies out of README.md and the handbook, pinned.

Every claim here was measured on a throwaway repo before it was written down:
the doctor transcript a clean config prints, the payload `next-item` emits, the
exit code git reports for a refused commit, the npm error a bare provider
install raises, the PowerShell switch arm that scores nothing. The assertions
compare the page against the code that produces it, so a transcript nobody can
reproduce fails here rather than costing a reader a failed command.
"""
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit.config import Config, Scope
from crapkit.cli import queue
from crapkit.errors import CrapkitError
from crapkit.invocation import _self
from crapkit.score import ScoredRow
from crapkit.uncovered import MissingLines

ROOT = Path(__file__).resolve().parent.parent.parent
README = "README.md"
HANDBOOK = "docs/handbook.html"
BLOB = "https://github.com/JeanFrancoisGagne/crapkit/blob/main"


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body under one heading, down to the next heading of the same depth."""
    lines = text.splitlines()
    assert heading in lines, f"the docs lost their {heading!r} heading"
    depth = heading.split(" ", 1)[0] + " "
    rest = lines[lines.index(heading) + 1:]
    end = next((i for i, ln in enumerate(rest) if ln.startswith(depth)), len(rest))
    return "\n".join(rest[:end])


def _row_starting(text: str, first_cell: str) -> str:
    rows = [ln for ln in text.splitlines() if ln.startswith(f"| {first_cell}")]
    assert len(rows) == 1, f"expected one row for {first_cell!r}, found {len(rows)}"
    return rows[0]


def _gate_row(command: str) -> str:
    return _row_starting(_section(_doc(README), "## The gate"), f"`{command}`")


def _subcommand_row(name: str) -> str:
    return _row_starting(_section(_doc(README), "## Subcommands"), f"`{name}")


# --- the score's provenance --------------------------------------------------

FORMULA = "CRAP = ccn^2 * (1 - cov)^3 + ccn"


def _formula_paragraph() -> str:
    """The fenced formula and the prose directly under it.

    Blank lines separate the blocks, so this is the fence plus the first
    paragraph beneath it: the two things a reader takes in together.
    """
    blocks = _doc(README).split("\n\n")
    index = next(i for i, block in enumerate(blocks) if FORMULA in block)
    return "\n\n".join(blocks[index:index + 2])


def test_the_formula_says_who_coined_the_metric():
    """C.R.A.P. is not crapkit's invention, and a page that prints the formula
    with no source reads as if it were. The handbook has carried the credit
    from its first draft; the README is where most readers meet the formula.
    """
    paragraph = _formula_paragraph()

    for name in ("Savoia", "Evans", "crap4j", "2007"):
        assert name in paragraph, f"the formula's paragraph never names {name}"


# --- step 2: the doctor transcript -------------------------------------------
#
# Measured: `crapkit doctor` on the quickstart's own repo prints five ok lines
# and the summary. The page used to show two, which reads as a tool that
# checked almost nothing.

def test_the_doctor_step_shows_one_line_per_check():
    block = _section(_doc(README), "### 2. Check the config against the repo")
    printed = [ln for ln in block.splitlines() if ln.startswith("ok   ")]

    assert len(printed) >= 5, "a clean doctor prints an ok line per check, not a bare summary"
    assert "doctor: no problems found" in block


def test_the_transcripts_ok_lines_are_the_ones_doctor_formats(capsys):
    """The `ok` prefix is column-padded by `_print_findings`. A hand-typed line
    with the wrong spacing is a transcript nobody's terminal produces."""
    from crapkit.cli.admin import _print_findings
    from crapkit.doctor import Finding

    _print_findings([Finding("ok", "lizard 1.24.0")])

    assert capsys.readouterr().out.splitlines() == ["ok   lizard 1.24.0",
                                                    "doctor: no problems found"]
    assert "ok   lizard " in _doc(README), "the transcript's ok lines lost their padding"


# --- step 4: the next-item payload -------------------------------------------

def _sample_payload() -> dict:
    """The JSON object the quickstart's step 4 prints."""
    block = _section(_doc(README), "### 4. Take the top item")
    (line,) = [ln for ln in block.splitlines() if ln.startswith("{")]
    return json.loads(line)


def _live_item() -> dict:
    from crapkit.worklist import admission

    row = ScoredRow("calc", "calc/grade.py", "classify( score )", 7, 28, 14, 14, 14,
                    22, 4, 8, 0.5, "measured", 38.5, "decompose", 13)
    # a path the artifacts spoke about, so the payload carries lines and no note
    return queue._next_item_payload(row, admission({}, 5),
                                    Config(target=6),
                                    MissingLines({"calc/grade.py": {9, 11}}, ""),
                                    handle="classify")


def test_the_sample_payload_carries_every_envelope_key_the_command_emits():
    sample = _sample_payload()

    assert set(sample) == {"schema", "empty", "item", "run_id", "commit",
                           "skipped_no_lane", "stale"}


def test_the_sample_item_carries_every_key_the_payload_builder_returns():
    """`handle` landed in 0.4.0 and the sample predated it, so a reader parsing
    the printed shape found a field the tool emits and the page never showed."""
    assert set(_sample_payload()["item"]) == set(_live_item())


def test_the_sample_payload_is_printed_with_sorted_keys():
    line = [ln for ln in _doc(README).splitlines() if ln.startswith('{"commit"')][0]

    assert line == json.dumps(json.loads(line), sort_keys=True), \
        "every read command prints sorted-keys JSON; the sample has to look like one"


# --- the gate table ----------------------------------------------------------

def test_the_precommit_row_says_which_exit_code_a_reader_actually_sees():
    """Measured: the hook exits 6 and `git commit` reports 1. git collapses any
    failed hook to 1, so a row promising 6 sends a reader hunting for a code
    their shell never shows."""
    assert "The hook exits 6; git reports 1" in _gate_row("crapkit hook-precommit")


def test_the_preview_row_says_it_needs_a_run_behind_it(tmp_path):
    """`rescore --gate` reads the newest run's coverage, so on a repo that has
    never scored it is exit 1 rather than a verdict."""
    from crapkit.cli._shared import _open_store

    with pytest.raises(CrapkitError) as raised:
        _open_store(tmp_path)

    assert f"run `{_self()} coverage` first" in str(raised.value)
    assert raised.value.exit_code == 1
    assert "after the first coverage run" in _gate_row("crapkit rescore FILE --gate")


# --- Route 3: the rev a reader pins ------------------------------------------

def test_the_rev_the_precommit_block_pins_is_the_newest_release_tag():
    """`rev` is resolved against the remote at install time, so a stale one
    silently gates a reader on an older crapkit."""
    (rev,) = re.findall(r"^\s+rev: (\S+)", _doc(README), re.M)
    tags = subprocess.run(["git", "tag", "--sort=-v:refname"],
                          cwd=ROOT, capture_output=True, text=True).stdout.split()

    assert tags, "no tags in this clone, so the rev cannot be checked"
    assert rev == tags[0], f"README pins {rev}, newest release tag is {tags[0]}"


def test_the_precommit_block_says_what_moves_the_rev():
    block = _section(_doc(README), "### Route 3: the pre-commit framework")

    assert any(ln.strip().startswith("#") and "release" in ln for ln in block.splitlines()), \
        "nothing in the yaml says the release step rewrites this line"


# --- the TypeScript quickstart -----------------------------------------------

def _provider_step() -> str:
    return _section(_doc(README), "### 2. Install a coverage provider")


def test_the_provider_install_is_pinned_to_the_vitest_major():
    """Measured on vitest 3.2.7: the unpinned install resolves the newest
    provider (4.x) and npm refuses the tree with ERESOLVE."""
    step = _provider_step()

    assert not any(ln.strip() == "npm i -D @vitest/coverage-v8" for ln in step.splitlines()), \
        "the printed command contradicts the sentence above it and fails on vitest 3"
    assert 'npm i -D "@vitest/coverage-v8@<your vitest major>"' in step


def test_the_provider_step_shows_both_worked_examples():
    step = _provider_step()

    for major in ("2", "3"):
        assert f'"@vitest/coverage-v8@{major}"' in step, f"no worked example for vitest {major}"


def test_the_failed_lane_step_says_it_stores_nothing():
    """Measured: with every lane failing, `coverage` exits 5 before it opens a
    store, so `.crapkit/crap.sqlite` is never created and the run ids below
    still start at 1."""
    assert "writes no run" in _provider_step()


# --- the name a command resolves ---------------------------------------------

def test_the_explain_row_states_how_it_resolves_a_name():
    row = _subcommand_row("explain")

    assert "exact" in row and "prefix" in row, \
        "the row never says which of two functions sharing an opening wins"


# --- the handbook ------------------------------------------------------------

def test_the_handbook_links_out_by_url_not_by_relative_path():
    """`../README.md` resolves above the project site on GitHub Pages, which is
    a 404 for every reader who did not open the page from a clone. A bare
    `lanes.md` is worse: Pages serves the file as text/markdown and the browser
    downloads it, so the reader gets a file where they expected a page."""
    page = _doc(HANDBOOK)

    assert 'href="../' not in page
    assert not re.search(r'href="[^"#:]*[.]md[#"]', page),         "the handbook links a .md file by relative path; Pages downloads those"
    for name in ("README.md", "AGENTS.md", "docs/lanes.md", "docs/ratchet.md",
                 "docs/configuration.md", "docs/agent-json.md", "docs/adoption.md"):
        assert f'href="{BLOB}/{name}"' in page, f"the handbook lost its {name} link"


# --- the README as PyPI renders it --------------------------------------------

_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")


def test_the_readme_links_out_by_url_so_pypi_renders_them():
    """PyPI publishes README.md verbatim as the long description, and a
    relative target like `docs/lanes.md` resolves against pypi.org there, where
    nothing answers. Every link target is absolute or an in-page anchor, and the
    handbook link opens the rendered page rather than its source."""
    targets = _LINK_TARGET.findall(_doc(README))

    relative = [t for t in targets if not re.match(r"https?://|#|mailto:", t)]
    assert relative == [], f"README links that go nowhere on PyPI: {relative}"
    assert "https://jeanfrancoisgagne.github.io/crapkit/handbook.html" in targets


def _packet_keys(monkeypatch) -> set[str]:
    """Every top-level key `brief` can publish, off the real builder."""
    import crapkit.coupling_cache
    import crapkit.dup
    import crapkit.gitio

    row = ScoredRow("core", "core/alpha.py", "alpha( a )", 1, 20, 8, 8, 8, 18, 1, 2,
                    0.5, "untested", 64.0, "decompose", 7)
    cfg = Config(churn_window_months=12, worklist_floor=1, target=6,
                 scopes=(Scope(name="core", paths=("core",), languages=("python",)),),
                 lanes=(), scoped_tests=(), diff_uncovered_max=None,
                 ratchet_file="crapkit-ratchet.tsv")
    for name, answer in (("_load_sources", {"core/alpha.py": "x\n"}), ("load_churn", {}),
                         ("load_uncovered", MissingLines({}, "no lane")),
                         ("head_commit", "abc123def4567"), ("ls_files", []),
                         ("_ratchet_entries", None),
                         ("_brief_versions", {"crapkit": "0"})):
        monkeypatch.setattr(queue, name, lambda *a, _v=answer, **k: _v)
    for module, name in ((crapkit.coupling_cache, "load_coupling"),
                         (crapkit.dup, "find_twins"), (crapkit.gitio, "file_log_patches")):
        monkeypatch.setattr(module, name, lambda *a, **k: [])
    store = SimpleNamespace(read_rows=lambda *a, **k: [row],
                            read_scored=lambda *a, **k: [row],
                            read_scored_file=lambda *a, **k: [row],
                            function_history=lambda *a, **k: [],
                            attempts_for=lambda keys: {key: [] for key in keys})
    loader = queue._BriefLoader(Path("/repo"), cfg, store, {"id": 7, "commit": "abc123def4567"})
    # `schema` is stamped on the way out by _print_json, not by the builder
    return set(queue._brief_packet(loader, row)) | {"schema"}


def test_the_handbook_counts_the_packet_fields_the_builder_returns(monkeypatch):
    """The count moved when `handle` and the dark-lines note landed. It is the
    first thing a reader checks a payload against."""
    assert f"{len(_packet_keys(monkeypatch))} top-level fields" in _doc(HANDBOOK)


def test_a_powershell_default_arm_scores_nothing(monkeypatch):
    """The handbook's reader bullet claims a point per arm. Measured, `default`
    is free: the same switch with and without one scores the same ccn."""
    from crapkit.analyze import analyze_source

    arms = "\n".join(f'        {n} {{ "{n}" }}' for n in (1, 2, 3))
    body = "function Get-Kind {\n    param($n)\n    switch ($n) {\n" + arms + "\n"
    (plain,) = analyze_source("s.ps1", body + "    }\n}\n")
    (defaulted,) = analyze_source("s.ps1", body + '        default { "many" }\n    }\n}\n')

    assert plain.ccn == defaulted.ccn == 4, "three arms over a base of 1, default free"
    assert "one point per non-default arm" in _doc(HANDBOOK)


def test_the_cognitive_sentence_admits_an_unmodelled_construct_reads_zero():
    """Cognitive complexity runs on every language, which is not the same as
    modelling every construct in them. A 0 that means "not counted here" reads
    as "nothing to read" unless the page says otherwise."""
    sentence = next(p for p in _doc(HANDBOOK).split("<p>") if "cognitive complexity" in p)

    assert "reads 0" in sentence


# --- the CI caveat -----------------------------------------------------------

@pytest.mark.parametrize("page", [README, HANDBOOK])
def test_no_page_claims_this_repos_ci_blocks_on_the_ratchet(page: str):
    """The ratchet verdict is the half of this repo's CI that is not armed.

    Two different lines, two different answers. The diff gate does block: the
    `test` job runs `python -m crapkit hook-precommit` and the exit code stands
    (`test_ci_does_not_swallow_the_crapkit_gate_exit_code` holds it there). The
    `dogfood` job still reports its verdict without enforcing it, so a rising
    mark, a new test failure or a dark diff line is reported and not enforced. A
    page claiming otherwise promises an enforcement point nothing arms, which is
    why the anchor below is the verdict's line rather than the gate's.

    That line moved when the dogfood job became a consumer of the composite
    action. It used to be `verify --json > verdict.json || true`, the job
    running verify itself and dropping the code; now the action runs verify and
    `gate: "false"` is what decides whether the code reaches the check. Same
    answer, and the anchor follows it.
    """
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    text = " ".join(_doc(page).lower().split())

    assert 'gate: "false"' in workflow, (
        "the caveat below is about this line; the dogfood job runs the action "
        "with the gate off, so verify's verdict is reported and not enforced. "
        "Arm it and these claims become sayable")
    for claim in ("ci blocks", "ci enforces", "ci fails the", "blocked in ci",
                  "enforced in ci", "ratchet in ci"):
        assert claim not in text, f"{page}: {claim!r}"
