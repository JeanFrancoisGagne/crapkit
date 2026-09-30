"""Arguments and layouts that used to produce an answer where a refusal belonged.

- `verify --override` with an empty or blank reason grants nothing and runs
  nothing (docs/ratchet.md, Overrides; model_verdict.override_refusal).
- `ratchet move` files marks under the repo-relative path a scored row
  carries, from any spelling and from a subdirectory (docs/adr/0002: relative
  path arguments are rebased from where the user stands).
- Under a crapkit root below the git top, the hook, `rescore --gate` and
  `verify` gate a touched function over its ceiling (docs/lanes.md, A crapkit
  root below the repo top; model_verdict.hook_gate and verify_gate).
- `--top` below 1 and an undeclared `--scope` are refused (README, Commands);
  an absolute path inside the root reads as its relative spelling.
- The override reads a marks file saved with a byte-order mark.
"""
from __future__ import annotations

import sys

import pytest

from accuracy.kit import drive, repos
from accuracy.verdict_model import cadence
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

WORLD = (vw.World()
         .with_fn("app", vw.Fn("a1", 1, 2)).with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_fn("lib", vw.Fn("b2", 7, 7))
         .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
PAST = vw.Fn("a1", 7, 0)        # ccn 8, cov 0: CRAP 72, over the ceiling of 6


def _seeded(make_repo, root: str = "") -> vw.Scenario:
    sc = vw.Scenario.build(make_repo, WORLD, root=root)
    assert sc.run("coverage").code == 0 and sc.run("ratchet", "seed").code == 0
    sc.commit("seed the ratchet")
    return sc


def _marks(sc) -> dict:
    return model.parse_marks(sc.marks_text()).marks


# --- the override reason -------------------------------------------------------------------------

@pytest.mark.process
@pytest.mark.parametrize("reason", cadence.tiered(["", "   ", "\t"], push={"blank"},
                                                  ids=["empty", "blank", "tab"]))
def test_empty_override_reason_refuses(make_repo, reason):
    """An empty or blank reason is refused (exit 3) before any lane runs: no
    run is stored, no mark changes, no lane log moves."""
    sc = _seeded(make_repo)
    sc.set(sc.world.with_fn("app", PAST))
    runs, marks = sc.runs(), sc.marks_text()
    log = (sc.root / ".crapkit" / "lane-a.log").stat().st_mtime_ns
    result = sc.run("verify", "--override", reason)
    assert model.override_refusal(reason, alert=True, regressions=0, new_failures=0) == "blank reason"
    assert result.code == 3, result.stdout + result.stderr
    assert (sc.runs(), sc.marks_text()) == (runs, marks)
    assert (sc.root / ".crapkit" / "lane-a.log").stat().st_mtime_ns == log


@pytest.mark.parametrize("cause", ["regressions", "new_failures", "unread"])
def test_the_model_refuses_an_override_beside_what_no_mark_carries(cause):
    """docs/ratchet.md, Overrides: a ratchet regression, a new test failure or
    an unread file in the same run refuses the override; alone, a reason and an
    alert grant it."""
    counts = {"regressions": 0, "new_failures": 0, "unread": 0}
    assert model.override_refusal("hotfix 412", alert=True, **counts) is None
    assert model.override_refusal("hotfix 412", alert=False, **counts) == "no alert_command"
    assert model.override_refusal("hotfix 412", alert=True, **{**counts, cause: 1}) == (
        "regression, new failure or unread file")


@pytest.mark.nightly
@pytest.mark.process
def test_override_reads_a_marks_file_with_a_bom(make_repo):
    """A marks file PowerShell 5.1 saved starts with a UTF-8 byte-order mark.
    The grant keys every mark as the file names it, the BOM on none."""
    sc = _seeded(make_repo)
    path = sc.root / "crapkit-ratchet.tsv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    sc.commit("saved by PowerShell 5.1")
    before = _marks(sc)
    sc.set(sc.world.with_fn("app", PAST))
    result = sc.run("verify", "--override", "reviewed debt")
    assert result.code == 0, result.stdout + result.stderr
    granted = {("src/app.py", "a1( x )"): model.mark_value(PAST.crap)}
    after = model.parse_marks(path.read_bytes().decode("utf-8-sig")).marks
    assert after == {**before, **granted}
    assert all(not path_.startswith("\ufeff") for path_, _ in after)


# --- ratchet move ------------------------------------------------------------------------------------

SPELLINGS = [("./src/app.py", "./lib/new.py", "."), ("src/app.py", "lib/new.py", "."),
             ("app.py", "../lib/new.py", "src")]
if sys.platform == "win32":
    SPELLINGS.append(("src\\app.py", "lib\\new.py", "."))


@pytest.mark.process
@pytest.mark.parametrize("old, new, where", cadence.tiered(
    SPELLINGS, push=SPELLINGS[-1:], ids=[f"{o}->{n}@{w}" for o, n, w in SPELLINGS], unpack=True))
def test_ratchet_move_path_spellings(make_repo, old, new, where):
    """Every spelling of the same move files a3's mark under lib/new.py, the
    path a scored row there would carry; nothing else moves."""
    sc = _seeded(make_repo)
    before = _marks(sc)
    result = drive.Driver(sc.root / where).run("ratchet", "move", old, new)
    assert result.code == 0, result.stdout + result.stderr
    moved = {("lib/new.py" if path == "src/app.py" else path, key): value
             for (path, key), value in before.items()}
    assert _marks(sc) == moved and ("lib/new.py", "a3( x )") in moved


@pytest.mark.process
def test_a_move_from_a_subdirectory_reads_its_paths_from_there(make_repo):
    """From lib/, `app.py` is lib/app.py, which holds no mark: refused, file untouched."""
    sc = _seeded(make_repo)
    before = sc.marks_text()
    result = drive.Driver(sc.root / "lib").run("ratchet", "move", "app.py", "moved.py")
    assert result.code == 3 and "lib/app.py" in result.stderr, result.stderr
    assert sc.marks_text() == before


# --- a crapkit root below the git top -------------------------------------------------------------------

def _nested_touched(make_repo) -> vw.Scenario:
    sc = vw.Scenario.build(make_repo, WORLD, root="app")
    assert sc.run("coverage").code == 0
    sc.set(sc.world.with_fn("app", PAST))
    return sc


def _hook(sc) -> int:
    repos.git(sc.top, "add", "-A")
    return sc.run("hook-precommit").code


GATES = {"hook": _hook,
         "rescore": lambda sc: sc.run("rescore", "src/app.py", "--gate").code,
         "verify": lambda sc: sc.run("verify").code}


@pytest.mark.process
@pytest.mark.parametrize("gate", cadence.tiered(sorted(GATES), push={"verify"}))
def test_nested_root_gates(make_repo, gate):
    """a1 touched to ccn 8 under a root one directory below the top: each gate
    refuses it with exit 6, as the model's gates say."""
    sc = _nested_touched(make_repo)
    fired = model.hook_gate(PAST.ccn, vw.TARGET, marked=False) and model.verify_gate(
        model.Row("src/app.py", PAST.long_name, 1, PAST.crap), True, vw.TARGET, None)
    assert fired and GATES[gate](sc) == 6


@pytest.mark.process
def test_the_nested_gate_names_root_relative_rows_and_nothing_above(make_repo):
    """docs/lanes.md, The gate gates below the top: the row's path is
    root-relative, and a staged file above the crapkit root is outside the diff
    by design and is not named."""
    sc = _nested_touched(make_repo)
    (sc.top / "README.md").write_text("above the root\n", encoding="utf-8")
    repos.git(sc.top, "add", "-A")
    result = sc.run("hook-precommit")
    output = result.stdout + result.stderr
    assert result.code == 6 and "src/app.py" in output, output
    assert "app/src/app.py" not in output and "README.md" not in output, output


# --- arguments at the boundary ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def measured(repo_templates, tmp_path_factory):
    top = tmp_path_factory.mktemp("layouts") / "repo"
    sc = vw.Scenario(repo_templates.copy(vw.spec(WORLD), top), WORLD)
    assert sc.run("coverage").code == 0
    return sc


TOP_BELOW_ONE = [("next-item", "--top", "0", "--claim"), ("duplication", "--top", "0"),
                 ("coupling", "--top", "-1"), ("worklist", "--top", "0")]


@pytest.mark.process
@pytest.mark.parametrize("args", cadence.tiered(TOP_BELOW_ONE, push=TOP_BELOW_ONE[:1],
                                                ids=[" ".join(a[:3]) for a in TOP_BELOW_ONE]))
def test_boundary_arguments_refuse(measured, args):
    """README, Commands: --top is a count of rows, so below 1 there is nothing
    to hand out. The command refuses, naming the bound, and claims nothing."""
    result = measured.run(*args)
    assert result.code != 0 and "top must be >= 1" in result.stderr, result.stdout + result.stderr
    assert measured.json("claims")["claims"] == []


@pytest.mark.process
@pytest.mark.parametrize("command", cadence.tiered(["worklist", "next-item"], push={"worklist"}))
def test_unknown_scope_refuses(measured, command):
    """README: an --scope no [[scope]] declares is a configuration error, exit
    3, naming the declared scopes."""
    result = measured.run(command, "--scope", "nope")
    assert result.code == 3 and "app" in result.stderr and "lib" in result.stderr, result.stderr


@pytest.mark.process
def test_an_absolute_path_inside_the_root_gates_as_its_relative_spelling(make_repo):
    """rescore --gate on the absolute path of a touched file exits 6, as the
    relative spelling does; a path outside the root is refused."""
    sc = _seeded(make_repo)
    sc.set(sc.world.with_fn("app", PAST))
    relative = sc.run("rescore", "src/app.py", "--gate")
    absolute = sc.run("rescore", str(sc.root / "src" / "app.py"), "--gate")
    assert (relative.code, absolute.code) == (6, 6)
    assert absolute.stdout == relative.stdout
    outside = sc.run("rescore", str(sc.top.parent / "outside.py"), "--gate")
    assert outside.code == 3 and "outside" in outside.stderr, outside.stderr


@pytest.mark.process
def test_a_scope_with_no_template_is_named(measured):
    """README exit 3: a test-scoped file under a scope with no template. The
    ./ spelling reaches the same scope as the plain one."""
    plain, dotted = measured.run("test-scoped", "src/app.py"), measured.run("test-scoped", "./src/app.py")
    assert (plain.code, dotted.code) == (3, 3)
    assert "'app'" in plain.stderr and plain.stderr == dotted.stderr

