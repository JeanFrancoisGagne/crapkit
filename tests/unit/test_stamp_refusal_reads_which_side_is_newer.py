"""A stamp refusal says which side is newer before it names a remedy.

One teammate upgrades crapkit, re-seeds and commits the marks. Everyone still on
the older release, the Action pinned one tag behind included, gets verify's stamp
refusal, and it told them to run coverage and re-seed. That seed restamps the
team's marks under the older analysis, and every upgraded teammate's verify then
refuses them. Marks a newer crapkit wrote are fixed by upgrading, never by
re-seeding. The merge driver's refusal had the same blind spot: "re-baseline one
side" under the older crapkit left the stamps apart.

A newer lizard alone is no newer release. crapkit pins lizard exactly, so marks a
newer lizard stamped come from a release that let lizard float (0.8.1 and
earlier), and the upgrade the refusal named was one no pinned crapkit can make.
Those marks take coverage, prune and seed, which move them to the pinned lizard.
"""
import argparse
import json
import sys

import pytest
from cli_inproc_repo import commit_all, repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main
from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.errors import ConfigError
from crapkit.ratchet import (RatchetEntry, coverage_then_seed, dump_ratchet, metric_version,
                             read_stamp, stamp_conflict, stamp_text)

import lizard

OLD = "crapkit-analysis=11 lizard=1.24.0"
NEW = "crapkit-analysis=12 lizard=1.24.0"
RUNNING = "crapkit-analysis=13 lizard=1.24.0"
MARKS = "crapkit-ratchet.tsv"


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/crapkit", "verify"])


def test_marks_a_newer_crapkit_wrote_are_named_as_newer_and_sent_to_an_upgrade():
    refusal = stamp_conflict(NEW, OLD)

    assert refusal.startswith(f"ratchet marks were recorded under [{NEW}] but this run "
                              f"measures [{OLD}]"), refusal
    assert "a newer crapkit" in refusal, refusal
    assert "upgrade" in refusal, refusal
    assert "ratchet seed" not in refusal, refusal


@pytest.mark.parametrize("recorded", [
    pytest.param("crapkit-analysis=10 lizard=1.24.3", id="older-analysis-newer-lizard"),
    pytest.param("crapkit-analysis=13 lizard=1.24.3", id="same-analysis-newer-lizard"),
    pytest.param("crapkit-analysis=13 lizard=1.17.10", id="older-lizard"),
])
def test_marks_a_newer_or_older_lizard_stamped_take_coverage_prune_and_seed(recorded):
    """The exact pin forbids the lizard upgrade, so the reseed is the remedy."""
    refusal = stamp_conflict(recorded, RUNNING)

    assert refusal.endswith(coverage_then_seed()), refusal
    assert "upgrade" not in refusal and "newer lizard" not in refusal, refusal


@pytest.mark.parametrize("recorded", [
    pytest.param("crapkit-analysis=14 lizard=1.24.0", id="newer-analysis-same-lizard"),
    pytest.param("crapkit-analysis=14 lizard=1.24.3", id="newer-analysis-newer-lizard"),
])
def test_marks_a_newer_analysis_stamped_name_a_crapkit_upgrade_at_every_pin(recorded):
    refusal = stamp_conflict(recorded, RUNNING)

    assert "the marks come from a newer crapkit than this install" in refusal, refusal
    assert "(the CLI, the Action's `uses:` pin and the pre-commit `rev` alike)" in refusal, refusal
    assert "newer lizard" not in refusal and "ratchet seed" not in refusal, refusal


def test_version_parts_compare_as_numbers_not_as_text():
    """1.9.0 is older than 1.24.0, though "9" sorts after "2"."""
    refusal = stamp_conflict("crapkit-analysis=11 lizard=1.9.0", OLD)

    assert "newer" not in refusal and refusal.endswith("`crapkit ratchet seed`"), refusal


def test_marks_an_older_crapkit_wrote_keep_coverage_then_seed():
    assert stamp_conflict(OLD, NEW).endswith(
        "run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline with "
        "`crapkit ratchet seed`")


def test_a_stamp_it_cannot_read_keeps_coverage_then_seed():
    assert stamp_conflict("lizard 0.0.1 analysis 0", OLD).endswith("`crapkit ratchet seed`")


