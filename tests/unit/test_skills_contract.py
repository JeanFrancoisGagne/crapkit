"""The skills are the surface an agent drives crapkit from without reading the docs.

A skill that prints a subcommand the parser dropped sends the agent to exit 2. A
description that quotes a refusal string the CLI no longer emits never fires on
the refusal it was written for. Both directions are pinned here against the
parser and against the modules that print those strings.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent.parent
CRAPKIT_SKILL = "skills/crapkit/SKILL.md"
CUTS = "skills/crapkit/cuts.md"
RECOVER_SKILL = "skills/crapkit-recover/SKILL.md"
ONBOARD_SKILL = "skills/crapkit-onboard/SKILL.md"
ADOPTION = "docs/adoption.md"
PAGES = (CRAPKIT_SKILL, CUTS, RECOVER_SKILL, ONBOARD_SKILL, ADOPTION)
AUTO_SKILLS = (CRAPKIT_SKILL, RECOVER_SKILL)

# A command mention is a code span or a command line, never prose: "a repo
# crapkit measures" is English, `crapkit worklist --top 5` is a call.
_SPAN = re.compile(r"`([^`\n]+)`")
_CALL = re.compile(r"^\$?\s*(?:python -m )?crapkit\s+([a-z][a-z0-9-]*)(.*)$")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
# Path shapes: anything with a slash in it, and bare source filenames.
_SLASHED = re.compile(r"(?<![\w./-])(?:[\w.-]+/)+[\w.*-]+")
_SUFFIXED = re.compile(r"(?<![\w./-])[\w-]+\.(?:py|ts|tsx|js|jsx|toml|json|md|cfg|ini)\b")


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    """One read per page for the whole module. Every assertion below reaches for
    several pages, and the pages cannot change while the suite runs."""
    page = ROOT / name
    assert page.is_file(), f"{name} does not exist"
    return page.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _subcommands() -> frozenset[str]:
    return frozenset(_choices())


def _choices() -> dict:
    import argparse

    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


@lru_cache(maxsize=None)
def _flags(name: str) -> frozenset[str]:
    """Every option string one subcommand accepts."""
    return frozenset(opt for action in _choices()[name]._actions for opt in action.option_strings)


def _calls(page: str) -> list[tuple[str, str]]:
    """(subcommand, the rest of the call) for every crapkit invocation a page prints."""
    text = _doc(page)
    candidates = _SPAN.findall(text) + [ln.strip() for ln in text.splitlines()]
    found = [_CALL.match(candidate) for candidate in candidates]
    return [(m.group(1), m.group(2)) for m in found if m]


def _frontmatter(page: str) -> str:
    _, _, rest = _doc(page).partition("---\n")
    block, _, _ = rest.partition("\n---\n")
    assert block, f"{page} carries no frontmatter block"
    return block


def _field(page: str, key: str) -> str:
    (line,) = [ln for ln in _frontmatter(page).splitlines() if ln.startswith(f"{key}:")]
    return line.partition(":")[2].strip()


# --- the commands the pages print --------------------------------------------

def test_every_crapkit_call_the_pages_print_names_a_subcommand_the_parser_defines():
    unknown = {(page, sub) for page in PAGES
               for sub, _ in _calls(page) if sub not in _subcommands()}
    assert unknown == set(), "each names a subcommand argparse answers with exit 2"


def test_every_flag_the_pages_print_exists_on_the_subcommand_it_follows():
    wrong = {(page, sub, flag) for page in PAGES
             for sub, rest in _calls(page) if sub in _subcommands()
             for flag in _FLAG.findall(rest) if flag not in _flags(sub)}
    assert wrong == set(), "each flag is unknown to the subcommand it is printed on"


def test_the_pages_print_calls_at_all():
    """Guards the two tests above: an extractor that matches nothing passes both."""
    assert len(_calls(CRAPKIT_SKILL)) >= 5
    assert _calls(ADOPTION), "the adoption page prints no crapkit call"


def test_prose_that_merely_names_crapkit_is_not_read_as_a_call():
    assert _CALL.match("a repo crapkit measures") is None
    assert _CALL.match("$ crapkit verify --base main").groups() == ("verify", " --base main")


# --- the machine strings the descriptions quote ------------------------------
#
# A description quoting a string the code no longer prints is a pointer that
# never fires. Both are pinned against the module that emits them.

GATE_REFUSAL = "exceed the complexity ceiling"
LANE_REFUSAL = "produced no artifact"


def test_the_crapkit_description_quotes_the_gate_refusal_the_hook_prints():
    from crapkit.cli import verifying

    emitted = Path(verifying.__file__).read_text(encoding="utf-8")
    assert f"staged function(s) {GATE_REFUSAL} of " in emitted, "the hook reworded its refusal"
    assert GATE_REFUSAL in _frontmatter(CRAPKIT_SKILL), "the description quotes something else"


def test_the_recover_description_quotes_the_lane_failure_the_runner_raises(tmp_path):
    from crapkit.config import Lane
    from crapkit.errors import ToolError
    from crapkit.lanes import _artifact_path

    lane = Lane(name="js", command="npm test", artifact=".crapkit/cov/js.json",
                parser="istanbul", scopes=("web",))
    with pytest.raises(ToolError) as raised:
        _artifact_path(tmp_path, lane)

    assert LANE_REFUSAL in str(raised.value), "the lane runner reworded its failure"
    assert LANE_REFUSAL in _frontmatter(RECOVER_SKILL), "the description quotes something else"


# --- frontmatter: what makes a skill reachable -------------------------------

@pytest.mark.parametrize("page,name", [(CRAPKIT_SKILL, "crapkit"),
                                       (RECOVER_SKILL, "crapkit-recover"),
                                       (ONBOARD_SKILL, "crapkit-onboard")])
def test_each_skill_is_named_for_the_directory_that_holds_it(page: str, name: str):
    assert _field(page, "name") == name
    assert (ROOT / page).parent.name == name


@pytest.mark.parametrize("page", AUTO_SKILLS)
def test_the_two_auto_skills_stay_model_invocable(page: str):
    assert "disable-model-invocation" not in _frontmatter(page)
    assert _field(page, "description"), "no description is no pointer"


def test_the_onboarding_skill_costs_no_context():
    """A human adopts crapkit once per repo, so its description stays out of
    every turn's window."""
    assert _field(ONBOARD_SKILL, "disable-model-invocation") == "true"


