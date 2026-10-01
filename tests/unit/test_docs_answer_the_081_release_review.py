"""The 0.8.1 release review read the docs against the code and found sentences the
code does not do. Each test here runs the code path a sentence describes, or reads
it, and pins the sentence that now says what it does.
"""
import ast
import io
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from cli_inproc_repo import (add_knotty, commit_all, git, istanbul, repo,  # noqa: F401
                             seed_artifacts, template_repo)

from crapkit.cli import main
from crapkit.store import SnapshotStore

ROOT = Path(__file__).resolve().parents[2]
GUIDE = "docs/upgrading.md"
_HEADING = re.compile(r"^(#{1,6}) ", re.M)


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(rel: str, heading: str) -> str:
    """The body under `heading`, its subsections included, joined as prose."""
    text = _doc(rel)
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    ends = [m.start() for m in _HEADING.finditer(text[start:]) if len(m[1]) <= level]
    return " ".join(text[start:start + ends[0] if ends else None].split())


def _changelog_steps() -> str:
    return _section("CHANGELOG.md", "### Upgrading from 0.8.0")


# --- verify --override with no alert_command (src[2]) -------------------------------

LANE_RAN = "lane_ran.py"


def test_an_override_with_no_alert_command_exits_3_before_any_lane_and_the_notes_list_it(
        repo, capsys):  # noqa: F811
    """0.8.0 refused the override only once the verdict wanted it, after every lane
    ran: a passing tree exited 0 and a ratchet regression 7. 0.8.1 refuses it at
    load. The guide's table of the rest of the exit moves had no row for it."""
    toml = repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    .replace('alert_command = "python append_alert.py"\n', "")
                    .replace('command = "python -c pass"', f'command = "python {LANE_RAN}"'),
                    encoding="utf-8", newline="\n")
    (repo / LANE_RAN).write_text("open('ran.txt', 'a').close()\n", encoding="utf-8")
    commit_all(repo, "no alert command")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    runs = len(SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs())

    code = main(["verify", "--override", "hotfix", "--repo", str(repo)])
    out = capsys.readouterr()

    assert (code, out.out) == (3, "")
    assert out.err.startswith("crapkit: no alert_command configured"), out.err
    assert not (repo / "ran.txt").exists(), "a lane ran before the refusal"
    assert len(SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()) == runs

    row = next(line for line in _doc(GUIDE).splitlines()
               if line.startswith("| `--override REASON` where `crapkit.toml` sets no `alert_command` |"))
    assert ("| `verify` | the verdict's: 0 on a passing tree, 7 on a ratchet regression, and 3 after "
            "every lane ran on a gate breach the override would grant | 3 before any lane runs, "
            "`no alert_command configured` |") in row
    assert row in _doc(GUIDE).split("## Other exit codes that move in 0.8.1", 1)[1].split("\n## ", 1)[0]
    moved = ("`verify --override` in a repo whose `crapkit.toml` sets no `alert_command` exits 3 "
             "before any lane runs")
    assert moved in _changelog_steps()
    assert moved in _section("docs/releases/0.8.1.md", "## Upgrading from 0.8.0")


# --- which JSON carries `unmeasured` (docs[3]) --------------------------------------

def _cc_only(repo) -> None:
    """`src` asks for no coverage and no lane reads it, and a function there sits
    over the ceiling, so every view ranks a row no measurement stands behind."""
    toml = repo / "crapkit.toml"
    text = toml.read_text(encoding="utf-8").replace(
        'languages = ["typescript"]\n\n[[scope]]\nname = "web"',
        'languages = ["typescript"]\ncoverage_optional = true\n\n[[scope]]\nname = "web"', 1)
    text = re.sub(r'\[\[lane\]\]\nname = "unit"\n(?:.*\n)*?scopes = \["src"\]\n', "", text)
    toml.write_text(text, encoding="utf-8", newline="\n")
    add_knotty(repo)
    commit_all(repo, "src is cc-only")
    seed_artifacts(repo, unit=False)


