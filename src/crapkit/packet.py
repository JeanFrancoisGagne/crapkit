"""The start-editing packet: everything a session needs before it opens the file.

`brief` answered what one function scores. A session then read the file to find
the other functions in it, guessed which ceiling the gate would apply, hunted for
the lane that measures the scope, and re-derived the commands to run. Each of
those is a value some caller already holds, so each is a field here instead of a
round trip.

The caller reads the store, git, configuration and file texts once per batch.
This module formats those values; command quoting follows the host platform's
shells. The packet keeps the existing `brief --json` field types.
"""
from __future__ import annotations

import base64
import os
import re
import shlex

from .ratchet_report import DAY, mark_age_days
from .keys import lookup, position, require_unambiguous

# What the gate actually enforces, said once. A session that reads a ceiling of
# 6 beside a standing mark of 72 otherwise reads a contradiction and either
# refuses to start or "fixes" debt nobody asked it to touch.
GATE_BINDS = ("changed functions only; a ratchet mark pardons standing debt "
              "at or under it")

_OPENERS = "([{<"
_CLOSERS = ")]}>"

# What lizard calls a function it could not name. Every anonymous function in a
# file prints the same string, which is why the handle below exists.
ANONYMOUS = "(anonymous)"

# `stale` clears when a run lands on the current commit and never before. The
# packet used to answer its own staleness warning with another `brief`, which
# re-reads the snapshot that is already stale. `--reuse-unchanged` reruns only
# the lanes whose scope files moved and parses the rest off the artifacts they
# already have, so it is the cheapest call that still writes a run.
#
# Every command the packet names is spelled as the console script, the
# resolution the hooks and the plugin manifest already trust (#20, #37). Bare
# `python` resolves to the WindowsApps stub, to a venv without crapkit, or,
# for a child of a venv interpreter launched without a shell, to the base
# interpreter the venv wraps: Windows searches the parent application's
# directory before PATH, and a venv's python.exe is a trampoline for that base.
REFRESH = "crapkit coverage --reuse-unchanged"


def function_source(text: str | None, start: int, end: int) -> str | None:
    """One function's lines out of the file text the caller already read.

    None means nobody read the file, which is not the same as a function whose
    span holds no lines.
    """
    if text is None:
        return None
    return "\n".join(text.splitlines()[start - 1:end])


def file_functions(rows) -> list[dict]:
    """Every scored row in the file, not just the one the brief is about.

    A decomposition lands in the neighbours: the helper it extracts into, the
    twin beside it, the row that is already at its ceiling and must stay there.
    """
    return [{"function": r.long_name, "start": r.start, "end": r.end, "ccn": r.ccn,
             "crap": r.crap, "remedy": r.remedy, "occurrence": position(r)[1]} for r in rows]


def file_totals(rows, scope_targets: dict, target: int) -> dict:
    """The file's own numbers, each row judged against ITS scope's ceiling.

    A file can hold rows from two scopes; scoring the whole file against one
    ceiling would report debt a per-scope target deliberately allows.
    """
    over = sum(1 for r in rows if r.crap > scope_targets.get(r.scope, target))
    return {"functions": len(rows), "over_target": over,
            "crap_load": round(sum(r.crap for r in rows), 2)}


def gate_rule(*, ceiling: int, mark: float | None, mark_age_days: int | None,
              diff_uncovered_max: int | None) -> dict:
    """The rule this function will be judged by, spelled out rather than implied."""
    return {"ceiling": ceiling, "binds": GATE_BINDS, "ratchet_mark": mark,
            "mark_age_days": mark_age_days, "diff_uncovered_max": diff_uncovered_max}


def lane_for(scope: str | None, lanes):
    """The first lane claiming this scope, or None when no lane measures it."""
    if scope is None:
        return None
    return next((lane for lane in lanes if scope in lane.scopes), None)


def lane_record(lane) -> dict | None:
    """The lane verbatim: what ran, where, and how long it is allowed to take.

    A session that reruns the lane by hand needs the cwd and the env as declared;
    reconstructing them from the command string is how the reruns drift.
    """
    if lane is None:
        return None
    return {"name": lane.name, "command": lane.command, "artifact": lane.artifact,
            "parser": lane.parser, "cwd": lane.cwd, "env": dict(lane.env),
            "timeout_seconds": lane.timeout_seconds}


def _windows_encoded(arguments: list[str]) -> str:
    """Cross cmd expansion and PowerShell parsing without exposing path text."""
    quoted = " ".join("'" + arg.replace("'", "''") + "'" for arg in arguments)
    script = ("$command = Get-Command crapkit -CommandType Application -TotalCount 1 -ErrorAction Stop; "
              "$LASTEXITCODE = 1; & $command.Source " + quoted + "; exit $LASTEXITCODE")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded


def _windows_argument(argument: str) -> str:
    if argument in ("--", "--gate") or re.fullmatch(r"[\w./:\\][-\w./:\\]*", argument, re.ASCII):
        return argument
    return '"' + argument + '"'


