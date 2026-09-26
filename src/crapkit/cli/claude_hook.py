"""Protocol 1: one Claude Code PostToolUse payload on stdin, a ccn advisory out.

Exit 2 with three lines of stderr is the only thing this ever says, and it says
it about an edit in a scope crapkit measures: a function the edit changed, over
its ceiling, carrying no ratchet mark; or a changed file the edit left unjudged,
because no reader could read it, because git names it in bytes that are not
UTF-8, or because git ran and could not report what the edit changed. The
unjudged lines name the file and the reason; the commit gate refuses the first
two once staged. Everything else is exit 0 and silence: the malformed payload,
the unmeasured repo, a machine with no git and the internal exception included.

An Edit, Write or MultiEdit event names its file in `tool_input.file_path` and
is judged as that one file. A Bash event carries `tool_input.command` instead —
a heredoc or `python - <<'PY'` writes source no file_path ever names — so it
falls back to the working tree: the changed *.py files fresh enough for this
command to have plausibly written, each through the same per-file ladder. Fresh
means an mtime inside the window and bytes this session has not judged yet, so
a touch or a same-bytes rewrite never repeats an advisory (`_Memory`).

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
- The snapshot store is never opened. The advisory needs source, configuration
  and committed ratchet marks; opening a store would add schema inspection and
  database I/O to every edit. Old stores can still need a migration. The hook
  stays independent of that lifecycle. The one thing it writes is its session
  memory, under the git directory, so the working tree stays byte-identical.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

PROTOCOL = "1"

# Git state meaning the working tree holds content this edit did not author.
_SEQUENCING_MARKERS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD")

# The Bash fallback's first filter: a dirty *.py whose mtime is older than this
# was not written by the command this event reports, so advising it again would
# repeat the advisory on every later Bash call in the session. A touch or a
# same-bytes rewrite moves an mtime into the window with no new content, so
# `_Memory` is the second filter. Content that lands with an old mtime (`mv`,
# `cp -p`, an unpacked archive, a write more than this long before the event)
# is never judged here: the documented miss, and the commit gate's to catch.
_FRESH_WINDOW_SECONDS = 12

# And its bound: PostToolUse waits this process out, so a huge dirty tree is a
# stall, not a license to judge everything in it.
_MAX_COMMAND_FILES = 25

# The session memory: `<git dir>/crapkit/claude-hook/<session_id>/`, one small
# file per judged path. A session idle this long is pruned when another starts;
# one resumed after that hears each advisory once more, and loses nothing else.
_MEMORY_DIR = ("crapkit", "claude-hook")
_MEMORY_DAYS = 7
_SESSION_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_SESSION_MAX = 128

# The closing line of each advisory that judged nothing: what to do next.
_GIT_NEXT = "fix what git reports; until git can read this repository, no edit in it is judged"


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
    if args.protocol != PROTOCOL:
        return 0
    memory = _memory(payload)
    edited = _edited_file(payload)
    if edited:
        return _judge_path(_edited_path(payload, edited), memory)
    return _advise_command(payload, memory)


def _judge_path(path: Path, memory: _Memory) -> int:
    """Root discovery and judgement for one absolute file path: the tail every
    event shape shares once it holds a file to answer for.

    The path below the root takes the case its directories list: on a
    case-insensitive disk a payload's `CALC\\mod.py` opens calc/mod.py, and
    keyed as typed it matched no scope, so a breach went unadvised, while
    `calc\\Mod.py` read the tracked file as untracked and advised debt the
    edit never touched."""
    from ..repopath import tracked_spelling

    root = _repo_root(path.parent)
    if root is None or _sequencing(root):
        return 0
    return _judge(root, tracked_spelling(root, path.relative_to(root).as_posix()), memory)


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
    return _native_path(edited, payload.get("cwd"))


def _native_path(raw: str | None, stand: str | None = None) -> Path:
    """A payload path as this OS opens it, through repopath's typed entry,
    imported here so an event that judges nothing never loads it. Claude Code on
    Windows reports a session cwd as `/c/Users/...`, and a model can write a
    file_path the same way; either one read as a Windows path named no
    directory. A payload with no `cwd` reads as the directory the hook runs in."""
    from ..repopath import typed_path

    return typed_path(raw or ".", stand or "")


def _command_event(payload: dict) -> bool:
    """Whether this is a PostToolUse for a tool that wrote through the shell.

    Bash carries `tool_input.command` and never `file_path`, so protocol 1 has
    no single file to judge and reads the working tree instead. Shape-based like
    `_edited_file`: NotebookEdit and friends carry no `command` and fall out
    here rather than needing a rule.
    """
    if payload.get("hook_event_name") != "PostToolUse":
        return False
    tool_input = payload.get("tool_input")
    return isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str)


def _advise_command(payload: dict, memory: _Memory) -> int:
    """The Bash fallback: judge the fresh *.py files the working tree changed.

    A shell heredoc or `python - <<'PY'` writes source no Edit event ever names,
    so judging only `file_path` left every Bash-written breach unadvised. Each
    file takes the same per-file ladder an Edit takes, so a file under no
    crapkit root, mid-sequencing, unscoped or marked stays silent, and exit 2
    means what it always means.
    """
    if not _command_event(payload):
        return 0
    top = _repo_top(_native_path(payload.get("cwd")))
    if top is None:
        return 0
    # Every file is judged and prints its own block; exit 2 when any drew one.
    return max((_judge_path(path, memory) for path in _fresh_python(top, memory)), default=0)


def _repo_top(cwd: Path) -> Path | None:
    """The git working-tree top above the command's own cwd, or None outside any
    repo. The event's `cwd` is where the command ran, and `status --porcelain`
    names every file relative to this top whatever directory asks. A directory
    named in bytes that are not UTF-8 keeps them, as every path git names does."""
    from ..repotext import escaped

    if not cwd.is_dir():
        return None
    res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True)
    top = escaped(res.stdout).strip()
    return Path(top) if res.returncode == 0 and top else None


def _fresh_python(top: Path, memory: _Memory) -> list[Path]:
    """Absolute paths of the changed *.py files this command plausibly wrote,
    at most `_MAX_COMMAND_FILES` of them."""
    cutoff = time.time() - _FRESH_WINDOW_SECONDS
    fresh: list[Path] = []
    for status, rel in _status_records(_porcelain(top)):
        if _unheard(top / rel, status, rel, cutoff, memory):
            fresh.append(top / rel)
        if len(fresh) == _MAX_COMMAND_FILES:
            break
    return fresh


def _unheard(path: Path, status: str, rel: str, cutoff: float, memory: _Memory) -> bool:
    """Dirty or untracked per git, on disk, with an mtime inside the window, and
    holding bytes no judgement in this session has read."""
    return _judgeable(status, rel) and _fresh(path, cutoff) and not memory.judged(path)


def _porcelain(top: Path) -> str:
    """`git status --porcelain -z` over the whole tree, or "" when git cannot
    answer. -uall, because a heredoc that creates a new DIRECTORY of source
    would otherwise arrive as one collapsed `?? newdir/` row naming no file.
    Each name keeps its bytes: read leniently, a Latin-1 name held U+FFFD,
    named no file on disk, and a breach written under it passed in silence."""
    from ..repotext import escaped

    res = subprocess.run(["git", "status", "--porcelain", "-z", "-uall"], cwd=top, capture_output=True)
    return escaped(res.stdout) if res.returncode == 0 else ""


def _status_records(text: str) -> Iterator[tuple[str, str]]:
    """(XY status, new-side path) per `--porcelain -z` record.

    -z is NUL-separated and never quoted, so a non-ASCII path arrives as
    itself. A rename or copy record carries the original name in a second
    field, consumed here so it cannot be read as the next record's status.
    """
    fields = text.split("\0")
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i][:2]
        yield status, fields[i][3:]
        i += 2 if status[:1] in ("R", "C") else 1


def _judgeable(status: str, rel: str) -> bool:
    """A *.py with content on disk. A deletion in either column has nothing
    left to judge, and every other language stays the commit gate's business:
    only Python is cheap enough to analyze per shell call."""
    return rel.endswith(".py") and "D" not in status


def _fresh(path: Path, cutoff: float) -> bool:
    """mtime inside the window: the first approximation of "this command wrote
    it", which `_Memory.judged` narrows to bytes the session has not judged.
    A path status names but disk lacks is not fresh, whatever the record said."""
    try:
        return path.stat().st_mtime >= cutoff
    except OSError:
        return False


def _repo_root(start: Path) -> Path | None:
    """The crapkit root above an edited file, or None when there is none.

    `rootfind.find_root`, the walk every command makes (ADR 0002): the nearest
    `crapkit.toml` wins and a `.git` entry without one stops the walk, so a
    linked worktree never borrows its parent checkout's config and store. The
    walk was born here and moved out when the commands adopted it; the import
    stays inside the function so this module's scope remains stdlib-only, and
    rootfind itself imports nothing of crapkit's.
    """
    from ..rootfind import find_root

    return find_root(start)


def _sequencing(root: Path) -> bool:
    """True mid-rebase, mid-merge or mid-cherry-pick.

    lizard reads live conflict markers as two coexisting copies of every
    function, and the changed-range rule inverts against the rebase's temporary
    HEAD: the same function draws opposite verdicts depending on which direction
    the rebase runs.
    """
    git_dir = root / ".git"
    return any((git_dir / marker).exists() for marker in _SEQUENCING_MARKERS)


def _judge(root: Path, rel: str, memory: _Memory) -> int:
    """Rungs 6 to 9: scope, analysis, verdict, output, and the session's record
    of the bytes the verdict read. A git failure records nothing: its advisory
    says nothing about the bytes, and the next read may get git's answer."""
    from ..gitpaths import readable

    cfg = _config(root)
    if not readable(rel):
        return _unreadable(cfg, rel)
    in_scope = _scoped(cfg, rel)
    if in_scope is None:
        return 0
    raw, records, ranges = _read(root, rel)
    code = _answer(root, cfg, in_scope, rel, records, ranges)
    if not isinstance(ranges, _Unknown):
        memory.remember(root / rel, raw)
    return code