def _json(argv, repo, capsys):
    assert main([*argv, "--repo", str(repo)]) in (0, 6), argv
    return json.loads(capsys.readouterr().out)


def test_worklist_json_rows_carry_no_unmeasured_key_and_the_guide_says_which_do(repo, capsys):  # noqa: F811
    """The guide's worklist bullet said "The JSON keeps `cov` and adds `unmeasured:
    true`". A script that read `row["unmeasured"]` off `worklist --json` got a
    KeyError: `_entry_json` never writes it. brief, next-item and rescore do."""
    _cc_only(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()

    (row,) = [r for r in _json(["worklist", "--json"], repo, capsys)["active"] if r["flag"] == "cc-only"]
    item = _json(["next-item"], repo, capsys)["item"]
    brief = _json(["brief", "src/app.ts", "knotty", "--json"], repo, capsys)
    gate = _json(["rescore", "src/app.ts", "--gate", "--json"], repo, capsys)["functions"]

    assert "unmeasured" not in row and row["cov"] == 0.0
    assert (item["flag"], item["unmeasured"]) == ("cc-only", True)
    assert brief["unmeasured"] is True
    assert {f["unmeasured"] for f in gate} == {True}

    values = _section(GUIDE, "## Values that move without an exit code")
    assert "The JSON keeps `cov` and adds `unmeasured: true`" not in values
    assert ("`worklist --json` keeps such a row's `cov` at `0.0` and adds no key: its `flag` "
            "reads `no-lane` or `cc-only`. `brief --json`, `next-item` and `rescore --json` keep "
            "`cov` and add `unmeasured: true`.") in values
    assert ("carries `unmeasured: true` in `brief`, `next-item` and `rescore`"
            in _section("CHANGELOG.md", "## 0.8.1 — unreleased"))
    detail = " ".join(_doc("docs/releases/0.8.1.md").split())
    assert ("A row no coverage measured carries `unmeasured: true` in `brief`, `next-item`, "
            "`rescore` and their MCP tools") in detail
    assert "A `worklist --json` row adds no key; its `flag` says the same." in detail


# --- a hand-typed mark with more than four decimals (docs[4]) -----------------------

# ccn 5 that no test reaches: CRAP 5^2 * 1^3 + 5 = 30.0, the CRAP the guide's row names.
FIVE = """
export function five(n: number): number {
  if (n > 1) { return 1; }
  if (n > 2) { return 2; }
  if (n > 3) { return 3; }
  if (n > 4) { return 4; }
  return 0;
}
"""


def test_verify_reads_a_hand_typed_mark_at_four_decimals_as_the_guide_row_says(repo, capsys):  # noqa: F811
    """The row said 0.8.1 exits 7 "for a mark of 30.00004 over a CRAP between
    30.0000 and 30.00004". verify reads that mark as 30.0 and compares the CRAP
    rounded to four places, so no CRAP there fails: 0.8.1 only passes what 0.8.0
    failed, never the other way."""
    from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(FIVE)
    commit_all(repo, "five")
    start = (repo / "src" / "app.ts").read_text(encoding="utf-8").splitlines().index(
        "export function five(n: number): number {") + 1
    seed_artifacts(repo)
    istanbul(repo, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 2), "plain": (13, 18, 1), "five": (start, start + 6, 0)})
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    marks = dump_ratchet([RatchetEntry("src/app.ts", "five ( n )", 30.0)], stamp=metric_version())

    def verify(typed: str) -> tuple[int, str]:
        (repo / "crapkit-ratchet.tsv").write_text(marks.replace("\t30.0000\n", f"\t{typed}\n"),
                                                   encoding="utf-8", newline="\n")
        code = main(["verify", "--reuse-artifacts", "--repo", str(repo)])
        return code, capsys.readouterr().out

    code, out = verify("29.9999")
    assert code == 7 and "RATCHET  src/app.ts  five ( n ): 29.9999 -> 30.0" in out, out
    assert verify("29.99996")[0] == 0, "0.8.0 compared 30.0 > 29.99996 and exited 7"
    assert verify("30.00004")[0] == 0

    row = _table_row(_section(GUIDE, "## Other exit codes that move in 0.8.1"),
                     "a mark typed by hand with more than four decimals")
    assert "7 for a mark of 30.00004" not in row
    assert ("| judged against the typed value: 7 for a mark of 29.99996 over a CRAP of 30.0 | "
            "judged against the four-decimal value every rewrite of the file already gave it: "
            "0 there. A mark that rounds down, such as 30.00004 over a CRAP of 30.0, exits 0 "
            "under both |") in row


