"""The CHANGELOG names the commands that prove legacy ratchet keys, and only those.

0.8.0's note on the hoisted key-group union said `ratchet report` runs the same
check. It never does: `ratchet report` reads the marks file's git history and
nothing else, so on a large consumer repo it took 0.553 s before the hoist and
0.551 s after, while `report`, which does run the check, fell from 158 s to 3.9 s.
Each command the sentence names is run here over marks with legacy keys, and the
check has to fire.
"""
from pathlib import Path

import pytest

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

from crapkit import ratchet
from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

ROOT = Path(__file__).resolve().parents[2]
ANCHOR = "The legacy ratchet key check builds its group union once"
ARGV = {
    "report": ["report"],
    "ratchet report": ["ratchet", "report"],
    "ratchet seed": ["ratchet", "seed"],
    "ratchet prune": ["ratchet", "prune"],
    "verify": ["verify", "--reuse-artifacts"],
}


def named_commands() -> list[str]:
    """The backticked commands in the bullet's 'run the same check' sentence."""
    text = " ".join((ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split())
    bullet = text[text.index(ANCHOR):].split(" - ", 1)[0]
    (sentence,) = [s for s in bullet.split(". ") if "run the same check" in s]
    return sentence.split("`")[1::2]


@pytest.fixture()
def measured(repo, capsys):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def legacy_marks(repo: Path) -> None:
    """One mark under the start-only keys an older crapkit wrote (no keys stamp)."""
    (repo / "crapkit-ratchet.tsv").write_text(
        dump_ratchet([RatchetEntry("src/app.ts", "dispatch ( kind )", 90.0)],
                     stamp=metric_version(), key_version=0),
        encoding="utf-8", newline="\n")


def proofs(repo: Path, argv: list[str], monkeypatch) -> int:
    """How often the command proved the legacy keys against a run."""
    calls = []
    real = ratchet.check_key_groups

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    legacy_marks(repo)
    monkeypatch.setattr(ratchet, "check_key_groups", counted)
    main([*argv, "--repo", str(repo)])
    monkeypatch.undo()
    return len(calls)


def test_the_sentence_still_names_commands():
    assert len(named_commands()) >= 2, named_commands()


def test_every_command_the_changelog_names_runs_the_legacy_key_check(measured, monkeypatch,
                                                                       capsys):
    ran = {name: proofs(measured, ARGV[name], monkeypatch) for name in named_commands()}
    capsys.readouterr()

    assert [name for name, count in ran.items() if not count] == [], ran


def test_ratchet_report_reads_history_and_never_proves_keys(measured, monkeypatch, capsys):
    assert proofs(measured, ARGV["ratchet report"], monkeypatch) == 0
