"""Render one pull-request comment out of crapkit's own JSON payloads.

The composite action at the repository root runs `crapkit coverage --json`,
`crapkit verify --json` and `crapkit worklist --json` in the consumer's
checkout and hands the three files here. This module turns them into the
markdown the action posts, and into the request body `gh api` sends, so the
escaping is json.dumps' problem and never a shell quoting question.

It reads the payloads and nothing else: no git, no network, no clock. Run it on
three saved files and you get the byte-identical comment the job would post,
which is how the rendering in README's action section was produced.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

# The line that makes the comment findable. The action greps for it to decide
# between a POST and a PATCH, so a second spelling means a comment per push
# instead of one comment edited in place. tests/unit/test_action_contract.py
# pins this string against the one action.yml searches for.
MARKER = "<!-- crapkit-action -->"

_HEADER = "| File | Function | ccn | risk | remedy |\n|---|---|---:|---:|---|"

# The rule each verify exit code stands for. verify reports the first that
# fires, so the phrase names the rule that refused the tree and the bullets
# below it list every finding the payload carries.
_RULES = {6: "complexity gate", 7: "ratchet regression", 8: "new test failures",
          9: "diff-coverage ceiling"}

# The verify lists whose entries name a function. The comment's table lists
# those rows first, so the function the gate stopped is not below the fold.
_FINDING_LISTS = ("gate_violations", "ratchet_regressions", "overridden")


def _read_text(path: str | None) -> str:
    """A file the action left behind, or "" when it did not: a step that
    failed leaves an empty redirect target, a step that was skipped leaves no
    file at all, and the comment reads both as "nothing here"."""
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: str | None) -> dict | None:
    """A payload, or None when the command that writes it did not.

    A crapkit read command with no run behind it exits 1 and prints its reason
    on stderr, leaving an empty redirect target. The comment says so; it does
    not crash on it.
    """
    try:
        return json.loads(_read_text(path))
    except ValueError:
        return None


def _read_lines(path: str | None) -> list[str]:
    """The changed-file list, empty when there is none."""
    text = _read_text(path)
    return [line.strip().replace("\\", "/") for line in text.splitlines() if line.strip()]


def _base_reason(sha_path: str | None, reason_path: str | None) -> str | None:
    """Why the base run was not made, or None when it was.

    The base step leaves crapkit-base.sha when it made a run and
    crapkit-base.reason when it did not; a push event and `delta: "false"`
    skip the step and leave neither, which is `no base commit`. A render with
    no --base-sha at all (the README's saved-payload route) is not asked.
    """
    if not sha_path or _read_text(sha_path).strip():
        return None
    return _read_text(reason_path).strip() or "no base commit"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _first_line(text) -> str:
    lines = str(text or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def coverage_failure(coverage: dict | None) -> str:
    """Why `crapkit coverage` failed, in one line.

    The first line of the first lane failure the summary carries; the error
    object's message when the command died before a summary (0.5.0 prints one
    under --json); or a pointer at the job log when nothing was printed at all,
    which is what every lane failing looks like: the lane errors went to
    stderr and the redirect target is empty.
    """
    if not coverage:
        return "no run summary was printed, so every lane failed; the lane errors are in the job log"
    error = coverage.get("error")
    if error:
        return _first_line(error.get("message"))
    for name, text in (coverage.get("lane_failures") or {}).items():
        return f"lane {name!r} failed: {_first_line(text)}"
    return "the summary names no failed lane; read the job log"


def no_verdict_line(coverage: dict | None, coverage_exit: int) -> str:
    """The verdict line when `crapkit coverage` failed.

    The action does not run verify then: a `verify --reuse-artifacts` over a
    failed measurement read the artifact the dead lane left from an earlier
    run, passed over it, and became the trusted baseline.
    """
    return (f"**no verdict: `crapkit coverage` exited {coverage_exit} "
            f"({coverage_failure(coverage)}); verify did not run.**")


def _over(coverage: dict) -> str:
    """`over ceiling 6`, or the per-scope list when scopes set their own. A
    payload without `ceilings` (a 0.4.x coverage) names no number."""
    ceilings = coverage.get("ceilings")
    if not ceilings:
        return "over the ceiling"
    scoped = [f"{scope} {ceiling}" for scope, ceiling in ceilings.items() if scope != "default"]
    if not scoped:
        return f"over ceiling {ceilings.get('default')}"
    return f"over their ceilings ({ceilings.get('default')}; {', '.join(scoped)})"


def _lane_failures(coverage: dict) -> str:
    """One clause per failed lane, its first error line only: the rest is the
    lane's own log and belongs in the job log."""
    return "".join(f"; lane {name!r} failed: {_first_line(text)}"
                   for name, text in (coverage.get("lane_failures") or {}).items())


