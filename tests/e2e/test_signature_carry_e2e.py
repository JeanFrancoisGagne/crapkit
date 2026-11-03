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

A passing verify then re-keys the mark: the marks file holds the new key at
the same value, the OK line counts `1 re-keyed`, and a second verify writes
nothing. While the mark is still carried, verify's ratchet check reads the
function that carries it, so a rise past the mark fails at exit 7 even when
the diff never touched it.
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


def seed(repo: Path, toml: str, encode=str.encode) -> Path:
    """The probe repo after `ratchet seed`, its marks committed, its source
    saved through `encode`."""
    (repo / "calc").mkdir()
    (repo / "crapkit.toml").write_text(toml, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncov/\n", encoding="utf-8")
    (repo / "calc" / "grade.py").write_bytes(encode(SEEDED))
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


def bash_hook(repo: Path):
    """claude-hook as a Bash command that rewrote the file reports it: no path,
    so the hook judges the repo's freshly written source."""
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(repo),
             "tool_input": {"command": "python rewrite.py calc/grade.py"}}
    return run_cli(repo, "claude-hook", "--protocol", "1", stdin=json.dumps(event), env_extra=NO_REASON)


def test_both_hooks_pass_the_probe(seeded):
    repo = edited(seeded, SIGNED)

    hook = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    advisory = claude_hook(repo)

    assert hook.returncode == 0, hook.stdout + hook.stderr
    (line,) = carried_lines(hook.stderr)
    assert line.startswith(CARRIED) and line.endswith(MOVE), line
    assert "1 staged function(s) carry a ratchet mark" in hook.stderr
    assert (advisory.returncode, advisory.stdout, advisory.stderr) == (0, "", "")


def test_the_bash_fallback_draws_the_edit_s_verdicts(seeded):
    """The same writes reported by a shell command: silence on the probe, the
    marked block with the carried note past the mark."""
    signed = bash_hook(edited(seeded, SIGNED))
    risen = bash_hook(edited(seeded, "def classify(a, b, c=None):\n" + body(7)))

    assert (signed.returncode, signed.stderr) == (0, "")
    assert risen.returncode == 2 and f"mark 7.0000 (carried from {OLD})" in risen.stderr, risen.stderr


def test_a_second_edit_past_the_carried_mark_refuses_on_both_hooks_with_the_carried_note(seeded):
    repo = edited(seeded, "def classify(a, b, c=None):\n" + body(7))

    hook = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    advisory = claude_hook(repo)

    note = f"{NEW}  mark 7.0000 (carried from {OLD})"
    assert (hook.returncode, advisory.returncode) == (6, 2), hook.stdout + hook.stderr + advisory.stderr
    assert note in hook.stdout and f"  ccn 8  calc/grade.py:1  {note}" in advisory.stderr
    assert carried_lines(hook.stderr) == [] and "mark the debt" not in advisory.stderr


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


def test_a_twin_refuses_on_both_hooks_until_the_move_it_names_settles_it(seeded):
    """The edit leaves the probe at ccn 7 beside a ccn-1 twin of its new name:
    twins never carry a mark, so both hooks refuse and name both keys and the
    move. The move re-keys the mark, and the next commit passes."""
    repo = edited(seeded, SIGNED + "\n\ndef classify(a, b, c=None):\n    return b\n")

    hook = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    advisory = claude_hook(repo)

    assert (hook.returncode, advisory.returncode) == (6, 2), hook.stdout + advisory.stderr
    for said in (hook.stdout, advisory.stderr):
        assert "twins never carry a mark" in said and f'"{NEW}#2"' in said
        assert f'ratchet move --function calc/grade.py "{OLD}" "{NEW}"' in said
    moved = run_cli(repo, "ratchet", "move", "--function", "calc/grade.py", OLD, NEW, env_extra=NO_REASON)
    assert moved.returncode == 0, moved.stdout + moved.stderr
    git(repo, "add", MARKS)
    again = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    assert again.returncode == 0 and carried_lines(again.stderr) == [], again.stdout + again.stderr


@pytest.mark.parametrize("extra_mark, source, keys", [
    pytest.param("calc/grade.py\tclassify( x )\t9.0000\n", SIGNED, (f'"{OLD}"', '"classify( x )"'),
                 id="two-marks-left"),
    pytest.param("", SIGNED + "\n\ndef classify(q):\n" + body(6), (f'"{NEW}"', '"classify( q )"'),
                 id="two-unmarked-functions"),
])
def test_several_candidates_refuse_on_both_hooks_and_name_both_keys(seeded, extra_mark, source, keys):
    if extra_mark:
        marks = seeded / MARKS
        marks.write_text(marks.read_text(encoding="utf-8") + extra_mark, encoding="utf-8", newline="\n")
        git_commit_all(seeded, "a second mark")
    repo = edited(seeded, source)

    hook = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    advisory = claude_hook(repo)

    assert (hook.returncode, advisory.returncode) == (6, 2), hook.stdout + advisory.stderr
    for said in (hook.stdout, advisory.stderr):
        assert all(key in said for key in keys) and "ratchet move --function is the explicit form" in said
    assert carried_lines(hook.stderr) == []


def utf16(text: str) -> bytes:
    return "﻿".encode("utf-16-le") + text.encode("utf-16-le")


