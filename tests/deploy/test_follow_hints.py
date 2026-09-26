"""A user who reads no docs past the install line: run `crapkit init`, then
only the commands crapkit itself prints as the next step, until it prints none.

A printed step is a command in backticks after `run` or `then`, or the
command after `-> next:`. Each one runs as printed, in the order a terminal
shows it: crapkit writes a note to stderr before its summary lines on stdout,
so stderr's steps come first. Each must exit 0. The chain should end at a
passing verify: that is the verdict the README calls the first passing state.
"""
from __future__ import annotations

import re

from kit import installers, repos
from kit.cells import cell
from kit.installers import said

PACKET = "deploy-channels"
PRINTED = re.compile(r"(?:\brun|\bthen) `([^`]+)`|-> next: (crapkit[^\n`]*)")
LIMIT = 12


def printed_steps(text: str) -> list[str]:
    """The commands a step prints as the next thing to run, in order."""
    return [(ran or arrow).strip() for ran, arrow in PRINTED.findall(text)]


def _run_printed(box, repo, command: str):
    """One printed command as the user pastes it. A coverage.py lane in a
    container meets the guard, which names a config key and no command, so
    the kit applies docs/lanes.md#containers before crapkit coverage runs."""
    if command.startswith("crapkit coverage"):
        installers.allow_containers_here(repo)
    return box.script(command, cwd=repo, expect=0, note="a step crapkit printed")


def follow(box, repo, first: str) -> list[tuple[str, object]]:
    """Run `first`, then every step the output prints that has not run yet."""
    queue, ran = [first], []
    while queue and len(ran) < LIMIT:
        command = queue.pop(0)
        if command in (done for done, _ in ran):
            continue
        step = _run_printed(box, repo, command)
        ran.append((command, step))
        queue.extend(printed_steps(step.stderr) + printed_steps(step.stdout))
    return ran


def _followed(box, templates) -> list[tuple[str, object]]:
    installers.pip_venv(box, "3.12")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    return follow(box, repo, "crapkit init")


@cell("lin-follow-hints", channel="pip venv", harness="none",
      scenario="fresh: from init, only the commands crapkit prints as next steps, each exit 0, until none",
      use_cases="init to verify", os="linux", image="core", cadence="nightly")
def test_every_step_crapkit_prints_runs_as_printed(box, templates):
    ran = _followed(box, templates)
    commands = [command for command, _ in ran]

    assert len(ran) < LIMIT
    assert commands[:2] == ["crapkit init", "python -m pip install pytest-cov"]
    assert commands[2:] == ["crapkit coverage", "crapkit worklist", "crapkit ratchet seed", "crapkit verify"]
    assert printed_steps(said(ran[-1][1])) == []
    assert all(step.exit == 0 for _, step in ran)


@cell("lin-follow-hints", channel="pip venv", harness="none",
      scenario="fresh: the printed steps end at a passing verify", use_cases="init to verify", os="linux",
      image="core", cadence="nightly")
def test_the_printed_steps_end_at_a_passing_verify(box, templates):
    ran = _followed(box, templates)

    assert said(ran[-1][1]).startswith("verify OK")
