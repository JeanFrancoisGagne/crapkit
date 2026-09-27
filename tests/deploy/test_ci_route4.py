"""Route 4: CI verifies a pull request against a committed portable baseline.

The default branch adopts crapkit from the README (in a container the
coverage.py lane refuses first, and docs/lanes.md#containers is the fix), then
emits and commits the baseline the Route 4 block names. The CI job clones the
pull request's branch the way actions/checkout does by default, one commit
deep, meets the shallow-clone refusal the README prints, runs the fix that
line names, and gets the verdict with its GitHub annotation.
"""
from __future__ import annotations

import re

from kit import docsnip, gitsurf, repos
from kit.cells import cell

PACKET = "deploy-git"


def route4_lines() -> tuple[str, str]:
    """The Route 4 block: emit on the default branch, verify in the PR job."""
    emit, verify = docsnip.commands(docsnip.fence(gitsurf.README, gitsurf.ROUTE4, index=0))
    return emit, verify


def shallow_refusal() -> tuple[str, str]:
    """The README's command and the refusal it prints in a shallow clone."""
    return docsnip.outputs(docsnip.fence(gitsurf.README, gitsurf.ROUTE4, contains="shallow clone"))[0]


@cell("lin-route4-ci", channel="Route 4", harness="git 2.47",
      scenario="fresh: file:// --depth 1 clone, is-shallow true; shallow refusal; unshallow; --emit-baseline; "
               "--baseline-tsv --github ::error; no container_ok",
      use_cases="verify, portable baseline", os="linux", image="cells", cadence="nightly")
def test_a_shallow_pr_clone_is_refused_then_verified_against_the_committed_baseline(box, templates):
    gitsurf.pip_venv(box)
    upstream = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, upstream, guard=True)
    emit, verify = route4_lines()
    box.script(emit, cwd=upstream, expect=0)
    box.run(["git", "add", "crapkit-baseline.tsv"], cwd=upstream, expect=0)
    box.run(["git", "commit", "-q", "-m", "commit the portable baseline"], cwd=upstream, env=box.commit_env(),
            expect=0)
    box.run(["git", "checkout", "-q", "-b", "pr"], cwd=upstream, expect=0)
    gitsurf.assert_accepted(gitsurf.commit(box, upstream, stage=gitsurf.breach(upstream)))

    ci = box.root / "ci"
    box.run(["git", "clone", "-q", "--depth", "1", "--branch", "pr", upstream.as_uri(), str(ci)], expect=0)
    assert box.run(["git", "rev-parse", "--is-shallow-repository"], cwd=ci, expect=0).stdout.strip() == "true"
    command, printed = shallow_refusal()
    refused = box.script(command, cwd=ci, expect=4)
    assert _commitless(refused.stderr.strip()) == _commitless(printed)
    box.script(re.search(r"run (git fetch --unshallow)", printed)[1], cwd=ci, expect=0)

    verdict = box.script(verify, cwd=ci, expect=6)
    assert "::error file=calc/route.py" in verdict.stdout, verdict.stdout


def _commitless(line: str) -> str:
    return re.sub(r"baseline commit \w+", "baseline commit <sha>", line)
