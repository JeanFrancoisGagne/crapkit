"""The 0.8.1 release review read the docs against the code and found sentences the
code does not do. Each test here runs the code path a sentence describes, or reads
it, and pins the sentence that now says what it does.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from cli_inproc_repo import (add_knotty, commit_all, istanbul, repo,  # noqa: F401
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
