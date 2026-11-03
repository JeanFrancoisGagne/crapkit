"""A signature edit keeps its mark: every gate this ticket owns pardons the probe.

The probe: a cc-only Python scope at ceiling 6, and `ratchet seed` wrote
`calc/grade.py classify( a , b ) 7.0000`. The developer changes only the
signature, to `def classify(a, b, c=None):`, at ccn 7. Before, `rescore --gate`
exited 6 and verify refused the function as new debt: signed debt had turned
into new debt. Now `rescore --gate`, check_gate over a real MCP server and
verify pass it and print one line naming both keys, and both hooks pass. A
second edit to ccn 8 fails every gate with no carried line. Twins, or several
candidates for one bare name, refuse and name each key and the
`ratchet move --function` command that ends the ambiguity. In a scope a lane
measures, rescore reads the carried function's CRAP from the coverage the
baseline holds under the old key, so the same probe passes there too.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import cli_runner, git, git_commit_all, git_init_repo

run_cli = cli_runner()
# The MCP server is a stdio process, so check_gate is asked of one.
serve_cli = cli_runner(spawn=True)

MARKS = "crapkit-ratchet.tsv"
NO_REASON = {"CRAPKIT_OVERRIDE_REASON": None}
TOML = """[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]
coverage_optional = true
"""
OLD, NEW = "classify( a , b )", "classify( a , b , c = None )"


def body(branches: int) -> str:
    """A function body at ccn 1 + BRANCHES."""
    return "".join(f"    if a == {n}:\n        return {n}\n" for n in range(branches)) + "    return b\n"


SEEDED = "def classify(a, b):\n" + body(6)
SIGNED = "def classify(a, b, c=None):\n" + body(6)
CARRIED = (f"crapkit gate: calc/grade.py {NEW} carries the mark 7.0000 recorded for {OLD}; "
           f"a passing verify re-keys it, or run ")
MOVE = f' ratchet move --function calc/grade.py "{OLD}" "{NEW}"'


# A hermetic coverage lane for the measured scope: an istanbul artifact that
# runs every line of calc/grade.py, whose one function starts on line 1.
GEN_COV = """\
import json, os, sys
artifact, rel = sys.argv[1], sys.argv[2]
with open(rel, encoding="utf-8") as fh:
    end = len(fh.read().splitlines())
key = os.path.join(os.getcwd(), rel.replace("/", os.sep))
lines = {str(n): {"start": {"line": n}, "end": {"line": n}} for n in range(2, end + 1)}
fn = {"name": "classify", "decl": {"start": {"line": 1}},
      "loc": {"start": {"line": 1}, "end": {"line": end}}}
out = {key: {"path": key, "fnMap": {"0": fn}, "f": {"0": 1}, "branchMap": {}, "b": {},
             "statementMap": lines, "s": dict.fromkeys(lines, 1)}}
os.makedirs(os.path.dirname(artifact), exist_ok=True)
with open(artifact, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""
MEASURED_TOML = TOML.replace("coverage_optional = true\n", "") + (
    "\n[[lane]]\nname = \"unit\"\n"
    f"command = '\"{sys.executable}\" gen_cov.py cov/unit.json calc/grade.py'\n"
    "artifact = \"cov/unit.json\"\nparser = \"istanbul\"\nscopes = [\"calc\"]\n")


def seed(repo: Path, toml: str) -> Path:
    """The probe repo after `ratchet seed`, its marks committed."""
    (repo / "calc").mkdir()
    (repo / "crapkit.toml").write_text(toml, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncov/\n", encoding="utf-8")
    (repo / "calc" / "grade.py").write_text(SEEDED, encoding="utf-8")
    git_commit_all(repo, "base")
    for step in (("coverage",), ("ratchet", "seed")):
        res = run_cli(repo, *step, env_extra=NO_REASON)
        assert res.returncode == 0, res.stdout + res.stderr
    assert f"calc/grade.py\t{OLD}\t7.0000" in (repo / MARKS).read_text(encoding="utf-8")
    git_commit_all(repo, "seed")
    return repo


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    """The probe in its cc-only scope."""
    return seed(git_init_repo(tmp_path), TOML)


@pytest.fixture()
def measured(tmp_path: Path) -> Path:
    """The probe in a scope a coverage lane measures, every line run."""
    repo = git_init_repo(tmp_path)
    (repo / "gen_cov.py").write_text(GEN_COV, encoding="utf-8")
    return seed(repo, MEASURED_TOML)


def edited(repo: Path, source: str) -> Path:
    (repo / "calc" / "grade.py").write_text(source, encoding="utf-8")
    git(repo, "add", "calc/grade.py")
    return repo


def carried_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines()
            if line.startswith("crapkit gate: ") and " carries the mark " in line]


def check_gate(repo: Path) -> dict:
    """check_gate's gate block, asked of `crapkit mcp` over stdio."""
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "check_gate", "arguments": {"path": "calc/grade.py"}}}
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    proc = serve_cli(repo, "mcp", "--repo", str(repo), stdin=f"{json.dumps(call)}\n{json.dumps(listing)}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    replies = {m["id"]: m["result"] for m in map(json.loads, proc.stdout.strip().splitlines())}
    assert len(replies[2]["tools"]) == 12
    assert replies[1]["isError"] is False, replies[1]
    return json.loads(replies[1]["content"][0]["text"])["gate"]


def claude_hook(repo: Path):
    event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(repo),
             "tool_input": {"file_path": str(repo / "calc" / "grade.py")}}
    return run_cli(repo, "claude-hook", "--protocol", "1", stdin=json.dumps(event), env_extra=NO_REASON)