def scored_line(coverage: dict | None) -> str:
    """What the run measured, in the words `crapkit coverage` uses for it,
    and the ceiling the over-count is judged against. When the command died
    before a summary, --json prints one error object and this line quotes
    the sentence that names the fix."""
    if not coverage:
        return "`crapkit coverage` wrote no run summary."
    error = coverage.get("error")
    if error:
        return f"`crapkit coverage` exited {error.get('exit')}: {_first_line(error.get('message'))}."
    return (f"{_plural(coverage.get('functions', 0), 'function')} in "
            f"{_plural(coverage.get('files', 0), 'file')}, "
            f"{coverage.get('over_target', 0)} {_over(coverage)}, "
            f"CRAP load {coverage.get('crap_load', 0)}, "
            f"grade {coverage.get('grade', '?')}{_lane_failures(coverage)}.")


def _findings(verify: dict) -> str:
    parts = [_plural(len(verify.get("gate_violations", [])), "gate violation"),
             _plural(len(verify.get("ratchet_regressions", [])), "ratchet regression"),
             _plural(len(verify.get("new_failures", [])), "new test failure"),
             _plural(verify.get("diff_uncovered_count", 0), "uncovered changed line")]
    return ", ".join(parts)


def _against(verify: dict) -> str:
    return (f"Run {verify.get('run_id')} against baseline {verify.get('baseline_run')}, "
            f"{_plural(verify.get('changed_files', 0), 'changed file')}")


def _exit_phrase(verify: dict, exit_code: int) -> str:
    """`exit 6: complexity gate`. The ceiling joins exit 9, since it is the
    number the uncovered lines went over and it was only in the job log."""
    rule = _RULES.get(exit_code)
    if rule is None:
        return f"exit {exit_code}"
    if exit_code == 9 and verify.get("diff_uncovered_max") is not None:
        rule = f"{rule} {verify['diff_uncovered_max']}"
    return f"exit {exit_code}: {rule}"


def _percent(cov) -> str:
    return "-" if cov is None else f"{round(cov * 100)}%"


def _crap(value) -> str:
    return "-" if value is None else str(round(value, 1))


def _gate_bullets(verify: dict) -> list[str]:
    return [f"- gate: `{v.get('path')}:{v.get('start')}` `{v.get('long_name')}` "
            f"ccn {v.get('ccn')}, cov {_percent(v.get('cov'))}, crap {_crap(v.get('crap'))} "
            f"-> {v.get('remedy')}"
            for v in verify.get("gate_violations", [])]


def _ratchet_bullets(verify: dict) -> list[str]:
    return [f"- ratchet: `{r.get('path')}` `{r.get('long_name')}` "
            f"{r.get('recorded')} -> {r.get('fresh_crap')} (recorded -> fresh)"
            for r in verify.get("ratchet_regressions", [])]


def _failure_bullets(verify: dict) -> list[str]:
    return [f"- new test failure: `{test}`" for test in verify.get("new_failures", [])]


def _uncovered_bullets(verify: dict) -> list[str]:
    """The first twenty uncovered changed lines, one bullet per file, then how
    many the cut hid. The payload itself carries at most fifty."""
    shown = verify.get("diff_uncovered", [])[:20]
    bullets = [f"- uncovered lines in `{path}`: " + ", ".join(str(e.get("line")) for e in group)
               for path, group in itertools.groupby(shown, key=lambda e: e.get("path"))]
    hidden = verify.get("diff_uncovered_count", 0) - len(shown)
    if hidden > 0:
        bullets.append(f"- and {_plural(hidden, 'more uncovered changed line')}")
    return bullets


def _failed(verify: dict, exit_code: int) -> str:
    """The exit phrase, one bullet per finding, and the counts line last, as
    it always read."""
    head = f"**verify failed, {_exit_phrase(verify, exit_code)}.**"
    counts = f"{_against(verify)}: {_findings(verify)}."
    bullets = (_gate_bullets(verify) + _ratchet_bullets(verify) + _failure_bullets(verify)
               + _uncovered_bullets(verify))
    if not bullets:
        return f"{head} {counts}"
    return "\n".join([head, "", *bullets, "", counts])


def verdict_line(verify: dict | None, exit_code: int, base_reason: str | None = None) -> str:
    """One line for the whole verdict, the exit code included.

    verify reports the first of 6, 7, 8, 9 that fires, so the code says which
    rule refused the tree and the counts say what it found. A pass with no base
    run behind it is a judgement of nothing: the diff was empty, the gate
    judged no changed function, and the line says so instead of "passed". A
    failure is a failure either way, since the ratchet runs without a base.
    """
    if verify is None:
        return (f"**`crapkit verify` exited {exit_code} and wrote no verdict.** "
                f"Read the job log: this is tooling, not a score.")
    if not verify.get("ok"):
        return _failed(verify, exit_code)
    if base_reason is not None:
        return (f"**verify judged no changed function:** the base run was not made "
                f"({base_reason}). {_against(verify)}.")
    return f"**verify passed.** {_against(verify)}."


