"""coverage.py JSON parser seam: report text in, per-file function coverage out. Pure."""
import json

import pytest

from coverage_readers import parse_coveragepy
from crapkit.errors import ToolError

REPORT = {
    "meta": {"branch_coverage": True, "version": "7.13.2"},
    "files": {
        "pylib\\mod.py": {
            "functions": {
                "guarded": {
                    "start_line": 1,
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [4],
                    "summary": {"covered_lines": 3, "num_statements": 4,
                                "num_branches": 4, "covered_branches": 3},
                },
                "helper.<locals>.inner": {
                    "start_line": 7,
                    "executed_lines": [],
                    "missing_lines": [7, 8],
                    "summary": {"covered_lines": 0, "num_statements": 2,
                                "num_branches": 0, "covered_branches": 0},
                },
            },
        }
    },
}


def test_branch_coverage_per_function_with_span_from_line_data():
    per_file = parse_coveragepy(json.dumps(REPORT), path_prefix="")
    fns = {f.name: f for f in per_file["pylib/mod.py"]}
    g = fns["guarded"]
    assert g.start == 1 and g.end == 4
    assert g.branches_total == 4 and g.branches_covered == 3
    assert g.coverage == 0.75


def test_zero_branch_function_falls_back_to_execution():
    per_file = parse_coveragepy(json.dumps(REPORT), path_prefix="")
    fns = {f.name: f for f in per_file["pylib/mod.py"]}
    inner = fns["helper.<locals>.inner"]
    assert inner.invoked is False
    assert inner.coverage == 0.0


def test_path_prefix_rebases_to_repo_relative():
    per_file = parse_coveragepy(json.dumps(REPORT), path_prefix="scripts")
    assert list(per_file) == ["scripts/pylib/mod.py"]


# --- a report that carries less than crapkit asked for --------------------
#
# `pytest --cov --cov-report=json` without --cov-branch is the default shape of
# an existing CI artifact, which is exactly what --reuse-artifacts is for, and
# it used to fail the whole lane. So did one file that lost its "functions" key,
# which threw away every other file in the same report.

NO_BRANCH = {
    "meta": {"branch_coverage": False, "version": "7.13.2"},
    "files": {"pylib/mod.py": {"functions": {
        "guarded": {"start_line": 1, "executed_lines": [1, 2, 3], "missing_lines": [4],
                    "summary": {"covered_lines": 3, "num_statements": 4}},
    }}},
}


def test_a_report_without_branch_data_scores_from_its_statements(capsys):
    """The model already falls back to statements for every branchless function,
    so the refusal blocked arithmetic crapkit performs on every normal run."""
    per_file = parse_coveragepy(json.dumps(NO_BRANCH), path_prefix="")

    (guarded,) = per_file["pylib/mod.py"]
    assert guarded.coverage == 0.75, "3 of 4 statements"
    assert "statement" in capsys.readouterr().err, "and the downgrade is said out loud"


def test_the_statement_downgrade_names_the_lane_that_read_the_report(capsys):
    parse_coveragepy(json.dumps(NO_BRANCH), path_prefix="", label="lane 'py'")

    assert "lane 'py'" in capsys.readouterr().err


def test_a_report_with_neither_branch_nor_statement_data_is_still_refused():
    """The one case the refusal was really written for: nothing to divide by, so
    every function would score as fully covered."""
    hollow = {"meta": {"branch_coverage": False},
              "files": {"a.py": {"functions": {"f": {
                  "start_line": 1, "executed_lines": [1], "missing_lines": [],
                  "summary": {"covered_lines": 0, "num_statements": 0}}}}}}

    with pytest.raises(ToolError, match="branch"):
        parse_coveragepy(json.dumps(hollow), path_prefix="")


def test_one_file_without_function_regions_does_not_throw_the_report_away(capsys):
    """coverage.py writes "functions" once per code-region kind the file's
    reporter declares, so a file measured by a plugin reporter that declares
    none loses the key while every .py file in the same report keeps it."""
    mixed = {"meta": {"branch_coverage": True},
             "files": {"pylib/mod.py": REPORT["files"]["pylib\\mod.py"],
                       "tpl/page.html": {"summary": {"num_statements": 3}}}}

    per_file = parse_coveragepy(json.dumps(mixed), path_prefix="")

    assert sorted(per_file) == ["pylib/mod.py"], "the file with no regions joins nothing"
    assert len(per_file["pylib/mod.py"]) == 2, "and the file that was fine is scored"
    assert "tpl/page.html" in capsys.readouterr().err, "the skipped file is named"


