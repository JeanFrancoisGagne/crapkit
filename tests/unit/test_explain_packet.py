"""`explain` as a start-editing packet: one assembly, two renderings.

Everything the text mode prints (the per-run scores, the ratchet mark, the dark
lines, `--history` commits and `--tests` attribution) is assembled once and then
either printed or handed to `_print_json`. Three things are pinned here:

- The text bytes. A JSON flag must not move one character of the human output.
- The commit bodies `--history` now carries: indented lines under a commit that
  has a body, and nothing at all under a commit that does not.
- The lookups that used to be redone once per matched function. With two matches
  the lane artifacts, the ratchet file, crapkit.toml and the run table are each
  read once, and the per-function reads stay one query apiece.

cmd_explain takes a namespace rather than running through the CLI, because the
handler is the seam that decides what the flag does.
"""
import argparse
import json
import subprocess
from pathlib import Path

import pytest

from crapkit.cli import reports
from crapkit.cli.reports import cmd_explain
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]
"""

PY_LANE = """
[[lane]]
name = "py"
command = "python --version"
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
"""

MODULE = '''def guarded(a, b):
    if a and b:
        return [x for x in range(a) if x % 2]
    return []


def guarded_twin(a, b):
    if a or b:
        return a
    return b
'''

NO_LANE = "none (no [[lane]] declared, so no artifact can say which lines are dark)"

NO_CONTEXT = ("  tests: no context data — run the py lane with dynamic_context = "
              "test_function and a --show-contexts JSON report\n")

# The four lines explain printed for each match before --json existed, captured
# off the unmodified handler. The commit is "a" * 40 because the run is written
# here, so these are real bytes and not a re-derivation of the format strings.
GOLDEN_A = (
    "pylib/mod.py  guarded( a , b )\n"
    "  run   1 @ aaaaaaaaaaa coverage  ccn   7  cov   50%  crap      8.8  measured\n"
    "  mark: no ratchet file\n"
    f"  uncovered lines: {NO_LANE}\n"
)
GOLDEN_B = (
    "pylib/mod.py  guarded_twin( a , b )\n"
    "  run   1 @ aaaaaaaaaaa coverage  ccn   3  cov  100%  crap      3.0  measured\n"
    "  mark: no ratchet file\n"
    f"  uncovered lines: {NO_LANE}\n"
)


def _run_git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8")
    return done.stdout


def _row(long_name: str, start: int, end: int, ccn: int, cov: float, crap: float) -> ScoredRow:
    return ScoredRow("py", "pylib/mod.py", long_name, start, end, ccn, ccn, ccn, 5, 1, 1,
                     cov, "measured", crap, "add-tests", 0)


def _make_repo(tmp_path: Path, cfg: str = CFG, *, commit: bool = True) -> Path:
    repo = tmp_path / "repo"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_text(MODULE, encoding="utf-8", newline="\n")
    (repo / "crapkit.toml").write_text(cfg, encoding="utf-8", newline="\n")
    _run_git(repo, "init", "-q", "-b", "main")
    if commit:
        _run_git(repo, "add", "-A")
        _run_git(repo, "commit", "-q", "-m", "seed the module")
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    store.write_run(commit="a" * 40, tool_versions={}, lanes={"py": {}},
                    rows=[_row("guarded( a , b )", 1, 4, 7, 0.5, 8.75),
                          _row("guarded_twin( a , b )", 7, 10, 3, 1.0, 3.0)])
    return repo


def _commit_in_span(repo: Path, subject: str, body: str | None = None) -> None:
    """A second commit that touches lines 1-4, so only `guarded` sees it."""
    text = (repo / "pylib" / "mod.py").read_text(encoding="utf-8")
    (repo / "pylib" / "mod.py").write_text(text.replace("if a and b:", "if a and b and a != b:"),
                                           encoding="utf-8", newline="\n")
    _run_git(repo, "add", "-A")
    message = ["-m", subject] + (["-m", body] if body else [])
    _run_git(repo, "commit", "-q", *message)


def _head(repo: Path) -> tuple[str, str]:
    sha, date = _run_git(repo, "log", "-1", "--format=%h %ad", "--date=short").split()
    return sha, date


def _seed_commit(repo: Path) -> tuple[str, str]:
    out = _run_git(repo, "log", "--format=%h %ad", "--date=short", "--reverse")
    return tuple(out.splitlines()[0].split())


def _args(repo: Path, **flags) -> argparse.Namespace:
    """explain's namespace. `--json` is not on the parser in this tree yet, so
    the default lives here; cmd_explain must read it as argparse will supply it."""
    fields = {"history": False, "tests": False, "json": False, **flags}
    return argparse.Namespace(repo=str(repo), path="pylib/mod.py", name="guarded", **fields)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path)


def _explain(repo: Path, capsys, **flags) -> str:
    assert cmd_explain(_args(repo, **flags)) == 0
    return capsys.readouterr().out


def _payload(repo: Path, capsys, **flags) -> dict:
    out = _explain(repo, capsys, json=True, **flags)
    assert out.count("\n") == 1, f"--json is one object on one line, got:\n{out}"
    return json.loads(out)


def _named(payload: dict, fragment: str) -> dict:
    (one,) = [f for f in payload["functions"] if fragment in f["long_name"]]
    return one


# --- the text bytes do not move ---------------------------------------------

def test_the_plain_text_output_is_byte_identical(repo, capsys):
    assert _explain(repo, capsys) == GOLDEN_A + GOLDEN_B


def test_a_commit_with_no_body_prints_the_one_line_it_always_printed(repo, capsys):
    """The bodiless case is the whole existing --history contract: one indented
    line per commit and nothing under it."""
    sha, date = _seed_commit(repo)
    seed = f"    {sha} {date} seed the module\n"

    assert _explain(repo, capsys, history=True) == GOLDEN_A + seed + GOLDEN_B + seed


def test_the_no_context_guidance_line_is_byte_identical(repo, capsys):
    assert _explain(repo, capsys, tests=True) == (GOLDEN_A + NO_CONTEXT + GOLDEN_B + NO_CONTEXT)


# --- --json carries every section the text prints ----------------------------

def test_json_is_one_object_at_schema_1_naming_what_was_asked(repo, capsys):
    payload = _payload(repo, capsys)

    assert payload["schema"] == 1
    assert payload["path"] == "pylib/mod.py"
    assert payload["name"] == "guarded"


def test_json_keys_are_sorted(repo, capsys):
    out = _explain(repo, capsys, json=True)

    assert out.index('"functions"') < out.index('"name"') < out.index('"schema"')


def test_json_carries_one_entry_per_match_in_the_order_text_prints_them(repo, capsys):
    payload = _payload(repo, capsys)

    assert [f["long_name"] for f in payload["functions"]] == \
        ["guarded( a , b )", "guarded_twin( a , b )"]


def test_json_carries_the_scored_rows_across_runs(repo, capsys):
    row = _named(_payload(repo, capsys), "guarded( ")["history"]

    assert [(h["run_id"], h["ccn"], h["cov"], h["crap"], h["flag"]) for h in row] == \
        [(1, 7, 0.5, 8.75, "measured")]


def test_json_carries_the_ratchet_mark_and_says_when_there_is_no_file(repo, capsys):
    entry = _named(_payload(repo, capsys), "guarded( ")

    assert entry["ratchet_mark"] is None
    assert entry["ratchet_mark_note"] == "no ratchet file"


def _write_ratchet(repo: Path, *rows: tuple[str, float]) -> None:
    lines = ["path\tlong_name\tcrap"] + [f"pylib/mod.py\t{name}\t{crap}" for name, crap in rows]
    (repo / "crapkit-ratchet.tsv").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8", newline="\n")


def test_json_carries_a_recorded_mark_as_the_number_it_is(repo, capsys):
    _write_ratchet(repo, ("guarded( a , b )", 12.5))

    entry = _named(_payload(repo, capsys), "guarded( ")

    assert entry["ratchet_mark"] == 12.5
    assert "ratchet_mark_note" not in entry, "a note only when there is no marks file"


def test_an_unmarked_function_in_a_marked_repo_is_null_with_no_note(repo, capsys):
    _write_ratchet(repo, ("guarded( a , b )", 12.5))

    entry = _named(_payload(repo, capsys), "guarded_twin")

    assert entry["ratchet_mark"] is None
    assert "ratchet_mark_note" not in entry


def test_the_mark_text_reads_the_same_as_it_always_did(repo, capsys):
    _write_ratchet(repo, ("guarded( a , b )", 12.5))

    out = _explain(repo, capsys)

    assert "  mark: 12.5000 (committed high-water mark)\n" in out
    assert "  mark: none (below target or never marked)\n" in out


def test_json_carries_the_dark_lines_as_null_plus_the_reason(repo, capsys):
    entry = _named(_payload(repo, capsys), "guarded( ")

    assert entry["uncovered_lines"] is None
    assert entry["uncovered_lines_note"] == \
        "no [[lane]] declared, so no artifact can say which lines are dark"


def _answered(monkeypatch, dark: set) -> None:
    """An artifact that answered, which no hand-built fixture reaches: a lane
    with no stamp is stale by definition, and a stale lane only ever answers null."""
    from crapkit.uncovered import MissingLines

    monkeypatch.setattr(reports, "load_uncovered",
                        lambda root, cfg: MissingLines({"pylib/mod.py": dark}, ""))


def test_answered_dark_lines_print_joined_and_a_covered_span_prints_none(repo, capsys,
                                                                        monkeypatch):
    _answered(monkeypatch, {2, 3, 40})

    out = _explain(repo, capsys)

    assert "  uncovered lines: 2, 3\n" in out, "inside guarded's span 1-4"
    assert "  uncovered lines: none\n" in out, "guarded_twin's span 7-10 is clean"


def test_answered_dark_lines_reach_json_as_a_list_with_no_note(repo, capsys, monkeypatch):
    _answered(monkeypatch, {2, 3, 40})

    payload = _payload(repo, capsys)

    assert _named(payload, "guarded( ")["uncovered_lines"] == [2, 3]
    assert _named(payload, "guarded_twin")["uncovered_lines"] == []
    assert "uncovered_lines_note" not in _named(payload, "guarded_twin")


def test_json_omits_the_sections_their_flags_did_not_ask_for(repo, capsys):
    entry = _named(_payload(repo, capsys), "guarded( ")

    assert "commits" not in entry and "tests" not in entry


# --- --history gains commit bodies -------------------------------------------

def test_history_json_carries_sha_date_subject_and_body(repo, capsys):
    _commit_in_span(repo, "widen the guard", "First body line.\nSecond body line.")
    sha, date = _head(repo)

    commits = _named(_payload(repo, capsys, history=True), "guarded( ")["commits"]

    assert commits[0] == {"sha": sha, "date": date, "subject": "widen the guard",
                          "body": "First body line.\nSecond body line."}


def test_history_json_gives_a_bodiless_commit_an_empty_body(repo, capsys):
    commits = _named(_payload(repo, capsys, history=True), "guarded( ")["commits"]

    assert [c["body"] for c in commits] == [""]


def test_history_text_indents_the_body_under_its_commit(repo, capsys):
    _commit_in_span(repo, "widen the guard", "First body line.\nSecond body line.")
    sha, date = _head(repo)
    seed_sha, seed_date = _seed_commit(repo)

    out = _explain(repo, capsys, history=True)

    assert out == (GOLDEN_A
                   + f"    {sha} {date} widen the guard\n"
                   + "      First body line.\n"
                   + "      Second body line.\n"
                   + f"    {seed_sha} {seed_date} seed the module\n"
                   + GOLDEN_B
                   + f"    {seed_sha} {seed_date} seed the module\n")


def test_a_blank_line_inside_a_body_survives_into_json(repo, capsys):
    """crapkit's own messages put a blank line before their trailers, so the
    paragraph break is body text and not a record boundary."""
    _commit_in_span(repo, "widen the guard", "Why it changed.\n\nCo-Authored-By: t <t@t>")

    commits = _named(_payload(repo, capsys, history=True), "guarded( ")["commits"]

    assert commits[0]["body"] == "Why it changed.\n\nCo-Authored-By: t <t@t>"


def test_a_blank_body_line_prints_blank_instead_of_bare_indentation(repo, capsys):
    _commit_in_span(repo, "widen the guard", "Why it changed.\n\nCo-Authored-By: t <t@t>")
    sha, date = _head(repo)

    out = _explain(repo, capsys, history=True)

    assert (f"    {sha} {date} widen the guard\n"
            "      Why it changed.\n"
            "\n"
            "      Co-Authored-By: t <t@t>\n") in out


def test_a_span_git_refuses_to_log_reports_no_commits_rather_than_failing(tmp_path, capsys):
    """`git log -L` on an unborn HEAD is a GitError. explain answers with an
    empty list: a history git cannot produce is not a reason to fail the call."""
    repo = _make_repo(tmp_path, commit=False)

    entry = _named(_payload(repo, capsys, history=True), "guarded( ")

    assert entry["commits"] == []
    assert "commits_note" not in entry, "the span exists, git just had nothing to say"


def test_a_span_git_refuses_to_log_prints_no_commit_lines(tmp_path, capsys):
    repo = _make_repo(tmp_path, commit=False)

    assert _explain(repo, capsys, history=True) == GOLDEN_A + GOLDEN_B


def test_a_function_the_latest_run_dropped_reports_null_commits(tmp_path, capsys):
    """No span, no line range to ask git about, and the text says so on one line."""
    repo = _make_repo(tmp_path)
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    store.write_run(commit="b" * 40, tool_versions={}, lanes={"py": {}},
                    rows=[_row("guarded_twin( a , b )", 7, 10, 3, 1.0, 3.0)])

    entry = _named(_payload(repo, capsys, history=True), "guarded( ")

    assert entry["commits"] is None
    assert entry["commits_note"] == "function not in the latest run"


def test_a_function_the_latest_run_dropped_keeps_its_one_text_line(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    store.write_run(commit="b" * 40, tool_versions={}, lanes={"py": {}},
                    rows=[_row("guarded_twin( a , b )", 7, 10, 3, 1.0, 3.0)])

    out = _explain(repo, capsys, history=True)

    assert "  commits: function not in the latest run\n" in out


# --- --tests attribution reaches json ----------------------------------------

def _with_contexts(tmp_path: Path, contexts: dict) -> Path:
    repo = _make_repo(tmp_path, CFG + PY_LANE)
    report = {"meta": {"branch_coverage": True, "show_contexts": True},
              "files": {"pylib/mod.py": {"contexts": contexts}}}
    (repo / "coverage-py.json").write_text(json.dumps(report), encoding="utf-8", newline="\n")
    return repo


def test_json_carries_the_covering_tests_sorted(tmp_path, capsys):
    repo = _with_contexts(tmp_path, {"2": ["t/b.py::test_beta|run", ""],
                                     "3": ["t/a.py::test_alpha|run"],
                                     "40": ["t/z.py::test_far|run"]})

    entry = _named(_payload(repo, capsys, tests=True), "guarded( ")

    assert entry["tests"] == ["t/a.py::test_alpha", "t/b.py::test_beta"]
    assert "tests_note" not in entry


def test_json_carries_the_no_context_note_instead_of_an_empty_list(repo, capsys):
    entry = _named(_payload(repo, capsys, tests=True), "guarded( ")

    assert entry["tests"] is None
    assert entry["tests_note"] == NO_CONTEXT.strip()[len("tests: "):]


# --- one lookup per command, not one per matched function --------------------

def _counted(monkeypatch, target: str, module=reports) -> list:
    calls: list = []
    real = getattr(module, target)

    def spy(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(module, target, spy)
    return calls


def test_the_dark_line_artifacts_load_once_for_every_match(repo, capsys, monkeypatch):
    calls = _counted(monkeypatch, "load_uncovered")

    _explain(repo, capsys)

    assert len(calls) == 1, "two matches reparsed every lane artifact twice"


def test_the_ratchet_file_is_read_once_for_every_match(repo, capsys, monkeypatch):
    calls = _counted(monkeypatch, "_ratchet_entries")

    _explain(repo, capsys)

    assert len(calls) == 1, "two matches read and parsed the marks file twice"


def test_the_repo_config_is_loaded_once_even_with_tests_attribution(repo, capsys, monkeypatch):
    calls = _counted(monkeypatch, "_load_repo_config")

    _explain(repo, capsys, tests=True)

    assert len(calls) == 1, "the attribution pass reloaded crapkit.toml per match"


def test_the_context_artifacts_are_parsed_once_for_every_match(tmp_path, capsys, monkeypatch):
    import crapkit.coverage_py as coverage_py
    repo = _with_contexts(tmp_path, {"2": ["t/a.py::test_alpha|run"]})
    calls = _counted(monkeypatch, "parse_coveragepy_contexts", coverage_py)

    _explain(repo, capsys, tests=True)

    assert len(calls) == 1, "one coveragepy lane, one parse, however many matches"


def test_the_run_table_is_scanned_once_for_every_match(repo, capsys, monkeypatch):
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    monkeypatch.setattr(reports, "_open_store", lambda root, *a, **kw: store)

    _explain(repo, capsys)

    scans = [s for s in seen if "FROM runs ORDER BY id" in s]
    assert len(scans) == 1, f"one run-table scan per command, not per match: {scans}"


def test_the_score_history_is_one_read_per_match_not_one_per_run(repo, capsys, monkeypatch):
    """Two runs and two matches still make two reads: the trajectory across runs
    comes back grouped, and a loop over runs here would make it four."""
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    store.write_run(commit="b" * 40, tool_versions={}, lanes={"py": {}},
                    rows=[_row("guarded( a , b )", 1, 4, 7, 0.5, 8.75),
                          _row("guarded_twin( a , b )", 7, 10, 3, 1.0, 3.0)])
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    monkeypatch.setattr(reports, "_open_store", lambda root, *a, **kw: store)

    _explain(repo, capsys)

    reads = [s for s in seen if "SELECT f.run_id" in s]
    assert len(reads) == 2, f"one grouped read per match, whatever the run count: {reads}"


def test_the_span_lookup_stays_one_targeted_query_per_match(repo, capsys, monkeypatch):
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    monkeypatch.setattr(reports, "_open_store", lambda root, *a, **kw: store)

    _explain(repo, capsys)

    spans = [s for s in seen if "SELECT f.start, f.end" in s]
    assert len(spans) == 2, "each match still asks for its own span, off the index"
