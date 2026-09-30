"""The contributor pages describe the repo a contributor works in.

The 0.8.1 docs audit found CONTRIBUTING.md, AGENTS.md and docs/accuracy.md out
of step with the code: the language guide named the wrong module for
`LANGUAGE_EXTENSIONS` and skipped change control, the pages gave one ccn
ceiling where `tools/accuracy` holds another, setup left out the Node tools the
push tier reads, the generate.py sentence named a third of what it writes, the
accuracy page left steps out of `fix` and "Add a check", buried its tasks under
the generated tables and named a receipt no plain run writes, and AGENTS.md
quoted `release.py check` with no version, described hang_guard.py twice and
counted the dev extra short. The MCP decision record kept a claim its
amendment reversed, and docs/architecture had no index and two links to files
not beside the page. Each test pins one
finding to the page, and to the code where the claim is checkable.
"""
import ast
import importlib.util
import re
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRIB = "CONTRIBUTING.md"
AGENTS = "AGENTS.md"
ACCURACY = "docs/accuracy.md"


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _prose(text: str) -> str:
    return " ".join(text.split())


def _section(rel: str, heading: str) -> str:
    """The body under a `## ` heading, up to the next `## ` heading."""
    return _doc(rel).split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def _tuple_constant(rel: str, name: str) -> tuple:
    tree = ast.parse(_doc(rel))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == name)
    return ast.literal_eval(node.value)


# --- CONTRIBUTING.md ----------------------------------------------------------------

def test_the_language_guide_names_the_module_that_defines_the_extensions():
    guide = _section(CONTRIB, "## Adding a language")

    assert re.search(r"^LANGUAGE_EXTENSIONS = ", _doc("src/crapkit/languages.py"), re.M)
    assert "from .languages import LANGUAGE_EXTENSIONS" in _doc("src/crapkit/universe.py")
    assert "| `src/crapkit/languages.py` | add its suffixes to `LANGUAGE_EXTENSIONS` |" in guide
    assert "`languages.LANGUAGE_EXTENSIONS`" in guide and "universe.py" not in guide


def test_a_version_bump_is_declared_where_the_guides_ask_for_it():
    guide = _prose(_section(CONTRIB, "## Adding a language"))
    bullet = _prose(_doc(AGENTS).split("- **Change what a metric measures", 1)[1].split("\n- **", 1)[0])

    assert "def _digest_row" in _doc("tools/accuracy/change_control.py")
    assert "`python tools/accuracy/change_control.py declare <id> --kind feature" in guide
    assert "`metric-digests.tsv` row for the new version" in guide
    assert "`python tools/accuracy/change_control.py declare` appends the `metric-digests.tsv` row" in bullet


def _ceilings() -> dict[str, int]:
    """Each scope's path and its ceiling in this repo's own crapkit.toml."""
    config = tomllib.loads(_doc("crapkit.toml"))
    default = config["crapkit"]["target"]
    return {path: scope.get("target", default) for scope in config["scope"] for path in scope["paths"]}


def test_every_ceiling_sentence_names_each_scope_target():
    ceilings = _ceilings()
    sentence = "6 for `src` and `tools`, 5 for `tools/accuracy`"

    assert (ceilings["src"], ceilings["tools"], ceilings["tools/accuracy"]) == (6, 6, 5)
    assert _prose(_doc(CONTRIB)).count(sentence) == 1
    assert _prose(_doc(AGENTS)).count(sentence) == 2
    assert "ccn 6 or below" not in _doc(AGENTS) and "ccn 6 or\n  lower" not in _doc(CONTRIB)


def test_setup_installs_the_node_tools_the_push_tier_reads():
    npm = "npm ci --prefix tools/accuracy/node/push"
    setup = _section(CONTRIB, "## Setup")

    assert 'f"npm ci --prefix tools/accuracy/node/{pin.tier}"' in _doc("tests/accuracy/kit/oracles.py")
    assert npm in setup.split("```", 2)[1]
    assert f"    {npm}\n" in _section(AGENTS, "## Setup")


