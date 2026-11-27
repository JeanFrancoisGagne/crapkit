"""`ratchet seed --scope NAME` repairs one scope, and seed signs per-scope stamps.

A two-scope repo: api (python, measured by coveragepy) and web (typescript,
measured by istanbul). The marks file is per scope; each test moves web's
recorded stamp away from the rules this crapkit runs, stores a coverage run
that recorded every scope's stamp, and seeds. The scoped seed rewrites web's
marks and line only; api's line and marks keep their bytes. Whether a web mark
may rise is `ratchet.SEED_RAISES`'s call: only the coverage flip lets it.
"""
from __future__ import annotations

import argparse
import codecs
import re
from pathlib import Path

import pytest

from crapkit import invocation, ratchet
from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.cli.verifying import verify_stamp_conflict
from crapkit.config import load_config_text
from crapkit.errors import ConfigError
from crapkit.invocation import _self
from crapkit.ratchet import (RatchetEntry, ScopeStamp, ScopeStamps, dump_ratchet, read_stamp,
                             running_stamp)
from crapkit.ratchetfile import RatchetFile
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from stamps import single_number

ROOT = Path(__file__).resolve().parents[2]
SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
MARKS = "crapkit-ratchet.tsv"


def _scope(name: str, language: str, extra: str = "") -> str:
    return f'[[scope]]\nname = "{name}"\npaths = ["{name}"]\nlanguages = ["{language}"]\n{extra}\n'


def _lane(name: str, parser: str, scope: str) -> str:
    return (f'[[lane]]\nname = "{name}"\ncommand = "python -c pass"\n'
            f'artifact = "coverage/{name}.json"\nparser = "{parser}"\nscopes = ["{scope}"]\n\n')


CONFIG = ("[crapkit]\ntarget = 6\n\n" + _scope("api", "python") + _scope("web", "typescript")
          + _lane("py", "coveragepy", "api") + _lane("ui", "istanbul", "web"))
# web scored cc-only: no lane measures it, coverage_optional asks for none.
CC_ONLY = ("[crapkit]\ntarget = 6\n\n" + _scope("api", "python")
           + _scope("web", "typescript", "coverage_optional = true\n") + _lane("py", "coveragepy", "api"))

API = RatchetEntry("api/a.py", "hot( n )", 72.0)
WEB = RatchetEntry("web/ui.ts", "render", 40.0)


def row(entry: RatchetEntry, crap: float, ccn: int = 8, occurrence: int = 0) -> ScoredRow:
    scope = entry.path.split("/")[0]
    return ScoredRow(scope, entry.path, entry.long_name, 1, 9, ccn, ccn, ccn, 5, 1, 1, 0.5,
                     "measured", crap, "decompose", 0, occurrence)


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / ".crapkit").mkdir()
    return tmp_path


def configure(repo, config: str = CONFIG):
    (repo / "crapkit.toml").write_text(config, encoding="utf-8")
    return load_config_text(config, root=repo)


def running(cfg) -> ScopeStamps:
    return running_stamp(cfg)


def moved(stamp: ScopeStamps, scope: str, **changes) -> ScopeStamps:
    """`stamp` with one scope's ScopeStamp fields replaced."""
    scopes = dict(stamp.scopes)
    scopes[scope] = scopes[scope]._replace(**changes)
    return ScopeStamps(stamp.lizard, scopes)


def older_typescript(stamp: ScopeStamps) -> ScopeStamps:
    return moved(stamp, "web", analysis={"typescript": stamp.scopes["web"].analysis["typescript"] - 1})


def write_marks(repo, entries, stamp, **dump) -> None:
    (repo / MARKS).write_text(dump_ratchet(entries, stamp=stamp, key_version=1, file_name=MARKS),
                              encoding="utf-8", newline="\n", **dump)


def store_run(repo, rows, stamps: ScopeStamps | None) -> int:
    versions = {"lizard": stamps.lizard if stamps else "1.0.0", "analysis_version": "13"}
    if stamps is not None:
        versions["scope_stamps"] = {name: stamp._asdict() for name, stamp in stamps.scopes.items()}
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit=SHA, tool_versions=versions, rows=rows, kind="coverage",
                             lanes={"py": {}, "ui": {}})
    store._conn.close()
    return run_id