def _read(root: Path, rel: str) -> tuple:
    """The file's bytes, its function records and what the edit changed.

    The statement order is the latency budget. `git diff` on one file costs
    31.4 ms and importing lizard costs 38.1, so the diff is started first and
    finishes inside the import that follows it.
    """
    diff = _diff_proc(root, rel)
    try:
        raw, records = _records(root / rel, rel)
        return raw, records, _changed(root, rel, diff)
    finally:
        diff.close()


def _unreadable(cfg, rel: str) -> int:
    """Exit 2 for a file a scope takes whose name git gives in bytes that are
    not UTF-8. No function in it can be keyed, so none is judged, and the
    commit gate refuses the file at exit 3 (Q17); saying nothing here would
    pass it unread. Advisory wording, as rung 9's: the edit landed. A name no
    scope takes stays silent, like any unscoped edit."""
    from ..gitpaths import shown
    from ..universe import claiming_scope

    scope = claiming_scope(rel, cfg)
    if scope is None:
        return 0
    print(f"crapkit advisory: {shown(rel)} is in scope {scope!r}, but git names it in bytes that "
          "are not UTF-8 and crapkit reads every path as UTF-8, so no function in it was judged "
          "(the edit landed; nothing was blocked)", file=sys.stderr)
    print("the commit gate refuses such a file (exit 3); rename it to a UTF-8 name", file=sys.stderr)
    return 2


