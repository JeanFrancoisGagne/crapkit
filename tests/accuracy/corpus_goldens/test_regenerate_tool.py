"""tools/accuracy/regenerate.py: canonical artifacts, the history bundle, and a re-recording.

- hand: the canonical forms on hand-written artifacts: forward-slash keys, the
  recording root cut out, the producers' clocks and host fixed.
- the committed history bundle is exactly what `regenerate.py history`
  builds: git names the same tip commit, so the bundle holds no hand edit.
- nightly: re-recording the small corpus under the pinned producers
  (coverage.py 7.16.1 on CPython 3.14, vitest with istanbul) gives the
  committed recorded/ artifacts, parsed JSON equal and JUnit text equal.
  The recording interpreter is CRAPKIT_ACCURACY_PY314, or the image's
  /opt/venv/3.14 (ACCURACY_VENVS moves it).
"""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from accuracy.kit import oracles, repos, runlog

TOOL = Path(__file__).resolve().parents[3] / "tools" / "accuracy" / "regenerate.py"


def _load():
    if "accuracy_regenerate_tool" not in sys.modules:
        spec = importlib.util.spec_from_file_location("accuracy_regenerate_tool", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["accuracy_regenerate_tool"]


regenerate = _load()
ROOTS = ("C:\\rec\\small\\", "C:/rec/small/")


def test_a_coveragepy_report_gets_forward_keys_and_the_fixed_clock():
    data = {"meta": {"version": "7.16.1", "timestamp": "2026-01-02T03:04:05.678"},
            "files": {"src\\py\\a.py": {"executed_lines": [1]}}, "totals": {"n": 1}}

    assert regenerate.coveragepy_canonical(data) == {
        "meta": {"version": "7.16.1", "timestamp": "2026-09-24T00:00:00"},
        "files": {"src/py/a.py": {"executed_lines": [1]}}, "totals": {"n": 1}}


def test_a_path_under_the_root_becomes_repo_relative_with_forward_slashes():
    data = {"C:\\rec\\small\\src\\web\\a.ts": {"path": "C:/rec/small/src/web/a.ts",
                                                "list": ["C:\\rec\\small\\other\\b.ts", 3],
                                                "message": "failed in C:/rec/small/x.ts"}}

    assert regenerate.cut_root(data, ROOTS) == {
        "src/web/a.ts": {"path": "src/web/a.ts", "list": ["other/b.ts", 3],
                         "message": "failed in x.ts"}}


def test_every_root_form_ends_with_each_separator_longest_first(tmp_path):
    forms = regenerate.root_forms(tmp_path)

    assert tmp_path.as_posix() + "/" in forms and str(tmp_path).rstrip("\\/") + "\\" in forms
    assert [len(form) for form in forms] == sorted((len(form) for form in forms), reverse=True)


def test_a_junit_file_gets_the_fixed_clock_and_host():
    text = ('<testsuite timestamp="2026-01-02T03:04:05" hostname="box" time="1.25">'
            '<testcase file="C:/rec/small/tests/t.py" time="0.5"/></testsuite>')

    assert regenerate.junit_canonical(text, ROOTS) == (
        '<testsuite timestamp="2026-09-24T00:00:00" hostname="recorder" time="0.000">'
        '<testcase file="tests/t.py" time="0.000"/></testsuite>')


def test_moved_files_are_the_new_the_gone_and_the_changed():
    before = {"a": "1", "b": "2", "c": "3"}
    after = {"a": "1", "b": "two", "d": "4"}

    assert regenerate.moved_files(before, after) == ["b", "c", "d"]


TINY = {"crapkit.toml": ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "tiny"\npaths = ["src"]\n'
                         'languages = ["python"]\ncoverage_optional = true\n'),
        "src/a.py": "def f(x):\n    if x:\n        return 1\n    return 0\n\n\ndef g(y):\n    return y\n"}


@pytest.mark.process
def test_the_full_golden_names_each_member_export_and_moves_only_on_change(tmp_path):
    """Two functions in the member give two rows in each export; a second
    rewrite from the same corpus moves nothing, and an edit moves both exports."""
    member = tmp_path / "corpus" / "tiny"
    for name, text in TINY.items():
        (member / name).parent.mkdir(parents=True, exist_ok=True)
        (member / name).write_bytes(text.encode("utf-8"))
    golden = tmp_path / "full.tsv"
    first = regenerate.rewrite_full(tmp_path / "corpus", golden, ["tiny"])
    rows = [line.split("\t")[:3] for line in golden.read_text(encoding="utf-8").splitlines()]
    again = regenerate.rewrite_full(tmp_path / "corpus", golden, ["tiny"])
    (member / "src" / "a.py").write_bytes(b"def f(x):\n    return x\n")
    edited = regenerate.rewrite_full(tmp_path / "corpus", golden, ["tiny"])

    assert first == edited == ["full/tiny/inventory.tsv", "full/tiny/scored.tsv"]
    assert rows == [["member", "export", "rows"], ["tiny", "inventory.tsv", "2"],
                    ["tiny", "scored.tsv", "2"]]
    assert again == []


def _tip(bundle: Path, where: Path) -> str:
    """The bundle's main, read from a directory outside any repository: a
    checkout mounted into a container can hold a .git file git cannot follow."""
    return repos.git(where, "bundle", "list-heads", str(bundle), "refs/heads/main").split()[0]


@pytest.mark.process
def test_the_committed_bundle_is_what_the_history_command_builds(tmp_path):
    built = tmp_path / "small.bundle"

    tip = regenerate.build_history(built)

    assert tip == _tip(built, tmp_path) == _tip(regenerate.BUNDLE, tmp_path)


# --- re-recording (nightly) -----------------------------------------------------------------

PY314_ENV = "CRAPKIT_ACCURACY_PY314"


def _python314() -> str:
    named = os.environ.get(PY314_ENV)
    venv = Path(os.environ.get("ACCURACY_VENVS", "/opt/venv")) / "3.14" / "bin" / "python"
    found = named or (str(venv) if venv.is_file() else None)
    if found is None:
        message = (f"no Python 3.14 recording interpreter: set {PY314_ENV} to one with the "
                   "nightly lock installed, or run in the accuracy image")
        runlog.note("infra", message=message)
        pytest.fail(message, pytrace=False)
    return found


def _read(path: Path):
    text = path.read_bytes().decode("utf-8")
    return json.loads(text) if path.suffix == ".json" else text


@pytest.mark.nightly
@pytest.mark.process
def test_re_recording_gives_the_committed_artifacts(tmp_path, oracle):
    oracle("vitest")
    names = regenerate.record(_python314(), oracles.node_modules("nightly"), tmp_path / "out")
    committed = regenerate.SMALL / "recorded"

    assert names == sorted(path.name for path in committed.iterdir())
    assert {name: _read(tmp_path / "out" / name) for name in names} == {
        name: _read(committed / name) for name in names}
