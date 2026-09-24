"""kit.surfaces: every surface of one seed run lands on the same (path, handle) keys."""
import json
from pathlib import Path
import sys

import pytest

import hang_guard
from accuracy.kit import drive, repos, surfaces

SEED = Path(__file__).resolve().parent / "fixtures" / "seed"
REPO = Path(__file__).resolve().parents[3]
COMMENT = REPO / "tools" / "action" / "comment.py"
CLASSIFY = ("src/py/calc.py", "classify")
UNUSED = ("src/py/calc.py", "unused")
LABEL = ("src/ts/grade.ts", "label")


def _row(path, long_name, start, occurrence=1):
    return {"path": path, "long_name": long_name, "start": str(start),
            "occurrence": str(occurrence)}


def test_a_unique_name_is_its_own_handle_and_twins_are_numbered_in_file_order():
    roster = surfaces.Roster([
        _row("a.py", "__post_init__( self )", 30), _row("a.py", "__post_init__( self )", 10),
        _row("a.py", "parse( text )", 5), _row("b.ts", "(anonymous)", 7),
        _row("b.ts", "(anonymous)", 7, occurrence=2), _row("b.ts", "(anonymous)", 3)])

    assert roster.key("a.py", start=10) == ("a.py", "__post_init__#1")
    assert roster.key("a.py", start=30) == ("a.py", "__post_init__#2")
    assert roster.key("a.py", "parse( text )") == ("a.py", "parse")
    assert roster.key("b.ts", start=3) == ("b.ts", "(anonymous)#1")


def test_a_name_twins_share_cannot_key_without_a_line():
    roster = surfaces.Roster([_row("a.py", "f( )", 1), _row("a.py", "f( )", 9)])

    with pytest.raises(KeyError, match="no single function"):
        roster.key("a.py", "f( )")


def test_a_portable_row_decodes_and_a_comment_is_kept_apart():
    text = ("# commit=abc run_kind=coverage\npath\tlong_name\tcrap\r\n"
            'src/a.py\tf( )\t1.0000\n@crapkit-record-v1\t["src/a\\nb.py","g( )","2.0000"]\n')

    comments, rows = surfaces.read_tsv(text)

    assert comments == ["# commit=abc run_kind=coverage"]
    assert rows == [{"path": "src/a.py", "long_name": "f( )", "crap": "1.0000"},
                    {"path": "src/a\nb.py", "long_name": "g( )", "crap": "2.0000"}]


def test_an_unknown_record_version_is_refused():
    with pytest.raises(ValueError, match="unknown record encoding"):
        surfaces.read_tsv('path\tlong_name\n@crapkit-record-v9\t["a","b"]\n')


def test_normalize_replaces_times_roots_versions_and_volatile_keys(tmp_path):
    volatile = surfaces.Volatile(roots=surfaces.spellings(tmp_path), versions={"crapkit": "0.8.0"})
    payload = {"generated_at": "x", "lane_seconds": 1.5, "db": str(tmp_path / "db"),
               "note": "crapkit 0.8.0 at 2026-09-24T19:22:48Z", "rows": [{"crap": 5.1}]}

    assert surfaces.normalize(payload, volatile) == {
        "generated_at": "<generated_at>", "lane_seconds": "<lane_seconds>",
        "db": "<root>" + ("\\db" if sys.platform == "win32" else "/db"),
        "note": "crapkit <crapkit> at <time>", "rows": [{"crap": 5.1}]}


@pytest.fixture(scope="module")
def bundle(repo_templates, tmp_path_factory):
    """One seed repo at ceiling 3, measured, marked, then edited so verify fails:
    every surface this module reads, written once."""
    built = repo_templates.copy(repos.tree_spec(SEED), tmp_path_factory.mktemp("surfaces") / "r")
    root = built.root
    config = root / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("target = 6", "target = 3"),
                      encoding="utf-8")
    driver = drive.Driver(root, date_now=repos.EPOCH + 86_400)
    out = {"root": root}
    repos.git(root, "commit", "-qam", "ceiling 3", date=repos.EPOCH + 60)
    out["inventory"] = driver.run("inventory", "--export", "inventory.tsv")
    out["coverage"] = driver.run("coverage", "--export", "scored.tsv", "--sarif", "cov.sarif",
                                 "--github", "--json")
    out["coverage_github"], out["coverage_json"] = surfaces.split_workflow_commands(
        out["coverage"].stdout)
    out["worklist_json"] = driver.run("worklist", "--json")
    out["worklist_text"] = driver.run("worklist")
    out["report"] = driver.run("report", "--out", "report.html")
    out["seed"] = driver.run("ratchet", "seed")
    repos.git(root, "add", "-A")
    repos.git(root, "commit", "-qm", "marks", date=repos.EPOCH + 120)
    calc = root / "src" / "py" / "calc.py"
    calc.write_text(calc.read_text(encoding="utf-8").replace("if flag:", "if flag and flag > 1:"),
                    encoding="utf-8")
    out["verify"] = driver.run("verify", "--json", "--sarif", "verify.sarif", "--github",
                               "--emit-baseline", "baseline.tsv")
    out["verify_github"], out["verify_json"] = surfaces.split_workflow_commands(
        out["verify"].stdout)
    out["error"] = driver.run("brief", "src/py/nowhere.py", "f", "--json")
    out["mcp"] = driver.mcp([("list_worklist", {}), ("get_next_item", {}), ("nope", {})])
    out["store"] = root / ".crapkit" / "crap.sqlite"
    return out