def test_report_without_function_regions_anywhere_is_rejected():
    """No file in the report carries regions, which is the "coverage is too old"
    case the message is written for."""
    old = {"meta": {"branch_coverage": True}, "files": {"a.py": {"summary": {}}}}
    with pytest.raises(ToolError, match="function regions"):
        parse_coveragepy(json.dumps(old), path_prefix="")


def test_an_old_report_without_cov_branch_names_the_coverage_version():
    """`pytest --cov --cov-report=json` on a coverage below 7.6 is one shape,
    not two: no regions anywhere AND no branch data. The branch verdict got
    there first and sent the reader off to add --cov-branch, which changes
    nothing, before they could learn the version is the cause."""
    old = {"meta": {"branch_coverage": False},
           "files": {"a.py": {"summary": {}}, "b.py": {"summary": {}}}}

    with pytest.raises(ToolError, match="function regions"):
        parse_coveragepy(json.dumps(old), path_prefix="")


def test_malformed_report_is_loud():
    with pytest.raises(ToolError, match="coverage.py"):
        parse_coveragepy("not json", path_prefix="")


def test_qualname_collapse_fails_conservative_never_confident():
    # coverage.py collapses conditionally-defined same-name functions into one
    # region; the executed twin's data lands in the "" bucket, which we skip.
    # The pinned direction: the mismatched span joins nothing, scores untested
    # (CRAP overstated), never inherits the wrong twin's coverage.
    collapsed = {
        "meta": {"branch_coverage": True},
        "files": {"m.py": {"functions": {
            "": {"start_line": 1, "executed_lines": [9, 10, 11], "missing_lines": [],
                  "summary": {"covered_lines": 3, "num_statements": 3, "num_branches": 2, "covered_branches": 2}},
            "make": {"start_line": 6, "executed_lines": [], "missing_lines": [6, 7],
                      "summary": {"covered_lines": 0, "num_statements": 2, "num_branches": 0, "covered_branches": 0}},
        }}},
    }
    import json as _json

    from crapkit.score import score_rows
    from crapkit.snapshot import InventoryRow
    per_file = parse_coveragepy(_json.dumps(collapsed), path_prefix="")
    assert [f.name for f in per_file["m.py"]] == ["make"], "module bucket is skipped"
    executed_twin = InventoryRow("py", "m.py", "make( x )", 9, 11, 3, 3, 3, 3, 1, 1)
    (scored,) = score_rows([executed_twin], per_file, lane_scopes={"py"})
    assert scored.cov == 0.0 and scored.flag == "untested"


def _region(executed, missing, branches=(0, 0)):
    lines = len(executed) + len(missing)
    return {"executed_lines": executed, "missing_lines": missing,
            "summary": {"covered_lines": len(executed), "num_statements": lines,
                        "num_branches": branches[0], "covered_branches": branches[1]}}


# coverage.py 7.10.6 over (no start_line: a region starts where its lines do).
# A one-line def's line sits in its own region only, and a def with nothing
# but a docstring holds no line at all:
# 62 def outer(flag):           72 def wrap(items):            83 LIMIT = 3
# 63     def inner(value):      73     total = len(items)      85 def one_line(value): return value + 1
# 64         if value: ...      74     def each(item):         88 def opens(flag):
# 67     if flag: ...           75         return item + total 89     def never(v): return v
#                               76     return [each(i) for i in items]  90     if flag: ...
# 95 def only_doc():            97 def after_doc(v): return v
# 96     """Nothing but a docstring."""
NO_START_LINE = {"meta": {"branch_coverage": True, "version": "7.10.6"}, "files": {"m.py": {
    "functions": {
        "": _region([1, 62, 72, 83, 88, 95], []),
        "outer": _region([63, 67, 69], [68], branches=(2, 1)),
        "outer.inner": _region([], [64, 65, 66], branches=(2, 0)),
        "wrap": _region([73, 74, 76], []),
        "wrap.each": _region([], [75]),
        "one_line": _region([85], [], branches=(2, 2)),
        "opens": _region([90, 91, 92], [], branches=(2, 2)),
        "opens.never": _region([89], [], branches=(2, 1)),
        "only_doc": _region([], []),
        "after_doc": _region([97], [], branches=(2, 1)),
    }}}}


