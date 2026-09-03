"""The report collects the worklist through the same shaping `worklist --json`
prints, committed marks included (0.5.0).

`worklist --json` rows carry `ratchet_mark`, the committed mark's value or null.
The report's collector built its own worklist without the marks file, so the
same run read `ratchet_mark: null` on the page's payload and `56.0` on the
command's. One shaping now: `queue._worklist_for`.
"""
import json
from pathlib import Path

from cli_inproc_repo import (add_knotty, commit_all, repo,  # noqa: F401
                             seed_artifacts, template_repo)

from crapkit.cli import main
from crapkit.cli._shared import _load_repo_config, _open_store
from crapkit.cli.reports import _report_payload


def _scored_with_a_marked_knotty(repo: Path, capsys) -> float:
    """A committed ccn-8 `knotty` over its ceiling, scored, and marked at its
    own CRAP in the ratchet file. Returns the mark."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--json", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert main(["worklist", "--json", "--repo", str(repo)]) == 0
    rows = json.loads(capsys.readouterr().out)["active"]
    knotty = next(r for r in rows if "knotty" in r["function"])
    (repo / "crapkit-ratchet.tsv").write_text(
        "path\tlong_name\tcrap\n" + f"src/app.ts\t{knotty['function']}\t{knotty['crap']:.4f}\n",
        encoding="utf-8", newline="\n")
    return round(knotty["crap"], 4)


def test_the_report_payload_carries_the_committed_mark_the_worklist_prints(repo, capsys):
    mark = _scored_with_a_marked_knotty(repo, capsys)
    main(["worklist", "--json", "--repo", str(repo)])
    printed = {r["function"]: r["ratchet_mark"]
               for r in json.loads(capsys.readouterr().out)["active"]}

    payload = _report_payload(repo, _load_repo_config(repo), _open_store(repo))

    collected = {r["function"]: r["ratchet_mark"] for r in payload["worklist"]["active"]}
    assert collected == printed, "the page and the command read one shaping"
    assert mark in collected.values(), collected