def _generate():
    spec = importlib.util.spec_from_file_location("docs_generate_contributor", ROOT / "tools/docs/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generate_sentence_names_every_file_generate_writes():
    sentence = _prose(_doc(CONTRIB).split("`python tools/docs/generate.py` rewrites", 1)[1].split("CI checks", 1)[0])
    written = set(_generate().generated(ROOT)) - {CONTRIB}

    assert "the test schedule here" in sentence
    assert [name for name in sorted(written) if name not in sentence] == []


# --- docs/accuracy.md -----------------------------------------------------------------

def test_add_a_check_names_the_counts_the_process_marker_and_the_tier_fields():
    steps = _prose(_section(ACCURACY, "## Add a check"))

    assert "mark it @pytest.mark.process" in _doc("tests/accuracy/kit/tiers.py")
    assert {"tiers", "os"} <= set(_doc("tools/accuracy/run.py").split("FIELDS = frozenset(", 1)[1]
                                  .split(")", 1)[0].replace('"', " ").replace(",", " ").split())
    assert "`python tools/accuracy/change_control.py counts --write`" in steps
    assert "`@pytest.mark.process`" in steps
    assert '(`"tiers": ["nightly"]`)' in steps and '`"os": ["linux"]`' in steps


def test_the_fix_row_lists_every_bugs_column_and_the_rows_beside_it():
    row = next(line for line in _doc(ACCURACY).splitlines() if line.startswith("| `fix` |"))
    columns = _tuple_constant("tools/accuracy/retro.py", "BUG_COLUMNS")

    assert f"({len(columns)} columns: {', '.join(columns)})" in row
    assert "`retro.tsv`" in row and "`ledger.tsv`" in row


def test_past_bugs_says_a_replay_needs_uv():
    assert '["uv", "venv",' in _doc("tools/accuracy/retro.py")
    assert '["uv", "pip", "install",' in _doc("tools/accuracy/retro.py")
    assert "`uv venv` and `uv pip install`" in _prose(_section(ACCURACY, "## Past bugs"))


def test_declaring_a_change_says_where_declare_agrees_with_the_goldens():
    declaring = _prose(_doc(ACCURACY).split("### Declaring a change\n", 1)[1].split("\n## ", 1)[0])

    assert "Run `declare` on Linux or in the accuracy image" in declaring
    assert "`src/py/Upper.PY`" in declaring


def test_the_contributor_tasks_come_before_the_generated_tables():
    headings = re.findall(r"^## (.+)$", _doc(ACCURACY), re.M)
    intro = _doc(ACCURACY).split("\n## ", 1)[0]

    assert headings[-3:] == ["Releases", "Rulings", "Conventions"]
    for anchor in ("run-each-tier-locally", "when-a-check-fails", "add-a-check", "past-bugs", "change-control"):
        assert f"(#{anchor})" in intro, anchor


def test_the_receipt_name_marks_the_shard_optional():
    assert '"-".join(filter(None, parts))' in _doc("tools/accuracy/run.py")
    assert "`.crapkit/accuracy/<tier>[-<shard>]-<os>-<python>.json`" in _doc(ACCURACY)
    assert "<tier>-<shard>-<os>-<python>" not in _doc(ACCURACY)


# --- AGENTS.md ------------------------------------------------------------------------

def test_the_release_check_is_quoted_with_its_version():
    assert "the version to release is an argument, never inferred" in _doc("tools/release/release.py")
    assert "`python tools/release/release.py check VERSION`" in _prose(_doc(AGENTS))
    assert "push the release commit" not in _prose(_doc(AGENTS))


def test_agents_describes_the_hang_guard_once():
    tests = _doc(AGENTS)

    assert tests.count("`CHILD_HOLD`") == 1 and tests.count("`tests/hang_guard.py`") == 1
    assert "`tests/unit/test_loaded_machine_waits.py`" in tests


def _dev_extra() -> list[str]:
    extra = tomllib.loads(_doc("pyproject.toml"))["project"]["optional-dependencies"]["dev"]
    return [re.split(r"[<>=!~\[ ]", requirement, maxsplit=1)[0] for requirement in extra]


def test_the_setup_names_every_package_of_the_dev_extra():
    sentence = _prose(_section(AGENTS, "## Setup")).split("The dev extra ships ", 1)[1].split(". ", 1)[0]

    assert [name for name in _dev_extra() if f"`{name}`" not in sentence] == []
    assert "None of the four" not in _doc(AGENTS)


# --- the decision record and docs/architecture -----------------------------------------

def test_the_decision_points_at_the_amendment_that_reversed_its_frame_claim():
    adr = _doc("docs/adr/0001-mcp-invalid-arguments-are-tool-results.md")
    decision = adr.split("## Amendments (0.8.1)", 1)[0]

    assert "0.8.1: an unparsable frame gets no reply, see Amendments" in decision
    assert adr.count("## Amendments (0.8.1)") == 1 and "Amendment (0.8.1):" not in adr


def test_the_architecture_index_links_every_review_folder():
    folder = ROOT / "docs" / "architecture"
    index = (folder / "README.md").read_text(encoding="utf-8")
    reviews = sorted(path.name for path in folder.iterdir() if path.is_dir())

    assert len(reviews) == 5
    assert [name for name in reviews if f"]({name}/)" not in index] == []
    assert "(publication-evidence.md)" in index


_LINK = re.compile(r'\]\(([^)\s]+)\)|href="([^"]+)"')


def _local_links(page: Path) -> list[str]:
    links = [md or html for md, html in _LINK.findall(page.read_text(encoding="utf-8"))]
    return [link.split("#", 1)[0] for link in links if not link.startswith(("#", "http:", "https:"))]


def _dead_links(page: Path) -> list[str]:
    """Each relative link on the page that names no file beside it."""
    name = page.relative_to(ROOT).as_posix()
    return [f"{name} -> {link}" for link in _local_links(page) if not (page.parent / link).exists()]


def test_every_relative_link_in_the_architecture_reviews_resolves():
    """state-review.md linked a file one folder up as if it sat beside it, and
    the rerun report linked an evidence.zip only the published copy holds."""
    folder = ROOT / "docs" / "architecture"
    pages = sorted(folder.rglob("*.md")) + sorted(folder.rglob("*.html"))

    assert len(pages) > 10
    assert [dead for page in pages for dead in _dead_links(page)] == []