def _console_command(arguments: list[str]) -> str:
    if os.name != "nt":
        return "crapkit " + " ".join(shlex.quote(arg) for arg in arguments)
    interpreted = set('%!$`\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029')
    if any(interpreted.intersection(arg) for arg in arguments):
        return _windows_encoded(arguments)
    return "crapkit " + " ".join(_windows_argument(arg) for arg in arguments)


def _file_command(command: str, path: str, flags=()) -> str:
    arguments = [command, path, *flags]
    if path.startswith("-"):
        arguments = [command, *flags, "--", path]
    return _console_command(arguments)


def commands(path: str, scoped: bool, note: str = "") -> dict:
    """The four commands a session runs next, with the paths already filled in.

    `refresh_writes_run` says refresh creates a coverage run. The other commands
    can also write artifacts, caches or verification records.
    """
    out = {"gate": _file_command("rescore", path, ["--gate"]),
           "scoped_tests": _file_command("test-scoped", path) if scoped else None,
           "verify": "crapkit verify",
           "refresh": REFRESH,
           "refresh_writes_run": True}
    if not scoped and note:
        out["scoped_tests_note"] = note
    return out


def bare_name(long_name: str) -> str:
    """The identifier a long_name opens with, before its parameter list.

    Two cuts, because lizard's readers spell a parameter list two ways. Python
    and shell close the name with `(` — `classify( score , limit = 1 )`,
    `classify()` — and Rust and Go print the parameters after a space with no
    parenthesis at all: `route cmd : & Cmd`, `Classify n int`. Cutting only at
    the `(` handed those back whole, so the handle a packet published was a
    signature no command would accept back.

    The leading token settles both. It moves no parenthesised language, because
    none of those puts a space before the `(`: `n::K::m( int a)` keeps its
    namespace and an Objective-C `doThing:( int )` keeps its selector colon.

    Empty for a function lizard could not name: both `(anonymous)` and
    `(anonymous) ( z )` open with the parenthesis, so an empty prefix IS the
    test for anonymity, with no second string to keep in step.
    """
    head = long_name.split("(")[0].strip()
    return head.split()[0] if head else ""


def exact_names(names, name: str) -> list[str]:
    """The long names `name` names outright: the whole string, or the bare one."""
    return [n for n in names if name in (n, bare_name(n))]


def matching_names(names, name: str) -> list[str]:
    """The long names one NAME resolves to, in the order `names` arrived.

    Exact first, the fragment second. `brief` matched only exactly and `explain`
    only loosely, so `route` picked one function in one command and three —
    `route`, `route_chain`, `route_num` — in the other, off the same string in
    the same payload. Nesting names is the ordinary case, so the loose command
    was wrong far more often than the strict one was unhelpful.

    The fragment survives as the fallback because a name nobody owns is usually
    a typo, and listing everything holding it is what tells a session which name
    it meant. An empty NAME resolves to nothing rather than to everything.
    """
    if not name:
        return []
    return exact_names(names, name) or [n for n in names if name in n]


def anonymous_positions(rows) -> list[tuple]:
    """Anonymous locations in source order, with scope copies shared."""
    rows = list(rows)
    require_unambiguous(rows)
    return sorted({lookup(r) for r in rows if not bare_name(r.long_name)},
                  key=lambda place: (place[2], place[3], place[1]))


def handles(rows) -> dict[tuple, str]:
    """The handle for every row in one file, keyed by its full stored location.

    Named twins carry #N, including #1; overloads retain their full signature.
    Anonymous handles count all anonymous spans in file order. Duplicate scopes
    share a span and a handle. Moving lines above a function keeps its ordinal.

    """
    rows = list(rows)
    require_unambiguous(rows)
    groups: dict[str, dict[str, set[tuple]]] = {}
    for row in rows:
        groups.setdefault(bare_name(row.long_name), {}).setdefault(row.long_name, set()).add(lookup(row))
    found = _named_handles(groups)
    found.update({place: f"{ANONYMOUS}#{n}"
                  for n, place in enumerate(anonymous_positions(rows), 1)})
    return found


def _named_handles(groups: dict) -> dict[tuple, str]:
    found = {}
    for siblings in groups.values():
        for name, starts in siblings.items():
            label = name if len(siblings) > 1 else bare_name(name)
            for n, start in enumerate(sorted(starts), 1):
                found[start] = f"{label}#{n}" if len(starts) > 1 else label
    return found


def handle_names(rows) -> list[str]:
    """Every anonymous handle this file offers, in order.

    What an out-of-range ordinal is reported against: a session that guessed #5
    needs the two that exist, the same way a wrong bare name gets the file's
    real names back.
    """
    return [f"{ANONYMOUS}#{n}" for n in range(1, len(anonymous_positions(rows)) + 1)]


def handle_ordinal(name: str) -> int | None:
    """The N in `(anonymous)#N`, or None when `name` is some other name form.

    None rather than an error: this is the question "is that string a handle",
    asked before the other name forms get their turn.
    """
    head, sep, tail = name.partition("#")
    if not sep or head.strip() != ANONYMOUS or not tail.isdigit():
        return None
    return int(tail)


