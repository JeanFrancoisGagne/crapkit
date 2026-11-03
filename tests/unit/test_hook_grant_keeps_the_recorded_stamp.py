"""The hook's override grant keeps the marks file's recorded metric stamp.

The grant synthesizes its numbers from ccn alone and compares no mark, so it
has nothing to say about which metric recorded the marks already there. It used
to restamp the whole file with the running metric: marks recorded under
analysis 7 then carried the analysis 10 stamp, and the next verify, which had
refused them, compared them instead. A stale file now stays stale, and verify
keeps refusing it until a fresh `coverage` and `ratchet seed` re-baseline the marks.
"""
from contextlib import closing

from cli_inproc_repo import (KNOTTY, TOML, add_knotty, commit_all, git, repo,  # noqa: F401
                             seed_artifacts, template_repo)
from stamps import running_in, scoped

from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, read_stamp
from crapkit.store import SnapshotStore

MARKS = "crapkit-ratchet.tsv"
STALE = "crapkit-analysis=7 lizard=1.17.10"
# The mark crapkit 0.8.1's hook-precommit grant wrote for knotty in the cc-only
# scope of test_a_hook_grant_in_a_cc_only_scope_writes_the_ccn, run on that fixture.
GRANT_081 = "8.0000"


def run(argv, repo, capsys):
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def write_marks(repo, stamp: str) -> None:
    header = f"# {stamp}\n" if stamp else ""
    (repo / MARKS).write_text(f"{header}path\tlong_name\tcrap\n"
                              "src/app.ts\tdispatch ( kind )\t90.0000\n",
                              encoding="utf-8", newline="\n")


def grant_knotty(repo, capsys, monkeypatch) -> str:
    add_knotty(repo)
    git(repo, "add", "src/app.ts")
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", "hotfix, ticket 41")
    code, out, err = run(["hook-precommit"], repo, capsys)
    assert code == 0, out + err
    assert "override granted with full audit" in out
    return (repo / MARKS).read_text(encoding="utf-8")


def test_a_hook_grant_on_a_stale_marks_file_leaves_verify_refusing_it(repo, capsys, monkeypatch):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    write_marks(repo, STALE)

    text = grant_knotty(repo, capsys, monkeypatch)

    assert read_stamp(text) == STALE
    assert "src/app.ts\tdispatch ( kind )\t90.0000" in text, "the old mark is untouched"
    assert "knotty ( n )" in text, "the grant still lands"
    monkeypatch.delenv("CRAPKIT_OVERRIDE_REASON")
    code, _, err = run(["verify", "--reuse-artifacts"], repo, capsys)
    assert code == 3
    assert f"ratchet marks were recorded under [{STALE}]" in err, err


def test_a_hook_grant_leaves_a_file_written_before_stamping_unstamped(repo, capsys, monkeypatch):
    write_marks(repo, "")

    assert read_stamp(grant_knotty(repo, capsys, monkeypatch)) == ""


def test_a_hook_grant_that_creates_the_marks_file_signs_the_running_per_scope_stamp(
        repo, capsys, monkeypatch):
    """No marks were recorded, so the only numbers in the new file are the
    grant's own, scored by this crapkit."""
    assert read_stamp(grant_knotty(repo, capsys, monkeypatch)) == running_in(repo)


def test_a_hook_grant_on_a_per_scope_file_keeps_every_scope_line_verbatim(repo, capsys, monkeypatch):
    """web's line records typescript 12, which this crapkit no longer runs: the
    grant adds no measured number, so that line stays as it was read."""
    running = running_in(repo)
    older = scoped(src=tuple(running.scopes["src"]), web=({"typescript": 12}, {"istanbul": 1}))
    (repo / MARKS).write_text(dump_ratchet([RatchetEntry("src/app.ts", "dispatch ( kind )", 90.0)],
                                           stamp=older, key_version=1, file_name=MARKS),
                              encoding="utf-8", newline="\n")
    before = scope_stamp_lines((repo / MARKS).read_text(encoding="utf-8"))

    text = grant_knotty(repo, capsys, monkeypatch)

    assert scope_stamp_lines(text) == before
    assert "#scope\tweb\tanalysis typescript=12; coverage istanbul=1" in text
    assert "knotty ( n )" in text


