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
import json
from pathlib import Path

# The line that makes the comment findable. The action greps for it to decide
# between a POST and a PATCH, so a second spelling means a comment per push
# instead of one comment edited in place. tests/unit/test_action_contract.py
# pins this string against the one action.yml searches for.
MARKER = "<!-- crapkit-action -->"

_HEADER = "| File | Function | ccn | risk | remedy |\n|---|---|---:|---:|---|"


def _read_json(path: str | None) -> dict | None:
    """A payload, or None when the command that writes it did not.

    A crapkit read command with no run behind it exits 1 and prints its reason
    on stderr, leaving an empty redirect target. The comment says so; it does
    not crash on it.
    """
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _read_lines(path: str | None) -> list[str]:
    """The changed-file list, empty when there is none."""
    if not path:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip().replace("\\", "/") for line in text.splitlines() if line.strip()]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def scored_line(coverage: dict | None) -> str:
    """What the run measured, in the words `crapkit coverage` uses for it."""
    if not coverage:
        return "`crapkit coverage` wrote no run summary."
    return (f"{_plural(coverage.get('functions', 0), 'function')} in "
            f"{_plural(coverage.get('files', 0), 'file')}, "
            f"{coverage.get('over_target', 0)} over target, "
            f"CRAP load {coverage.get('crap_load', 0)}, "
            f"grade {coverage.get('grade', '?')}.")


def _findings(verify: dict) -> str:
    parts = [_plural(len(verify.get("gate_violations", [])), "gate violation"),
             _plural(len(verify.get("ratchet_regressions", [])), "ratchet regression"),
             _plural(len(verify.get("new_failures", [])), "new test failure"),
             _plural(verify.get("diff_uncovered_count", 0), "uncovered changed line")]
    return ", ".join(parts)


def _against(verify: dict) -> str:
    return (f"Run {verify.get('run_id')} against baseline {verify.get('baseline_run')}, "
            f"{_plural(verify.get('changed_files', 0), 'changed file')}")


def verdict_line(verify: dict | None, exit_code: int) -> str:
    """One line for the whole verdict, the exit code included.

    verify reports the first of 6, 7, 8, 9 that fires, so the code says which
    rule refused the tree and the counts say what it found.
    """
    if verify is None:
        return (f"**`crapkit verify` exited {exit_code} and wrote no verdict.** "
                f"Read the job log: this is tooling, not a score.")
    if verify.get("ok"):
        return f"**verify passed.** {_against(verify)}."
    return f"**verify failed, exit {exit_code}.** {_against(verify)}: {_findings(verify)}."


def _in_diff(active: list[dict], changed: list[str]) -> list[dict]:
    """worklist paths are root-relative and unquoted, and so is `git diff
    --name-only` under crapkit's own `core.quotePath=false`, so this is a
    string match and not a path walk."""
    return [row for row in active if row.get("path") in changed]


def rows(worklist: dict | None, changed: list[str], top: int) -> list[dict]:
    """The ranked rows for the changed files, worst first, capped at `top`.

    `crapkit worklist` ranks the whole repository by ccn times churn; the rows
    a reviewer can act on are the ones in the diff in front of them. With no
    changed-file list (a push, a shallow clone) every row is a candidate.
    """
    active = (worklist or {}).get("active") or []
    if changed:
        active = _in_diff(active, changed)
    return active[:top]


def _cell(row: dict) -> str:
    return (f"| `{row.get('path')}:{row.get('start')}` | `{row.get('function')}` "
            f"| {row.get('ccn')} | {row.get('risk')} | {row.get('remedy') or '-'} |")


def table(entries: list[dict]) -> str:
    if not entries:
        return "No ranked function in these files."
    return "\n".join([_HEADER] + [_cell(row) for row in entries])


def _scope_line(changed: list[str], entries: list[dict]) -> str:
    if changed:
        return f"### Worklist: {_plural(len(changed), 'changed file')}"
    return f"### Worklist: the whole repository, top {len(entries)}"


def body(coverage, verify, exit_code: int, worklist, changed: list[str], top: int) -> str:
    """The whole comment. The marker leads, so a truncated body still carries
    it and the next run still edits this comment instead of adding one."""
    entries = rows(worklist, changed, top)
    return "\n".join([MARKER, "", "## crapkit", "",
                      scored_line(coverage), "",
                      verdict_line(verify, exit_code), "",
                      _scope_line(changed, entries), "",
                      table(entries), ""])


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coverage", help="crapkit coverage --json output")
    parser.add_argument("--verify", help="crapkit verify --json output")
    parser.add_argument("--verify-exit", type=int, default=0, help="verify's exit code")
    parser.add_argument("--worklist", help="crapkit worklist --json output")
    parser.add_argument("--changed", help="file holding one changed path per line")
    parser.add_argument("--top", type=int, default=5, help="rows to render (default 5)")
    parser.add_argument("--out", required=True, help="where to write the markdown")
    parser.add_argument("--json-out", help="where to write the {\"body\": ...} gh api sends")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    text = body(_read_json(args.coverage), _read_json(args.verify), args.verify_exit,
                _read_json(args.worklist), _read_lines(args.changed), args.top)
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"body": text}),
                                       encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
