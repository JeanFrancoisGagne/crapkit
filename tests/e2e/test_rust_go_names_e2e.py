"""Naming a Rust or Go function on the command line, through the real CLI.

Three defects meet here, all of them found by a fresh session on a Rust repo:

  * `brief src/lib.rs route` was rejected. lizard prints a Rust signature as
    `route cmd : & Cmd` and a Go one as `Classify n int`, with no parenthesis to
    cut at, so the packet's `handle` was the whole signature and the bare name
    it advertised was a string nothing accepted back.
  * `explain src/lib.rs route` answered with `route`, `route_chain` and
    `route_num`, because it ran a SQL LIKE where `brief` matched exactly.
  * the Rust `match` those functions dispatch on scored 0 cognitive.

Everything asserts through a subprocess CLI on a tmp_path repo, because the
defect a session hit was in what the command accepted, not in what a helper
returned. The scores are hand-counted beside the sources.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

# route: base 1 + three outer non-wildcard arms + two inner ones = ccn 6.
# Cognitive: the outer match +1 at nesting 0, the inner match +1 and +1 for the
# nesting it sits in = 3.
LIB_RS = """fn route(cmd: &Cmd) -> u8 {
    match cmd {
        Cmd::Run(mode) => match mode {
            Mode::Fast => 1,
            Mode::Slow => 2,
            _ => 3,
        },
        Cmd::Stop => 4,
        Cmd::Wait => 5,
        _ => 0,
    }
}

fn route_chain(cmds: &[Cmd]) -> u8 {
    let mut n = 0;
    for c in cmds {
        if n > 3 {
            return n;
        }
        n += route(c);
    }
    n
}

fn route_num(n: u8) -> u8 {
    if n > 9 {
        0
    } else {
        n
    }
}
"""

ROUTE_CCN = 6
ROUTE_COGNITIVE = 3

# Classify: base 1 + two ifs = 3. ClassifyAll: base 1 + the range loop = 2.
ROUTE_GO = """package gosrc

func Classify(n int) string {
\tif n == 0 {
\t\treturn "zero"
\t}
\tif n == 1 {
\t\treturn "one"
\t}
\treturn "many"
}

func ClassifyAll(ns []int) []string {
\tout := []string{}
\tfor _, n := range ns {
\t\tout = append(out, Classify(n))
\t}
\treturn out
}
"""

CLASSIFY_CCN = 3

MOD_PY = "def ok(a):\n    return a\n"

# A lane exists only because `coverage` refuses to write a scored run without
# one. It measures the python scope; the Rust and Go scopes are cc-only.
MAKE_COV = '''import json

FILE = {"missing_lines": [], "summary": {"num_branches": 0, "covered_branches": 0},
        "functions": {"ok": {"summary": {"num_branches": 0, "covered_branches": 0},
                             "missing_lines": []}}}

with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": {"pylib/mod.py": FILE}}, fh)
'''

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]

[[scope]]
name = "rs"
paths = ["src"]
languages = ["rust"]
coverage_optional = true

[[scope]]
name = "go"
paths = ["gosrc"]
languages = ["go"]
coverage_optional = true

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["py"]
full_suite = false
"""


run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace")


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    for rel, text in (("src/lib.rs", LIB_RS), ("gosrc/route.go", ROUTE_GO),
                      ("pylib/mod.py", MOD_PY), ("make_cov.py", MAKE_COV),
                      ("crapkit.toml", CONFIG)):
        write(tmp_path, rel, text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    for args in (["config", "core.autocrlf", "false"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=tmp_path, check=True, capture_output=True)
    res = run_cli(tmp_path, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return tmp_path


def brief(repo: Path, path: str, name: str) -> dict:
    res = run_cli(repo, "brief", path, name, "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def explained(repo: Path, path: str, name: str) -> list[str]:
    res = run_cli(repo, "explain", path, name, "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return [f["long_name"] for f in json.loads(res.stdout)["functions"]]


# --- the bare name a session types back --------------------------------------

def test_brief_takes_the_bare_rust_name(repo: Path):
    """Exit 1 before the fix: `route` was not `route cmd : & Cmd` and there was
    no other name to try."""
    out = brief(repo, "src/lib.rs", "route")

    assert out["function"] == "route cmd : & Cmd"
    assert out["scored"]["ccn"] == ROUTE_CCN


def test_brief_takes_the_bare_go_name(repo: Path):
    out = brief(repo, "gosrc/route.go", "Classify")

    assert out["function"] == "Classify n int"
    assert out["scored"]["ccn"] == CLASSIFY_CCN


def test_the_handle_a_packet_publishes_is_a_name_brief_accepts(repo: Path):
    """The round trip that was broken. `handle` read `route cmd : & Cmd`, so the
    one string the packet offered as the short form was rejected by every
    command that took a NAME."""
    for path, expected in (("src/lib.rs", "route"), ("gosrc/route.go", "Classify")):
        handle = brief(repo, path, expected)["handle"]

        assert handle == expected
        assert brief(repo, path, handle)["handle"] == expected


def test_the_long_name_still_works_beside_the_bare_one(repo: Path):
    assert brief(repo, "src/lib.rs", "route cmd : & Cmd")["handle"] == "route"


# --- one NAME rule for both commands -----------------------------------------

def test_explain_resolves_an_exact_name_to_one_function(repo: Path):
    """Three trajectories before the fix: `route` is a substring of `route_chain`
    and `route_num`, and explain matched on containment alone."""
    assert explained(repo, "src/lib.rs", "route") == ["route cmd : & Cmd"]


def test_explain_and_brief_answer_the_same_name_with_the_same_function(repo: Path):
    assert explained(repo, "src/lib.rs", "route") == [brief(repo, "src/lib.rs", "route")["function"]]


def test_a_fragment_no_function_is_named_still_finds_all_three(repo: Path):
    """The fallback is intact: `rout` is nobody's name, so the answer is
    everything holding it, which is what tells a session which one it meant."""
    assert explained(repo, "src/lib.rs", "rout") == \
        ["route cmd : & Cmd", "route_chain cmds : & [ Cmd ]", "route_num n : u8"]


def test_an_ambiguous_fragment_in_brief_exits_one_naming_the_candidates(repo: Path):
    res = run_cli(repo, "brief", "src/lib.rs", "rout", "--json")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "ambiguous" in res.stderr
    assert "route_chain" in res.stderr and "route_num" in res.stderr


# --- the match is visible in the cognitive column ----------------------------

def test_the_nested_match_scores_cognitive_through_the_whole_pipeline(repo: Path):
    """0 before the fix, in the store and in every payload built off it: the one
    construct crapkit corrected a reader for was the one the second column could
    not see."""
    assert brief(repo, "src/lib.rs", "route")["scored"]["cognitive"] == ROUTE_COGNITIVE