def seed(repo, scope: str | None = None, action: str = "seed", baseline: int | None = None) -> int:
    return cmd_ratchet(argparse.Namespace(action=action, repo=str(repo), baseline=baseline, scope=scope,
                                          files=[], json=False, enforce=False))


def lines(repo) -> list[str]:
    return (repo / MARKS).read_text(encoding="utf-8").splitlines()


def scope_line(repo, name: str) -> str:
    return next(line for line in lines(repo) if line.startswith(f"#scope\t{name}\t"))


def marks_of(repo, prefix: str) -> list[str]:
    return [line for line in lines(repo) if line.startswith(prefix)]


def passes(repo, cfg) -> bool:
    return verify_stamp_conflict(RatchetFile.read(repo / MARKS), cfg) is None


# --- one scope ---------------------------------------------------------------------

def test_a_scoped_seed_changes_only_its_scope_and_verify_then_passes(repo, capsys):
    """The typescript raise moved no web function up: web tightens, api keeps its bytes."""
    cfg = configure(repo)
    write_marks(repo, [API, WEB], older_typescript(running(cfg)))
    api_line, api_marks = scope_line(repo, "api"), marks_of(repo, "api/")
    assert not passes(repo, cfg)
    run_id = store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))

    assert seed(repo, "web") == 0

    assert scope_line(repo, "api") == api_line and marks_of(repo, "api/") == api_marks
    assert scope_line(repo, "web") == f"#scope\tweb\t{running(cfg).scopes['web'].render()}"
    assert marks_of(repo, "web/") == ["web/ui.ts\trender\t30.0000"]
    assert passes(repo, cfg)
    assert capsys.readouterr().out.startswith(
        f"{MARKS}: scope web: added 0, tightened 1 - 2 mark(s) vs run {run_id} ({SHA[:11]})\n")


def answer(monkeypatch, name: str) -> None:
    """Patch `SEED_RAISES` to one of the three answers; "shipped" keeps the shipped table."""
    if name != "shipped":
        monkeypatch.setattr(ratchet, "SEED_RAISES", dict.fromkeys(ratchet.SEED_RAISES, name == "any-move"))


def test_seed_raises_lets_marks_rise_on_the_coverage_flip_only():
    assert ratchet.SEED_RAISES == {"coverage flip": True, "coverage": False, "analysis": False,
                                   "reader": False, "lizard": False, "unstamped": False}


@pytest.mark.parametrize("table, mark, said", [
    ("shipped", "40.0000", ""),
    ("any-move", "55.0000", ", raised 1 (first web/ui.ts render 40.0000 -> 55.0000)"),
    ("never", "40.0000", ""),
], ids=["shipped", "any-move", "never"])
def test_an_analysis_raise_lets_a_mark_rise_only_where_the_table_says(repo, capsys, monkeypatch,
                                                                     table, mark, said):
    """Under the shipped table the mark keeps 40 while the run scores 55, so verify fails that
    function until `verify --override` records the rise."""
    answer(monkeypatch, table)
    cfg = configure(repo)
    write_marks(repo, [API, WEB], older_typescript(running(cfg)))
    store_run(repo, [row(API, 50.0), row(WEB, 55.0)], running(cfg))

    assert seed(repo, "web") == 0

    assert marks_of(repo, "web/") == [f"web/ui.ts\trender\t{mark}"]
    assert marks_of(repo, "api/") == ["api/a.py\thot( n )\t72.0000"]
    assert passes(repo, cfg)
    assert capsys.readouterr().out.startswith(f"{MARKS}: scope web: added 0, tightened 0{said} - ")


@pytest.mark.parametrize("table, mark", [("shipped", "55.0000"), ("any-move", "55.0000"), ("never", "40.0000")],
                         ids=["shipped", "any-move", "never"])
def test_the_coverage_flip_lets_the_scope_s_marks_take_the_run_s_values(repo, monkeypatch, capsys,
                                                                      table, mark):
    """web was cc-only and a lane measures it now; api's line and marks keep their bytes."""
    answer(monkeypatch, table)
    flipped = running(configure(repo, CC_ONLY))
    cfg = configure(repo)
    write_marks(repo, [API, WEB], moved(running(cfg), "web", coverage=flipped.scopes["web"].coverage))
    api_line = scope_line(repo, "api")
    store_run(repo, [row(API, 50.0), row(WEB, 55.0)], running(cfg))

    assert seed(repo, "web") == 0

    assert marks_of(repo, "web/") == [f"web/ui.ts\trender\t{mark}"]
    assert scope_line(repo, "api") == api_line and marks_of(repo, "api/") == ["api/a.py\thot( n )\t72.0000"]
    assert passes(repo, cfg)
    raised = ", raised 1 (first web/ui.ts render 40.0000 -> 55.0000)" if mark == "55.0000" else ""
    assert capsys.readouterr().out.startswith(f"{MARKS}: scope web: added 0, tightened 0{raised} - ")


