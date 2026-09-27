"""lin-hooks-sim: the plugin's hooks/hooks.json through each harness's [hooks] rules.

A user who installs the crapkit plugin in a harness gets whatever that
harness makes of hooks/hooks.json: the handlers it registers, the fields it
keeps and the events it fires them on. Each cell takes the file from the
candidate's plugin, counts its handlers from the file itself, and works out
which handlers fire when the agent edits a Python function over its
ceiling. It then runs each command those handlers spawn, exactly as the
harness would build it, with the harness's own PostToolUse payload on stdin.

A cell fails when a spawn is a bare `crapkit` (a harness that drops `args`
runs crapkit's usage on every edit), when the harness reads the exit 2
the advisory uses as a reason to block the edit, or when the advisory never
reaches the place the harness hands text to its model: exit 2's stderr in
Claude Code, one JSON object on stdout with exit 0 in Cursor, VS Code and
Copilot CLI. BUGS names a harness whose cell fails on a known deploy bug.
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

from kit import hooks_rules, profiles
from kit.cells import cell

PACKET = "deploy-harnesses"
EDITED = "calc/grade.py"
# A branch that takes grade() from ccn 8 to ccn 9, over the default ceiling of 6.
BREACH = ('    return "D"', '    if attempts > 5 and bonus:\n        return "E"\n    return "D"')
BUGS: dict[str, str] = {}


def params() -> list:
    return [pytest.param(key, id=key, marks=[pytest.mark.xfail(strict=True, reason=BUGS[key])] if key in BUGS else [])
            for key in profiles.keys()]


def breach(repo) -> None:
    source = repo / EDITED
    source.write_text(source.read_text(encoding="utf-8").replace(*BREACH), encoding="utf-8")


def problems(box, repo, profile, fired: list) -> list[str]:
    return [problem for argv, step in spawn_results(box, repo, profile, fired).items()
            for problem in [*hooks_rules.problems(profile, list(argv), step.exit), *unheard(profile, step)]]


def unheard(profile, step) -> list[str]:
    return [] if hooks_rules.heard(profile, step) else [
        f"{profile.name} never hears the advisory: exit {step.exit}, stdout {step.stdout[:80]!r}"]


def record_hooks_evidence(record_property, profile) -> None:
    inferred = profile.inferred([f"hooks.{name}" for name in profile.hooks])
    record_property("evidence_inferred", ",".join(inferred) or "none")


def spawn_results(box, repo, profile, fired: list) -> dict[tuple, object]:
    """Each distinct command the fired handlers spawn, run once with the
    harness's payload on stdin: argv -> the step."""
    payload = json.dumps(hooks_rules.payload(repo, EDITED, profile=profile))
    results = {}
    for argv in dict.fromkeys(tuple(hooks_rules.argv(profile, handler)) for handler in fired):
        results[argv] = box.run(list(argv), cwd=repo, input=payload, note=f"{profile.name} hook spawn")
    return results


@pytest.mark.parametrize("profile_key", params())
@cell("lin-hooks-sim", channel="plugin hooks.json through each [hooks] block", harness="27 profiles",
      scenario="fresh: fail on a bare `crapkit` spawn or a blocking exit 2", use_cases="advisory hook portability",
      os="linux", image="core", cadence="push", real_cli=False)
def test_lin_hooks_sim(profile_key, box, templates, candidate, record_property):
    profile = profiles.load(profile_key)
    plugin = candidate.staged / "plugin"
    declared = hooks_rules.handlers(plugin)
    record_hooks_evidence(record_property, profile)
    assert declared and Counter(handler.event for handler in declared)["PostToolUse"] == len(declared)

    repo = profiles.measured_repo(box, templates)
    breach(repo)
    fired = hooks_rules.fired(profile, plugin, tool="Edit", path=EDITED)
    box.transcript.note(f"{profile.name} registers {len(hooks_rules.registered(profile, plugin))} of "
                        f"{len(declared)} handlers; {len(fired)} fire on an Edit of {EDITED}")
    record_property("hook_spawns_per_edit", str(len(fired)))

    assert problems(box, repo, profile, fired) == []