def _table_row(prose: str, first_cell: str) -> str:
    """The one five-column row, in prose `_section` joined, whose first cell is `first_cell`."""
    (row,) = re.findall(rf"\| {re.escape(first_cell)} \|(?:[^|]*\|){{4}}", prose)
    return row


# --- the marks file the hooks and worklist read (docs[5]) ---------------------------

MARKS = "crapkit-ratchet.tsv"


def _untracked_mark(repo, path: str, long_name: str, crap: float) -> None:
    """A mark in the working tree's marks file that no commit and no index holds."""
    from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

    (repo / MARKS).write_text(dump_ratchet([RatchetEntry(path, long_name, crap)],
                                           stamp=metric_version()), encoding="utf-8", newline="\n")
    assert git(repo, "status", "--porcelain", "--", MARKS) == f"?? {MARKS}\n"


def _hook(repo, capsys) -> tuple[int, str]:
    code = main(["hook-precommit", "--repo", str(repo)])
    return code, capsys.readouterr().err


def _prose(rel: str) -> str:
    return " ".join(_doc(rel).split())


def test_hook_precommit_pardons_a_staged_function_on_a_mark_no_commit_holds(repo, capsys):  # noqa: F811
    """README said the hooks pardon "a function the committed ratchet already
    carries a mark for". The gate loads the marks file from the working tree, so
    a mark never staged and never committed lets a staged breach through at 0."""
    add_knotty(repo)
    git(repo, "add", "src/app.ts")
    assert _hook(repo, capsys)[0] == 6
    _untracked_mark(repo, "src/app.ts", "knotty ( n )", 72.0)

    code, err = _hook(repo, capsys)

    assert code == 0
    assert "1 staged function(s) carry a ratchet mark and were not gated" in err, err
    readme = _prose("README.md")
    assert "the committed ratchet already carries a mark for" not in readme
    assert ("Both hooks pardon a function that the marks file in the working tree carries a mark "
            "for, whether or not that mark is staged or committed") in readme
    assert ("In CI the file on disk is the committed one, so `verify` there fails a function "
            "whose mark never reached a commit") in readme
    assert ("pardoned: any function the working-tree marks file marks, counted in one stderr line"
            in _doc("docs/handbook.html"))