@pytest.mark.parametrize("page", (CRAPKIT_SKILL, RECOVER_SKILL, ONBOARD_SKILL))
def test_each_frontmatter_block_parses_as_yaml(page: str):
    """The descriptions carry colons and quoted machine strings; unquoted, the
    loader that reads them stops at the colon."""
    yaml = pytest.importorskip("yaml")

    parsed = yaml.safe_load(_frontmatter(page))
    assert set(parsed) <= {"name", "description", "disable-model-invocation"}


# --- the catalogue stays language-shaped -------------------------------------

def test_the_cuts_catalogue_names_no_file_that_exists_in_this_repo():
    """cuts.md is decomposition shapes, not a tour of crapkit's own source. A
    real path in it reads as an instruction to go open that file."""
    text = _doc(CUTS)
    tokens = set(_SLASHED.findall(text)) | set(_SUFFIXED.findall(text))
    assert sorted(t for t in tokens if (ROOT / t).exists()) == []


def test_the_path_detector_sees_the_paths_it_is_pointed_at():
    """Guards the test above, which passes on an extractor that finds nothing."""
    assert _SLASHED.findall("read src/crapkit/packet.py first") == ["src/crapkit/packet.py"]
    assert _SUFFIXED.findall("edit crapkit.toml now") == ["crapkit.toml"]


# --- the handbook (HTML, so spans are <code> tags, not backticks) -------------

HANDBOOK = "docs/handbook.html"
_HTML_SPAN = re.compile(r"<code>([^<]+)</code>")


def _handbook_spans() -> list[str]:
    import html

    return [html.unescape(s) for s in _HTML_SPAN.findall(_doc(HANDBOOK))]


def test_the_handbook_exists_and_prints_crapkit_calls():
    assert any(_CALL.match(s.strip()) for s in _handbook_spans()), \
        "the handbook prints no crapkit call"


def test_every_handbook_crapkit_call_names_a_real_subcommand_and_flags():
    for span in _handbook_spans():
        m = _CALL.match(span.strip())
        if not m:
            continue
        assert m.group(1) in _subcommands(), \
            f"handbook names a subcommand the parser dropped: {m.group(1)}"
        for flag in _FLAG.findall(m.group(2)):
            assert flag in _flags(m.group(1)), \
                f"handbook puts {flag} on `crapkit {m.group(1)}`, which does not take it"


def test_bare_command_spans_in_the_handbook_start_with_real_subcommands():
    """The moment tables print commands without the `crapkit` prefix. The first
    token still has to be a live subcommand; flags are only checked on the
    explicit calls above, because a loose span may chain two commands."""
    bare = [s.strip() for s in _handbook_spans()
            if s.strip().split(" ")[0] in _subcommands() or ""]
    assert bare, "the handbook's moment tables print no bare commands"


def test_the_handbook_is_self_contained():
    """No network: the page must reference no external stylesheet, font host,
    script src, or raster image — it reads the same from file:// on a clone."""
    page = _doc(HANDBOOK)
    for needle in ("http://", "https://fonts.", "<script src", "<img", "@import"):
        assert needle not in page, f"handbook is not self-contained: found {needle!r}"
    assert 'href="https://' not in page.replace('href="https://github.com', ""), \
        "handbook links an external stylesheet or resource"