def _config(root: Path):
    """crapkit.toml, parsed straight rather than through `cli._shared`, whose
    module scope imports the snapshot store this hook must never open.

    The read is `repotext.repo_text`, the one reader every command uses, and a
    core module that imports nothing but `errors`: a config PowerShell's
    `Out-File -Encoding utf8` wrote carries a BOM, and reading it strictly made
    every advisory in that repo exit 0 on a `does not parse` the catch-all
    swallowed. A UTF-16 file is the reader's configuration error, and the
    catch-all still turns that into silence; `crapkit doctor` is where that
    file gets named."""
    from ..config import load_config_text
    from ..repotext import repo_text

    return load_config_text(repo_text(root / "crapkit.toml", "crapkit.toml"), root=root)


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

    Scoped to the path on purpose: 31.4 ms against 92.4 for the whole tree.
    The commit gate's adapter owns display flags, exact paths and binary-marked
    source fallback. A git that cannot start raises here, before anything is
    judged, and the catch-all keeps a machine with no git silent. A diff that
    started and failed is `_changed`'s to read.
    """
    from ..gitio import SourcePatch

    return SourcePatch(root, "HEAD", paths=(rel,))


def _records(path: Path, rel: str) -> tuple:
    """The edited file's bytes and functions, off the working tree the edit just
    landed in: (None, a refusal) when the file cannot be read at all.

    This import is what pulls lizard in, so it happens here, with the diff
    subprocess already running. A file no reader could read comes back as an
    `UnanalyzableFile`, empty and carrying the reader's reason, and `_unjudged`
    names it instead of reading its zero records as zero breaches.
    """
    from ..analyze import analyze_source, decode_source
    from ..merge import UnanalyzableFile

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, UnanalyzableFile(f"{rel}: {exc.strerror or exc}")
    return raw, analyze_source(rel, decode_source(raw), note=False)


class _Unknown:
    """The change set git could not report, carrying git's error."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _changed(root: Path, rel: str, diff):
    """New-side ranges this edit changed; None when every function in the file
    counts; `_Unknown` when git ran and could not say.

    None is not "nothing changed": git diff cannot see a file it never recorded,
    and reading its empty diff as an empty change set would pass every function
    in it. So an untracked file is judged in full, exactly as `rescore --gate`
    judges one, and so is every file before the first commit. A failed read is
    never an answer: it read as "nothing changed" (a staged breach before the
    first commit drew silence) and as "untracked" (a corrupt index made a
    one-line edit answer for every legacy function in the file).
    """
    from ..errors import GitError

    try:
        text = diff.result()
    except GitError as exc:
        return _without_diff(root, rel, exc)
    if text.strip():
        from ..diffparse import changed_ranges

        return changed_ranges(text).get(rel, [])
    return _listed(root, rel)


