"""The report page, one fragment at a time, word for word.

report.py: the banner stacks every reason the numbers below cannot be trusted,
the chart scales CRAP load into a 660 by 130 box with 8 px margins (x from 0
to 660 in equal steps, y from 122 at zero load to 8 at the peak), a chip's
tone follows its value, and every value passes through html.escape with
quote=True.
"""
from crapkit import report

HERO = ('<header class="hero"><p class="kicker">crapkit report &middot; self-contained &middot; '
        'generated g</p><h1>r</h1><p class="standfirst">Run 7 at <code>0123456789a</code>, '
        'target ccn 6. Every number here comes from <code>crapkit worklist --json</code> and '
        '<code>crapkit trend --json</code> at their defaults.</p></header>')


def _worklist(**fields) -> dict:
    return {"run_id": 7, "commit": "0123456789abcdef", "stale": False, "active": [],
            "dormant_count": 3, "churn_window_months": 12, "floor": 5, **fields}


def test_the_header_and_footer_name_the_run_and_its_window():
    payload = {"generated_at": "g", "repo": "r", "target": 6, "worklist": _worklist()}

    assert report._header(payload) == HERO
    assert report._footer(payload) == (
        "<footer>Ranked by risk (ccn times recency-weighted churn) over the 12 months before "
        "HEAD's commit date, admitted at ccn &gt;= 5. Run the drill-down command on a row for "
        "its dark lines, history and mark.</footer>")


def test_the_banner_stacks_every_reason_the_numbers_cannot_be_trusted():
    payload = {"lanes": [], "worklist": _worklist(stale=True)}

    assert report._banner(payload) == (
        '<div class="banner stale"><p class="shout">Read this before the numbers below</p>'
        "<p>This repo declares no [[lane]], so no artifact can say which lines any test ran. "
        "Coverage reads 0 by default here, not by measurement.</p>"
        "<p>The snapshot is run 7 at <code>0123456789a</code> and HEAD has moved on. Fresh "
        "artifacts do not rescue a run measured at another commit: rerun "
        "<code>crapkit coverage</code>.</p></div>")


def test_the_stale_lanes_are_counted_and_listed_with_their_notes():
    lanes = [{"name": "a", "note": "edited"}, {"name": "b", "note": ""},
             {"name": "c", "note": "moved"}]

    assert report._stale_lane_reason(lanes) == [
        "<p><b>2 of 3 lanes are stale.</b> One stale lane blacks out line-level coverage "
        "repo-wide, not just its own scopes. Rerun "
        '<code>crapkit coverage</code>.</p><ul class="lanes"><li><b>a</b>: edited</li>'
        "<li><b>c</b>: moved</li></ul>"]


def test_the_chart_scales_each_load_into_the_box():
    loads = [float(load) for load in range(8)]

    assert report._chart_points(loads) == [
        (0.0, 122.0), (94.3, 105.7), (188.6, 89.4), (282.9, 73.1), (377.1, 56.9),
        (471.4, 40.6), (565.7, 24.3), (660.0, 8.0)]


def test_two_runs_draw_a_line_and_one_draws_nothing():
    runs = [{"crap_load": 4.0}, {"crap_load": 2.0}]

    assert report._chart(runs[:1]) == ""
    assert report._chart(runs) == (
        '<figure class="panel"><svg viewBox="0 0 660 130" role="img" '
        'aria-label="CRAP load across 2 runs"><polyline class="series" '
        'points="0.0,8.0 660.0,65.0"/></svg><figcaption>CRAP load across 2 scored runs, '
        "oldest at the left. Peak 4.0. Down is better.</figcaption></figure>")


def test_each_chip_takes_the_tone_of_its_value():
    tones = [report._verdict_cell({"flag": flag, "remedy": remedy})
             for flag, remedy in (("measured", "ok"), ("estimated", "add-tests"), (None, None))]

    assert tones == ['<span class="chip i">measured</span><span class="chip o">ok</span>',
                     '<span class="chip w">estimated</span><span class="chip a">add-tests</span>',
                     ""]
    assert [report._grade_tone(grade) for grade in ("A+", "A", "B", "C", "D", "F")] == [
        "o", "o", "o", "w", "a", "a"]