def _file(bundle, name):
    return (bundle["root"] / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def roster(bundle):
    return surfaces.roster_from_tsv(_file(bundle, "scored.tsv"))


@pytest.mark.process
def test_the_exports_key_every_function(bundle, roster):
    scored = surfaces.from_tsv(_file(bundle, "scored.tsv"), roster)
    inventory = surfaces.from_tsv(_file(bundle, "inventory.tsv"), roster)

    assert len(scored) == 9
    assert scored.keys() == inventory.keys()
    assert scored[CLASSIFY]["crap"] == "5.1157407407407405"
    assert scored[CLASSIFY]["remedy"] == "decompose"
    assert ("src/ts/grade.ts", "clamp") in scored


@pytest.mark.process
def test_sarif_and_annotations_name_the_same_findings(bundle, roster):
    sarif = surfaces.from_sarif(json.loads(_file(bundle, "cov.sarif")), roster)
    annotations = surfaces.from_annotations(bundle["coverage_github"], roster)

    assert sarif.keys() == annotations.keys() == {CLASSIFY, UNUSED, LABEL,
                                                  ("src/ts/grade.ts", "never")}
    assert {name: sarif[CLASSIFY][name] for name in ("crap", "ceiling", "ccn", "cov", "remedy")} == {
        "crap": "5.1", "ceiling": "3", "ccn": "5", "cov": "83", "remedy": "decompose"}
    assert annotations[CLASSIFY]["cov"] == "83"


@pytest.mark.process
def test_the_json_payloads_and_mcp_land_on_the_same_keys(bundle, roster):
    worklist = surfaces.from_json(bundle["worklist_json"].json(), roster)
    listed = surfaces.mcp_result(bundle["mcp"][0], roster)

    assert worklist.keys() == listed.keys()
    assert worklist[CLASSIFY]["crap"] == listed[CLASSIFY]["crap"]
    assert surfaces.mcp_result(bundle["mcp"][2], roster)[("", "mcp_error")]["text"]


@pytest.mark.process
def test_the_verify_payload_reads_each_finding_list_by_section(bundle, roster):
    verdict = surfaces.from_json(json.loads(bundle["verify_json"]), roster)

    assert bundle["verify"].code == 6
    assert verdict[UNUSED]["gate_violations.crap"] == 12.0
    assert verdict[UNUSED]["ratchet_regressions.recorded"] == 6.0


@pytest.mark.process
def test_the_html_report_and_worklist_text_read_the_same_rows(bundle, roster):
    html = surfaces.from_html(_file(bundle, "report.html"), roster)
    text = surfaces.from_worklist_text(bundle["worklist_text"].stdout, roster)

    assert html.keys() == text.keys() and CLASSIFY in html
    assert (html[CLASSIFY]["CRAP"], html[CLASSIFY]["Cov"]) == ("5.1", "83%")
    assert (text[CLASSIFY]["crap"], text[CLASSIFY]["cov"]) == ("5.1", "83")


@pytest.mark.process
def test_the_ratchet_file_and_the_baseline_export_key_by_name(bundle, roster):
    marks = surfaces.from_tsv(_file(bundle, "crapkit-ratchet.tsv"), roster)
    baseline = surfaces.from_tsv(_file(bundle, "baseline.tsv"), roster)

    assert marks.keys() == {CLASSIFY, UNUSED, LABEL, ("src/ts/grade.ts", "never")}
    assert marks[UNUSED]["crap"] == "6.0000"
    assert len(baseline) == 9


@pytest.mark.process
def test_the_store_reads_the_run_the_export_wrote(bundle, roster):
    scored = surfaces.from_tsv(_file(bundle, "scored.tsv"), roster)
    stored = surfaces.from_store(bundle["store"], json.loads(bundle["coverage_json"])["run_id"])

    assert stored.keys() == scored.keys()
    assert repr(stored[CLASSIFY]["crap"]) == scored[CLASSIFY]["crap"]
    assert stored[CLASSIFY]["remedy_name"] == "decompose"


@pytest.mark.process
def test_the_pr_comment_reads_its_table_and_gate_bullets(bundle, roster, tmp_path):
    saved = {}
    texts = {"coverage": bundle["coverage_json"], "verify": bundle["verify_json"],
             "worklist_json": bundle["worklist_json"].stdout}
    for name, text in texts.items():
        saved[name] = tmp_path / f"{name}.json"
        saved[name].write_text(text, encoding="utf-8")
    markdown = tmp_path / "comment.md"
    done = hang_guard.run([sys.executable, str(COMMENT), "--coverage", str(saved["coverage"]),
                           "--verify", str(saved["verify"]), "--verify-exit", "6",
                           "--worklist", str(saved["worklist_json"]), "--out", str(markdown)])
    assert done.returncode == 0, done.stderr

    comment = surfaces.from_pr_comment(markdown.read_text(encoding="utf-8"), roster)

    assert comment[UNUSED]["gate.crap"] == "12.0"
    assert comment[CLASSIFY]["worklist.remedy"].startswith("decompose")


@pytest.mark.process
def test_an_error_envelope_and_the_run_list_name_no_function(bundle):
    error = surfaces.error_envelope(bundle["error"].json())[("", "error")]

    assert (error["exit"], error["kind"], error["schema"]) == (bundle["error"].code, "state", 1)
    listing = drive.Driver(bundle["root"]).json("runs", "list")
    assert ("", "run 1") in surfaces.runs(listing)