def test_hook_precommit_with_nothing_staged_pardons_on_the_working_tree_marks(repo, capsys):  # noqa: F811
    """Outside a commit with nothing staged, the form `pre-commit run --all-files`
    runs, the hook judges every tracked file against the same file on disk. Three
    pages said "the committed ratchet"; in CI that is the same file, locally it is
    not."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    assert _hook(repo, capsys)[0] == 6
    _untracked_mark(repo, "src/app.ts", "knotty ( n )", 72.0)

    code, err = _hook(repo, capsys)

    assert code == 0, err
    assert "carry a ratchet mark and were not gated" in err, err
    assert ("a function over its ceiling fails unless the marks file in the working tree marks "
            "it, which in CI is the committed file") in _prose("README.md")
    assert ("exits 6 on each function over its ceiling that the marks file in the working tree "
            "does not mark") in _section(GUIDE, "## The commit gate in 0.8.1")
    assert ("with the functions the marks file in the working tree marks exempt as before"
            in _prose("docs/releases/0.8.1.md"))
    for page in ("README.md", GUIDE, "docs/releases/0.8.1.md", "docs/handbook.html"):
        assert "the committed ratchet marks" not in _prose(page), page
        assert "the committed ratchet does not mark" not in _prose(page), page


def test_claude_hook_skips_a_function_on_a_mark_no_commit_holds(tmp_path, monkeypatch, capsys):
    """The other of "both hooks": claude-hook reads the same file on disk."""
    from claude_hook_repo import BREACH, edit_event, hook, measured, write

    hooked = measured(tmp_path)
    edited = write(hooked, "calc/grade.py", BREACH)
    assert hook(monkeypatch, capsys, edit_event(edited))[0] == 2
    _untracked_mark(hooked, "calc/grade.py", "sprawl( n )", 72.0)

    assert hook(monkeypatch, capsys, edit_event(edited)) == (0, [])


def test_worklist_reads_ratchet_mark_from_the_working_tree(repo, capsys):  # noqa: F811
    """README, the agent JSON page and the handbook called `ratchet_mark` the
    committed mark. worklist reads the marks file on disk, so a mark no commit
    holds shows up there."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    _untracked_mark(repo, "src/app.ts", "knotty ( n )", 72.0)

    rows = _json(["worklist", "--json"], repo, capsys)["active"]

    assert [r["ratchet_mark"] for r in rows if r["function"] == "knotty ( n )"] == [72.0]
    assert ("its `ratchet_mark` when the marks file in the working tree signs for it"
            in _prose("README.md"))
    assert ("`ratchet_mark`: the value of its mark in the marks file in the working tree, or "
            "`null`" in _prose("docs/agent-json.md"))
    assert ("<code>ratchet_mark</code>, the function's mark in the working-tree marks file or "
            "<code>null</code>" in _prose("docs/handbook.html"))
    for page in ("README.md", "docs/agent-json.md", "docs/handbook.html"):
        assert "committed mark" not in _prose(page).replace("committed marks", ""), page


# --- what adding a language asks of the hooks (docs[6]) -----------------------------

def test_adding_a_language_edits_no_hook_file_and_the_contributor_guide_says_so():
    """CONTRIBUTING said to "regenerate plugin/hooks/hooks.json from
    languages.LANGUAGE_EXTENSIONS (a test rebuilds it and diffs)". hooks.json is
    one Edit|Write handler with no extension in it, so there is nothing to
    rebuild, and claude-hook screens each edit by the map itself."""
    from crapkit.cli.claude_hook import _suffixes
    from crapkit.languages import LANGUAGE_EXTENSIONS

    raw = _doc("plugin/hooks/hooks.json")
    hooks = json.loads(raw)["hooks"]
    handlers = [(group["matcher"], handler["command"])
                for groups in hooks.values() for group in groups for handler in group["hooks"]]
    suffixes = {e.lower() for exts in LANGUAGE_EXTENSIONS.values() for e in exts}

    assert handlers == [("Edit|Write", "crapkit claude-hook --protocol 1")]
    assert [s for s in suffixes if s in raw.lower()] == []
    assert _suffixes() == suffixes

    guide = _section("CONTRIBUTING.md", "## Adding a language")
    polyglot = _doc("tests/unit/test_polyglot_constants.py")
    assert "a test rebuilds it" not in guide and "regenerate `plugin/hooks/hooks.json`" not in guide
    assert ("Nothing in `plugin/hooks/hooks.json` changes: it registers one `Edit|Write` handler "
            "and lists no extension, and `claude-hook` screens each edit by "
            "`languages.LANGUAGE_EXTENSIONS`") in guide
    assert ("give it a `cc-only` row in the handbook's language table "
            "(`<table id=\"languages\">`) listing its suffixes") in guide
    assert "add its display name to `DISPLAY` in `tests/unit/test_polyglot_constants.py`" in guide
    assert "def test_the_handbook_table_carries_a_row_for_every_supported_language" in polyglot
    assert "def test_every_supported_language_has_a_display_name" in polyglot


# --- the version-gap line claude-hook prints (docs[7]) ------------------------------

