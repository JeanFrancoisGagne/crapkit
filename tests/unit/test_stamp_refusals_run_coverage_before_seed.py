"""Every stamp refusal names coverage before seed.

Seed stamps the metric of the run it reads. Right after an upgrade the newest
trusted run was measured under the old metric, so the remedy the refusals used
to print, `ratchet seed` alone, kept the old stamp and verify refused again.
The remedy is a fresh coverage run, then the seed.
"""
import argparse
import sys

import pytest

from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.cli.verifying import _guard_ratchet_stamp
from crapkit.errors import ConfigError
from crapkit.ratchet import stamp_conflict
from crapkit.ratchetfile import RatchetFile

OLD = "crapkit-analysis=9 lizard=1.24.0"
NEW = "crapkit-analysis=10 lizard=1.24.0"


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    """The refusals name the invocation the process started with; a console
    script run spells it `crapkit`."""
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/crapkit", "verify"])


def marks(path, stamp: str):
    header = f"# {stamp}\n" if stamp else ""
    path.write_text(f"{header}path\tlong_name\tcrap\nsrc/a.py\tf( )\t40.0000\n",
                    encoding="utf-8", newline="\n")
    return path


def test_the_stamp_refusal_names_coverage_then_seed():
    assert stamp_conflict(OLD, NEW) == (
        f"ratchet marks were recorded under [{OLD}] but this run measures [{NEW}] — CRAP "
        "scores are not comparable across metric versions; run `crapkit coverage`, then "
        "re-baseline with `crapkit ratchet seed`")


def test_the_unstamped_warning_names_coverage_then_seed(tmp_path, capsys):
    saved = RatchetFile.read(marks(tmp_path / "crapkit-ratchet.tsv", ""))

    _guard_ratchet_stamp(saved, "crapkit-ratchet.tsv")

    assert capsys.readouterr().err == (
        "warning: crapkit-ratchet.tsv carries no metric stamp (written before stamping) — "
        "run `crapkit coverage`, then re-baseline with `crapkit ratchet seed` to stamp it\n")


def test_the_merge_refusal_names_coverage_then_seed(tmp_path):
    files = [str(marks(tmp_path / name, stamp))
             for name, stamp in (("base", OLD), ("ours", OLD), ("theirs", NEW))]

    with pytest.raises(ConfigError) as refused:
        cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))

    assert str(refused.value) == (
        f"ratchet merge refused: ours is [{OLD}] and theirs is [{NEW}] — marks from different "
        "metric versions cannot merge; run `crapkit coverage`, then re-baseline one side with "
        "`crapkit ratchet seed`")
