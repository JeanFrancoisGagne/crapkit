"""One repo carried through every era: 0.4.0 to 0.4.15 to 0.6.0 to 0.7.6 to
the candidate, in one pip venv.

Each hop installs the next release and runs the steps that release's own
docs gave, read from its tag in the mirror (README 'Upgrading from 0.4.4' at
v0.4.15, CHANGELOG 'Upgrading from 0.4.x' at v0.6.0, docs/upgrading.md at
v0.7.6): the `crapkit ...` commands that section names, in order, doctor's
failures fixed the way doctor says. Then verify; when it refuses, the user
runs the command the refusal names and verifies again. The last hop is the
candidate's guide (state.walk). The runs, claim and override 0.4.0 recorded
must all still be listed at the end.
"""
from __future__ import annotations

import re

from kit import gitmirror, state, state_manifest
from kit.cells import cell
from kit.state import output

PACKET = "deploy-upgrade"
HOPS = (("0.4.15", "README.md", "Upgrading from 0.4.4"),
        ("0.6.0", "CHANGELOG.md", "Upgrading from 0.4.x"),
        ("0.7.6", "docs/upgrading.md", "Measure before changing marks"))
COMMAND = re.compile(r"`(crapkit [^`\n]+)`|^(?:\$ )?(crapkit [^`\n]+)$", re.M)
HEADING = re.compile(r"^(#+) (.*)$")
PLACEHOLDER = re.compile(r"\b[A-Z]{1,5}\b|<|\.\.\.")


def _outside_fences(text: str) -> list[tuple[str, bool]]:
    """Each line and whether it sits outside a ``` fence, where a `#` line is a heading."""
    marked, inside = [], False
    for line in text.splitlines():
        inside = not inside if line.startswith("```") else inside
        marked.append((line, not inside and not line.startswith("```")))
    return marked


def _level(line: str, outside: bool) -> int | None:
    match = HEADING.match(line) if outside else None
    return len(match[1]) if match else None


def _ends(level: int | None, found: int | None) -> bool:
    return bool(level and found and found <= level)


def _opens(line: str, found: int | None, heading: str) -> int | None:
    return found if found and line.split(" ", 1)[1].strip() == heading else None


def section(text: str, heading: str) -> str:
    """The lines under `heading` up to the next heading of its level or above."""
    kept, level = [], None
    for line, outside in _outside_fences(text):
        found = _level(line, outside)
        if _ends(level, found):
            break
        kept += [line] if level else []
        level = level or _opens(line, found, heading)
    return "\n".join(kept)


def era_commands(text: str, heading: str) -> list[str]:
    """The complete `crapkit ...` commands a section names, in page order."""
    found = [first or second for first, second in COMMAND.findall(section(text, heading))]
    return list(dict.fromkeys(command.strip() for command in found if not PLACEHOLDER.search(command)))


def run_era_step(box, repo, command: str) -> None:
    step = state.run_line(box, repo, command, expect=None, note=f"era step: {command}")
    if step.exit and command == "crapkit doctor":
        state.resolve_doctor(box, repo, step)


def follow_refusal(box, repo) -> None:
    """verify; when it refuses, the commands its refusal names, then verify again."""
    refused = box.run(["crapkit", "verify"], cwd=repo)
    if not refused.exit:
        return
    for command in re.findall(r"`(crapkit [^`]+)`", output(refused)):
        state.run_line(box, repo, command, note=f"the fix verify's refusal names: {command}")
    state.commit(box, repo, "reseed as verify asked")
    assert "verify OK" in output(box.run(["crapkit", "verify"], cwd=repo, expect=0))


def hop(box, repo, mirror, version: str, page: str, heading: str) -> None:
    """Upgrade to `version` and run the steps its own docs gave."""
    state.pip_install(box, "--upgrade", f"crapkit=={version}")
    commands = era_commands(mirror.git("show", f"v{version}:{page}"), heading)
    box.transcript.note(f"era {version}: {page} > {heading}: {commands}")
    assert commands, f"v{version}:{page} > {heading} names no crapkit command"
    for command in commands:
        run_era_step(box, repo, command)
    state.commit(box, repo, f"upgrade to crapkit {version}")
    follow_refusal(box, repo)


@cell("lin-up-chain", channel="pip venv", harness="none",
      scenario="upgrade 0.4.0 to 0.4.15 to 0.6.0 to 0.7.6 to candidate, each era's steps",
      use_cases="upgrade guide", os="linux", image="core", cadence="nightly")
def test_lin_up_chain(box, templates, candidate):
    source = state.build(box, state.source_version("0.4.0"), cache=templates)
    repo = source.checkout(box)
    mirror = gitmirror.make(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit=={source.version}", "pytest", "pytest-cov")
    before = state_manifest.take(repo)

    for version, page, heading in HOPS:
        hop(box, repo, mirror, state.source_version(version), page, heading)
    state.walk(box, repo, candidate, source, state.upgrade_line("pip in the active environment"))

    state_manifest.check(box, before, state_manifest.take(repo), label="0.4.0 to the candidate")