def scope_stamp_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


# src measures no coverage: the grant's mark is the function's ccn.
CC_ONLY = TOML.replace('paths = ["src"]\nlanguages = ["typescript"]\n',
                       'paths = ["src"]\nlanguages = ["typescript"]\ncoverage_optional = true\n')
CC_ONLY = CC_ONLY.replace(CC_ONLY[CC_ONLY.index('[[lane]]\nname = "unit"'):CC_ONLY.index('[[lane]]\nname = "ui"')], "")


def knotty_mark(text: str) -> str:
    return next(line.split("\t")[2] for line in text.splitlines() if "\tknotty ( n )\t" in line)


def test_a_hook_grant_in_a_cc_only_scope_writes_the_ccn(repo, capsys, monkeypatch):
    """Three independent checks on the mark the grant writes for knotty, ccn 8.

    1. Stock lizard, run outside crapkit, reports ccn 8, and a hand count agrees:
       one path plus seven `if`s.
    2. `coverage --export` scores knotty's CRAP in this cc-only scope at 8.
    3. crapkit 0.8.1's hook-precommit grant on this fixture wrote 8.0000.
    """
    import lizard

    (repo / "crapkit.toml").write_text(CC_ONLY, encoding="utf-8")
    commit_all(repo, "src is cc-only")
    hand_count = 1 + KNOTTY.count("if (")

    text = grant_knotty(repo, capsys, monkeypatch)

    stock = {f.name: f.cyclomatic_complexity
             for f in lizard.analyze_file.analyze_source_code("app.ts", KNOTTY).function_list}
    assert stock["knotty"] == hand_count == 8
    assert knotty_mark(text) == f"{stock['knotty']:.4f}" == "8.0000"
    seed_artifacts(repo)
    export = repo / ".crapkit" / "export.tsv"
    assert main(["coverage", "--reuse-artifacts", "--export", str(export), "--repo", str(repo)]) == 0
    header, *rows = [line.split("\t") for line in export.read_text(encoding="utf-8").splitlines()]
    scored = {row["long_name"]: row for row in (dict(zip(header, line)) for line in rows)}
    assert f"{float(scored['knotty ( n )']['crap']):.4f}" == knotty_mark(text)
    assert knotty_mark(text) == GRANT_081


# An anonymous callback past the ceiling of 6: seven ifs, ccn 8.
ANON_TS = ("export const out = [1, 2].map((n: number) => {\n"
           + "".join(f"  if (n > {i}) {{ return {i}; }}\n" for i in range(1, 8))
           + "  return 0;\n});\n")


def test_a_hook_grant_for_an_anonymous_callback_under_an_older_reader_grants_nothing(
        repo, capsys, monkeypatch):
    """Expression reader 10 renumbered anonymous callbacks, so an anonymous mark
    under an older analysis stamp has no proof it names the same function. The
    grant keeps the recorded stamp, so its own `(anonymous)` mark would carry
    that older stamp and every later reader, seed included, would refuse the
    file. The grant refuses first: no alert, no override row, no mark."""
    (repo / MARKS).write_text("# crapkit-analysis=9 lizard=1.24.0\n# crapkit-keys=1\n"
                              "path\tlong_name\tcrap\nsrc/app.ts\tdispatch ( kind )\t90.0000\n",
                              encoding="utf-8", newline="\n")
    before = (repo / MARKS).read_bytes()
    (repo / "src" / "anon.ts").write_text(ANON_TS, encoding="utf-8", newline="\n")
    git(repo, "add", "src/anon.ts")
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", "hotfix, ticket 41")

    code, out, err = run(["hook-precommit"], repo, capsys)

    assert code != 0, out + err
    assert "expression reader 10 changed anonymous function ordinals in src/anon.ts" in err, err
    assert (repo / MARKS).read_bytes() == before
    assert not (repo / "alerts.log").exists()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    with closing(store._conn):
        assert all(not store.read_overrides(run["id"]) for run in store.list_runs())
