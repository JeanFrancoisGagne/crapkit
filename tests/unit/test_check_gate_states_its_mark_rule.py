"""check_gate says which mark rule it applies, and the rule it names is the one it runs.

check_gate runs `rescore --gate`, where a ratchet mark pardons a function only
while its crap sits at or under the mark. The pre-commit hook pardons any
marked function, because a staged blob has no coverage to score. The
description used to say check_gate answers the hook's commit gate, so an agent
that saw gate.ok false expected a refused commit that never came.
"""
import json

from cli_inproc_repo import add_knotty, git, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit.cli import main
from crapkit.mcp_server import tool_listing


def description() -> str:
    (entry,) = [t for t in tool_listing() if t["name"] == "check_gate"]
    return entry["description"]


def test_the_description_names_the_rule_check_gate_applies():
    text = description()

    assert "rescore --gate's rule" in text, text
    assert "pardoned only at or under its ratchet mark" in text, text


def test_the_description_says_the_hook_is_more_lenient():
    text = description()

    assert "The hook's commit gate pardons any marked function" in text, text
    assert "stricter" in text and "predicts a CLI verify refusal" in text, text


@pytest.fixture()
def scored(repo, capsys):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def test_a_function_past_its_mark_fails_check_gate_and_still_commits(scored, capsys):
    """The contrast the description states, on one tree: mark 20, fresh crap 72."""
    add_knotty(scored)
    (scored / "crapkit-ratchet.tsv").write_text(
        "path\tlong_name\tcrap\nsrc/app.ts\tknotty ( n )\t20.0000\n",
        encoding="utf-8", newline="\n")
    git(scored, "add", "-A")

    gate = main(["rescore", "src/app.ts", "--gate", "--json", "--repo", str(scored)])
    verdict = json.loads(capsys.readouterr().out)["gate"]
    hook = main(["hook-precommit", "--repo", str(scored)])
    hook_err = capsys.readouterr().err

    assert (gate, verdict["ok"]) == (6, False)
    assert [(b["function"], b["crap"]) for b in verdict["breaches"]] == [("knotty ( n )", 72.0)]
    assert hook == 0
    assert "1 staged function(s) carry a ratchet mark and were not gated" in hook_err, hook_err
