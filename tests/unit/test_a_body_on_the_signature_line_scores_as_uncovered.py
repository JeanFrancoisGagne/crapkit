"""A Python def whose body starts on its signature's last line scores as uncovered.

coverage.py reads a statement's lines as the statement's first line, and it
counts the `def` statement, which runs at import, as the enclosing scope's. A
body that starts on the line where the signature's colon sits is read as part
of that statement, whether the signature spans several lines, the body goes on
inside brackets, or a backslash joins the next line: a called
`def multi_called(a,\\n b): return a` read measured 0.0, and an uncalled
`def cont_uncalled(x): return [\\n x]` read 0.5. The one-line floor asked only
whether the def starts and ends on one line, so both read add-tests, advice no
test can satisfy. The reader now marks the def, and the coverage run, rescore's
preview and a rejudged packet floor it off that mark.
"""
import json
import subprocess
import sys
from contextlib import closing

from crapkit import packet
from crapkit.analyze import analyze_files, analyze_source, fingerprint, load_cache, save_cache
from crapkit.coverage_py import parse_coveragepy_both_file
from crapkit.score import ScoredRow, overlay_stale_coverage, score_rows, scored_tsv_lines
from crapkit.snapshot import InventoryRow, build_inventory_rows, tsv_lines
from crapkit.store import SnapshotStore
from untraced_child import untraced_env

BACKSLASH = "\\"
MODULE = ("def multi_called(a,\n"
          "        b): return a\n"
          "def cont_uncalled(x): return [\n"
          "    x]\n"
          "def cont_called(x): return [\n"
          "    x]\n"
          "def joined_called(x): " + BACKSLASH + "\n"
          "    return x\n"
          "def two(x):\n"
          "    return x\n")
DRIVER = ("import mod\nmod.multi_called(1, 2)\nmod.cont_called(1)\n"
          "mod.joined_called(1)\nmod.two(1)\n")

# At ceiling 1 an uncovered ccn-1 function scores 1 + 1 = 2 and fails it, so the
# remedy says which way out there is; a covered one scores 1 and passes.
CEILING = 1


def _names(rows) -> dict:
    return {r.long_name.split("(")[0].strip(): r for r in rows}


def test_the_reader_marks_a_body_that_starts_on_the_signature_line():
    source = MODULE + ("def one(x): return x\n"
                       "def noted(x):  # the body is on the next line\n"
                       "    return x\n"
                       "def lam(f=lambda x: x):\n"
                       "    return f\n"
                       "def annotated(x) -> dict[str, int]: return {}\n"
                       "def generic[T](x: T) -> T: return x\n"
                       "class C:\n"
                       "    def m(self): return (\n"
                       "        1)\n"
                       "    def n(self):\n"
                       "        return 1\n")

    marks = {name: r.inline_body for name, r in _names(analyze_source("mod.py", source)).items()}

    assert marks == {"multi_called": 1, "cont_uncalled": 1, "cont_called": 1,
                     "joined_called": 1, "two": 0, "one": 1, "noted": 0, "lam": 0,
                     "annotated": 1, "generic": 1, "m": 1, "n": 0}


def test_a_typescript_function_is_never_marked():
    records = analyze_source("app.ts", "function f() { return 1; }\nconst g = (x) =>\n  x;\n")

    assert [r.inline_body for r in records] == [0, 0]


