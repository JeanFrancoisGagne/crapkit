"""The hook's override grant keeps the marks file's recorded metric stamp.

The grant synthesizes its numbers from ccn alone and compares no mark, so it
has nothing to say about which metric recorded the marks already there. It used
to restamp the whole file with the running metric: marks recorded under
analysis 7 then carried the analysis 10 stamp, and the next verify, which had
refused them, compared them instead. A stale file now stays stale, and verify
keeps refusing it until a fresh `coverage` and `ratchet seed` re-baseline the marks.
"""
from contextlib import closing

from cli_inproc_repo import add_knotty, git, repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.ratchet import metric_version, read_stamp
from crapkit.store import SnapshotStore

MARKS = "crapkit-ratchet.tsv"
STALE = "crapkit-analysis=7 lizard=1.17.10"


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


def test_a_hook_grant_that_creates_the_marks_file_stamps_the_metric_it_ran_under(
        repo, capsys, monkeypatch):
    """No marks were recorded, so the only numbers in the new file are the
    grant's own, scored by this crapkit."""
    assert read_stamp(grant_knotty(repo, capsys, monkeypatch)) == metric_version()


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
