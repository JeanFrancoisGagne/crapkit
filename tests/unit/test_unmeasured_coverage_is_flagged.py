"""A coverage number nobody measured is flagged `unmeasured` where an agent reads it.

A row whose scope no lane covers (`no-lane`) or whose scope asks for no coverage
(`cc-only`) scores at cov 0.0, and `brief` and `next-item` multiplied that
stand-in into `est_uncovered_paths`: every decision path untested, for code no
artifact could speak about. `rescore` and the MCP `check_gate` tool gave a
function added or renamed since the run cov 0% and `untested`, the same words a
function a test suite never reached gets.

Each of those rows now carries `unmeasured: true`, and the text says `not
measured`. `cov`, `crap`, `flag`, `remedy` and `est_uncovered_paths` keep the
values and meanings this schema has always given them; a function the run judged
`untested` was measured at 0.0 and reads `unmeasured: false`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, repo, seed_artifacts, template_repo  # noqa: F401
from hand_scored_repo import run

from crapkit import mcp_server

KNOT = ("def knot(a, b, c, d, e, f, g):\n"
        + "".join(f"    if {v}:\n        return {i}\n" for i, v in enumerate("abcdefg"))
        + "    return 0\n")
SRC = '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
LIB = '[[scope]]\nname = "lib"\npaths = ["lib"]\nlanguages = ["python"]\n'


def lane(name: str, scope: str) -> str:
    return (f'[[lane]]\nname = "{name}"\ncommand = "python -c pass"\nartifact = "cov.json"\n'
            f'parser = "istanbul"\nscopes = ["{scope}"]\n')


# what an artifact says about src/a.py's knot: nothing, one of 15 statements, or no lane at all
CONFIGS = {
    "cc-only": (SRC + "coverage_optional = true\n", {}),
    "no-lane": (SRC + "\n" + LIB + "\n" + lane("lib", "lib"), {"lib/b.py": ("cold", 1, 4)}),
    "untested": (SRC + "\n" + lane("py", "src"), {"src/other.py": ("cold", 1, 4)}),
    "measured": (SRC + "\n" + lane("py", "src"), {"src/a.py": ("knot", 1, 16)}),
}


def istanbul(root: Path, functions: dict) -> None:
    """cov.json keyed by absolute path, one function per file, its first
    statement run and the rest not."""
    data = {}
    for rel, (name, start, end) in functions.items():
        key = str(root / rel)
        lines = range(start + 1, end + 1)
        data[key] = {"path": key, "branchMap": {}, "b": {}, "f": {"0": 1},
                     "fnMap": {"0": {"name": name, "decl": {"start": {"line": start}},
                                     "loc": {"start": {"line": start}, "end": {"line": end}}}},
                     "statementMap": {str(i): {"start": {"line": n}, "end": {"line": n}}
                                      for i, n in enumerate(lines)},
                     "s": {str(i): int(i == 0) for i, _ in enumerate(lines)}}
    (root / "cov.json").write_text(json.dumps(data), encoding="utf-8")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                    *args], cwd=root, check=True, capture_output=True)


@pytest.fixture(params=sorted(CONFIGS))
def measured_as(request, tmp_path, capsys) -> tuple[str, Path]:
    """A scored run in which src/a.py's knot (ccn 8) is `request.param`."""
    scopes, functions = CONFIGS[request.param]
    root = tmp_path / request.param
    for rel, text in {"src/a.py": KNOT, "src/other.py": "def cold(n):\n    if n:\n        return 1\n    return 0\n",
                      "lib/b.py": "def cold(n):\n    if n:\n        return 1\n    return 0\n",
                      ".gitignore": ".crapkit/\ncov.json\n",
                      "crapkit.toml": "[crapkit]\ntarget = 6\n\n" + scopes}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    istanbul(root, functions)
    code, out, err = run(root, capsys, "coverage", "--reuse-artifacts")
    assert code == 0, out + err
    return request.param, root


def test_brief_flags_a_cov_nobody_measured_and_keeps_its_numbers(measured_as, capsys):
    flag, root = measured_as

    code, out, err = run(root, capsys, "brief", "src/a.py", "knot", "--json")

    packet = json.loads(out)
    assert code == 0, err
    assert packet["scored"]["flag"] == flag
    assert packet["unmeasured"] is (flag in ("no-lane", "cc-only"))
    if packet["unmeasured"]:
        assert (packet["scored"]["cov"], packet["est_uncovered_paths"]) == (0.0, 8), \
            "0.8.1 adds the flag; the stand-in numbers change only with schema 2"


