"""Per-test coverage contexts (coverage.py --show-contexts) and the watch
loop's pure core. The transport shells stay thin; the logic lives here."""
import json

from crapkit.coverage_py import parse_coveragepy_contexts
from crapkit.watch import changed_paths

REPORT = {
    "meta": {"branch_coverage": True, "show_contexts": True},
    "files": {"pylib/mod.py": {
        "functions": {},
        "contexts": {
            "2": ["tests/test_mod.py::test_over|run", "tests/test_mod.py::test_under|run"],
            "3": ["tests/test_mod.py::test_over|run"],
            "9": [""],
        },
    }},
}


def test_contexts_map_lines_to_cleaned_test_ids():
    ctx = parse_coveragepy_contexts(json.dumps(REPORT), path_prefix="")
    assert ctx["pylib/mod.py"][2] == ["tests/test_mod.py::test_over", "tests/test_mod.py::test_under"]
    assert ctx["pylib/mod.py"][3] == ["tests/test_mod.py::test_over"]
    assert 9 not in ctx["pylib/mod.py"], "the empty (module import) context is not a test"


def test_contexts_absent_returns_empty():
    bare = {"meta": {"branch_coverage": True}, "files": {"a.py": {"functions": {}}}}
    assert parse_coveragepy_contexts(json.dumps(bare), path_prefix="") == {}


def test_changed_paths_reports_new_modified_and_deleted():
    before = {"a.py": 1.0, "b.py": 2.0, "gone.py": 3.0}
    after = {"a.py": 1.0, "b.py": 5.0, "new.py": 1.0}
    assert changed_paths(before, after) == ["b.py", "gone.py", "new.py"]


def test_changed_paths_quiet_when_nothing_moved():
    snap = {"a.py": 1.0}
    assert changed_paths(snap, dict(snap)) == []