def _without_diff(root: Path, rel: str, exc) -> None | _Unknown:
    """What a failed `git diff HEAD` leaves, read off HEAD itself.

    When HEAD resolves, git failed at something else, so its error is named.
    When it does not, there is no commit to diff against: before the first
    commit, or in a measured directory no repository holds. Every function in
    the file is new there, so the file is judged whole, staged or not.
    """
    if _head_resolves(root):
        return _Unknown(f"git diff HEAD -- {rel}: {_git_said(exc, root)}")
    return None


def _git_said(exc, root: Path) -> str:
    """git's own words out of a GitError, without the argv the model has no use
    for; the whole message when it is not shaped `... failed in ROOT: WORDS`."""
    text = str(exc)
    return text.partition(f" failed in {root}: ")[2] or text


def _head_resolves(root: Path) -> bool:
    """Whether `git rev-parse --verify --quiet HEAD` names a commit. Asked only
    after the diff failed, so it costs nothing on the ordinary path."""
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=root,
                          capture_output=True).returncode == 0


def _listed(root: Path, rel: str) -> list | None | _Unknown:
    """For a file with no diff against HEAD: [] when the index lists it (nothing
    changed), None when it does not (untracked), git's error when ls-files fails.
    Asked only when the diff came back empty, the one case that cannot tell
    untracked from unchanged. Literal, so `[id].py` never matches `i.py`."""
    from ..repotext import lenient

    listed = subprocess.run(["git", "--literal-pathspecs", "ls-files", "--", rel], cwd=root,
                            capture_output=True)
    if listed.returncode != 0:
        return _Unknown(f"git ls-files -- {rel}: {lenient(listed.stderr).strip()}")
    return [] if listed.stdout.strip() else None


def _answer(root: Path, cfg, in_scope: dict, rel: str, records: list, ranges) -> int:
    """Rungs 8 and 9: an edit judged nowhere says why; the rest get the verdict."""
    unjudged = _unjudged(rel, records, ranges)
    if unjudged:
        return _say(unjudged)
    breaches, ceiling = _verdict(cfg, in_scope, rel, records, ranges)
    return _report(root, cfg, rel, breaches, ceiling, records)