# Two coverage moves that are not the flip: a coveragepy lane joins web's istanbul,
# and web drops coverage_optional with no lane, cc-only to no-lane.
TWO_READERS = CONFIG + _lane("uipy", "coveragepy", "web")
NO_LANE = ("[crapkit]\ntarget = 6\n\n" + _scope("api", "python") + _scope("web", "typescript")
           + _lane("py", "coveragepy", "api"))


@pytest.mark.parametrize("before, after", [(CONFIG, TWO_READERS), (CC_ONLY, NO_LANE)],
                         ids=["reader-set", "cc-only-to-no-lane"])
def test_a_coverage_move_other_than_the_flip_raises_no_mark(repo, capsys, before, after):
    """Only cc-only or no-lane to a reader is the flip; any other coverage move keeps marks only falling."""
    recorded = running(configure(repo, before)).scopes["web"].coverage
    cfg = configure(repo, after)
    assert recorded != running(cfg).scopes["web"].coverage
    write_marks(repo, [API, WEB], moved(running(cfg), "web", coverage=recorded))
    store_run(repo, [row(API, 50.0), row(WEB, 55.0)], running(cfg))

    assert seed(repo, "web") == 0

    assert marks_of(repo, "web/") == ["web/ui.ts\trender\t40.0000"]
    assert passes(repo, cfg)
    assert capsys.readouterr().out.startswith(f"{MARKS}: scope web: added 0, tightened 0 - ")


def test_a_scoped_seed_with_a_baseline_reads_that_run(repo):
    cfg = configure(repo)
    write_marks(repo, [API, WEB], older_typescript(running(cfg)))
    named = store_run(repo, [row(API, 50.0), row(WEB, 31.0)], running(cfg))
    store_run(repo, [row(API, 50.0), row(WEB, 33.0)], running(cfg))

    assert seed(repo, "web", baseline=named) == 0

    assert marks_of(repo, "web/") == ["web/ui.ts\trender\t31.0000"]


# --- the plain seed ------------------------------------------------------------------

def test_a_plain_seed_on_a_single_number_file_writes_every_scope_s_stamp(repo, capsys):
    cfg = configure(repo)
    write_marks(repo, [API, WEB], single_number(13, running(cfg).lizard))
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))

    assert seed(repo) == 0

    assert read_stamp((repo / MARKS).read_text(encoding="utf-8")) == running(cfg)
    assert passes(repo, cfg)
    assert "warning" not in capsys.readouterr().err


def test_a_plain_seed_re_signs_every_scope_and_drops_a_scope_the_config_lost(repo):
    cfg = configure(repo)
    old = older_typescript(running(cfg))
    gone = ScopeStamps(old.lizard, {**old.scopes, "legacy": ScopeStamp({"python": 1}, "cc-only")})
    write_marks(repo, [API, WEB], gone)
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))

    assert seed(repo) == 0

    assert read_stamp((repo / MARKS).read_text(encoding="utf-8")) == running(cfg)
    assert marks_of(repo, "api/") == ["api/a.py\thot( n )\t50.0000"]


def test_a_plain_seed_signs_no_line_for_a_scope_only_the_run_recorded(repo):
    """The run recorded legacy, a scope crapkit.toml no longer declares: the file gets no line for it."""
    cfg = configure(repo)
    now = running(cfg)
    write_marks(repo, [API, WEB], older_typescript(now))
    recorded = ScopeStamps(now.lizard, {**now.scopes, "legacy": ScopeStamp({"python": 13}, "cc-only")})
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], recorded)

    assert seed(repo) == 0

    assert [line.split("\t")[1] for line in marks_of(repo, "#scope")] == ["api", "web"]
    assert read_stamp((repo / MARKS).read_text(encoding="utf-8")) == now


# --- variations ------------------------------------------------------------------------