def test_the_probe_passes_rescore_gate_with_one_carried_line(seeded):
    res = run_cli(edited(seeded, SIGNED), "rescore", "calc/grade.py", "--gate", env_extra=NO_REASON)

    assert res.returncode == 0, res.stdout + res.stderr
    (line,) = carried_lines(res.stderr)
    assert line.startswith(CARRIED) and line.endswith(MOVE) and line.isascii(), line
    assert "gate: 1 changed function(s) judged, 0 over ceiling 6" in res.stdout


def test_the_probe_s_gate_json_lists_the_carry(seeded):
    res = run_cli(edited(seeded, SIGNED), "rescore", "calc/grade.py", "--gate", "--json",
                  env_extra=NO_REASON)

    gate = json.loads(res.stdout)["gate"]
    assert (res.returncode, gate["ok"], gate["breaches"]) == (0, True, [])
    assert gate["carried"] == [{"path": "calc/grade.py", "long_name": NEW, "key_name": NEW,
                                "carried_from": OLD, "mark": 7.0}]
    assert len(carried_lines(res.stderr)) == 1


def test_check_gate_answers_ok_with_one_carried_item(seeded):
    gate = check_gate(edited(seeded, SIGNED))

    assert gate["ok"] is True and gate["breaches"] == []
    assert [(c["key_name"], c["carried_from"], c["mark"]) for c in gate["carried"]] == [(NEW, OLD, 7.0)]


def test_both_hooks_pass_the_probe(seeded):
    repo = edited(seeded, SIGNED)

    hook = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    advisory = claude_hook(repo)

    assert hook.returncode == 0, hook.stdout + hook.stderr
    assert advisory.returncode == 0, advisory.stdout + advisory.stderr


def test_verify_passes_the_probe_prints_the_line_once_and_lists_a_carried_item(seeded):
    res = run_cli(edited(seeded, SIGNED), "verify", "--json", env_extra=NO_REASON)

    payload = json.loads(res.stdout)
    assert res.returncode == 0, res.stdout + res.stderr
    (line,) = carried_lines(res.stderr)
    assert line.startswith(CARRIED) and line.endswith(MOVE), line
    (item,) = [f for f in payload["findings"] if f["kind"] == "carried"]
    assert item == {"kind": "carried", "fails": False, "exit_code": None, "overridable": False,
                    "dirty": True, "rule": "carried mark", "path": "calc/grade.py",
                    "long_name": NEW, "key_name": NEW, "carried_from": OLD, "mark": 7.0}
    assert "carried_marks" not in res.stdout
    assert "carry no ratchet mark" not in res.stderr, res.stderr


def test_a_second_edit_past_the_carried_mark_fails_every_gate_with_no_carried_line(seeded):
    repo = edited(seeded, "def classify(a, b, c=None):\n" + body(7))

    gate = run_cli(repo, "rescore", "calc/grade.py", "--gate", env_extra=NO_REASON)
    asked = check_gate(repo)
    verified = run_cli(repo, "verify", env_extra=NO_REASON)

    assert (gate.returncode, asked["ok"], verified.returncode) == (6, False, 6)
    assert carried_lines(gate.stderr) == [] and carried_lines(verified.stderr) == []
    assert asked["carried"] == []