def _unjudged(rel: str, records: list, ranges) -> list[str]:
    """The advisory for an edit no function of which could be judged, or [].

    A git failure first: without the change set nothing can be judged. Then a
    file no reader could read, when the edit changed it: zero records read as
    zero breaches, so every function in it passed unjudged. A file left as HEAD
    has it stays silent, as the commit gate never judges an untouched file.
    """
    if isinstance(ranges, _Unknown):
        return _unjudged_lines(f"git could not report what changed in {rel}", ranges.reason,
                               _GIT_NEXT)
    reason = getattr(records, "reason", None)
    if reason is not None and ranges != []:
        return _unread_advisory(rel, reason)
    return []


def _unread_advisory(rel: str, reason: str) -> list[str]:
    """The advisory for an edited file no reader could read, in the advisory's
    own voice: the head line says nothing was blocked, the reason is the
    commit gate's own UNREAD line, and the last line says what that gate will
    do and how to clear it."""
    from ..merge import UNREAD_ADVICE

    return [f"crapkit advisory: {rel} could not be read, so no function in it was judged "
            "(the edit landed; nothing was blocked)",
            f"  UNREAD  {rel}: {reason}",
            f"the commit gate refuses this file once staged; {UNREAD_ADVICE}"]


def _unjudged_lines(what: str, reason: str, next_step: str) -> list[str]:
    """Three lines, shaped like the breach advisory: what went unjudged, why in
    the reader's or git's own words, and what to do."""
    return [f"crapkit advisory: {what}, so no function in it was judged "
            "(the edit landed; nothing was blocked)", f"  {reason}", next_step]


def _say(lines: list[str]) -> int:
    """Protocol 1's one channel: stderr and exit 2. stdout stays empty."""
    for line in lines:
        print(line, file=sys.stderr)
    return 2


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


def _report(root: Path, cfg, rel: str, breaches: list, ceiling: int, records: list) -> int:
    """Rung 9. stdout stays empty whatever happens: protocol 1 reserves it for a
    future JSON channel, and Claude Code parses stdout JSON on exit 0."""
    from ..keys import key_of

    keys = _keys(records)
    marked = _marks_for(root / cfg.ratchet_file, rel, records)
    unmarked = [rec for rec in breaches if key_of(keys, rec)[1] not in marked]
    if not unmarked:
        return 0
    return _say(_advisory_lines(rel, unmarked, ceiling))


def _marks_for(marks_path: Path, rel: str, records=()) -> set[str]:
    """The ratchet KEY names one file carries marks for, `#N` ordinals included.

    Existence, not the numeric high-water rule `verify` applies: crap needs
    coverage, coverage needs the store, and the store stays closed. A mark is a
    recorded decision to carry that function as it stands, so without this the
    advisory nags about debt the repo already signed for on every edit.

    Parse only this file's lines and the format comments. Whole-repo entry
    construction costs 35 ms for 40,303 marks and answers no extra question.
    The file reads by `repotext.marks_text`, the rule every marks reader
    shares, so a UTF-16 save keeps its marks and a cp1252 byte costs only the
    mark whose name held it.
    """
    from ..repotext import marks_text

    if not marks_path.is_file():
        return set()
    text = marks_text(marks_path.read_bytes())
    return _known_marks(_file_lines(text, rel), records)


def _file_lines(text: str, rel: str) -> str:
    """The format comments and every mark line naming `rel`. A mark whose path
    starts with `#` or holds a tab or a line break is written as an
    `@crapkit-record-v1` line, which the raw prefix alone never matches."""
    from ..records import record_lines

    prefix = rel + "\t"
    return "\n".join(line for line in record_lines(text) if line.startswith(prefix)
                     or (line.startswith(("#", "@")) and _carries(line, rel)))


def _carries(line: str, rel: str) -> bool:
    """A comment line, or an `@` line whose decoded path is `rel`. A raw line
    naming `rel` already matched its prefix, so only these two shapes remain."""
    from ..ratchet import comment_line

    if line.startswith("#"):
        return comment_line(line)
    return _names(line, rel)


def _names(line: str, rel: str) -> bool:
    from ..records import decode_record

    try:
        return decode_record(line)[:1] == [rel]
    except ValueError:
        return False


def _known_marks(text: str, records) -> set[str]:
    """Unproved key identity grants no advisory exemption and writes nothing."""
    from ..ratchet import checked_key_version, read_ratchet

    try:
        checked_key_version(text, records)
    except ValueError:
        return set()
    return {entry.long_name for entry in read_ratchet(text)[0]}


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


