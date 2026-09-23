"""`brief` and `next-item` judge the remedy against today's ceiling.

Both commands print `target` and the budget from the ceiling crapkit.toml holds
now, and used to print the remedy the run stored under the ceiling it was
scored with. Lower `target` from 6 to 4 without committing and `brief` on a
ccn-5 function said `remedy: ok` beside `target: 4` and `est_splits: 2`, and
`next-item` never offered the function at all. Nothing marked the payload
stale, because HEAD had not moved.

The remedy rule, from the docs: `decompose` when ccn is over the ceiling, `ok`
when CRAP is at or under it, `split-lines` when another function declares the
same source lines, `add-tests` otherwise. Every CRAP below is ccn^2 x
(1 - cov)^3 + ccn worked by hand.
"""
import json

import pytest

from crapkit.mcp_server import tool_listing
from hand_scored_repo import make_repo, run, scored, write_run, write_toml

# Scored under a ceiling of 6.
MID = scored("mid( )", 1, 10, ccn=5, cov=0.9, crap=5.025, remedy="ok")
LEAN = scored("lean( )", 12, 20, ccn=3, cov=0.5, crap=4.125, remedy="ok")
# Two functions declaring one span: coverage cannot tell them apart.
LEFT = scored("left( )", 30, 30, ccn=2, cov=0.0, crap=6.0, remedy="ok", flag="untested")
RIGHT = scored("right( )", 30, 30, ccn=2, cov=0.0, crap=6.0, remedy="ok", flag="untested")

BIG = scored("big( )", 1, 10, ccn=7, cov=1.0, crap=7.0, remedy="decompose")
WIDE = scored("wide( )", 12, 20, ccn=7, cov=0.5, crap=13.125, remedy="decompose")
TWIN_A = scored("twin_a( )", 30, 30, ccn=7, cov=0.0, crap=56.0, remedy="decompose",
                flag="untested")
TWIN_B = scored("twin_b( )", 30, 30, ccn=7, cov=0.0, crap=56.0, remedy="decompose",
                flag="untested")


def _repo_with_ceiling(tmp_path, rows, today: int, **config):
    root = make_repo(tmp_path / "repo", target=6)
    write_run(root, rows)
    write_toml(root, today, **config)  # uncommitted: the run's commit is still HEAD
    return root


def _brief(root, capsys, name: str) -> dict:
    code, out, err = run(root, capsys, "brief", "src/app.py", name, "--json")
    assert code == 0, err
    return json.loads(out)


def _offered(root, capsys) -> dict:
    code, out, err = run(root, capsys, "next-item", "--top", "10")
    assert code == 0, err
    payload = json.loads(out)
    return {item["function"]: item["remedy"] for item in payload.get("items", [])}


@pytest.fixture()
def lowered(tmp_path):
    return _repo_with_ceiling(tmp_path, [MID, LEAN, LEFT, RIGHT], today=4)


@pytest.fixture()
def raised(tmp_path):
    return _repo_with_ceiling(tmp_path, [BIG, WIDE, TWIN_A, TWIN_B], today=8)


def test_a_lowered_ceiling_makes_brief_say_decompose_beside_its_own_budget(lowered, capsys):
    packet = _brief(lowered, capsys, "mid")

    assert (packet["target"], packet["est_splits"]) == (4, 2)
    assert packet["remedy"] == "decompose", "ccn 5 over a ceiling of 4"
    assert packet["scored"]["remedy"] == "decompose", "scored.remedy carries the same value"
    assert packet["stale"] is False, "HEAD did not move; the remedy is what changed"


def test_a_lowered_ceiling_rejudges_every_function_in_the_file(lowered, capsys):
    packet = _brief(lowered, capsys, "mid")

    remedies = {f["function"]: f["remedy"] for f in packet["file_functions"]}
    assert remedies == {"mid( )": "decompose", "lean( )": "add-tests",
                        "left( )": "split-lines", "right( )": "split-lines"}
    assert packet["file_totals"]["over_target"] == 4


def test_a_lowered_ceiling_puts_the_rows_it_breaks_in_the_queue(lowered, capsys):
    assert _offered(lowered, capsys) == {"left( )": "split-lines", "right( )": "split-lines",
                                         "mid( )": "decompose", "lean( )": "add-tests"}


def test_a_raised_ceiling_clears_what_now_sits_under_it(raised, capsys):
    packet = _brief(raised, capsys, "big")

    assert (packet["target"], packet["est_splits"]) == (8, 0)
    assert packet["remedy"] == "ok", "ccn 7 and CRAP 7.0 both sit under a ceiling of 8"


def test_a_raised_ceiling_turns_decompose_into_the_advice_left(raised, capsys):
    assert _brief(raised, capsys, "wide")["remedy"] == "add-tests"
    assert _brief(raised, capsys, "twin_a")["remedy"] == "split-lines", \
        "the run stored decompose, so the shared span has to be read off the file"


def test_a_raised_ceiling_drops_the_cleared_row_from_the_queue(raised, capsys):
    assert _offered(raised, capsys) == {"twin_a( )": "split-lines", "twin_b( )": "split-lines",
                                        "wide( )": "add-tests"}


def _listed(root, capsys) -> dict:
    code, out, err = run(root, capsys, "worklist", "--json")
    assert code == 0, err
    payload = json.loads(out)
    return {e["function"]: e["remedy"] for e in payload["active"] + payload["dormant_top"]}


def _served_worklist_remedy() -> str:
    tool = next(t for t in tool_listing() if t["name"] == "list_worklist")
    return tool["outputSchema"]["properties"]["active"]["items"]["properties"]["remedy"]["description"]


def test_list_worklist_says_its_remedy_is_the_runs_verdict(raised, capsys):
    """worklist prints the verdict the run stored, so after the raise it still
    calls big( ) decompose while next-item has dropped it. list_worklist's
    schema said every row but ok reaches get_next_item."""
    described = _served_worklist_remedy()

    assert _listed(raised, capsys)["big( )"] == "decompose"
    assert "big( )" not in _offered(raised, capsys)
    assert "as the run judged it" in described, described
    assert "get_next_item's own remedy decides" in described, described


def test_the_batch_packets_carry_todays_remedy(lowered, capsys):
    code, out, err = run(lowered, capsys, "brief", "--batch", "4", "--json")

    assert code == 0, err
    packets = json.loads(out)["packets"]
    assert {p["function"]: p["remedy"] for p in packets} == {
        "left( )": "split-lines", "right( )": "split-lines",
        "mid( )": "decompose", "lean( )": "add-tests"}


def test_a_scope_ceiling_added_after_the_run_judges_that_scope(tmp_path, capsys):
    """The repo ceiling stays 6 and the `src` scope sets its own 4, uncommitted."""
    root = _repo_with_ceiling(tmp_path, [MID, LEAN], today=6, scope_target=4)
    packet = _brief(root, capsys, "mid")

    assert (packet["target"], packet["remedy"], packet["est_splits"]) == (4, "decompose", 2)
    assert _offered(root, capsys) == {"mid( )": "decompose", "lean( )": "add-tests"}