def test_claude_hook_names_a_flag_it_does_not_know_and_the_commands_page_quotes_it(
        monkeypatch, capsys):
    """commands.md said "The advisory is the only thing it ever says". A flag a
    newer plugin passes gets one stderr line at exit 0, and the edit goes
    unjudged."""
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    code = main(["claude-hook", "--protocol", "1", "--budget", "5"])
    printed = capsys.readouterr()

    assert (code, printed.out) == (0, "")
    (line,) = printed.err.splitlines()
    section = _section("docs/commands.md", "## claude-hook")
    assert "The advisory is the only thing it ever says" not in section
    assert line in section, line
    assert "on stderr at exit 0" in section


# --- the rerun reasons --reuse-unchanged gives (tests[2]) ---------------------------

MAKE_COV = (
    "import json, os, pathlib, sys\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "fn = {'name': 'one', 'decl': {'start': {'line': 1}},\n"
    "      'loc': {'start': {'line': 1}, 'end': {'line': 3}}}\n"
    "report = {app: {'fnMap': {'0': fn}, 'f': {'0': 1}, 's': {'0': 1},\n"
    "                'statementMap': {'0': {'start': {'line': 2}}}, 'branchMap': {}, 'b': {}}}\n"
    "pathlib.Path('coverage').mkdir(exist_ok=True)\n"
    "pathlib.Path('coverage', sys.argv[1] + '.json').write_text(json.dumps(report))\n"
)


def _two_lanes() -> str:
    """One lane that lists its inputs and one that reads the whole tree."""
    python = sys.executable.replace("\\", "/")
    lane = ('[[lane]]\nname = "{0}"\ncommand = \'"{1}" make_cov.py {0}\'\n'
            'artifact = "coverage/{0}.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    return ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
            'languages = ["typescript"]\n\n' + lane.format("inputs", python)
            + 'inputs = ["src", "make_cov.py"]\n\n' + lane.format("tree", python))


def test_a_stamp_commit_no_longer_behind_head_is_no_rerun_reason(tmp_path):
    """lanes.md listed "an artifact built at a commit that is no longer behind
    HEAD" among the reasons --reuse-unchanged reruns a lane. After a reset past
    the measured commit, a lane with inputs compares trees and reuses, and a lane
    without them reruns on the HEAD it needs; `_not_behind` speaks only for the
    legacy stamp `_commit_drift` reads."""
    from crapkit.config import load_config_text
    from crapkit.lanes import lane_reuse_verdict, run_lane, write_stamps

    root = tmp_path / "mini"
    for rel, text in {"src/app.ts": "export function one(): number {\n  return 1;\n}\n",
                      "docs/notes.md": "first\n", "make_cov.py": MAKE_COV,
                      "crapkit.toml": _two_lanes(), ".gitignore": ".crapkit/\ncoverage/\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    git(root, "init", "-q")
    commit_all(root, "first")
    first = git(root, "rev-parse", "HEAD").strip()
    (root / "docs" / "notes.md").write_text("second\n", encoding="utf-8")
    commit_all(root, "second")
    measured = git(root, "rev-parse", "HEAD").strip()
    lanes = load_config_text((root / "crapkit.toml").read_text(encoding="utf-8")).lanes
    write_stamps(root, {lane.artifact: run_lane(root, lane).stamp for lane in lanes})
    git(root, "reset", "--hard", "-q", first)

    inputs, tree = (lane_reuse_verdict(root, lane).reason for lane in lanes)

    assert inputs == ""
    assert tree == f"HEAD is {first[:11]} and its artifact was built at {measured[:11]}"
    tree = ast.parse(_doc("src/crapkit/lane_freshness.py"))
    callers = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
               for call in ast.walk(fn)
               if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "_not_behind"}
    assert callers == {"_commit_drift"}

    reruns = (_prose("docs/lanes.md").split("A rerun names the first condition that failed:", 1)[1]
              .split("`crapkit.toml` is compared with CRLF", 1)[0])
    assert "no longer behind HEAD" not in reruns
    assert "`HEAD is X and its artifact was built at Y`" in reruns