def test_a_scoped_seed_keeps_line_1_as_the_file_wrote_it(repo):
    """Only the scope's own line changes. A marks file renamed by hand still
    names its old name on line 1, and the scoped seed leaves that line alone."""
    cfg = configure(repo)
    text = dump_ratchet([API, WEB], stamp=older_typescript(running(cfg)), key_version=1, file_name="marks.tsv")
    (repo / MARKS).write_text(text, encoding="utf-8", newline="\n")
    head = lines(repo)[0]
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))

    assert seed(repo, "web") == 0

    assert lines(repo)[0] == head and ": marks.tsv " in head


def test_a_scope_with_no_marks_takes_only_its_stamp_line(repo):
    cfg = configure(repo)
    write_marks(repo, [API], older_typescript(running(cfg)))
    before = lines(repo)
    store_run(repo, [row(API, 50.0)], running(cfg))

    assert seed(repo, "web") == 0

    changed = [(was, now) for was, now in zip(before, lines(repo)) if was != now]
    assert changed == [(before[2 + 1], f"#scope\tweb\t{running(cfg).scopes['web'].render()}")]


def test_a_renamed_scope_keeps_the_old_name_s_line_until_a_plain_seed(repo):
    """The `unstamped` kind: crapkit.toml renamed scope ui to web."""
    cfg = configure(repo)
    now = running(cfg)
    renamed = ScopeStamps(now.lizard, {"api": now.scopes["api"], "ui": now.scopes["web"]})
    write_marks(repo, [API, WEB], renamed)
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], now)

    assert seed(repo, "web") == 0
    assert [line.split("\t")[1] for line in marks_of(repo, "#scope")] == ["api", "ui", "web"]

    assert seed(repo) == 0
    assert [line.split("\t")[1] for line in marks_of(repo, "#scope")] == ["api", "web"]


def test_a_utf16_crlf_file_keeps_its_encoding_endings_and_key_format(repo):
    cfg = configure(repo)
    text = dump_ratchet([API, WEB], stamp=older_typescript(running(cfg)), key_version=1, file_name=MARKS)
    (repo / MARKS).write_bytes(codecs.BOM_UTF16_LE + text.replace("\n", "\r\n").encode("utf-16-le"))
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))

    assert seed(repo, "web") == 0

    data = (repo / MARKS).read_bytes()
    assert data.startswith(codecs.BOM_UTF16_LE)
    written = data[2:].decode("utf-16-le")
    assert "\r\n" in written and "\n" not in written.replace("\r\n", "")
    assert "# crapkit-keys=1\r\n" in written and "web/ui.ts\trender\t30.0000" in written


def test_a_twin_inside_the_scope_is_reseeded_under_its_own_key_and_one_outside_is_not(repo):
    cfg = configure(repo)
    web_twin, api_twin = RatchetEntry("web/ui.ts", "cb#2", 40.0), RatchetEntry("api/a.py", "cb#2", 40.0)
    marks = [RatchetEntry("web/ui.ts", "cb", 40.0), web_twin, RatchetEntry("api/a.py", "cb", 40.0), api_twin]
    write_marks(repo, marks, older_typescript(running(cfg)))
    rows = [row(RatchetEntry(m.path, "cb", 0), crap, occurrence=n)
            for m, crap, n in ((marks[0], 35.0, 1), (web_twin, 30.0, 2), (marks[2], 35.0, 1), (api_twin, 30.0, 2))]
    store_run(repo, rows, running(cfg))

    assert seed(repo, "web") == 0

    assert marks_of(repo, "web/") == ["web/ui.ts\tcb\t35.0000", "web/ui.ts\tcb#2\t30.0000"]
    assert marks_of(repo, "api/") == ["api/a.py\tcb\t40.0000", "api/a.py\tcb#2\t40.0000"]


# --- refusals ----------------------------------------------------------------------------

def test_an_unknown_scope_is_refused_naming_the_configured_ones(repo):
    configure(repo)
    with pytest.raises(ConfigError, match="declares no scope 'ui'; its scopes are api, web"):
        seed(repo, "ui")


@pytest.mark.parametrize("action", ["prune", "merge", "move", "report"])
def test_scope_is_refused_by_every_action_but_seed(repo, action):
    with pytest.raises(ConfigError, match=f"ratchet {action} signs no scope's stamp, so it takes no --scope"):
        seed(repo, "web", action=action)