def budget(row, ceiling: int) -> dict:
    """What the work costs: pieces a decomposition needs, decision paths no test
    walks.

    One definition for both readers. `next-item` published these and `brief` did
    not, so a session that opened on a packet re-derived numbers the queue had
    already computed — and two derivations of one formula drift with nothing to
    catch it.
    """
    return {"est_splits": 0 if row.ccn <= ceiling else -(-row.ccn // ceiling),
            "est_uncovered_paths": max(0, round((1 - row.cov) * row.ccn))}


def regrowth(history: list[dict]) -> dict:
    """Whether this function's complexity fell and then came back.

    A function somebody already decomposed once, back over its ceiling, is a
    different job from one that has always been big: the decomposition that was
    tried is on record and did not hold.
    """
    return {"regrown": _fell_then_rose([h["ccn"] for h in history]),
            "history": [[h["run_id"], h["ccn"]] for h in history]}


def _fell_then_rose(ccns: list[int]) -> bool:
    """True once a drop is followed anywhere later by a climb."""
    fell = False
    for before, after in zip(ccns, ccns[1:]):
        if fell and after > before:
            return True
        fell = fell or after < before
    return False


def params(long_name: str) -> list[dict]:
    """The parameter list out of lizard's long_name, name first.

    lizard prints the signature it parsed: `f( a , b = 1 , c : int = 2 )` in
    Python, `dispatch ( a , b Record , c )` in TypeScript. The name leads in
    both; whatever follows it is the type annotation as lizard printed it.
    Anything this cannot read is an empty list, never a guess.
    """
    inner = _param_text(long_name)
    if inner is None:
        return []
    return [_one_param(part) for part in _split_top(inner) if part]


def _param_text(long_name: str) -> str | None:
    """What sits inside the LAST balanced parentheses, or None when there are none.

    Not the first `(`: lizard names an anonymous function `(anonymous) ( z )`,
    where the first one belongs to the name and the parameter list is the group
    that closes the string.
    """
    closed = long_name.rfind(")")
    opened = _matching_open(long_name, closed)
    return None if opened is None else long_name[opened + 1:closed]


def _matching_open(text: str, closed: int) -> int | None:
    """The index of the `(` that opens the group closing at `closed`."""
    depth = 0
    for i in range(closed, -1, -1):
        depth += (text[i] == ")") - (text[i] == "(")
        if depth == 0 and text[i] == "(":
            return i
    return None


def _split_top(text: str) -> list[str]:
    """Split on commas that are not inside brackets, so `Map<a , b>` stays one."""
    parts = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def _one_param(part: str) -> dict:
    """One parameter as {name, type}. The default value is not part of either."""
    head = part.split("=")[0].strip()
    if ":" in head:
        name, _, annotated = head.partition(":")
        return {"name": name.strip(), "type": annotated.strip() or None}
    if head.startswith("*"):
        return {"name": "".join(head.split()), "type": None}  # lizard prints `* args`
    name, _, trailing = head.partition(" ")
    return {"name": name, "type": trailing.strip() or None}


def coupling_partners(ranked: list[dict], path: str, is_test, top: int = 5) -> list[dict]:
    """One file's coupled partners out of the ranking every path shares.

    The ranking is global on purpose — a quiet file's own partners must not fall
    behind the repo's noisiest pairs — so it is computed once for a whole batch
    and cut per path here. `is_test` marks the partner that is a test file,
    which is the partner an agent edits rather than reads.
    """
    out = []
    for pair in ranked:
        first, second = pair["files"]
        if path not in pair["files"]:
            continue
        other = second if first == path else first
        out.append({"path": other, "support": pair["support"],
                    "confidence": pair["confidence"], "is_test": is_test(other)})
    return out[:top]


def with_contained(twins: list[dict]) -> list[dict]:
    """Twins, each saying whether it is wholly contained in the target.

    A twin the duplication pass did not flag reads as not contained rather than
    as unknown: `contained` is a claim about the shingles, and no claim is False.
    """
    return [{**t, "contained": bool(t.get("contained", False))} for t in twins]


def notes(cfg, scope) -> dict:
    """The prose the config carries for this repo and this scope, or nulls.

    The config's own scope_notes table is the source of truth for a scope;
    the record's attribute is the fallback. Read defensively: a config that
    declares no notes at all is the ordinary case, and the packet must not
    depend on any of these keys existing.
    """
    table = dict(getattr(cfg, "scope_notes", None) or {})
    scoped = list(table.get(_scope_name(scope)) or ()) or _note_of(scope)
    return {"repo": _note_of(cfg), "scope": scoped or None}


def _scope_name(scope) -> str | None:
    named = getattr(scope, "name", None)
    return named or (scope if isinstance(scope, str) else None)


def _note_of(holder) -> list[str] | str | None:
    found = getattr(holder, "notes", None) or getattr(holder, "note", None)
    if found is None:
        return None
    return list(found) if isinstance(found, tuple) else found


def versions_block(report: dict, analysis_version: int) -> dict:
    """What produced these numbers: the tools, plus the metric's own version.

    A packet outlives the run it describes. Without the analysis version, marks
    and scores from two metric generations read as one series.
    """
    return {**report, "analysis_version": analysis_version}
