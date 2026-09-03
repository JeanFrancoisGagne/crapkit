"""`crapkit report`: one static page off the payloads the CLI already answers.

The page renders three things and invents none of them: the worklist ranked by
risk, the per-scope grades `trend --json` carries on its newest run, and the
trend series itself. So the renderer is a pure function from those payloads to a
string, and it is pinned here against a recorded one: a real `crapkit coverage`
run over a two-scope consumer repo, captured from `worklist --json` and
`trend --json`.

Two hazards get their own tests. A function name is source text, not markup: the
recording carries `read_chunk( path , max_bytes = 1 < < 20 , ... )`, and
unescaped its `<` opens a tag the browser swallows the rest of the row into. And
a lane whose artifact predates the working tree makes every line-level number on
the page a lie, which is a banner, not a footnote.
"""
import json
import re
from pathlib import Path

import pytest

from crapkit.errors import ConfigError
from crapkit.report import REPORT_ROW_CEILING, render_report, report_top

ROOT = Path(__file__).resolve().parent.parent.parent
RECORDED = ROOT / "tests" / "fixtures" / "recorded" / "report_payload.json"

# The recorded row whose signature is markup if nobody escapes it.
SHIFT_ROW = 1


def payload() -> dict:
    return json.loads(RECORDED.read_text(encoding="utf-8"))


def rendered() -> str:
    return render_report(payload())


def _rows(page: str) -> list[str]:
    return re.findall(r'<tr class="wl">(.*?)</tr>', page, re.S)


def _with_lanes(lanes: list[dict]) -> dict:
    return {**payload(), "lanes": lanes}


def _stale_lanes() -> list[dict]:
    """11 of 14 stale: the state `crapkit next-item` reports on openclaw today."""
    stale = [{"name": f"lane{i}", "note": f"lane 'lane{i}': files in its scopes changed"}
             for i in range(11)]
    return stale + [{"name": f"fresh{i}", "note": ""} for i in range(3)]


# --- the recorded payload ----------------------------------------------------

def test_the_recorded_payload_renders_a_whole_html_document():
    page = rendered()
    assert page.startswith("<!DOCTYPE html>")
    assert page.rstrip().endswith("</html>")
    assert "<title>" in page


def test_the_page_makes_no_network_request():
    """The contract the handbook is already held to: it reads the same from
    file:// on a clone with the wifi off."""
    page = rendered()
    for needle in ("http://", "https://", "<script", "<img", "@import"):
        assert needle not in page, f"report is not self-contained: found {needle!r}"


def test_the_page_carries_one_row_per_recorded_worklist_entry():
    assert len(_rows(rendered())) == len(payload()["worklist"]["active"])


def test_the_rows_keep_the_risk_order_the_worklist_ranked_them_in():
    """The recorded risks are 2.6154, 0.0336, 0.0144. A renderer that re-sorts
    would put a different function first than `crapkit worklist` just printed."""
    rows = _rows(rendered())
    risks = [e["risk"] for e in payload()["worklist"]["active"]]

    assert risks == sorted(risks, reverse=True), "the recording is not ranked"
    assert [str(r) in row for r, row in zip(risks, rows)] == [True] * len(risks)


def test_every_row_prints_the_drill_down_command_for_its_function():
    """The per-function detail was cut: 46,567 rendered rows measured 9.85 MB.
    Each row prints the command that answers instead."""
    page = rendered()
    for entry in payload()["worklist"]["active"]:
        assert f"crapkit explain {entry['path']}" in page


def test_the_page_states_how_many_rows_the_worklist_left_out():
    """`dormant_count` is 2 in the recording. A page that shows three rows and
    never says three of how many reads as the whole corpus."""
    assert f'{payload()["worklist"]["dormant_count"]} dormant' in rendered()


def test_the_page_grades_every_scope_the_newest_trend_run_carries():
    page = rendered()
    newest = payload()["trend"]["runs"][-1]

    assert set(newest["by_scope"]) == {"py", "src"}, "the recording lost a scope"
    for scope, block in newest["by_scope"].items():
        assert f'data-scope="{scope}"' in page
        assert block["grade"] in page


def test_the_page_carries_every_run_the_trend_series_holds():
    page = rendered()
    for run in payload()["trend"]["runs"]:
        assert f'data-run="{run["run_id"]}"' in page


def test_the_page_names_the_run_and_commit_it_was_built_from():
    page = rendered()
    assert payload()["worklist"]["commit"][:11] in page
    assert payload()["generated_at"] in page


# --- escaping ----------------------------------------------------------------

def test_the_recorded_shift_default_is_escaped_rather_than_parsed_as_a_tag():
    entry = payload()["worklist"]["active"][SHIFT_ROW]

    assert "1 < < 20" in entry["function"], "the recording lost its angle brackets"
    page = rendered()

    assert "1 &lt; &lt; 20" in page
    assert "1 < < 20" not in page


