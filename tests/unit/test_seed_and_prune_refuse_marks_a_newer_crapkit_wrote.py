"""seed and prune leave alone marks a newer crapkit wrote.

A teammate on the newer release re-seeded and committed the marks. On an older
release, `ratchet seed` restamped the whole file under its own analysis version,
so every upgraded teammate's verify then refused the marks, and `ratchet prune`
dropped the marks whose functions the older reader names differently as code
that was gone. Both now refuse before writing and send the reader to the upgrade.
"""
import pytest
from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

import lizard

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version, read_stamp, stamp_text

MARKS = "crapkit-ratchet.tsv"


def run(repo, capsys, *argv: str) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def measured_with_marks(repo, capsys, analysis: int) -> str:
    """A coverage run this crapkit measured, and a marks file stamped `analysis`."""
    seed_artifacts(repo)
    assert run(repo, capsys, "coverage", "--reuse-artifacts")[0] == 0
    text = dump_ratchet([RatchetEntry("src/app.ts", "gone( x )", 90.0)],
                        stamp=stamp_text(analysis, lizard.version), key_version=1)
    (repo / MARKS).write_text(text, encoding="utf-8", newline="\n")
    return text


@pytest.mark.parametrize("action", ["seed", "prune"])
def test_marks_a_newer_crapkit_wrote_are_refused_and_left_as_they_are(repo, capsys, action):
    text = measured_with_marks(repo, capsys, ANALYSIS_VERSION + 1)

    code, out, err = run(repo, capsys, "ratchet", action)

    assert code == 3, out + err
    assert f"ratchet {action} refused" in err, err
    assert "a newer crapkit" in err and "upgrade" in err, err
    assert (repo / MARKS).read_text(encoding="utf-8") == text


def test_marks_an_older_crapkit_wrote_still_reseed(repo, capsys):
    measured_with_marks(repo, capsys, ANALYSIS_VERSION - 1)

    code, _, err = run(repo, capsys, "ratchet", "seed")

    assert code == 0, err
    assert read_stamp((repo / MARKS).read_text(encoding="utf-8")) == metric_version()


def test_a_plain_seed_pinned_to_an_older_run_refuses_and_names_the_newer_one(repo, capsys):
    """This crapkit wrote the marks, and a failed verify pins seed to a run an
    older release measured: seeding it restamped the marks backwards, so verify
    refused them again. The refusal names the run to read instead."""
    from pinned_store import OLDER, failed_verify, fresh_run, twin, write_run

    write_run(repo, [twin(1), twin(2)], versions=OLDER)
    failed_verify(repo)
    fresh = fresh_run(repo)
    text = dump_ratchet([], stamp=metric_version(), key_version=1)
    (repo / MARKS).write_text(text, encoding="utf-8", newline="\n")
    capsys.readouterr()

    code, _, err = run(repo, capsys, "ratchet", "seed")

    assert code == 3, err
    assert f"pass `--baseline {fresh}` to read run {fresh}" in err, err
    assert (repo / MARKS).read_text(encoding="utf-8") == text