def test_verify_over_newer_marks_refuses_before_the_lanes_and_leaves_them_alone(repo, capsys):
    newer = stamp_text(ANALYSIS_VERSION + 1, lizard.version)
    text = dump_ratchet([RatchetEntry("src/app.ts", "dispatch( kind : string )", 90.0)],
                        stamp=newer)
    (repo / "crapkit-ratchet.tsv").write_text(text, encoding="utf-8", newline="\n")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()

    code = main(["verify", "--reuse-artifacts", "--json", "--repo", str(repo)])
    err = capsys.readouterr().err

    assert code == 3, err
    assert f"recorded under [{newer}] but this run measures [{metric_version()}]" in err, err
    assert "newer crapkit" in err and "ratchet seed" not in err, err
    assert (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8") == text


def _one_patch_up(version: str) -> str:
    *head, patch = version.split(".")
    return ".".join([*head, str(int(patch) + 1)])


def _doctor_lines(repo, capsys) -> tuple[int, dict]:
    """doctor's exit, and its FAIL ("problems") and WARN ("warnings") lines that name the marks."""
    code = main(["doctor", "--json", "--repo", str(repo)])
    payload = json.loads(capsys.readouterr().out)
    return code, {kind: [line for line in payload[kind] if MARKS in line]
                  for kind in ("problems", "warnings")}


def test_marks_a_newer_lizard_stamped_clear_with_the_reseed_verify_names(repo, capsys):
    """A crapkit that lets lizard float seeded these marks: verify, doctor,
    prune and seed all sent the user to a lizard upgrade the pin forbids."""
    stamped = stamp_text(ANALYSIS_VERSION, _one_patch_up(lizard.version))
    (repo / MARKS).write_text(
        dump_ratchet([RatchetEntry("src/app.ts", "dispatch( kind : string )", 90.0)],
                     stamp=stamped), encoding="utf-8", newline="\n")
    commit_all(repo, "marks a newer lizard stamped")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()

    refused = main(["verify", "--reuse-artifacts", "--repo", str(repo)])
    err = capsys.readouterr().err
    doctor = _doctor_lines(repo, capsys)

    assert refused == 3, err
    assert (f"recorded under [{stamped}] but this run measures [{metric_version()}] - CRAP "
            f"scores are not comparable across metric versions; {coverage_then_seed()}") in err, err
    assert "newer lizard" not in err, err
    assert doctor[0] == 0 and doctor[1]["problems"] == [], doctor
    assert len(doctor[1]["warnings"]) == 1 and coverage_then_seed() in doctor[1]["warnings"][0], doctor
    for step in (["coverage", "--reuse-artifacts"], ["ratchet", "prune"], ["ratchet", "seed"]):
        assert main([*step, "--repo", str(repo)]) == 0, (step, capsys.readouterr().err)
    assert read_stamp((repo / MARKS).read_text(encoding="utf-8")) == metric_version()
    assert main(["verify", "--reuse-artifacts", "--repo", str(repo)]) == 0, capsys.readouterr().err


def marks(path, stamp: str):
    path.write_text(f"# {stamp}\npath\tlong_name\tcrap\nsrc/a.py\tf( )\t40.0000\n",
                    encoding="utf-8", newline="\n")
    return str(path)


@pytest.mark.parametrize("ours, theirs, newer_side", [(OLD, NEW, "theirs"), (NEW, OLD, "ours")])
def test_the_merge_refusal_names_the_newer_side_and_the_crapkit_that_wrote_it(
        tmp_path, ours, theirs, newer_side):
    files = [marks(tmp_path / "base", OLD), marks(tmp_path / "ours", ours),
             marks(tmp_path / "theirs", theirs)]

    with pytest.raises(ConfigError) as refused:
        cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))

    said = str(refused.value)
    assert said.startswith(f"ratchet merge refused: ours is [{ours}] and theirs is [{theirs}]"), said
    assert f"{newer_side} is newer" in said, said
    assert f"a crapkit that measures [{NEW}]" in said, said
    assert "one side" not in said, said


def test_sides_that_differ_only_in_lizard_name_no_newer_side_and_take_the_reseed(tmp_path):
    ours, theirs = "crapkit-analysis=11 lizard=1.24.3", OLD
    files = [marks(tmp_path / "base", OLD), marks(tmp_path / "ours", ours),
             marks(tmp_path / "theirs", theirs)]

    with pytest.raises(ConfigError) as refused:
        cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))

    said = str(refused.value)
    assert said.startswith(f"ratchet merge refused: ours is [{ours}] and theirs is [{theirs}]"), said
    assert "is newer" not in said, said
    assert said.endswith(coverage_then_seed("re-baseline one side")), said