def test_a_function_name_cannot_close_the_cell_it_sits_in():
    data = payload()
    data["worklist"]["active"][0]["function"] = "f( )</td><td>injected"

    page = render_report(data)

    assert "&lt;/td&gt;" in page
    assert "</td><td>injected" not in page


def test_a_path_carrying_an_ampersand_stays_one_ampersand():
    data = payload()
    data["worklist"]["active"][0]["path"] = "src/a&b.ts"

    page = render_report(data)

    assert "src/a&amp;b.ts" in page
    assert "src/a&b.ts" not in page


def test_a_scope_name_is_escaped_in_the_grades_table():
    data = payload()
    newest = data["trend"]["runs"][-1]
    newest["by_scope"] = {"<b>py</b>": next(iter(newest["by_scope"].values()))}

    page = render_report(data)

    assert "&lt;b&gt;py&lt;/b&gt;" in page
    assert "<b>py</b>" not in page


def test_a_lane_note_is_escaped_in_the_banner():
    page = render_report(_with_lanes([{"name": "js", "note": "changed <under> src"}]))

    assert "&lt;under&gt;" in page
    assert "<under>" not in page


# --- the staleness banner ----------------------------------------------------

def test_the_banner_shouts_which_lanes_are_stale():
    page = render_report(_with_lanes(_stale_lanes()))

    assert 'class="banner stale"' in page
    assert "11 of 14" in page
    for lane in _stale_lanes():
        if lane["note"]:
            assert lane["name"] in page


def test_a_stale_banner_names_the_command_that_clears_it():
    """A banner that says what is wrong and not what to run is a dead end:
    nothing rereads an artifact until a run does."""
    assert "crapkit coverage" in render_report(_with_lanes(_stale_lanes()))


def test_the_banner_is_quiet_when_every_lane_is_current():
    page = rendered()
    assert 'class="banner stale"' not in page
    assert 'class="banner fresh"' in page


def test_a_repo_with_no_lanes_says_so_rather_than_claiming_freshness():
    """No lane means no artifact can speak for any line, which reads as fresh
    only because there is nothing left to be stale."""
    page = render_report(_with_lanes([]))

    assert 'class="banner stale"' in page
    assert "no [[lane]]" in page


def test_a_run_behind_head_is_its_own_stale_banner():
    """`stale: true` means the snapshot predates HEAD. Fresh artifacts do not
    rescue a run measured at a different commit."""
    data = payload()
    data["worklist"]["stale"] = True

    page = render_report(data)

    assert 'class="banner stale"' in page
    assert "HEAD has moved on" in page


# --- the row ceiling ---------------------------------------------------------

def test_report_top_takes_the_configured_worklist_top():
    assert report_top(50) == 50


def test_report_top_refuses_a_worklist_that_would_not_open():
    """Measured: 46,567 admitted rows render to 9.85 MB and 46,567 DOM rows. A
    page that hangs the tab is worse than no page."""
    with pytest.raises(ConfigError) as raised:
        report_top(REPORT_ROW_CEILING + 1)

    assert "worklist_top" in str(raised.value)
    assert str(REPORT_ROW_CEILING) in str(raised.value)


# --- one look ----------------------------------------------------------------

def _colour_tokens(css: str) -> list[str]:
    return re.findall(r"--[a-z0-9-]+: *#[0-9A-Fa-f]{6};", css)


def test_the_report_paints_from_the_handbook_palette():
    """One tool, one look. Every colour token the page defines is one the
    handbook already defines, so the two pages cannot drift apart."""
    handbook = (ROOT / "docs" / "handbook.html").read_text(encoding="utf-8")
    tokens = _colour_tokens(rendered())

    assert len(tokens) > 20, "the page defines almost no colour tokens"
    assert [t for t in tokens if t not in handbook] == []


def test_the_page_reads_in_both_colour_schemes():
    assert "prefers-color-scheme: dark" in rendered()


# --- the number on the row ---------------------------------------------------

def _scored_payload() -> dict:
    """The recording predates the `crap` and `cov` row fields; a page rendered
    from a 0.5.0 payload carries both on every row."""
    data = payload()
    for entry, crap, cov in zip(data["worklist"]["active"], (12.5, 56.0, 3.0), (0.4, 0.0, 1.0)):
        entry.update(crap=crap, cov=cov)
    return data


def test_the_worklist_table_carries_crap_and_cov_columns():
    page = render_report(_scored_payload())

    assert "<th>CRAP</th>" in page and "<th>Cov</th>" in page
    first = _rows(page)[0]
    assert "12.5" in first and "40%" in first


def test_the_page_no_longer_claims_the_score_is_absent():
    page = render_report(_scored_payload())

    assert "no repo-wide payload" not in page
    assert "not on this page" not in page


def test_a_row_no_run_scored_leaves_the_score_cells_empty():
    """An inventory-only run scored nothing: null, rendered as nothing rather
    than as a number the page invented."""
    data = payload()
    data["worklist"]["active"][0].update(crap=None, cov=None)

    first = _rows(render_report(data))[0]

    assert "None" not in first