def test_a_region_with_no_start_line_starts_on_its_def_statement():
    """Before 7.13.1 a region names no start_line and its lines begin at the
    body. The def statement is the statement just ahead of the body, held by
    the code around the function, past the one-line defs that open the body.
    A one-line def holds its own def statement, and a def with no line has
    no place."""
    per_file = parse_coveragepy(json.dumps(NO_START_LINE), path_prefix="")

    assert {fn.name: (fn.start, fn.end) for fn in per_file["m.py"]} == {
        "outer": (62, 69), "outer.inner": (63, 66), "wrap": (72, 76), "wrap.each": (74, 75),
        "one_line": (85, 85), "opens": (88, 92), "opens.never": (89, 89),
        "only_doc": (0, 0), "after_doc": (97, 97)}


def test_a_function_whose_body_opens_with_a_one_line_def_joins_its_own_region():
    """never's line sits in never's region alone. Read as a def statement ahead
    of a body, the module line before it put never on opens's def line, and
    opens started on its second body line: the join handed opens never's 1 of
    2 branches. A one-line def after a def with no line took that def's line
    the same way."""
    from crapkit.score import score_rows
    from crapkit.snapshot import InventoryRow

    rows = [InventoryRow("s", "m.py", name, start, end, 2, 2, 2, 3, 1, 1)
            for name, start, end in (("one_line( value )", 85, 85), ("opens( flag )", 88, 92),
                                     ("opens.never( v )", 89, 89), ("only_doc( )", 95, 96),
                                     ("after_doc( v )", 97, 97))]
    per_file = parse_coveragepy(json.dumps(NO_START_LINE), path_prefix="")

    scored = score_rows(rows, per_file, lane_scopes={"s"})

    assert [(row.long_name, row.cov, row.flag) for row in scored] == [
        ("one_line( value )", 0.0, "untested"), ("opens( flag )", 1.0, "measured"),
        ("opens.never( v )", 0.0, "untested"), ("only_doc( )", 0.0, "untested"),
        ("after_doc( v )", 0.0, "untested")]


def test_a_nested_def_joins_its_own_region_in_a_report_with_no_start_line():
    """outer's body opens with inner's def, so a region starting at its first
    body line put outer on inner's start line, and the join handed inner
    outer's 1 of 2 branches while no test called inner."""
    from crapkit.score import score_rows
    from crapkit.snapshot import InventoryRow

    rows = [InventoryRow("s", "m.py", name, start, end, 2, 2, 2, 3, 1, 1)
            for name, start, end in (("outer( flag )", 62, 69), ("outer.inner( value )", 63, 66),
                                     ("wrap( items )", 72, 76), ("wrap.each( item )", 74, 75))]
    per_file = parse_coveragepy(json.dumps(NO_START_LINE), path_prefix="")

    scored = score_rows(rows, per_file, lane_scopes={"s"})

    assert [(row.long_name, row.cov) for row in scored] == [
        ("outer( flag )", 0.5), ("outer.inner( value )", 0.0),
        ("wrap( items )", 1.0), ("wrap.each( item )", 0.0)]


# coverage.py 7.6.0 over (no start_line), with the arcs a region lists; a
# return is an arc to minus the first line of the code that returns:
# 110 def host(items):                      120 def with_gen(items):
# 111     base = len(items)                 121     def pick(xs):
# 112     def add(v): return v + base       122         return any(x for x in xs)
# 113     def each(item):                   123     return pick(items)
# 114         return item + base            126 def only_nested():
# 115     return [add(each(i)) for i in items]  127     def lone(v): return v
ONE_LINE_REGIONS = {"meta": {"branch_coverage": True, "version": "7.6.0"}, "files": {"m.py": {
    "functions": {
        "": _region([1, 110, 120, 126], []),
        "host": _region([111, 113, 115], []),
        "host.add": {**_region([112], [], branches=(2, 2)),
                     "executed_branches": [[112, 113], [112, -112]]},
        "host.each": _region([114], []),
        "with_gen": _region([121, 123], []),
        "with_gen.pick": {**_region([122], [], branches=(3, 3)),
                          "executed_branches": [[122, -121], [122, 122], [122, -122]]},
        "only_nested": _region([], []),
        "only_nested.lone": {**_region([127], [], branches=(2, 1)),
                             "executed_branches": [[127, -126]],
                             "missing_branches": [[127, -127]]},
    }}}}


