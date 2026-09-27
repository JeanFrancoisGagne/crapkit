"""A user installs the candidate, adopts a repo with it, then goes back to
0.7.6 (a bad day, a pinned CI, a teammate's older laptop).

Each command a user reaches for first (doctor, coverage, verify, next-item and
one MCP call) either works on the store and marks the candidate wrote, or
refuses in words that name the cause, with an exit code from the README's
table and no traceback. Where the refusal names a fix, the cell follows it and
expects verify to pass. Nothing the candidate recorded may be lost.
"""
from __future__ import annotations

from kit import state, state_manifest
from kit.cells import cell
from kit.mcp_client import McpClient
from kit.state import output

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")
# README "Exit codes": the answers a clear refusal may give; 1 is a crash or an
# overloaded verdict, 2 an argparse usage error.
CLEAR_EXITS = {0, 3, 4, 5, 6, 7, 8, 9}
CRASH_WORDS = ("Traceback", "sqlite3.", "OperationalError", "IntegrityError")


def clear(box, step) -> None:
    text = output(step)
    assert step.exit in CLEAR_EXITS, f"exit {step.exit} is not a documented answer\n{box.transcript.text()}"
    assert not [word for word in CRASH_WORDS if word in text], f"a crash, not a refusal:\n{text}"
    assert step.exit == 0 or text.strip().startswith("crapkit"), f"a refusal that names no cause:\n{text}"


@cell("lin-downgrade-0.7.6", channel="pip venv", harness="none",
      scenario="downgrade onto candidate state: doctor, coverage, verify, next-item, one MCP call work or refuse clearly",
      use_cases="downgrade", os="linux", image="core", cadence="nightly")
def test_lin_downgrade_0_7_6(box, templates, candidate):
    source = state.build(box, candidate.version, cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={candidate.version}")
    before = state_manifest.take(repo)

    state.pip_install(box, f"crapkit[py]=={OLD}")
    assert OLD in box.run(["crapkit", "--version"], expect=0).stdout
    for argv in (["doctor"], ["coverage"], ["next-item"]):
        clear(box, box.run(["crapkit", *argv], cwd=repo))
    refusal = box.run(["crapkit", "verify"], cwd=repo)
    clear(box, refusal)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        assert client.initialize()["serverInfo"]["version"] == OLD
        answer = client.call("get_next_item")
    assert not answer.get("isError"), answer

    assert refusal.exit == 3 and "re-baseline with `crapkit ratchet seed`" in output(refusal), box.transcript.text()
    box.run(["crapkit", "ratchet", "seed"], cwd=repo, expect=0, note="the fix 0.7.6's refusal names")
    state.commit(box, repo, "marks under 0.7.6 again")
    assert "verify OK" in output(box.run(["crapkit", "verify"], cwd=repo, expect=0))
    state.kept(box, repo, source)
    state_manifest.check(box, before, state_manifest.take(repo), label="downgrade")