def _in_diff(active: list[dict], changed: list[str]) -> list[dict]:
    """worklist paths are root-relative and unquoted, and so is `git diff
    --name-only` under crapkit's own `core.quotePath=false`, so this is a
    string match and not a path walk."""
    return [row for row in active if row.get("path") in changed]


def named_by_findings(verify: dict | None) -> set[tuple[str, str]]:
    """The (path, function) pairs verify's findings name: gate violations,
    ratchet regressions and overridden entries. These are the only function
    identities the comment holds, verify's `changed_files` being a count."""
    found = itertools.chain.from_iterable((verify or {}).get(key) or [] for key in _FINDING_LISTS)
    return {(entry.get("path"), entry.get("long_name")) for entry in found}


def _named_first(active: list[dict], named) -> list[dict]:
    """The rows a finding names, in ranking order, then the rest: a stable
    sort on one bit."""
    return sorted(active, key=lambda row: (row.get("path"), row.get("function")) not in named)


def rows(worklist: dict | None, changed: list[str], top: int, named=frozenset()) -> list[dict]:
    """The ranked rows for the changed files, worst first, capped at `top`,
    the rows a finding names ahead of the rest.

    `crapkit worklist` ranks the whole repository by ccn times churn; the rows
    a reviewer can act on are the ones in the diff in front of them. With no
    changed-file list (a push, a shallow clone) every row is a candidate. The
    cap comes last, so the function the gate stopped is never the row it drops.
    """
    active = (worklist or {}).get("active") or []
    if changed:
        active = _in_diff(active, changed)
    return _named_first(active, named)[:top]


def _remedy(row: dict) -> str:
    """The run's verdict on the row, and whether the committed ratchet already
    carries a mark for it: signed debt an untouched function sits on should
    not read like the pull request's own new function."""
    remedy = row.get("remedy") or "-"
    return f"{remedy} (accepted debt)" if row.get("ratchet_mark") is not None else remedy


def _cell(row: dict) -> str:
    return (f"| `{row.get('path')}:{row.get('start')}` | `{row.get('function')}` "
            f"| {row.get('ccn')} | {row.get('risk')} | {_remedy(row)} |")


def table(entries: list[dict]) -> str:
    if not entries:
        return "No ranked function in these files."
    return "\n".join([_HEADER] + [_cell(row) for row in entries])


def _scope_line(changed: list[str], entries: list[dict]) -> str:
    if changed:
        return f"### Worklist: {_plural(len(changed), 'changed file')}"
    return f"### Worklist: the whole repository, top {len(entries)}"


def body(coverage, verify, exit_code: int, worklist, changed: list[str], top: int,
         base_reason: str | None = None, coverage_exit: int = 0) -> str:
    """The whole comment. The marker leads, so a truncated body still carries
    it and the next run still edits this comment instead of adding one."""
    entries = rows(worklist, changed, top, named_by_findings(verify))
    verdict = (no_verdict_line(coverage, coverage_exit) if coverage_exit
               else verdict_line(verify, exit_code, base_reason))
    return "\n".join([MARKER, "", "## crapkit", "",
                      scored_line(coverage), "",
                      verdict, "",
                      _scope_line(changed, entries), "",
                      table(entries), ""])


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coverage", help="crapkit coverage --json output")
    parser.add_argument("--coverage-exit", type=int, default=0,
                        help="coverage's exit code; non-zero means verify was not run")
    parser.add_argument("--verify", help="crapkit verify --json output")
    parser.add_argument("--verify-exit", type=int, default=0, help="verify's exit code")
    parser.add_argument("--base-sha", help="file holding the fork point the base run was made at; "
                        "empty or missing when it was not")
    parser.add_argument("--base-reason", help="file holding why the base run was not made")
    parser.add_argument("--worklist", help="crapkit worklist --json output")
    parser.add_argument("--changed", help="file holding one changed path per line")
    parser.add_argument("--top", type=int, default=5, help="rows to render (default 5)")
    parser.add_argument("--out", required=True, help="where to write the markdown")
    parser.add_argument("--json-out", help="where to write the {\"body\": ...} gh api sends")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    text = body(_read_json(args.coverage), _read_json(args.verify), args.verify_exit,
                _read_json(args.worklist), _read_lines(args.changed), args.top,
                _base_reason(args.base_sha, args.base_reason), args.coverage_exit)
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"body": text}),
                                       encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
