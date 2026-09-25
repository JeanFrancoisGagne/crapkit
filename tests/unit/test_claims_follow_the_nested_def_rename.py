"""A claim taken before analysis version 11 keeps holding the nested def it named.

Analysis version 11 names a def nested three deep `outer.mid.inner( ... )`, where
0.7.x read `outer.outer.mid.inner( ... )`. A claim saves the name it was handed
out under, so after the upgrade it named no function in the new run: next-item
and `brief --batch` handed the def to the next session, verify never closed the
claim, `claims release` refused the name crapkit now prints, and brief listed no
attempt on the def. Each test below starts from the claim row 0.7.6 wrote.
"""
import json
import sqlite3
import subprocess

import pytest

from crapkit import keys
from crapkit.cli import main
from crapkit.cli.queue import _named_claims
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.worklist import closable_claims

TOML = """[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]
coverage_optional = true
"""

NESTED = """def outer(rows):
    def mid(row):
        def inner(value, low, high, strict):
            if value is None:
                return 0
            if value < low:
                return -1
            if value > high:
                return 1
            if strict and value == low:
                return -2
            if strict and value == high:
                return 2
            return 0
        return inner(row, 0, 10, True)
    return [mid(row) for row in rows]
"""

PATH = "calc/nested.py"
OLD = "outer.outer.mid.inner( value , low , high , strict )"
NEW = "outer.mid.inner( value , low , high , strict )"


def git(root, *args) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout


