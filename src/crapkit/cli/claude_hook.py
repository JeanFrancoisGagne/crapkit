"""Protocol 1: one Claude Code PostToolUse payload on stdin, a ccn advisory out.

Exit 2 with three lines of stderr is the only thing this ever says, and it says
it about exactly one thing: a function the edit changed, in a scope crapkit
measures, over its ceiling, carrying no ratchet mark. Everything else is exit 0
and silence: the malformed payload, the unmeasured repo, the half-typed source
and the internal exception included.

That silence is the design, not laziness. On PostToolUse a nonzero exit that is
not 2 is invisible and a 2 is text the model has to read, so a hook that fires
where crapkit measures nothing is either useless or unbearable; 47.5% of the
edits this was measured against land in repos with no crapkit.toml. It is also
why this rung diverges from the house exit-3 policy: a hook that exited 3 in
every unmeasured repo could not be installed machine-wide at all.

The hook never blocks and never says it did. PostToolUse runs after the write.
`hook-precommit` stays the only enforcement point.

Two constraints shape the code rather than the contract:

- Module scope is stdlib only, and stays that way. `_Handler` imports this
  module before the body runs, so anything imported here is paid by every edit
  on the machine, including the ones in repos crapkit never measures.
- The snapshot store is never opened. `SnapshotStore.__init__` has no read-only
  path: it runs the schema script, seeds, and applies ALTER TABLE migrations, so
  a per-edit hook would migrate whatever store it touched, and with two crapkit
  versions installed whichever fired first would rewrite the schema. It has no
  busy timeout either, so a store the weekly job holds means an uncaught
  OperationalError and a multi-second stall that PostToolUse renders invisible.
  Nothing here writes anything, for the same reason.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROTOCOL = "1"

# Git state meaning the working tree holds content this edit did not author.
_SEQUENCING_MARKERS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD")

# Deeper than any real checkout, bounded so a pathological path cannot turn the
# root walk into a filesystem scan.
_MAX_LEVELS = 64


def cmd_claude_hook(args) -> int:
    """The whole subcommand, wrapped in the catch-all the contract promises.

    An uncaught failure here would be exit 1, which PostToolUse shows nobody,
    after a stall the harness waits out. Silence is the same answer without the
    stall.
    """
    try:
        return _advise(args, sys.stdin)
    except Exception:  # noqa: BLE001 - the catch-all IS the contract
        return 0


def _advise(args, stream) -> int:
    """The ladder. Each rung that fails to advance exits 0 and says nothing."""
    payload = _payload(stream)
    edited = _edited_file(payload)
    if not edited:
        return 0
    path = _edited_path(payload, edited)
    root = _repo_root(path.parent)
    if root is None or _sequencing(root) or args.protocol != PROTOCOL:
        return 0
    return _judge(root, path.relative_to(root).as_posix())


def _payload(stream) -> dict:
    """The event as a dict; anything that is not one JSON object reads as no event."""
    event = json.loads(stream.read())
    return event if isinstance(event, dict) else {}


def _edited_file(payload: dict) -> str:
    """The path this event edited, or "" when protocol 1 does not judge the event.

    PostToolUse only: PreToolUse arrives before the edit lands and judges source
    that does not exist yet, and a Stop hook's exit 2 blocks the stop, which on a
    verdict read off the filesystem is an infinite loop generator. NotebookEdit
    carries `notebook_path`, so it falls out here rather than needing a rule.
    """
    if payload.get("hook_event_name") != "PostToolUse":
        return ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    edited = tool_input.get("file_path")
    return edited if isinstance(edited, str) else ""


def _edited_path(payload: dict, edited: str) -> Path:
    """The edited file as an absolute path.

    A relative one is read against the event's own `cwd`, the only base the
    payload offers. `${CLAUDE_PROJECT_DIR}` is deliberately absent from this
    module: it stays at the session root while `cwd` follows a worktree, so an
    edit inside a worktree would resolve to the mainline checkout's store with
    the edited file untracked from that root.
    """
    path = Path(edited)
    if path.is_absolute():
        return path
    return Path(payload.get("cwd") or ".") / path


def _repo_root(start: Path) -> Path | None:
    """The crapkit root above an edited file, or None when there is none.

    First directory holding `crapkit.toml` wins. A `.git` entry without one stops
    the walk: a linked worktree carries `.git` as a FILE, and walking past it
    would lend that worktree its parent checkout's config and its parent's store.
    That refusal is the same one `_load_repo_config` makes by never walking up at
    all. Each level costs one stat.
    """
    for directory in [start, *start.parents][:_MAX_LEVELS]:
        if (directory / "crapkit.toml").is_file():
            return directory
        if (directory / ".git").exists():
            return None
    return None


def _sequencing(root: Path) -> bool:
    """True mid-rebase, mid-merge or mid-cherry-pick.

    lizard reads live conflict markers as two coexisting copies of every
    function, and the changed-range rule inverts against the rebase's temporary
    HEAD: the same function draws opposite verdicts depending on which direction
    the rebase runs.
    """
    git_dir = root / ".git"
    return any((git_dir / marker).exists() for marker in _SEQUENCING_MARKERS)


def _judge(root: Path, rel: str) -> int:
    """Rungs 6 to 9: scope, analysis, verdict, output.

    The statement order is the latency budget. `git diff` on one file costs
    31.4 ms and importing lizard costs 38.1, so the diff is started first and
    finishes inside the import that follows it.
    """
    cfg = _config(root)
    in_scope = _scoped(cfg, rel)
    if in_scope is None:
        return 0
    diff = _diff_proc(root, rel)
    records = _records(root, rel)
    ranges = _changed(root, rel, diff.communicate()[0])
    breaches, ceiling = _verdict(cfg, in_scope, rel, records, ranges)
    return _report(root, cfg, rel, breaches, ceiling, _keys(records))


def _config(root: Path):
    """crapkit.toml, parsed straight rather than through `cli._shared`, whose
    module scope imports the snapshot store this hook must never open."""
    from ..config import load_config_text

    return load_config_text((root / "crapkit.toml").read_text(encoding="utf-8"))


def _scoped(cfg, rel: str) -> dict | None:
    """The scope assignment for the one edited path, or None when no scope claims it.

    1.1 ms, and it runs before anything imports lizard. The loud unscoped warning
    stays where it already lives, in `hook-precommit` at commit time; per edit,
    silence wins.
    """
    from ..universe import assign_files

    in_scope = assign_files([rel], cfg)
    return in_scope if any(in_scope.values()) else None


def _diff_proc(root: Path, rel: str):
    """`git diff HEAD` for one file, started and not awaited.

    Scoped to the path on purpose: 31.4 ms against 92.4 for the whole tree. Its
    stderr is dropped because every way this fails (no HEAD, no git, a path git
    dislikes) is the same answer, silence.

    diff.relative because `rel` is relative to the crapkit root and git names a
    diff's files relative to the repo TOP: under a root one directory down the
    lookup in `_changed` missed every time and read as "nothing touched", so a
    breaching edit drew silence. This spawn is its own, not gitio's, so it needs
    its own flag.
    """
    return subprocess.Popen(
        ["git", "-c", "diff.relative=true", "diff", "HEAD", "-U0", "--no-renames", "--", rel],
        cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace")


def _records(root: Path, rel: str) -> list:
    """The edited file's functions, off the working tree the edit just landed in.

    This import is what pulls lizard in, so it happens here, with the diff
    subprocess already running. Source nobody can parse yields zero functions and
    therefore zero breaches, which is the right failure direction for a hook that
    fires while an agent is still typing.
    """
    from ..analyze import analyze_source

    return analyze_source(rel, (root / rel).read_text(encoding="utf-8", errors="replace"))


def _changed(root: Path, rel: str, diff_text: str):
    """New-side ranges this edit changed, or None when the file is untracked.

    None is not "nothing changed": git diff cannot see a file it never recorded,
    and reading its empty diff as an empty change set would pass every function
    in it. That is the one state where the verdict would lie, so an untracked
    file is judged in full, exactly as `rescore --gate` judges one.
    """
    if diff_text.strip():
        from ..diffparse import changed_ranges

        return changed_ranges(diff_text).get(rel, [])
    return [] if _tracked(root, rel) else None


def _tracked(root: Path, rel: str) -> bool:
    """Whether git has this one path in the index. Asked only when the diff came
    back empty, which is the only case that cannot tell untracked from unchanged."""
    listed = subprocess.run(["git", "ls-files", "--", rel], cwd=root, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return bool(listed.stdout.strip())


def _verdict(cfg, in_scope: dict, rel: str, records: list, ranges) -> tuple[list, int]:
    """The breaching functions and the ceiling they broke.

    `file_ceilings` is the commit gate's own map, so a mid-session advisory and
    the commit's verdict cannot disagree about which number applies.
    """
    from ..hook import file_ceilings

    ceiling = file_ceilings(cfg, in_scope, [rel])[rel]
    return _breaches(records, ranges, ceiling), ceiling


def _breaches(records: list, ranges, ceiling: int) -> list:
    """Functions over the ceiling this edit is answerable for, worst first."""
    over = [rec for rec in records if rec.ccn > ceiling]
    return sorted(_answerable(over, ranges), key=lambda rec: (-rec.ccn, rec.start))


def _answerable(over: list, ranges) -> list:
    """Of the over-ceiling functions, the ones this edit has to answer for.

    Judging the whole file instead of the changed ranges would flag every legacy
    function in it, so on any repo with seeded debt the advisory fires on every
    edit and says nothing. `ranges` None inverts that: the file is untracked, git
    diff can see none of it, and every function in it counts.
    """
    from ..hook import _touches

    if ranges is None:
        return over
    return [rec for rec in over if _touches(rec, ranges)]


def _keys(records: list) -> dict:
    """The file's ratchet keys, built from every record rather than the breaching
    ones: the ordinal counts same-named functions in file order."""
    from ..keys import key_names

    return key_names(records)


def _report(root: Path, cfg, rel: str, breaches: list, ceiling: int, keys: dict) -> int:
    """Rung 9. stdout stays empty whatever happens: protocol 1 reserves it for a
    future JSON channel, and Claude Code parses stdout JSON on exit 0."""
    from ..keys import key_of

    marked = _marks_for(root / cfg.ratchet_file, rel)
    unmarked = [rec for rec in breaches if key_of(keys, rec)[1] not in marked]
    if not unmarked:
        return 0
    for line in _advisory_lines(rel, unmarked, ceiling):
        print(line, file=sys.stderr)
    return 2


def _marks_for(marks_path: Path, rel: str) -> set[str]:
    """The ratchet KEY names one file carries marks for, `#N` ordinals included.

    Existence, not the numeric high-water rule `verify` applies: crap needs
    coverage, coverage needs the store, and the store stays closed. A mark is a
    recorded decision to carry that function as it stands, so without this the
    advisory nags about debt the repo already signed for on every edit.

    Read as lines, not as parsed entries: building 40,303 of them to answer one
    file's question costs 35 ms, and the answer is a prefix test.
    """
    if not marks_path.is_file():
        return set()
    prefix = rel + "\t"
    text = marks_path.read_text(encoding="utf-8", errors="replace")
    return {line[len(prefix):].rsplit("\t", 1)[0]
            for line in text.splitlines() if line.startswith(prefix)}


def _advisory_lines(rel: str, breaches: list, ceiling: int) -> list[str]:
    """The advisory, word for word.

    It never claims the edit was blocked, never opens with "crapkit gate:", and
    never asks for a decomposition "before committing". PostToolUse cannot block
    and the edit is already on disk, so the commit gate's own wording would tell
    an agent its landed edit was rejected when it was not. The head line says the
    opposite outright, because the reader is a model holding a nonzero exit code.
    """
    head = (f"crapkit advisory: {len(breaches)} function(s) over ceiling {ceiling} "
            f"in {rel} (the edit landed; nothing was blocked)")
    body = [f"  ccn {rec.ccn}  {rel}:{rec.start}  {rec.long_name}" for rec in breaches]
    return [head, *body,
            "the commit gate enforces this; decompose there or mark the debt"]