def _coverage_py_report(tmp_path):
    (tmp_path / "mod.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "driver.py").write_text(DRIVER, encoding="utf-8")
    env = {**untraced_env(), "COVERAGE_FILE": str(tmp_path / ".coverage")}
    for command in (["run", "--branch", "driver.py"], ["json", "-o", "coverage.json"]):
        subprocess.run([sys.executable, "-m", "coverage", *command], cwd=tmp_path, env=env,
                       check=True, capture_output=True, text=True)
    functions, _, _ = parse_coveragepy_both_file(tmp_path / "coverage.json", path_prefix="")
    return functions


def test_the_run_floors_each_shape_whether_or_not_a_test_called_it(tmp_path):
    rows = build_inventory_rows({"src": analyze_source("mod.py", MODULE)})

    scored = score_rows(rows, _coverage_py_report(tmp_path), lane_scopes={"src"},
                        target=CEILING)

    floor = ("untested", 0.0, "split-lines")
    assert {name: (r.flag, r.cov, r.remedy) for name, r in _names(scored).items()} == {
        "multi_called": floor, "cont_uncalled": floor, "cont_called": floor,
        "joined_called": floor, "two": ("measured", 1.0, "ok")}


# ccn 3 at cov 0 scores 3 * 3 * 1 + 3 = 12, over a ceiling of 6.
INLINE = InventoryRow("src", "src/mod.py", "f( a , b )", 1, 2, 3, 3, 3, 2, 2, 0, 0, 1,
                      inline_body=1)


def test_the_preview_floors_a_body_moved_onto_the_signature_line():
    measured = ScoredRow("src", "src/mod.py", "f( a , b )", 1, 3, 3, 3, 3, 3, 2, 0,
                         1.0, "measured", 3.0, "ok", 0, 1)

    preview = overlay_stale_coverage([INLINE], [measured], lane_scopes={"src"}, target=6)

    assert [(r.flag, r.cov, r.remedy) for r in preview] == [("untested", 0.0, "split-lines")]


def refuse(path):
    raise AssertionError(f"the mark answers without a read of {path}")


def test_a_packet_rejudges_a_marked_def_to_split_lines():
    """ccn 1 at cov 0 scores 2: ok at a ceiling of 6, over a ceiling of 1."""
    row = ScoredRow("src", "src/app.py", "multi( a , b )", 30, 31, 1, 1, 1, 2, 2, 0,
                    0.0, "untested", 2.0, "ok", 0, 1, inline_body=1)

    assert packet.rejudged(row, 1, refuse).remedy == "split-lines"


def test_the_store_keeps_the_mark_on_scored_and_inventory_rows(tmp_path):
    marked = ScoredRow("src", "app.py", "f( a , b )", 1, 2, 1, 1, 1, 2, 2, 0,
                       0.0, "untested", 2.0, "split-lines", 0, 1, inline_body=1)
    plain = marked._replace(long_name="g( x )", start=3, end=4, inline_body=0)
    store = SnapshotStore(tmp_path / "state.sqlite")
    with closing(store._conn):
        scored = store.write_run(commit="c1", tool_versions={}, rows=[marked, plain])
        inventory = store.write_run(commit="c2", tool_versions={}, rows=[INLINE],
                                    kind="inventory")

        assert [r.inline_body for r in store.read_scored(scored)] == [1, 0]
        assert [r.inline_body for r in store.read_scored_file(scored, "app.py")] == [1, 0]
        assert [r.inline_body for r in store.read_rows(inventory)] == [1]


def test_a_store_written_before_the_column_reads_every_row_unmarked(tmp_path):
    path = tmp_path / "state.sqlite"
    store = SnapshotStore(path)
    with closing(store._conn):
        run = store.write_run(commit="c1", tool_versions={}, rows=[INLINE], kind="inventory")
        store._conn.execute("ALTER TABLE functions DROP COLUMN inline_body")
        store._conn.commit()
    reopened = SnapshotStore(path)
    with closing(reopened._conn):
        assert [r.inline_body for r in reopened.read_rows(run)] == [0]
        later = reopened.write_run(commit="c2", tool_versions={}, rows=[INLINE], kind="inventory")
        assert [r.inline_body for r in reopened.read_rows(later)] == [1]


def test_the_exports_keep_their_columns():
    """The mark is an input the run already applied to cov, flag and remedy, which
    the exports carry, so the TSV headers stay the ones a portable baseline reads."""
    scored = score_rows([INLINE], {}, lane_scopes={"src"})

    scored_lines = [line.split("\t") for line in "".join(scored_tsv_lines(scored)).splitlines()]
    inventory_lines = [line.split("\t") for line in "".join(tsv_lines([INLINE])).splitlines()]

    assert scored_lines[0][-2:] == inventory_lines[0][-2:] == ["cognitive", "occurrence"]
    assert [len(fields) for fields in scored_lines] == [17, 17]
    assert [len(fields) for fields in inventory_lines] == [13, 13]


def test_the_cache_keeps_the_mark(tmp_path):
    (tmp_path / "mod.py").write_text(MODULE, encoding="utf-8")
    cold, _, cache = analyze_files(tmp_path, ["mod.py"], cache={})
    save_cache(tmp_path / "cache.json", cache)

    warm, hits, _ = analyze_files(tmp_path, ["mod.py"], cache=load_cache(tmp_path / "cache.json"))

    assert (warm, hits) == (cold, 1)
    assert [r.inline_body for r in warm["mod.py"]] == [1, 1, 1, 1, 0]


def test_a_cache_written_before_the_mark_reads_cold(tmp_path):
    """cache=4 records have no inline_body, and reading them warm would score every
    marked def as though the reader had not marked it."""
    (tmp_path / "mod.py").write_text(MODULE, encoding="utf-8")
    _, _, old = analyze_files(tmp_path, ["mod.py"], cache={})
    old["fp"] = fingerprint().rsplit(";cache=", 1)[0] + ";cache=4"

    _, hits, _ = analyze_files(tmp_path, ["mod.py"], cache=old)

    assert hits == 0


def test_a_cached_mark_other_than_zero_or_one_reads_cold(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"fp": fingerprint(), "entries": {
        "bad": [["a.py", "f()", 1, 2, 1, 1, 1, 1, 0, 0, 0, 1, 2]]}}), encoding="utf-8")

    assert load_cache(path) == {}