def run(root, capsys, *argv) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(root)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def upgraded(tmp_path, capsys):
    """A repo scored under analysis 11, holding the claim 0.7.6 took on the def
    under the name analysis 10 gave it."""
    root = tmp_path / "repo"
    (root / "calc").mkdir(parents=True)
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8", newline="\n")
    (root / "calc" / "__init__.py").write_text("", encoding="utf-8")
    (root / "calc" / "nested.py").write_text(NESTED, encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    assert run(root, capsys, "coverage")[0] == 0
    conn = sqlite3.connect(root / ".crapkit" / "crap.sqlite")
    with conn:
        conn.execute("INSERT INTO attempts (path, long_name, commit_sha, handle, key_name, key_version) "
                     "VALUES (?, ?, ?, ?, ?, 1)",
                     (PATH, OLD, git(root, "rev-parse", "HEAD").strip(), "outer.outer.mid.inner", OLD))
    conn.close()
    return root


def test_next_item_does_not_hand_out_a_def_claimed_under_its_old_name(upgraded, capsys):
    code, out, _ = run(upgraded, capsys, "next-item")
    payload = json.loads(out)

    assert code == 0
    assert payload.get("item") is None, f"handed out {payload['item']['function']} again"
    assert payload["skipped_claimed"] == 1


def test_a_brief_batch_skips_it_as_next_item_does(upgraded, capsys):
    _, out, _ = run(upgraded, capsys, "brief", "--batch", "3", "--json")
    payload = json.loads(out)

    assert [p["function"] for p in payload["packets"]] == []
    assert payload["skipped_claimed"] == 1


def test_brief_lists_the_old_claim_among_the_defs_attempts(upgraded, capsys):
    _, out, _ = run(upgraded, capsys, "brief", PATH, "outer.mid.inner", "--json")
    packet = json.loads(out)

    assert packet["function"] == NEW
    assert [a["closed"] for a in packet["attempts"]] == [None]


@pytest.mark.parametrize("name", ["outer.mid.inner", NEW])
def test_claims_release_takes_the_name_crapkit_prints_now(upgraded, capsys, name):
    code, out, _ = run(upgraded, capsys, "claims", "release", PATH, name, "--json")

    assert (code, json.loads(out)) == (0, {"released": 1, "schema": 1})


def test_claims_release_still_takes_the_name_the_claim_was_handed_out_under(upgraded, capsys):
    code, out, _ = run(upgraded, capsys, "claims", "release", PATH, "outer.outer.mid.inner", "--json")

    assert (code, json.loads(out)["released"]) == (0, 1)


def row(name: str, crap: float) -> ScoredRow:
    return ScoredRow("calc", PATH, name, 3, 14, 6, 6, 6, 12, 4, 1, 1.0, "measured", crap,
                     "ok" if crap <= 6 else "decompose")


def old_claim(**saved) -> dict:
    return {"id": 7, "path": PATH, "long_name": OLD, "commit": "c1",
            "handle": "outer.outer.mid.inner", "key_name": OLD, "key_version": 1, **saved}


def test_verify_closes_the_claim_once_the_renamed_def_is_at_its_ceiling():
    assert closable_claims([old_claim()], [row(NEW, 6.0)], target=6, scope_targets={},
                           stale_commits=set()) == [7]


def test_verify_keeps_the_claim_while_the_renamed_def_is_over_its_ceiling():
    assert closable_claims([old_claim()], [row(NEW, 42.0)], target=6, scope_targets={},
                           stale_commits=set()) == []


def test_a_name_the_run_still_holds_keeps_its_own_function():
    """A def nested in a def of its own name is `a.a.b.c` under analysis 11
    too. When the run holds that name, the claim is on it, not on `a.b.c`."""
    claim = old_claim(long_name="a.a.b.c( x )", key_name="a.a.b.c( x )", handle="a.a.b.c")
    rows = [row("a.a.b.c( x )", 42.0), row("a.b.c( x )", 3.0)._replace(start=20, end=24)]

    assert closable_claims([claim], rows, target=6, scope_targets={}, stale_commits=set()) == []


def test_release_by_the_new_name_leaves_a_claim_saved_under_that_name_alone():
    exact = {"id": 1, "path": PATH, "long_name": "a.b.c( x )", "commit": "c1",
             "handle": "a.b.c", "key_name": "a.b.c( x )", "key_version": 1}
    nested = old_claim(id=2, long_name="a.a.b.c( x )", key_name="a.a.b.c( x )", handle="a.a.b.c")

    assert [c["id"] for c in _named_claims([exact, nested], PATH, "a.b.c")] == [1]


def test_release_by_a_name_no_claim_answers_to_still_lists_the_open_ones():
    with pytest.raises(CrapkitError, match=r"no open claim on 'outer\.mid' in calc/nested\.py"):
        _named_claims([old_claim()], PATH, "outer.mid")


@pytest.mark.parametrize("before, after", [
    ("a.a.b.c( x )", "a.b.c( x )"),
    ("a.a.b.a.a.b.c.d( x )", "a.b.c.d( x )"),
    ("outer.outer.mid.inner#2", "outer.mid.inner#2"),
    ("outer.outer.mid.inner( v )#2", "outer.mid.inner( v )#2"),
    ("a.b.c.d( x )", "a.b.c.d( x )"),
    ("a.b( x )", "a.b( x )"),
    ("a.a.b( x )", "a.a.b( x )"),
    ("f( x )", "f( x )"),
])
def test_the_rename_rewrites_only_the_doubled_spelling(before, after):
    assert keys.respelled_nested(PATH, before) == after


def test_the_rename_is_python_only():
    assert keys.respelled_nested("web/a.js", "a.a.b.c") == "a.a.b.c"


def test_a_respelled_claim_keeps_its_twin_ordinal():
    claim = old_claim(key_name=f"{OLD}#2", handle="outer.outer.mid.inner#2")

    moved = keys.claim_in_run(claim, lambda path: {NEW})

    assert (moved["long_name"], moved["key_name"], moved["handle"]) == \
        (NEW, f"{NEW}#2", "outer.mid.inner#2")
    assert keys.claim_key(moved) == (PATH, f"{NEW}#2")


def test_attempts_asked_with_no_run_pair_a_claim_by_the_name_it_saved(tmp_path):
    from crapkit.store import SnapshotStore

    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.record_claim(path=PATH, long_name=OLD, commit="c1", handle="outer.outer.mid.inner")

    found = store.attempts_for([(PATH, OLD), (PATH, NEW)])

    assert [len(found[key]) for key in ((PATH, OLD), (PATH, NEW))] == [1, 0]