@pytest.mark.parametrize("stamps, scope, said", [
    (None, None, "per scope"),
    (None, "web", "for scope web"),
    ("api only", "web", "for scope web"),
], ids=["none-plain", "none-scoped", "not-for-name"])
def test_a_run_with_no_stamp_for_the_scope_is_refused_naming_coverage(repo, stamps, scope, said):
    cfg = configure(repo)
    write_marks(repo, [API, WEB], running(cfg))
    before = (repo / MARKS).read_bytes()
    recorded = None if stamps is None else ScopeStamps(running(cfg).lizard, {"api": running(cfg).scopes["api"]})
    run_id = store_run(repo, [row(API, 50.0)], recorded)

    with pytest.raises(ConfigError, match=rf"run {run_id} recorded no metric {said} .* run `{re.escape(_self())} coverage`"):
        seed(repo, scope)

    assert (repo / MARKS).read_bytes() == before


def test_a_run_that_measured_the_scope_under_other_rules_is_refused(repo):
    cfg = configure(repo)
    write_marks(repo, [API, WEB], older_typescript(running(cfg)))
    before = (repo / MARKS).read_bytes()
    old_run = store_run(repo, [row(API, 50.0), row(WEB, 30.0)], older_typescript(running(cfg)))

    with pytest.raises(ConfigError, match=rf"seed --scope web refused: run {old_run} measured scope web "
                                          rf"under \[.*\], not this crapkit's \[.*\].* `{re.escape(_self())} coverage`"):
        seed(repo, "web")

    assert (repo / MARKS).read_bytes() == before


def one_patch_up(version: str) -> str:
    *head, patch = version.split(".")
    return ".".join([*head, str(int(patch) + 1)])


# A recorded stamp whose lizard this crapkit does not run, in each form a marks file carries.
# The last is what a 0.8.1 install that pulled a newer lizard seeded (docs/upgrading.md).
LIZARD_MOVED = {
    "per-scope": lambda now: ScopeStamps("1.17.10", now.scopes),
    "per-scope-and-web-analysis": lambda now: ScopeStamps("1.17.10", older_typescript(now).scopes),
    "single-number": lambda now: single_number(11, "1.17.10"),
    "single-number-under-a-newer-lizard": lambda now: single_number(13, one_patch_up(now.lizard)),
}
RECORDED = {**LIZARD_MOVED, "web-analysis-only": older_typescript}


@pytest.mark.parametrize("recorded", list(RECORDED))
def test_running_each_seed_verify_names_clears_its_refusal(repo, recorded):
    """One lizard version signs every scope and a scoped seed keeps the file's,
    so a moved lizard must send the user to the plain seed, or the remedy never ends."""
    cfg = configure(repo)
    write_marks(repo, [API, WEB], RECORDED[recorded](running(cfg)))
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))
    refusal = verify_stamp_conflict(RatchetFile.read(repo / MARKS), cfg).refusal(MARKS)

    for flags in re.findall(r"ratchet seed([^`]*)`", refusal):
        named = re.search(r"--scope (\S+)", flags)
        assert seed(repo, named and named.group(1)) == 0, refusal

    assert passes(repo, cfg), refusal


def test_a_moved_lizard_names_the_plain_seed_and_a_moved_language_its_scope():
    lizard = ratchet.stamp_conflict(ScopeStamps("1.17.10", RUN.scopes), RUN, frozenset({"api", "web"}))
    language = ratchet.stamp_conflict(ScopeStamps(RUN.lizard, older_typescript(RUN).scopes), RUN,
                                      frozenset({"api", "web"}))

    assert lizard.seeds(" --baseline 7") == f"`{_self()} ratchet seed --baseline 7`"
    assert language.seeds(" --baseline 7") == f"`{_self()} ratchet seed --scope web --baseline 7`"


