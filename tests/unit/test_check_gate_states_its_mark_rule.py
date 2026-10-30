"""check_gate says which mark rule it applies, and the rule it names is the one it runs.

check_gate runs `rescore --gate`, where a ratchet mark pardons a function only
while its crap sits at or under the mark. The pre-commit hook sees no coverage,
so it refuses a marked function only once its staged ccn passes the mark. An
agent that read the older text, where the hook pardoned any marked function,
expected every marked function to commit and met exit 6 at `git commit`.
"""
import json

from cli_inproc_repo import KNOTTY, add_knotty, commit_all, git, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit import mcp_server
from crapkit.cli import main
from crapkit.mcp_server import tool_listing

# knotty with one branch fewer: ccn 7.
KNOTTY_CCN_7 = KNOTTY.replace("  if (n > 7) { return 7; }\n", "")

# A scope no lane measures: its CRAP is its ccn.
CC_ONLY_SCOPE = """
[[scope]]
name = "cc"
paths = ["cc"]
languages = ["typescript"]
coverage_optional = true
"""


def entry() -> dict:
    (gate,) = [t for t in tool_listing() if t["name"] == "check_gate"]
    return gate


def description() -> str:
    return entry()["description"]


def test_the_description_names_the_rule_check_gate_applies():
    """The mark is weighed against crap, not ccn: a function at ccn 8 under a
    mark of 20 still fails when its crap is 72."""
    text = description()

    assert "rescore --gate's rule" in text, text
    assert "pardoned only while its crap is at or under its ratchet mark" in text, text


def test_the_description_states_the_hooks_marked_rule():
    text = description()

    assert "refuses a marked function only once its ccn passes the mark" in text, text
    assert "stricter" in text and "predicts a verify refusal" in text, text
    assert "pardons any marked function" not in text, text


def test_the_description_is_ascii_with_no_semicolon_chain():
    text = description()

    assert text.isascii(), text
    assert ";" not in text, text


def test_no_other_surface_calls_check_gate_the_commit_gate():
    """The title and the server instructions are the first text a model reads."""
    assert "Commit gate verdict" not in entry()["title"], entry()["title"]
    assert "rescore --gate" in entry()["title"], entry()["title"]
    assert "clears the commit gate" not in mcp_server._INSTRUCTIONS
    assert "clears rescore --gate, which is stricter than the commit hook" in mcp_server._INSTRUCTIONS


def test_the_path_argument_says_what_it_accepts_and_what_judges_nothing():
    """The facts the 560-character description has no room for."""
    text = entry()["inputSchema"]["properties"]["path"]["description"]

    assert "absolute inside repo" in text, text
    assert "Outside the repo, missing or a directory answers a config error" in text, text
    assert "an unchanged or unscoped file judges 0" in text, text
    assert "any directory under the checkout" in text, text


def _score(repo, capsys):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


@pytest.fixture()
def scored(repo, capsys):
    return _score(repo, capsys)


@pytest.fixture()
def cc_scored(repo, capsys):
    """The template plus a committed, empty `cc/rules.ts` under a cc-only scope."""
    with open(repo / "crapkit.toml", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(CC_ONLY_SCOPE)
    (repo / "cc").mkdir()
    (repo / "cc" / "rules.ts").write_text("", encoding="utf-8")
    commit_all(repo, "cc scope")
    return _score(repo, capsys)


def test_a_function_past_its_mark_fails_check_gate_and_still_commits(scored, capsys):
    """The contrast the description states, on one tree: mark 20, fresh crap 72.
    The hook sees ccn 8, at or under the mark, so coverage could still hold the
    CRAP there: it refuses nothing."""
    add_knotty(scored)
    (scored / "crapkit-ratchet.tsv").write_text(
        "path\tlong_name\tcrap\nsrc/app.ts\tknotty ( n )\t20.0000\n",
        encoding="utf-8", newline="\n")
    git(scored, "add", "-A")

    gate = main(["rescore", "src/app.ts", "--gate", "--json", "--repo", str(scored)])
    verdict = json.loads(capsys.readouterr().out)["gate"]
    hook = main(["hook-precommit", "--repo", str(scored)])
    hook_out = capsys.readouterr().out

    assert (gate, verdict["ok"]) == (6, False)
    assert [(b["function"], b["crap"]) for b in verdict["breaches"]] == [("knotty ( n )", 72.0)]
    assert hook == 0
    assert "rose past their ratchet mark" not in hook_out and "knotty" not in hook_out, hook_out


def both_gates(repo, capsys, source: str, body: str) -> tuple[int, dict, int, str]:
    """Append `body` to `source`, mark knotty at 7.0000, stage it all, then
    answer check_gate's exit and gate block and hook-precommit's exit and stdout."""
    with open(repo / source, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    (repo / "crapkit-ratchet.tsv").write_text(
        f"path\tlong_name\tcrap\n{source}\tknotty ( n )\t7.0000\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")

    gate = main(["rescore", source, "--gate", "--json", "--repo", str(repo)])
    verdict = json.loads(capsys.readouterr().out)["gate"]
    hook = main(["hook-precommit", "--repo", str(repo)])
    return gate, verdict, hook, capsys.readouterr().out


def test_a_function_whose_ccn_passed_its_mark_fails_check_gate_and_the_commit(scored, capsys):
    """The other half of the sentence: ccn 8 under mark 7. Its CRAP is at least
    8 at any coverage, so both gates refuse it."""
    gate, verdict, hook, out = both_gates(scored, capsys, "src/app.ts", KNOTTY)

    assert (gate, verdict["ok"]) == (6, False)
    assert [b["function"] for b in verdict["breaches"]] == ["knotty ( n )"]
    assert hook == 6, out
    assert "rose past their ratchet mark" in out, out
    (row,) = [line for line in out.splitlines() if "knotty ( n )" in line]
    assert row.lstrip().startswith("ccn   8 ") and row.endswith("knotty ( n )  mark 7.0000"), row


@pytest.mark.parametrize(("body", "refused"), [
    pytest.param(KNOTTY, True, id="ccn-8-under-mark-7"),
    pytest.param(KNOTTY_CCN_7, False, id="ccn-7-at-mark-7"),
])
def test_a_cc_only_scope_holds_both_gates_to_its_ccn_against_the_mark(cc_scored, capsys, body, refused):
    """CRAP equals ccn there, so check_gate and the hook draw the same line."""
    gate, verdict, hook, out = both_gates(cc_scored, capsys, "cc/rules.ts", body)
    code = 6 if refused else 0

    assert (gate, verdict["ok"], hook) == (code, not refused, code), (verdict, out)
    assert ("knotty ( n )  mark 7.0000" in out) is refused, out
