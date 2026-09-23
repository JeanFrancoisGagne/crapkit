"""verify settles its baseline first, then refuses a stale stamp with the seed that clears it.

The stamp guard used to run before verify read `--baseline`. On a store pinned
behind a failed verify (#75) that cost three things: a named run that could not
serve was refused for the stamp instead of for itself, the taint warning naming
`--baseline` never printed, and the stamp refusal prescribed a plain `ratchet
seed`, which reads the pinned run and cannot clear it. Reading the baseline
first lets the refusal name the run to seed from.
"""
import sys
from pathlib import Path

from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import pinned, stale_marks, write_run, twin

import lizard

from crapkit import ratchet
from crapkit.cli import main
from crapkit.cli.verifying import _stamp_refusal
from crapkit.invocation import _self

ROOT = Path(__file__).resolve().parent.parent.parent


def verify(repo, capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["verify", "--reuse-artifacts", *argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_named_run_this_crapkit_measured_is_the_run_the_refusal_seeds_from(repo, capsys):
    _, _, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys, "--baseline", str(fresh))

    assert code == 3
    assert err.strip().endswith(
        "CRAP scores are not comparable across metric versions; re-baseline from run "
        f"{fresh} with `{_self()} ratchet seed --baseline {fresh}`"), err


def test_a_named_run_an_older_crapkit_measured_needs_a_coverage_run_first(repo, capsys):
    pinned(repo)
    older = write_run(repo, [twin(1), twin(2)],
                      versions={"analysis_version": "7", "lizard": lizard.version})
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys, "--baseline", str(older))

    assert code == 3
    assert err.strip().endswith(
        f"; run {older} was measured under another metric too, so run `{_self()} coverage`, "
        f"then re-baseline from the run it writes with `{_self()} ratchet seed --baseline ID`"), err


def test_a_named_run_that_cannot_serve_is_refused_for_itself_not_for_the_stamp(repo, capsys):
    _, failed, _ = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys, "--baseline", str(failed))

    assert code == 1
    assert f"run {failed} is a failed verify and cannot serve as a baseline" in err, err
    assert "were recorded under" not in err, err


def test_the_taint_warning_prints_before_the_stamp_refusal(repo, capsys):
    """Without a name, the refusal seeds from the run the warning above it names:
    a plain `ratchet seed` reads the pinned run and cannot clear the stamp."""
    _, failed, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys)

    assert code == 3
    warning = f"warning: run {fresh} is not the baseline: verify run {failed} FAILED"
    assert warning in err, err
    assert err.index(warning) < err.index("were recorded under"), err
    assert err.strip().endswith(
        f"re-baseline from run {fresh} with `{_self()} ratchet seed --baseline {fresh}`"), err


def test_the_ratchet_page_prints_the_refusal_a_named_baseline_gets(monkeypatch):
    """The page's session is `$ crapkit verify --baseline 12` on a crapkit that
    measures analysis 10, so the quoted line is the one that process prints."""
    running = "crapkit-analysis=10 lizard=1.24.0"
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/crapkit", "verify"])
    monkeypatch.setattr(ratchet, "metric_version", lambda: running)
    named = {"id": 12, "tool_versions": {"analysis_version": "10", "lizard": "1.24.0"}}

    refusal = _stamp_refusal(ratchet.stamp_conflict("crapkit-analysis=9 lizard=1.24.0", running),
                             named)

    page = (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8")
    assert f"crapkit: {refusal}" in page