@pytest.mark.parametrize("recorded", list(LIZARD_MOVED))
@pytest.mark.parametrize("named", [False, True], ids=["plain", "baseline"])
def test_a_scoped_seed_refuses_a_file_whose_lizard_moved_and_names_the_plain_seed(repo, recorded, named):
    cfg = configure(repo)
    stamp = LIZARD_MOVED[recorded](running(cfg))
    write_marks(repo, [API, WEB], stamp)
    before = (repo / MARKS).read_bytes()
    run_id = store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))
    baseline = f" --baseline {run_id}" if named else ""

    with pytest.raises(ConfigError) as refused:
        seed(repo, "web", baseline=run_id if named else None)

    assert str(refused.value) == (
        f"ratchet seed --scope web refused: {MARKS} records lizard {stamp.lizard} and this crapkit runs "
        f"lizard {running(cfg).lizard}; one lizard version signs every scope and a scoped seed keeps the "
        f"file's, so verify would refuse the marks again; run `{_self()} ratchet seed{baseline}`, which "
        "signs every scope")
    assert (repo / MARKS).read_bytes() == before


def test_the_ratchet_page_quotes_the_lizard_refusal(repo, monkeypatch):
    """docs/ratchet.md quotes it for a 0.8.1 file stamped lizard 1.24.3, under a crapkit that runs 1.24.0."""
    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)  # the page runs `$ crapkit`
    cfg = configure(repo)
    write_marks(repo, [API, WEB], single_number(13, one_patch_up(running(cfg).lizard)))
    store_run(repo, [row(API, 50.0), row(WEB, 30.0)], running(cfg))

    with pytest.raises(ConfigError) as refused:
        seed(repo, "web")

    said = re.sub(r"records lizard \S+ and this crapkit runs lizard \S+;",
                  "records lizard 1.24.3 and this crapkit runs lizard 1.24.0;", str(refused.value))
    assert f"crapkit: {said}" in (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8"), said


def test_the_single_number_stamp_text_still_reads():
    """A plain seed's input may still be the single-number form 0.8.x wrote."""
    assert read_stamp("# crapkit-analysis=13 lizard=1.24.0\n") == "crapkit-analysis=13 lizard=1.24.0"


# --- the stamp and marks a scoped seed writes, at ratchet's seam --------------------------

RUN = ScopeStamps("1.24.0", {"api": ScopeStamp({"python": 13}, {"coveragepy": 1}),
                             "web": ScopeStamp({"typescript": 13}, {"istanbul": 1})})


def test_a_scoped_seed_on_a_single_number_file_reads_each_other_scope_at_its_one_number():
    """api keeps what the 0.8.x file recorded for it: its number for python and
    the mode 0.8.x read its lane under; web takes the run's line, and the file's lizard stays."""
    signed = ratchet.rescoped(single_number(11, "1.17.10"), RUN, "web")

    assert signed == ScopeStamps("1.17.10", {"api": ScopeStamp({"python": 11}, {"coveragepy": 1}),
                                             "web": RUN.scopes["web"]})


def test_a_scoped_seed_on_an_unstamped_file_writes_only_its_scope_under_the_run_s_lizard():
    assert ratchet.rescoped(ratchet.MetricStamp.NONE, RUN, "web") == ScopeStamps(
        "1.24.0", {"web": RUN.scopes["web"]})


def test_a_rising_scope_adds_a_new_mark_raises_a_lower_one_and_lowers_a_higher_one():
    """Where `raises` admits the path, each over-ceiling row takes the run's
    value: a mark the file lacks is added, and a kept one moves up or down to it."""
    fresh = [row(RatchetEntry("web/a.ts", "new", 0), 20.0), row(WEB, 55.0),
             row(RatchetEntry("web/b.ts", "lower", 0), 12.0)]
    prior = [WEB, RatchetEntry("web/b.ts", "lower", 30.0)]

    seeded, added, tightened = ratchet.seed_ratchet(prior, fresh, target=6, raises=lambda path: True)

    assert (added, tightened) == (1, 1)
    assert {entry.long_name: entry.crap for entry in seeded} == {"new": 20.0, "render": 55.0, "lower": 12.0}


def test_a_raised_mark_is_the_run_s_crap_at_four_decimals_and_an_equal_one_tightens_nothing():
    """Accuracy check 1: a raised mark equals the run's CRAP rounded to 4 places.
    A mark the run scores at exactly its value stays and counts no tighten."""
    same = RatchetEntry("web/c.ts", "same", 25.0)
    fresh = [row(WEB, 55.123456), row(same, 25.0)]

    seeded, added, tightened = ratchet.seed_ratchet([WEB, same], fresh, target=6, raises=lambda path: True)

    assert (added, tightened) == (0, 0)
    assert {entry.long_name: entry.crap for entry in seeded} == {"render": 55.1235, "same": 25.0}
