"""Each line the pages quote about a missing value is the line the code prints.

README, AGENTS.md, the recover skill and docs/upgrading.md quote the refusals and
notes crapkit prints when a value was never measured: the junit verify cannot
read, the shallow clone, the mutant no test judged, the function whose scope the
older run never scored, the coverage rescore never took. Each check builds the
line from the code that prints it and looks for it on the page, so a reworded
message fails here and not in a reader's log search.
"""
import importlib.util
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
README = "README.md"
AGENTS = "AGENTS.md"
RECOVER = "plugin/skills/crapkit-recover/SKILL.md"
UPGRADING = "docs/upgrading.md"


@lru_cache(maxsize=None)
def _flat(name: str) -> str:
    """The page with its line breaks folded, since a quoted clause may wrap."""
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def _lane(results_artifact: str | None = "junit.xml"):
    from crapkit.config import Lane

    return Lane(name="py", command="pytest", artifact="cov.json", parser="coveragepy",
                scopes=("src",), results_artifact=results_artifact)


@pytest.mark.parametrize("page", [README, UPGRADING, RECOVER])
def test_the_pages_quote_the_step_verifys_junit_refusal_ends_with(page: str):
    from crapkit.cli.verifying import _unread_results_line

    step = _unread_results_line(_lane()).rsplit("; ", 1)[1]

    assert step == "run verify without --reuse-artifacts so the lane writes it again"
    assert f"`{step}`" in _flat(page)


@pytest.mark.parametrize("page", [README, RECOVER])
def test_the_pages_quote_the_shallow_line_worklist_prints(page: str):
    from crapkit.gitio import shallow_warning

    assert shallow_warning("churn counts") in _flat(page)


def test_the_action_repeats_the_line_the_readme_says_it_repeats():
    spec = importlib.util.spec_from_file_location(
        "crapkit_action_comment", ROOT / "tools" / "action" / "comment.py")
    comment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(comment)
    from crapkit.gitio import shallow_warning

    assert comment.SHALLOW_LINE == shallow_warning("churn counts")


def test_the_upgrade_note_quotes_the_start_of_the_shallow_line():
    from crapkit.gitio import shallow_warning

    start = shallow_warning("churn counts").split(";", 1)[0]

    assert f"`{start}`" in _flat(UPGRADING)


def test_the_exit_4_rows_name_the_refusal_ratchet_report_raises(tmp_path):
    """Pinned on the code path: with a debt key set, --enforce in a shallow clone
    raises a GitError, exit 4, ending with the fix the rows quote."""
    from crapkit.errors import GitError
    from crapkit.gitio import _SHALLOW_FIX, shallow_refusal

    refusal = shallow_refusal("ratchet report --enforce judges mark ages")

    assert isinstance(refusal, GitError) and refusal.exit_code == 4
    assert str(refusal).endswith(_SHALLOW_FIX)
    for page in (README, RECOVER):
        assert "with a debt key set" in _flat(page), page


def test_the_mutate_pages_quote_the_no_verdict_line_and_name_the_payload_keys():
    from crapkit.cli.analyses import _mutation_payload, _unjudged_lines
    from crapkit.mutate_pool import MutantVerdict

    (line,) = _unjudged_lines({"timed_out": 0, "killed": 7, "survived": 0, "no_verdict": 3})
    quoted = line.strip().split(";", 1)[0].replace("3 of the 7", "N of the K")
    payload = _mutation_payload([], [MutantVerdict.NO_VERDICT], [])

    assert quoted == "no verdict: N of the K killed ran no test (exit 5), so no test caught them"
    assert payload["killed"] == 1, "a no-verdict mutant stays inside killed under schema 1"
    assert {"timed_out", "no_verdict"} <= set(payload)
    for page in (README, UPGRADING):
        assert f"`{quoted}`" in _flat(page), page
        assert "`timed_out`" in _flat(page) and "`no_verdict`" in _flat(page), page


def test_the_digest_row_quotes_both_lines_digest_prints_for_a_function_the_older_run_lacks():
    from crapkit.config import Config
    from crapkit.digest import build_digest
    from crapkit.score import ScoredRow

    def row(scope, path, name):
        return ScoredRow(scope, path, name, 1, 9, 9, 9, 9, 5, 1, 1, 0.0, "measured", 90.0,
                         "decompose")

    before = [row("src", "src/a.ts", "f( )")]
    after = [*before, row("src", "src/b.ts", "g( )"), row("legacy", "legacy/c.ts", "h( )")]
    lines = build_digest(before, after, ceiling_of=Config(target=6).ceiling_of).lines
    prefixes = {line.split(":", 1)[0] for line in lines[1:]}

    assert prefixes == {"new over ceiling", "newly scored over ceiling in scope legacy"}
    assert "`new over ceiling`" in _flat(README)
    assert "`newly scored over ceiling in scope NAME`" in _flat(README)


def test_the_rescore_row_quotes_the_tail_rescore_prints_for_coverage_nobody_took():
    from crapkit.cli.scoring import _rescore_cov
    from crapkit.score import ScoredRow

    row = ScoredRow("src", "src/a.py", "f( )", 1, 9, 3, 3, 3, 5, 1, 1, 0.0, "no-lane", 12.0,
                    "add-tests")
    cell, tail = _rescore_cov(row, set())

    assert cell.strip() == "-"
    assert f"`{tail.strip()}`" in _flat(README)


def test_the_agents_field_table_names_the_fields_next_item_emits():
    from crapkit.cli.queue import _next_head

    head = _next_head({"id": 1, "commit": "abc"}, 0, 0, False, True)

    assert head["shallow"] is True
    for field in ("`shallow`", "`unmeasured`"):
        assert f"| {field} |" in (ROOT / AGENTS).read_text(encoding="utf-8"), field