def test_brief_text_says_not_measured_where_it_printed_0_percent(measured_as, capsys):
    flag, root = measured_as

    code, out, _ = run(root, capsys, "brief", "src/a.py", "knot")

    second = out.splitlines()[1]
    assert code == 0
    if flag in ("no-lane", "cc-only"):
        assert "cov not measured" in second and "0%" not in second
    else:
        assert "cov not measured" not in second and "%" in second


def test_next_item_flags_the_item_it_hands_out(measured_as, capsys):
    """A no-lane row never reaches next-item: it counts under skipped_no_lane,
    so that variation checks the count instead of an item."""
    flag, root = measured_as

    code, out, _ = run(root, capsys, "next-item")

    payload = json.loads(out)
    assert code == 0
    if flag == "no-lane":
        assert payload["empty"] is True and payload["skipped_no_lane"] == 1
        return
    item = payload["item"]
    assert (item["path"], item["flag"]) == ("src/a.py", flag)
    assert item["unmeasured"] is (flag == "cc-only")


# --- rescore and check_gate: a function the run never saw ----------------------

FRESH = """
export function fresh(a: number, b: number): number {
  if (a > 1) { return 1; }
  if (b > 1) { return 2; }
  return 3;
}
"""
QUIET = """
export function quiet(a: number, b: number): number {
  if (a > b) { return a; }
  return b;
}
"""


@pytest.fixture()
def rescored(repo, capsys) -> Path:  # noqa: F811 (the imported fixture)
    """The template measured, plus src/quiet.ts, which the run scored untested:
    its lane covers src and its artifact never mentions the file."""
    (repo / "src" / "quiet.ts").write_text(QUIET, encoding="utf-8", newline="\n")
    commit_all(repo, "quiet")
    seed_artifacts(repo)
    code, out, err = run(repo, capsys, "coverage", "--reuse-artifacts")
    assert code == 0, out + err
    return repo


def edit(root: Path, change: str) -> tuple[str, str]:
    """Change src/app.ts after the run: the file to rescore, and the function
    in it to look at."""
    path = root / "src" / "app.ts"
    text = path.read_text(encoding="utf-8")
    if change == "new":
        path.write_text(text + FRESH, encoding="utf-8", newline="\n")
        return "src/app.ts", "fresh"
    if change == "renamed":
        path.write_text(text.replace("function plain(", "function plain2("), encoding="utf-8",
                        newline="\n")
        return "src/app.ts", "plain2"
    return {"control": ("src/app.ts", "dispatch"),
            "judged-untested": ("src/quiet.ts", "quiet")}[change]


def rows_by_name(payload: dict) -> dict:
    return {r["function"].split("(")[0].strip(): r for r in payload["functions"]}


@pytest.mark.parametrize("change, unmeasured, cov, flag", [
    ("control", False, 1.0, "measured"),
    ("judged-untested", False, 0.0, "untested"),
    ("new", True, 0.0, "untested"),
    ("renamed", True, 0.0, "untested"),
])
@pytest.mark.parametrize("argv", [("rescore",), ("rescore", "--gate")])
def test_rescore_flags_a_function_the_run_never_measured(rescored, capsys, argv, change,
                                                         unmeasured, cov, flag):
    path, name = edit(rescored, change)

    code, out, err = run(rescored, capsys, *argv, path, "--json")

    rows = rows_by_name(json.loads(out))
    assert code in (0, 6), err
    assert (rows[name]["unmeasured"], rows[name]["cov"], rows[name]["flag"]) == (unmeasured, cov,
                                                                                 flag)
    assert {r["unmeasured"] for n, r in rows.items() if n != name} <= {False}


def table_lines(out: str, path: str, word: str = "") -> list[str]:
    """rescore's table rows for PATH, only those holding WORD when one is given."""
    return [ln for ln in out.splitlines() if f"{path}:" in ln and word in ln]


@pytest.mark.parametrize("change, said", [("control", False), ("new", True), ("renamed", True)])
def test_the_rescore_table_says_not_measured_on_that_row_alone(rescored, capsys, change, said):
    path, name = edit(rescored, change)

    code, out, _ = run(rescored, capsys, "rescore", path)

    lines = table_lines(out, path)
    flagged = table_lines(out, path, "not measured")
    assert code == 0 and len(lines) >= 2
    assert [f"  {name} (" in ln for ln in flagged] == ([True] if said else [])


def test_check_gate_carries_the_flag_to_an_mcp_client(rescored):
    edit(rescored, "new")

    result = mcp_server._call_tool(rescored, "check_gate", {"path": "src/app.ts"})

    assert result["isError"] is False, result["content"][0]["text"][-400:]
    rows = rows_by_name(result["structuredContent"])
    assert (rows["fresh"]["unmeasured"], rows["dispatch"]["unmeasured"]) == (True, False)