def crlf(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode("utf-8")


@pytest.mark.parametrize("encode", [utf16, crlf], ids=["utf-16-le-bom", "crlf"])
def test_the_probe_s_hook_verdicts_hold_in_every_source_encoding(tmp_path, encode):
    """Seeded and edited in the same encoding: the probe passes both hooks with
    the carried line, and ccn 8 refuses on both with the carried note."""
    repo = seed(git_init_repo(tmp_path), TOML, encode)
    source = repo / "calc" / "grade.py"

    source.write_bytes(encode(SIGNED))
    git(repo, "add", "calc/grade.py")
    passed = run_cli(repo, "hook-precommit", env_extra=NO_REASON), claude_hook(repo)
    source.write_bytes(encode("def classify(a, b, c=None):\n" + body(7)))
    git(repo, "add", "calc/grade.py")
    risen = run_cli(repo, "hook-precommit", env_extra=NO_REASON), claude_hook(repo)

    assert [done.returncode for done in passed + risen] == [0, 0, 6, 2], [d.stdout + d.stderr for d in risen]
    assert len(carried_lines(passed[0].stderr)) == 1 and passed[1].stderr == ""
    assert f"(carried from {OLD})" in risen[0].stdout and f"(carried from {OLD})" in risen[1].stderr


def test_a_utf16_marks_file_carries_the_same_way(seeded):
    marks = seeded / MARKS
    marks.write_bytes(utf16(marks.read_text(encoding="utf-8")))
    git_commit_all(seeded, "marks saved as UTF-16")
    repo = edited(seeded, SIGNED)

    hook = run_cli(repo, "hook-precommit", env_extra=NO_REASON)
    advisory = claude_hook(repo)

    assert (hook.returncode, advisory.returncode, advisory.stderr) == (0, 0, ""), hook.stdout + hook.stderr
    assert len(carried_lines(hook.stderr)) == 1


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


REKEYED_TAIL = f"ratchet: 0 dropped, 0 tightened, 1 re-keyed -> git add {MARKS}"


def test_a_passing_verify_re_keys_the_probe_once_and_says_so(seeded):
    repo = edited(seeded, SIGNED)

    first = run_cli(repo, "verify", env_extra=NO_REASON)
    marks = (repo / MARKS).read_text(encoding="utf-8")
    payload = run_cli(repo, "verify", "--json", env_extra=NO_REASON)

    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout.splitlines()[0].endswith(REKEYED_TAIL), first.stdout
    assert f"calc/grade.py\t{NEW}\t7.0000" in marks and f"\t{OLD}\t" not in marks, marks
    assert payload.returncode == 0 and json.loads(payload.stdout)["ratchet_changes"] is None
    assert (repo / MARKS).read_text(encoding="utf-8") == marks


def test_the_json_receipt_counts_the_re_key(seeded):
    res = run_cli(edited(seeded, SIGNED), "verify", "--json", env_extra=NO_REASON)

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["ratchet_changes"] == {"dropped": 0, "tightened": 0, "rekeyed": 1}


def test_no_tighten_keeps_the_old_key_and_the_next_passing_verify_re_keys_it(seeded):
    repo = edited(seeded, SIGNED)

    held = run_cli(repo, "verify", "--no-tighten", env_extra=NO_REASON)
    kept = (repo / MARKS).read_text(encoding="utf-8")
    moved = run_cli(repo, "verify", env_extra=NO_REASON)

    assert (held.returncode, moved.returncode) == (0, 0), held.stderr + moved.stderr
    assert f"\t{OLD}\t7.0000" in kept
    assert moved.stdout.splitlines()[0].endswith(REKEYED_TAIL), moved.stdout


def risen(repo: Path) -> Path:
    """The carried function at ccn 8, committed and measured, so verify's diff
    since its baseline touches nothing."""
    edited(repo, "def classify(a, b, c=None):\n" + body(7))
    git_commit_all(repo, "rise")
    res = run_cli(repo, "coverage", env_extra=NO_REASON)
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


def test_an_untouched_carried_function_that_rose_fails_the_ratchet_check(seeded):
    repo = risen(seeded)
    before = (repo / MARKS).read_bytes()

    res = run_cli(repo, "verify", "--json", env_extra=NO_REASON)
    text = run_cli(repo, "verify", env_extra=NO_REASON)

    assert (res.returncode, text.returncode) == (7, 7), res.stdout + res.stderr
    (item,) = [f for f in json.loads(res.stdout)["findings"] if f["kind"] == "ratchet_regression"]
    assert (item["long_name"], item["recorded"], item["fresh_crap"], item["carried_from"]) == (
        NEW, 7.0, 8.0, OLD)
    assert f"  RATCHET  calc/grade.py  {NEW}: 7.0 -> 8.0 (mark recorded for {OLD})" in text.stdout
    assert (repo / MARKS).read_bytes() == before


ALERTED = TOML.replace("target = 6\n", f"target = 6\nalert_command = '\"{sys.executable}\" -c \"\"'\n")


def test_an_override_on_a_touched_carried_rise_is_refused_and_writes_no_mark(tmp_path):
    repo = edited(seed(git_init_repo(tmp_path), ALERTED), "def classify(a, b, c=None):\n" + body(7))
    before = (repo / MARKS).read_bytes()

    res = run_cli(repo, "verify", "--override", "signed off", env_extra=NO_REASON)

    assert res.returncode == 6, res.stdout + res.stderr
    assert f"  RATCHET  calc/grade.py  {NEW}: 7.0 -> 8.0 (mark recorded for {OLD})" in res.stdout
    assert "ratchet regression" in res.stderr and "raise the mark by hand and commit it" in res.stderr
    assert (repo / MARKS).read_bytes() == before