def _starts(report):
    return {fn.name: (fn.start, fn.end)
            for fn in parse_coveragepy(json.dumps(report), path_prefix="")["m.py"]}


def test_a_region_of_one_line_starts_where_its_arcs_return():
    """A region of one line that opens no other is a one-line def, or a def
    whose body is that one statement. add's arcs return to its own line: a
    one-line def. each lists no arc, and its encloser's region spans its def,
    so it starts on the statement ahead. pick's line holds a generator, whose
    return goes to that line too, and pick's own return goes to its def."""
    assert _starts(ONE_LINE_REGIONS) == {
        "host": (110, 115), "host.add": (112, 112), "host.each": (113, 114),
        "with_gen": (120, 123), "with_gen.pick": (121, 122),
        "only_nested": (126, 126), "only_nested.lone": (127, 127)}


def _without_arcs(report):
    """The same report as `coverage json` writes it for a run without --branch."""
    functions = report["files"]["m.py"]["functions"]
    bare = {name: {key: value for key, value in fn.items() if not key.endswith("_branches")}
            for name, fn in functions.items()}
    return {"meta": {"version": "7.6.0"}, "files": {"m.py": {"functions": bare}}}


def test_a_nested_region_of_one_line_with_no_arcs_starts_on_the_statement_ahead():
    """A report with no branch data lists no arc. Nested, the region starts on
    the statement ahead: the encloser spans the def and would win the join.
    add, a one-line def, then starts on its encloser's line before it, where
    no other function starts."""
    bare, with_arcs = _starts(_without_arcs(ONE_LINE_REGIONS)), _starts(ONE_LINE_REGIONS)

    assert bare.pop("host.add") == (111, 112)
    with_arcs.pop("host.add")
    assert bare == with_arcs


def _excluded_region(executed, excluded, start_line=None):
    region = {"executed_lines": executed, "missing_lines": [], "excluded_lines": excluded,
              "summary": {"covered_lines": 0, "num_statements": 0, "num_branches": 0,
                          "covered_branches": 0, "excluded_lines": len(excluded)}}
    return {**region, "start_line": start_line} if start_line else region


# 92 def excluded(value):  # pragma: no cover      97 def stub():
# 93     if value and value > 0:                   98     ...
# 94         return 1                              99 def partly(value):
# 95     return 0                                 100     if value is None:
#                                                 101         raise NotImplementedError
#                                                 102     return value
def _pragma_report(version, executed, start_lines):
    excluded, stub = start_lines
    return {"meta": {"branch_coverage": True, "version": version}, "files": {"m.py": {
        "functions": {
            "": _region([1, 92, 97, 99], []),
            "excluded": _excluded_region(executed, [93, 94, 95], excluded),
            "stub": _excluded_region([], [98], stub),
            "partly": {**_region([100, 102], []), "excluded_lines": [101]},
        }}}}


def test_a_region_whose_every_statement_is_excluded_is_marked_excluded():
    """coverage.py keeps the region of a `# pragma: no cover` def, and of a
    stub whose body is `...`, with no statements and its lines excluded.
    7.10.6 also lists the excluded lines a call ran as executed. A region
    that keeps any statement is measured as before. With no start_line the
    stub, a module-level def with a body of one line, starts on that line."""
    for report, stub_start in ((_pragma_report("7.16.1", [], (92, 97)), 97),
                               (_pragma_report("7.10.6", [93, 94], (None, None)), 98)):
        fns = parse_coveragepy(json.dumps(report), path_prefix="")["m.py"]

        assert [(fn.name, fn.start, fn.end, fn.excluded) for fn in fns] == [
            ("excluded", 92, 95, True), ("stub", stub_start, 98, True),
            ("partly", 99, 102, False)]
