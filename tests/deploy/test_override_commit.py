"""An override through the commit gate: CRAPKIT_OVERRIDE_REASON on `git commit`.

docs/ratchet.md "Overrides and the audit trail": three records or nothing. With
an alert_command the commit lands with an alert line, a row in the override
log and a mark staged into the same commit; without one the override is
refused and git refuses the commit.
"""
from __future__ import annotations

from pathlib import Path

from kit import docsnip, gitsurf, repos
from kit.cells import cell

PACKET = "deploy-git"
OVERRIDES = "Overrides and the audit trail"
ALERT = 'alert_command = "cat >> .crapkit/alerts.log"'


def override_commit() -> tuple[str, list[str]]:
    """The page's override commit and the lines it prints that name no path."""
    command, output = docsnip.outputs(docsnip.fence(gitsurf.RATCHET_DOC, OVERRIDES,
                                                    contains="CRAPKIT_OVERRIDE_REASON="))[0]
    return command, [line for line in output.splitlines() if not line.lstrip().startswith("ccn")]


def no_alert_refusal() -> str:
    output = docsnip.outputs(docsnip.fence(gitsurf.RATCHET_DOC, OVERRIDES, contains="no alert_command"))[0][1]
    return output.splitlines()[0]


def set_alert(box, repo: Path, line: str) -> None:
    """Write `line` as the [crapkit] table's alert_command, or drop it for ""."""
    config = repo / "crapkit.toml"
    kept = [text for text in config.read_text(encoding="utf-8").splitlines() if not text.startswith("alert_command")]
    table = kept.index("[crapkit]")
    config.write_text("\n".join(kept[:table + 1] + ([line] if line else []) + kept[table + 1:]) + "\n",
                      encoding="utf-8", newline="\n")
    box.run(["git", "commit", "-q", "-am", "alert_command"], cwd=repo, env=box.commit_env(), expect=0)


def alerts(repo: Path) -> str:
    log = repo / ".crapkit" / "alerts.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


@cell("lin-override-commit", channel="Route 1", harness="git 2.47",
      scenario="fresh: CRAPKIT_OVERRIDE_REASON with alert_command writes 3 records; unset alert_command refuses",
      use_cases="override audit", os="linux", image="cells", cadence="nightly")
def test_an_override_commit_writes_three_records_and_refuses_without_an_alert(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo)
    gitsurf.route1(box, repo)
    set_alert(box, repo, ALERT)
    command, printed = override_commit()
    box.run(["git", "add", gitsurf.breach(repo)], cwd=repo, expect=0)

    granted = box.script(command, cwd=repo, env=box.commit_env(), expect=0)
    assert [line for line in printed if line not in granted.stderr] == [], granted.stderr
    assert "route( a , b , c , d )" in alerts(repo)
    assert "calc/route.py  route( a , b , c , d )" in gitsurf.committed_marks(box, repo)
    assert "calc/route.py  route( a , b , c , d )" in box.run(["crapkit", "overrides"], cwd=repo, expect=0).stdout

    set_alert(box, repo, "")
    box.run(["git", "add", gitsurf.breach(repo, name="route_two.py")], cwd=repo, expect=0)
    refused = box.script(command, cwd=repo, env=box.commit_env(), expect=1)
    assert no_alert_refusal() in refused.stderr
    assert "route_two.py" not in alerts(repo) + box.run(["crapkit", "overrides"], cwd=repo, expect=0).stdout