TWINS = SIGNED + "\n\n" + SIGNED


@pytest.mark.parametrize("source, keys", [
    pytest.param(TWINS, (f'"{NEW}"', f'"{NEW}#2"'), id="twins"),
    pytest.param(SIGNED + "\n\ndef classify(q):\n" + body(6), (f'"{NEW}"', '"classify( q )"'),
                 id="two-unmarked-functions"),
])
def test_an_ambiguous_carry_refuses_and_names_every_key_and_the_move(seeded, source, keys):
    repo = edited(seeded, source)

    gate = run_cli(repo, "rescore", "calc/grade.py", "--gate", "--json", env_extra=NO_REASON)
    asked = check_gate(repo)
    verified = run_cli(repo, "verify", "--json", env_extra=NO_REASON)

    assert (gate.returncode, asked["ok"], verified.returncode) == (6, False, 6)
    breaches = json.loads(gate.stdout)["gate"]["breaches"]
    sentences = {b["mark_ambiguity"] for b in breaches} | {
        f["mark_ambiguity"] for f in json.loads(verified.stdout)["findings"]
        if f["kind"] == "gate_violation"}
    (sentence,) = sentences
    assert all(key in sentence for key in keys) and f'ratchet move --function calc/grade.py "{OLD}"' in sentence
    assert sentence in gate.stderr and carried_lines(gate.stderr) == []


def test_two_marks_leaving_one_bare_name_refuse_and_name_both(seeded):
    marks = seeded / MARKS
    marks.write_text(marks.read_text(encoding="utf-8") + "calc/grade.py\tclassify( x )\t9.0000\n",
                     encoding="utf-8", newline="\n")
    git_commit_all(seeded, "a second mark")

    res = run_cli(edited(seeded, SIGNED), "rescore", "calc/grade.py", "--gate", "--json",
                  env_extra=NO_REASON)

    (breach,) = json.loads(res.stdout)["gate"]["breaches"]
    assert res.returncode == 6
    assert "2 marks left the file under that name" in breach["mark_ambiguity"]
    assert f'"{OLD}"' in breach["mark_ambiguity"] and '"classify( x )"' in breach["mark_ambiguity"]


def test_a_carried_function_at_its_ceiling_prints_no_carried_line(seeded):
    """The marks are read only on a breach, so nothing asks about a carry."""
    res = run_cli(edited(seeded, "def classify(a, b, c=None):\n" + body(5)), "rescore", "calc/grade.py",
                  "--gate", env_extra=NO_REASON)

    assert res.returncode == 0 and carried_lines(res.stderr) == [], res.stderr


def test_a_measured_carry_at_its_mark_passes_every_gate_with_the_carried_line(measured):
    """The baseline holds the coverage under the old key alone, and rescore
    judges the carried function on that stale number, as it judges a body edit
    on the number its own name holds."""
    repo = edited(measured, SIGNED)

    gate = run_cli(repo, "rescore", "calc/grade.py", "--gate", "--json", env_extra=NO_REASON)
    asked = check_gate(repo)
    verified = run_cli(repo, "verify", env_extra=NO_REASON)

    assert (gate.returncode, asked["ok"], verified.returncode) == (0, True, 0), gate.stderr + verified.stderr
    payload = json.loads(gate.stdout)
    assert payload["gate"]["carried"] == asked["carried"] == [
        {"path": "calc/grade.py", "long_name": NEW, "key_name": NEW, "carried_from": OLD, "mark": 7.0}]
    (judged,) = payload["functions"]
    assert (judged["cov"], judged["crap"], judged["unmeasured"]) == (1.0, 7.0, False)
    assert len(carried_lines(gate.stderr)) == len(carried_lines(verified.stderr)) == 1


def test_a_measured_carry_past_its_mark_fails_rescore_gate_and_verify(measured):
    repo = edited(measured, "def classify(a, b, c=None):\n" + body(7))

    gate = run_cli(repo, "rescore", "calc/grade.py", "--gate", "--json", env_extra=NO_REASON)
    asked = check_gate(repo)
    verified = run_cli(repo, "verify", env_extra=NO_REASON)

    assert (gate.returncode, asked["ok"], verified.returncode) == (6, False, 6)
    (breach,) = json.loads(gate.stdout)["gate"]["breaches"]
    assert (breach["cov"], breach["crap"], breach["mark_ambiguity"]) == (1.0, 8.0, None)
    assert carried_lines(gate.stderr) == carried_lines(verified.stderr) == []