# --- the session memory ------------------------------------------------------


class _Memory:
    """The bytes this session's judgements read: per file, the sha256 of the
    bytes last judged there, one small file per path under the git directory.

    Every judgement writes its record, a silent one included, so bytes that
    leave and come back count as new. The Bash fallback reads it and skips a
    fresh file whose bytes match: a touch, a same-bytes rewrite or a command
    that writes nothing right after an Edit never repeats an advisory. An Edit
    is always judged; it names its file, and the record only follows it.

    A payload with no usable `session_id` gets no memory, and a record that
    cannot be read or written reads as "not judged". Either way the hook
    judges as it did before this memory existed, and no verdict changes.
    """

    __slots__ = ("session",)

    def __init__(self, session: str | None) -> None:
        self.session = session

    def judged(self, path: Path) -> bool:
        """Whether the bytes at `path` are the ones this session last judged there."""
        slot = self._slot(path)
        if slot is None:
            return False
        try:
            return slot.read_text(encoding="ascii") == _digest(path.read_bytes())
        except (OSError, ValueError):
            return False

    def remember(self, path: Path, raw: bytes | None) -> None:
        """Record `raw` as the bytes judged at `path`; nothing when there are none."""
        slot = self._slot(path) if raw is not None else None
        if slot is not None:
            _record(slot, _digest(raw))

    def _slot(self, path: Path) -> Path | None:
        git_dir = _git_dir(path.parent) if self.session is not None else None
        if git_dir is None:
            return None
        return git_dir.joinpath(*_MEMORY_DIR, self.session, _path_key(path))


def _memory(payload: dict) -> _Memory:
    """The session's memory, keyed on the payload's `session_id`. Claude Code
    sends one on every event; an id that could climb out of the memory
    directory, or none at all, turns the memory off."""
    session = payload.get("session_id")
    usable = isinstance(session, str) and 0 < len(session) <= _SESSION_MAX
    return _Memory(session if usable and set(session) <= _SESSION_CHARS else None)


def _git_dir(start: Path) -> Path | None:
    """The git directory above `start`, found without starting git: the first
    `.git` directory, or the directory a `.git` file names (a linked worktree
    or a submodule). The crapkit root sits at or under the git top, so the walk
    from any judged file ends at the repository that tracks it."""
    for directory in [start, *start.parents][:64]:
        entry = directory / ".git"
        if entry.is_dir():
            return entry
        if entry.is_file():
            return _named_git_dir(entry)
    return None


def _named_git_dir(entry: Path) -> Path | None:
    """The directory a `.git` file's `gitdir:` line names, relative to the file."""
    try:
        first = entry.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return None
    name = first.partition("gitdir:")[2].strip()
    return entry.parent / name if first.startswith("gitdir:") and name else None


def _path_key(path: Path) -> str:
    """One file name per judged path, the same whichever event spelled it: an
    Edit's payload path and the Bash fallback's `git status` path resolve alike."""
    import hashlib

    name = os.path.normcase(os.path.realpath(path))
    return hashlib.sha256(os.fsencode(name)).hexdigest()[:32]


def _digest(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def _record(slot: Path, digest: str) -> None:
    """Write one record whole, through a rename, so a parallel hook reads the
    old record or the new one. A session directory made now prunes idle ones.
    A write that fails loses a record and changes no verdict."""
    started = not slot.parent.is_dir()
    try:
        slot.parent.mkdir(parents=True, exist_ok=True)
        part = slot.with_name(f"{slot.name}.{os.getpid()}.part")
        part.write_text(digest, encoding="ascii")
        os.replace(part, slot)
    except OSError:
        return
    if started:
        _prune(slot.parent)


def _prune(session: Path) -> None:
    """Remove every other session directory idle past `_MEMORY_DAYS`."""
    import shutil

    cutoff = time.time() - _MEMORY_DAYS * 86400
    for other in _sessions(session.parent):
        if other != session and _last_write(other) < cutoff:
            shutil.rmtree(other, ignore_errors=True)


def _sessions(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except OSError:
        return []


def _last_write(directory: Path) -> float:
    """When the session last wrote a record; unknown reads as now, and stays."""
    try:
        return directory.stat().st_mtime
    except OSError:
        return time.time()