def test_a_row_no_run_scored_leaves_its_cells_empty_and_quotes_are_escaped():
    assert (report._crap_cell({}), report._cov_cell({})) == ("", "")
    assert report._esc("a\"b'c<d>&") == "a&quot;b&#x27;c&lt;d&gt;&amp;"


def test_the_grades_table_has_a_row_per_scope():
    block = {"functions": 4, "over_target": 1, "crap_load": 12.5, "grade": "C"}
    payload = {"trend": {"runs": [{"by_scope": {"b": block, "a": {**block, "grade": "A"}}}]}}

    assert report._scopes(payload) == (
        '<section id="scopes">\n<h2>Grades by scope</h2>\n<div class="tw"><table><thead><tr>'
        "<th>Scope</th><th>Functions</th><th>Over target</th><th>CRAP load</th><th>Grade</th>"
        '</tr></thead><tbody><tr data-scope="a"><td class="mono">a</td><td class="mono">4</td>'
        '<td class="mono">1</td><td class="mono">12.5</td><td><span class="chip o">A</span>'
        '</td></tr><tr data-scope="b"><td class="mono">b</td><td class="mono">4</td>'
        '<td class="mono">1</td><td class="mono">12.5</td><td><span class="chip w">C</span>'
        "</td></tr></tbody></table></div>\n</section>")


def test_an_empty_history_says_so_in_every_table():
    payload = {"trend": {"runs": []}, "worklist": _worklist()}

    assert report._scopes(payload).endswith(
        '<tbody><tr><td colspan="5" class="empty">no scored run yet</td></tr></tbody></table>'
        "</div>\n</section>")
    assert report._trend(payload) == (
        '<section id="trend">\n<h2>Trend: 0 scored run(s)</h2>\n<div class="tw"><table><thead>'
        "<tr><th>Run</th><th>Commit</th><th>When</th><th>Functions</th><th>Over target</th>"
        "<th>CRAP load</th><th>Average</th></tr></thead><tbody><tr><td colspan=\"7\" "
        'class="empty">no scored run yet</td></tr></tbody></table></div>\n</section>')
    assert report._worklist(payload).startswith(
        '<section id="worklist">\n<h2>Worklist: 0 ranked, 3 dormant</h2>\n')
    assert '<td colspan="10" class="empty">nothing admitted</td>' in report._worklist(payload)


def test_a_trend_row_carries_every_field_of_its_run():
    run = {"run_id": 7, "commit": "0123456789abcdef", "created_at": "t", "functions": 4,
           "over_target": 1, "crap_load": 12.5, "avg": 3.1}

    assert report._trend_row(run) == (
        '<tr data-run="7"><td class="mono">7</td><td class="mono">0123456789a</td>'
        '<td class="mono">t</td><td class="mono">4</td><td class="mono">1</td>'
        '<td class="mono">12.5</td><td class="mono">3.1</td></tr>')


def test_rows_follow_one_another_with_nothing_between(monkeypatch):
    monkeypatch.setattr(report, "_worklist_row", lambda entry: "<r>")
    monkeypatch.setattr(report, "_trend_row", lambda run: "<t>")
    monkeypatch.setattr(report, "_chart", lambda runs: "")
    payload = {"trend": {"runs": [{}, {}]}, "worklist": _worklist(active=[{}, {}])}

    assert "<tbody><r><r></tbody>" in report._worklist(payload)
    assert "<tbody><t><t></tbody>" in report._trend(payload)


def test_the_page_joins_its_parts_line_by_line_and_escapes_its_title():
    payload = {"generated_at": "g", "repo": "a&b", "target": 6, "lanes": [],
               "worklist": _worklist(), "trend": {"runs": []}}

    page = report.render_report(payload)

    assert "<title>crapkit report: a&amp;b</title>" in page
    assert "</header>\n<div class=\"banner stale\">" in page


def test_the_row_ceiling_itself_is_allowed():
    assert report.report_top(report.REPORT_ROW_CEILING) == report.REPORT_ROW_CEILING
