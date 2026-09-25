"""Git shell layer: the tracked-file universe and the current commit."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from .errors import GitError, ToolError
from .gitpaths import nul_paths, nul_records, split_record
from .repotext import exact_text, lenient, lenient_lines

_OBJECT_NAME = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_LOG_HEADER = re.compile(r"^\0(-?\d+) ([0-9a-f]+)\n", re.MULTILINE)

# Every path this module hands out is joined against root-relative rows, because
# `git ls-files` answers relative to the cwd. Diffs do not: git names their files
# relative to the repo TOP, so under a root one directory down (a monorepo
# member, a project nested in a worktree) `app/core/x.py` met rows saying
# `core/x.py` and every join missed — the commit gate passed a ccn 12 function,
# lane reuse read a changed scope as unchanged. One config flag on the process
# covers every diff command here, so a reader added later is right by default.
#
# core.quotePath answers the same question about spelling. On by default, it
# prints a path holding any byte over 0x7f as C-quoted octal text
# (`"src/b\303\252ta.py"`), which no `ls-files -z` row equals, so a dirty
# non-ASCII file fell out of every set built by intersecting the two: lane reuse
# republished a stale score and its scope read as unchanged. Off, status_names,
# diff_names_since, unstaged_paths, the churn window's log (churn_log, through
# _git_lines) and the `diff -U0` headers all spell the path the way ls-files
# does. git still quotes a path holding a double-quote or a control character
# whatever this says, which is why gitpaths.unquote_path stays for
# line-oriented history and diff headers.
#
# i18n.logOutputEncoding answers the same question about free text. A repo that
# sets i18n.commitEncoding or i18n.logOutputEncoding had its UTF-8 author names
# and subjects re-encoded into that encoding on the way out, so `José` reached
# the churn window as `Jos\xe9`. Pinned to UTF-8, git prints what a commit
# stored and re-encodes only a commit whose header names another encoding. A
# commit with no header that holds bytes that are not UTF-8 still comes out as
# written, and the readers below take those through repotext.lenient.
_RELATIVE = ("-c", "diff.relative=true", "-c", "core.quotePath=false",
             "-c", "i18n.logOutputEncoding=UTF-8")
# Parsed patches are a protocol, independent of display settings and converters.
_PATCH = ("-U0", "--no-renames", "--no-color", "--src-prefix=a/", "--dst-prefix=b/",
          "--no-ext-diff", "--no-textconv", "--inter-hunk-context=0",
          "--output-indicator-new=+", "--output-indicator-old=-", "--output-indicator-context= ")


def _environment() -> dict[str, str]:
    """GIT_DIFF_OPTS overrides even explicit -U0; it is display state."""
    environment = dict(os.environ)
    environment.pop("GIT_DIFF_OPTS", None)
    return environment


def _git(root: Path, *args: str) -> str:
    return _run(root, (*_RELATIVE, *args), args)


def _git_unflagged(root: Path, *args: str) -> str:
    """A spawn carrying none of the -c flags this module injects.

    For the one read whose subject IS git's configuration: command-line config
    is config, so `-c diff.relative=true` came back out of `config --get
    diff.relative` as this repo's own setting.
    """
    return _run(root, args, args)


def _spawn(root: Path, argv: tuple[str, ...], *, binary: bool = False) -> subprocess.CompletedProcess:
    """One git process run to completion, whatever it exits with.

    Read as bytes and decoded here, never by subprocess: a text-mode read
    decodes in a reader thread on Windows, where a byte that is not UTF-8 killed
    the thread and handed the caller None for stdout. Text mode is for answers
    that are object names, words or filesystem paths (`rev-parse`, `config`):
    stdout keeps each byte that is not UTF-8 as the lone surrogate Python gives
    an OS path, so a directory named in Latin-1 on Linux still opens. stderr is
    only ever quoted in a message, so it reads through repotext.lenient."""
    try:
        res = subprocess.run(["git", *argv], cwd=root, env=_environment(), capture_output=True)
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    if binary:
        return res
    return subprocess.CompletedProcess(res.args, res.returncode,
                                       exact_text(res.stdout), lenient(res.stderr))


def _run(root: Path, argv: tuple[str, ...], named: tuple[str, ...], *, binary: bool = False):
    """`named` is what the error says ran — the injected flags are crapkit's
    business, not the caller's. A binary read answers stdout as bytes, and the
    caller decides what they are."""
    res = _spawn(root, argv, binary=binary)
    if res.returncode != 0:
        error = lenient(res.stderr) if binary else res.stderr
        raise GitError(f"git {' '.join(named)} failed in {root}: {error.strip()}")
    return res.stdout


def _git_text(root: Path, *args: str) -> str:
    """Free text as a commit or a blob stored it: author names, messages, patch
    lines. git re-encodes none of it for a commit with no encoding header, so
    bytes that are not UTF-8 read as U+FFFD instead of ending the command.
    Paths are not free text: _git_paths reads them. Read binary, so git's own
    line framing reaches the caller unconverted."""
    return lenient(_git_bytes(root, *args))


def _git_bytes(root: Path, *args: str) -> bytes:
    return _run(root, (*_RELATIVE, *args), args, binary=True)


def _git_paths(root: Path, *args: str) -> list[str]:
    """NUL path records, each name that is not UTF-8 left out and named on
    stderr (gitpaths.nul_paths)."""
    return nul_paths(_git_bytes(root, *args))


def _git_lines(root: Path, *args: str) -> Iterator[str]:
    """Same contract as _git, streamed: the caller sees one line at a time.

    A failing command yields nothing and raises at the end of iteration, so the
    consumer never mistakes an empty stream for an empty history.

    Undecodable bytes read as U+FFFD. git prints an author name as the commit
    stored it unless the commit names its encoding, so one commit written in
    Latin-1 inside the churn window stopped every command that reads churn with
    a UnicodeDecodeError. The laid-down copy of the log decodes the same way, so
    a walk and a read of the copy hand out the same lines.

    Lines end at LF alone, as they do in that copy. Universal newlines also
    ended one at a CR inside an author name, which cut the header off its dates.
    """
    try:
        proc = subprocess.Popen(["git", *_RELATIVE, *args], cwd=root, env=_environment(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    with proc:
        yield from lenient_lines(proc.stdout)
        stderr = lenient(proc.stderr.read())
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {root}: {stderr.strip()}")


def stage_path(root: Path, rel_path: str) -> None:
    """git add one path — the hook override's ratchet debt must land IN the commit."""
    _git(root, "add", "--", rel_path)


def ls_files(root: Path) -> list[str]:
    return _git_paths(root, "ls-files", "-z")


def untracked_files(root: Path) -> list[str]:
    """Paths `git add` would pick up: untracked and not ignored.

    git applies the ignore rules, so a build directory never reads as source
    somebody forgot to add.
    """
    return _git_paths(root, "ls-files", "--others", "--exclude-standard", "-z")


def config_value(root: Path, key: str) -> str:
    """One `git config` value, or "" when it is unset.

    `git config --get` exits 1 on an unset key, which is an answer rather than a
    failure — every caller here asks about a setting the repo need not have.

    Unflagged, because this reads configuration and `-c diff.relative=true` is
    configuration: through _git, a repo that never set diff.relative answered
    `true` and one that set it to `false` answered `true` as well.
    """
    try:
        return _git_unflagged(root, "config", "--get", key).strip()
    except GitError:
        return ""


def index_modes(root: Path, pathspec: str) -> dict[str, str]:
    """path -> index mode for everything git tracks under `pathspec`.

    `git ls-files -s` is the only place the executable bit is readable on
    Windows, where the filesystem has no such bit and the working copy always
    looks 0644.
    """
    modes = {}
    for record in _git_bytes(root, "ls-files", "-s", "-z", "--", pathspec).split(b"\0"):
        meta, path = split_record(record, 1)
        if path:
            modes[path] = meta.split(" ", 1)[0]
    return modes


def staged_diff(root: Path) -> str:
    # --no-renames: a renamed file becomes delete+add, so a rename stays a
    # touched file and its functions still face the gate (and the old ratchet
    # entry's drop is matched by fresh gating at the new path).
    return _read_source_patch(root, "--cached")


def unstaged_paths(root: Path) -> set[str]:
    """Tracked files whose working-tree content differs from the index.

    git decides it, through its own filters. Comparing a staged blob to the
    file's raw bytes reads every file as different under `core.autocrlf=true` —
    git-for-windows' installer default — because the blob holds LF and the
    checkout holds CRLF by design.
    """
    return set(_diff_names(root))


def _diff_names(root: Path, *args: str) -> list[str]:
    """One `git diff --name-only` answer with exact root-relative paths."""
    return _git_paths(root, "diff", "--name-only", "--no-renames", "-z", *args)


def diff_since(root: Path, commit: str) -> str:
    return _read_source_patch(root, commit)


def diff_names_since(root: Path, commit: str) -> list[str]:
    """Files with committed changes between a commit and HEAD."""
    return _diff_names(root, commit, "HEAD")


def _rename_pairs(fields: list[str]) -> dict[str, str]:
    """Walk `--name-status -z` records: a status field, then one path — two for R and C.

    Consuming one path per record would read a rename's destination as the next
    record's status and shift every entry after it.
    """
    pairs: dict[str, str] = {}
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        paths = 2 if status[0] in ("R", "C") else 1
        if status[0] == "R":
            pairs[fields[i + 1]] = fields[i + 2]
        i += 1 + paths
    return pairs


def renamed_paths(root: Path, since: str, *, similarity: int = 50) -> dict[str, str]:
    """old path -> new path for files git reads as renamed between `since` and HEAD.

    Tree-to-tree, not a walk of history: a rename here is content similarity
    between the two endpoints, so widening the window costs nothing extra and
    cannot invent a pairing git does not already see. Copies are excluded — the
    source still exists, so nothing about it moved. Under diff.relative git
    reports a rename that crosses INTO the crapkit root from above as an `A`,
    so only renames wholly inside the root pair up here; a mark on a file moved
    in from above the root reads as new.
    """
    fields = nul_records(_git_bytes(root, "diff", "--name-status", f"-M{similarity}", "-z", since, "HEAD"))
    # A name that is not UTF-8 holds its record's place as None and pairs with nothing.
    return {old: new for old, new in _rename_pairs(fields).items() if old is not None and new is not None}


def status_names(root: Path) -> list[str]:
    """Files with uncommitted changes: staged, unstaged, or never added.

    Two diffs and an ls-files rather than `git status --porcelain`, which names
    its files relative to the repo TOP and cannot be talked out of it —
    `status.relativePaths=true` and `--porcelain=v1` both still print
    `app/core/x.py` from a root one directory down, while every caller joins
    these names against root-relative rows. The diffs take `diff.relative` like
    the rest of this module and `ls-files --others` answers relative to the cwd
    already.

    Untracked files are in the set because lane reuse reads it: a test file that
    exists and git has never seen still makes that lane's coverage stale. The
    dirty-file set verify builds from this only ever meets tracked rows, so the
    wider answer cannot relabel a finding there.
    """
    return sorted({*_diff_names(root, "--cached"), *_diff_names(root),
                   *untracked_files(root)})


_SHALLOW_FIX = ("this shallow clone does not hold every commit: set fetch-depth: 0 on the "
                "checkout or run git fetch --unshallow")


def shallow_warning(counts: str) -> str:
    """The one stderr line a reader of commit history prints in a shallow clone:
    what it counted, and the fetch that brings the rest of the history in. Churn,
    mark ages and repayments are counts of commits, and a depth-1 clone holds
    one, so the numbers read as every file changed once and every mark new."""
    return f"warning: {counts} read only the commits this clone holds; {_SHALLOW_FIX}"


def shallow_refusal(judgement: str) -> GitError:
    """The refusal a verdict over commit history gives in a shallow clone."""
    return GitError(f"{judgement}; {_SHALLOW_FIX}")


def merge_base(root: Path, ref: str) -> str:
    """The commit REF and HEAD forked from — a branch's real diff basis, which
    is what a mid-branch run's own commit is not.

    A refusal says why. git exits 1 with nothing on stderr when the two share no
    commit it holds: unrelated histories, or a shallow clone whose boundary cuts
    the fork off. A shallow clone is the default CI checkout, so there every
    refusal also names the fetch that brings the missing history in.
    """
    res = _spawn(root, (*_RELATIVE, "merge-base", ref, "HEAD"))
    if res.returncode != 0:
        raise GitError(_merge_base_refusal(root, ref, res) + shallow_fix(root))
    return res.stdout.strip()


def _merge_base_refusal(root: Path, ref: str, res: subprocess.CompletedProcess) -> str:
    reason = res.stderr.strip()
    if res.returncode == 1 and not reason:
        return f"no merge base between {ref} and HEAD in {root}"
    return f"git merge-base {ref} HEAD failed in {root}: {reason}"


def shallow_fix(root: Path) -> str:
    """The fetch advice in a shallow clone; "" in a full one, and "" when git
    cannot say, so the merge-base reason is the one the refusal keeps."""
    try:
        shallow = is_shallow(root)
    except GitError:
        return ""
    return f"; {_SHALLOW_FIX}" if shallow else ""


def is_ancestor(root: Path, commit: str, other: str = "HEAD") -> bool:
    """True when `commit` is at or behind `other`; git counts a commit as its own
    ancestor, which is what "at or behind" needs."""
    try:
        res = subprocess.run(["git", "merge-base", "--is-ancestor", commit, other],
                             cwd=root, capture_output=True)
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    return res.returncode == 0


def is_shallow(root: Path) -> bool:
    """True when this checkout is a depth-limited clone: one that does not hold
    every commit its history names. The ancestor check reads it to blame the
    right thing, since a commit a shallow clone never fetched is not one a
    rebase rewrote. `rev-parse` answers with the word `true` or `false`."""
    return _git(root, "rev-parse", "--is-shallow-repository").strip() == "true"


def shallow_checkout(root: Path) -> bool:
    """is_shallow, read straight out of .git: git marks a depth-limited clone
    with a `shallow` file in its common directory and deletes it on `fetch
    --unshallow`, and `rev-parse --is-shallow-repository` answers from that file.

    worklist, next-item, brief and ratchet report ask on every call, and an
    empty next-item queue starts no git process at all; a spawn costs ~20 ms on
    Windows. With no .git above root, git is asked, as head_commit asks it.
    """
    gitdir = _git_dir(root)
    if gitdir is None:
        return is_shallow(root)
    return (_common_dir(gitdir) / "shallow").is_file()


def blob_at(root: Path, commit: str, rel_path: str) -> bytes | None:
    """The bytes `rel_path` (relative to `root`) held at `commit`, or None when
    that commit holds no such file. A commit this clone does not hold, or any
    other git failure, raises GitError: it is not a commit without the file."""
    listed = _git_paths(root, "--literal-pathspecs", "ls-tree", "-z", commit, "--", rel_path)
    if not listed:
        return None
    blob = ("cat-file", "blob", listed[0].split(None, 3)[2])
    return _run(root, blob, blob, binary=True)


def commits_touching(root: Path, rev_range: str, rel_path: str) -> list[str]:
    """The commits in `rev_range` that changed `rel_path`, newest first. A range
    this clone does not hold raises GitError: it is not a range with no commits."""
    return _git(root, "--literal-pathspecs", "log", "--format=%H", rev_range, "--",
                rel_path).split()


def _batch_stream(root: Path, requests: bytes) -> bytes:
    try:
        res = subprocess.run(["git", "cat-file", "--batch"], cwd=root,
                             input=requests, capture_output=True)
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    if res.returncode != 0:
        raise GitError(f"git cat-file --batch failed in {root}: "
                       f"{lenient(res.stderr).strip()}")
    return res.stdout


def _framed_blob(stream: bytes, pos: int) -> tuple[bytes, int]:
    """One record: `<oid> blob <size>` header line, exactly size bytes, then LF.

    Sliced by the declared byte count, never split on newlines — blobs are
    binary. A path absent from the index gets a `<request> missing` line and a
    zero exit, so the absent case is detected here, not from a return code.
    """
    end = stream.index(b"\n", pos)
    header = lenient(stream[pos:end])
    if header.endswith(" missing"):
        raise GitError(f"git cat-file --batch: {header[:-len(' missing')]} is not in the index")
    body_at = end + 1
    size = int(header.rsplit(" ", 1)[1])
    return stream[body_at:body_at + size], body_at + size + 1


def _framed_blobs(stream: bytes, rel_paths: list[str]) -> dict[str, bytes]:
    """The batch answer split back into one blob per requested path, in order."""
    blobs: dict[str, bytes] = {}
    pos = 0
    for rel in rel_paths:
        blobs[rel], pos = _framed_blob(stream, pos)
    return blobs


def staged_blobs(root: Path, rel_paths: list[str]) -> dict[str, bytes]:
    """Every staged blob from one `git cat-file --batch` process.

    One `git show` per path costs ~22ms of process spawn each and dominates the
    hook; batching makes the fetch flat in file count.
    """
    if not rel_paths:
        return {}
    if _line_paths(rel_paths):
        return _individual_blobs(root, rel_paths)
    return _framed_blobs(_batch_stream(root, _batch_requests(rel_paths)), rel_paths)


def _line_paths(paths: list[str]) -> bool:
    return any("\n" in path or "\r" in path for path in paths)


def _individual_blobs(root: Path, paths: list[str]) -> dict[str, bytes]:
    """Line-bearing names cannot use line-framed requests on older Git versions."""
    return {path: _Started(root, ("show", f":./{path}"), stdin=False).result()
            for path in paths}


def _batch_requests(rel_paths: list[str]) -> bytes:
    """`:./<path>` per line: `git cat-file` reads a bare `:<path>` from the repo
    TOP whatever the cwd is, so a root-relative name asked for that way is
    `is not in the index` under a nested root. The `./` is gitrevisions' own
    spelling for cwd-relative and is a no-op at the top."""
    return "".join(f":./{rel}\n" for rel in rel_paths).encode("utf-8")


class _Started:
    """A git process started now and read later.

    communicate() writes the request and reads the answer in one call, so a
    request stream larger than a pipe buffer cannot deadlock against the child's
    own output. The answer is bytes: every caller reads a patch, a blob or NUL
    records, and decides how those decode.
    """

    def __init__(self, root: Path, args: tuple[str, ...], *, stdin: bool) -> None:
        self._args, self._root = args, root
        try:
            self._proc = subprocess.Popen(
                ["git", *_RELATIVE, *args], cwd=root, env=_environment(),
                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise GitError("git executable not found") from exc

    def result(self, payload=None):
        out, err = self._proc.communicate(payload)
        if self._proc.returncode != 0:
            raise GitError(f"git {' '.join(self._args)} failed in {self._root}: {lenient(err).strip()}")
        return out

    def close(self) -> None:
        """Shut down a process nothing ever read. A process already read to the
        end has a return code, and closing that one is nothing at all."""
        if self._proc.returncode is None:
            self._proc.kill()
            self._proc.communicate()


def start_read(root: Path, *args: str) -> _Started:
    """One git read with no stdin, started now and collected later with
    `.result()`, which answers bytes or raises GitError."""
    return _Started(root, args, stdin=False)


def _source_diff_args(basis: tuple[str, ...], paths: tuple[str, ...], *,
                      force_text: bool = False) -> tuple[str, ...]:
    # Source body bytes need no character decoding to count hunk lines.
    text = ("--text",) if force_text else ()
    return ("--literal-pathspecs", "diff", *basis,
            *_PATCH, *text, "--", *paths)


def _binary_source_path(record: bytes, extensions: tuple[str, ...]) -> str | None:
    counts, path = split_record(record, 2)
    if counts == "-\t-" and path and path.endswith(extensions):
        return path
    return None


def _binary_source_paths(root: Path, basis: tuple[str, ...],
                         paths: tuple[str, ...]) -> tuple[str, ...]:
    from .universe import LANGUAGE_EXTENSIONS

    extensions = tuple(ext for group in LANGUAGE_EXTENSIONS.values() for ext in group)
    records = _git_bytes(root, "--literal-pathspecs", "diff", *basis, "--numstat", "-z",
                         "--no-renames", "--no-ext-diff", "--no-textconv", "--", *paths).split(b"\0")
    return tuple(path for record in records if (path := _binary_source_path(record, extensions)))


class SourcePatch:
    """A parsed source patch, started before analysis imports need its answer.

    Ordinary patches cost one process. A binary display attribute can hide an
    analyzable source file, so only a binary summary triggers exact NUL metadata
    and a forced patch for supported source paths. PNG/ZIP payloads stay binary.

    UTF-8 surrogateescape preserves opaque body bytes, including admitted cp1252
    source. It does not replace bytes or relax path decoding: a header or NUL
    record that names a file in bytes that are not UTF-8 leaves that file out
    (gitpaths), and every other path keeps its exact spelling.
    """

    def __init__(self, root: Path, *basis: str, paths: tuple[str, ...] = ()) -> None:
        self._root, self._basis, self._paths = root, basis, paths
        self._read = _Started(root, _source_diff_args(basis, paths), stdin=False)

    def result(self) -> str:
        patch = exact_text(self._read.result())
        if "\nBinary files " not in patch:
            return patch
        paths = _binary_source_paths(self._root, self._basis, self._paths)
        if not paths:
            return patch
        forced = _Started(self._root, _source_diff_args(self._basis, paths, force_text=True), stdin=False)
        try:
            return patch + exact_text(forced.result())
        finally:
            forced.close()

    def close(self) -> None:
        self._read.close()


def _read_source_patch(root: Path, *basis: str) -> str:
    read = SourcePatch(root, *basis)
    try:
        return read.result()
    finally:
        read.close()


class GitReads:
    """Where the pre-commit gate's staged bytes come from: one git process each,
    spawned at the moment the gate asks for it."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def staged_diff(self) -> str:
        return staged_diff(self.root)

    def staged_blobs(self, rel_paths: list[str]) -> dict[str, bytes]:
        return staged_blobs(self.root, rel_paths)


class _StartedReads:
    """The same two answers, from processes that are already running.

    The gate cannot use either one until lizard is imported, and that import
    costs more than both spawns together, so the spawns belong underneath it.
    """

    def __init__(self, root: Path, base: str | None = None) -> None:
        self._root = root
        basis = (merge_base(root, base),) if base is not None else ()
        self._diff = SourcePatch(root, "--cached", *basis)
        self._batch = _Started(root, ("cat-file", "--batch"), stdin=True)

    def staged_diff(self) -> str:
        return self._diff.result()

    def staged_blobs(self, rel_paths: list[str]) -> dict[str, bytes]:
        if not rel_paths:
            return {}
        if _line_paths(rel_paths):
            return _individual_blobs(self._root, rel_paths)
        stream = self._batch.result(_batch_requests(rel_paths))
        return _framed_blobs(stream, rel_paths)

    def close(self) -> None:
        self._diff.close()
        self._batch.close()


@contextmanager
def staged_reads(root: Path, base: str | None = None):
    """The gate's two git reads, started before the caller needs either.

    Both processes are shut down on the way out, whichever of them the caller got
    around to reading: a commit with nothing staged never asks for a blob, and a
    machine with no lizard never asks for anything at all.
    """
    reads = _StartedReads(root, base)
    try:
        yield reads
    finally:
        reads.close()


class LogEntry(NamedTuple):
    """One commit that touched a file: when, which, and its -U0 patch."""
    timestamp: int
    commit: str
    patch: str


def file_log(root: Path, rel_path: str) -> list[LogEntry]:
    """Each commit touching one file, oldest first, with its patch.

    -U0: the readers look at +/- lines alone, so context lines are pipe traffic
    that grows with the file.
    No --follow: rename detection cost 0.6s of a 1.14s `ratchet report` on a
    72k-commit history and found nothing. The cost is real, since --follow also
    gives up the commit-graph path filtering the plain log gets (measured on a
    30k-commit synthetic: 0.436s vs 0.257s, same events either way). The price
    is that renaming the file restarts its history at the rename, which
    `commit_renames` lets a reader name.
    """
    # A path may hold U+0001, the old separator. Body NULs have +/- prefixes;
    # only a physical header line starts with the NUL timestamp marker. Raw LF
    # framing prevents CR in a legacy field from manufacturing a header line.
    out = _git_text(root, "--literal-pathspecs", "log", "--reverse", "--format=%x00%at %H",
                    "-p", *_PATCH, "--text", "--", rel_path)
    return _log_entries(out)


def commit_renames(root: Path, commit: str) -> dict[str, str]:
    """old path -> new path for each file one commit renamed, relative to root.

    One commit's diff, with rename detection on for it alone. A commit with no
    parent (the first commit, or a shallow clone's boundary) renamed nothing.
    """
    fields = _git_paths(root, "diff-tree", "-r", "-M", "--relative", "--name-status",
                        "--no-commit-id", "-z", commit)
    return _rename_pairs(fields)


def _log_entries(out: str) -> list[LogEntry]:
    entries = []
    head, start = None, 0
    for header in _LOG_HEADER.finditer(out):
        if head is not None:
            entries.append(LogEntry(int(head.group(1)), head.group(2), out[start:header.start()]))
        head, start = header, header.end()
    if head is not None:
        entries.append(LogEntry(int(head.group(1)), head.group(2), out[start:]))
    elif out:
        raise GitError("Git patch history has no timestamp header")
    return entries


# core.longpaths on the worktree calls, and on those alone. On a 31,459-file
# repo every one of four parallel `worktree add` calls died "Filename too long"
# without it — git deleted its own half-built checkout each time, so four adds
# cost 40 s and produced nothing — and `worktree remove` then failed the same
# way on a tree that DID materialize, leaving the directory behind and only the
# admin entry pruned. The limit is Windows' 260 characters, reached by any base
# plus the repo's own deepest path, and the setting is git's switch to the
# long-path API. Every other platform ignores it.
_LONGPATHS = ("-c", "core.longpaths=true")


def _worktree_git(root: Path, *args: str, owner=None) -> str:
    if owner is None:
        return _git(root, *args)
    from .procs import run_owned
    try:
        result = run_owned(["git", *_RELATIVE, *args], cwd=root, env=_environment(),
                           capture_output=True, owner=owner)
    except FileNotFoundError as error:
        raise GitError("git executable not found") from error
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {root}: {result.stderr.strip()}")
    return result.stdout


def worktree_add(root: Path, path: Path, *, owner=None) -> None:
    """A detached checkout of HEAD at `path`: a second working tree that shares
    the object store, so a worker can edit files without touching the real one.

    Retried once, because git's add starts by enumerating the existing
    worktrees/* admin entries and reading each one's commondir (`git worktree
    list` alone dies the same way, rc 128). An entry a peer add is still
    building kills the reader whether that commondir is missing or exists at
    zero bytes; the same absence on a settled entry is ignored. Both errnos come
    up (`No error` from strerror(0) on the zero-byte read, `No such file or
    directory` on the missing one), so the guard reads the admin path instead,
    and reads it as two fragments because that path is named against the git
    dir, which is `.git` only in a plain clone. Nothing to clean up first: an
    add that dies in the scan leaves neither the target directory nor an entry.
    """
    try:
        _worktree_git(root, *_LONGPATHS, "worktree", "add", "--detach", str(path), owner=owner)
        return
    except GitError as first:
        message = str(first)
        if "worktrees/" not in message or "commondir" not in message:
            raise
    time.sleep(0.05)
    _worktree_git(root, *_LONGPATHS, "worktree", "add", "--detach", str(path), owner=owner)


def worktree_remove(root: Path, path: Path, *, owner=None) -> None:
    """Teardown. --force because the worker's tree is dirty by construction, and
    it never raises: a cleanup error must not mask the failure that caused it.
    `prune` is the fallback that drops the admin entry a stuck directory leaves.
    Parallel removes survive the same admin-entry enumeration that kills a
    parallel add (0 failures in 320 concurrent removes measured), so no retry.
    An owned git that failed to start raises a ToolError, and falls back too."""
    try:
        _worktree_git(root, *_LONGPATHS, "worktree", "remove", "--force", str(path), owner=owner)
    except (GitError, ToolError):
        shutil.rmtree(path, ignore_errors=True)
        _prune_quietly(root, owner=owner)


def worktree_reset(tree: Path, commit: str, *, owner=None) -> None:
    """A worktree back to `commit`, content and all, without a fresh checkout.
    30.6 s of `worktree add` on a 31,459-file tree against 0.46 s here.

    `commit` is a sha the CALLER read from the repository it cares about, never
    the literal HEAD: HEAD inside a linked worktree is that worktree's own
    detached head, which is the commit it was created at. A pool kept across a
    commit would restore the old content under a run that believes it is
    mutating the new one. Naming the sha also means a reused tree needs no
    stale-state check: whatever it held, it holds `commit` afterwards.

    `checkout --force <sha>` rather than the pathspec form `-- .`, because a
    file the new commit DELETED stays on disk and in the index under a pathspec
    checkout, and the tree's own head keeps pointing at the old commit. Both
    forms cost the same (0.44 s against 0.61 s for four 31,459-file trees,
    best of 5 with 4,000 untracked artifact files planted first).

    Then clean: checkout restores tracked files and leaves every artifact the
    last suite wrote. -x because an ignored one (a stale coverage file, a
    node_modules) is what the next suite reads, -d for directories, -ff to
    descend into a checkout something left nested inside.

    The guard is not a courtesy. Both commands find their repository by walking
    UP from the cwd, and a caller keeping worktrees inside the repo (the mutate
    pool lives at `.crapkit/mutate-pool/`) hands this a directory whose `.git`
    pointer a killed run may have taken with it. Without the guard, `checkout
    --force` and `clean -xdff` would find the MAIN repository and run there:
    every uncommitted line and every untracked file gone, from a function whose
    job is to tidy a scratch tree.
    """
    if not (tree / ".git").exists():
        raise GitError(f"{tree} is not a git worktree")
    _worktree_git(tree, *_LONGPATHS, "checkout", "--force", commit, owner=owner)
    _worktree_git(tree, *_LONGPATHS, "clean", "-xdff", owner=owner)


def _prune_quietly(root: Path, *, owner=None) -> None:
    try:
        _worktree_git(root, "worktree", "prune", owner=owner)
    except (GitError, ToolError):
        pass


def _git_dir(root: Path) -> Path | None:
    """The admin directory for `root`, found the way git finds it: walk up to
    the first ancestor holding a .git, and read that one.

    A crapkit root one directory below the repo top is the layout PR #23 added
    support for, and looking only at `root/.git` made the HEAD fast path miss
    every one of them — a spawn each, for a string sitting in a file two
    directories up. The first .git found ends the walk whatever it holds, so a
    submodule stops at its own and never answers with the superproject's HEAD.
    """
    for base in [root, *root.parents]:
        if (base / ".git").exists():
            return _git_dir_at(base)
    return None


def _git_dir_at(base: Path) -> Path | None:
    """.git is a directory in a normal clone and a `gitdir:` pointer in a linked
    worktree or a submodule. A relative pointer is relative to the directory
    holding the file — `gitdir: ../../.git/modules/sub` joined anywhere else
    misses."""
    dot = base / ".git"
    if dot.is_dir():
        return dot
    text = _file_text(dot)
    if not text.startswith("gitdir:"):
        return None
    named = Path(text[len("gitdir:"):].strip())
    return named if named.is_absolute() else (base / named)


def _file_text(path: Path) -> str:
    """A ref file's content, or "" when it is not there or not readable.

    Unreadable is an answer here, not a failure: every caller's fallback is the
    git process, which is what read the file correctly in the first place.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _sha_or_none(text: str) -> str | None:
    """Object names only: 40 hex for sha1 repos, 64 for sha256 ones."""
    return text if _OBJECT_NAME.fullmatch(text) else None


def _packed_sha(gitdir: Path, ref: str) -> str | None:
    """The ref's line in packed-refs, where `git pack-refs` puts it once the
    loose file is gone. `^`-prefixed lines are peeled tags and never a match."""
    for line in _file_text(gitdir / "packed-refs").splitlines():
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return _sha_or_none(sha)
    return None


def _common_dir(gitdir: Path) -> Path:
    """A linked worktree keeps HEAD in its own admin directory and shares
    refs/heads with the repository it was made from."""
    common = _file_text(gitdir / "commondir")
    return (gitdir / common).resolve() if common else gitdir


def _ref_sha(gitdir: Path, ref: str) -> str | None:
    loose = _sha_or_none(_file_text(gitdir / ref))
    if loose:
        return loose
    shared = _common_dir(gitdir)
    return _sha_or_none(_file_text(shared / ref)) or _packed_sha(shared, ref)


def head_from_refs(root: Path) -> str | None:
    """HEAD read straight out of .git, or None meaning "ask git".

    Every command opens by asking where HEAD is, and the answer is a 40-character
    string in a file: on Windows the spawn that fetches it costs ~20ms. Anything
    unexpected — a symref chain, a ref this does not find, a torn write — returns
    None rather than a guess, and the caller pays for the process instead.
    """
    gitdir = _git_dir(root)
    if gitdir is None:
        return None
    head = _file_text(gitdir / "HEAD")
    if not head.startswith("ref: "):
        return _sha_or_none(head)  # detached: HEAD holds the object name itself
    return _ref_sha(gitdir, head[len("ref: "):].strip())


def head_commit(root: Path) -> str:
    fast = head_from_refs(root)
    if fast:
        return fast
    out = _git(root, "rev-parse", "HEAD").strip()
    if not out:
        raise GitError(f"no HEAD commit in {root}")
    return out


def worktree_root(root: Path) -> Path:
    """The checkout containing root, including a linked or nested worktree."""
    return Path(_git_unflagged(root, "rev-parse", "--show-toplevel").strip()).resolve()


class GitFacts:
    """One command's answers to the three questions every lane asks.

    HEAD, the dirty-file set and a diff against a stamp commit are the same for
    every lane in a run, but each lane used to pay its own `git` spawn for all
    three. Build one of these per command and pass it down.

    Asking once also FIXES the answer at the moment the run started, which is
    what the reuse decision wants: a lane command writes into the working tree,
    so a later lane re-asking git would judge itself against another lane's
    output. Errors are not memoized — GitError propagates on every call, so a
    non-git sandbox keeps behaving like one.

    Parallel lanes share one of these, so the lazy fills take a lock: the first
    caller pays the spawn and the rest wait for its answer instead of racing to
    ask git the same question again.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._head: str | None = None
        self._status: tuple[str, ...] | None = None
        self._diffs: dict[str, tuple[str, ...]] = {}
        self._ancestry: dict[tuple[str, str], bool] = {}
        self._shallow: bool | None = None

    def head_commit(self) -> str:
        with self._lock:
            if self._head is None:
                self._head = head_commit(self.root)
            return self._head

    def status_names(self) -> tuple[str, ...]:
        with self._lock:
            if self._status is None:
                self._status = tuple(status_names(self.root))
            return self._status

    def diff_names_since(self, commit: str) -> tuple[str, ...]:
        with self._lock:
            if commit not in self._diffs:
                self._diffs[commit] = tuple(diff_names_since(self.root, commit))
            return self._diffs[commit]

    def is_ancestor(self, commit: str, other: str = "HEAD") -> bool:
        """Memoized per (commit, other) the way the diffs are: verify asks about
        the same commit once per lane, once per open claim and once for the
        baseline, and history does not move under a running command."""
        with self._lock:
            key = (commit, other)
            if key not in self._ancestry:
                self._ancestry[key] = is_ancestor(self.root, commit, other)
            return self._ancestry[key]

    def is_shallow(self) -> bool:
        """Asked once: a clone does not deepen under a running command."""
        with self._lock:
            if self._shallow is None:
                self._shallow = is_shallow(self.root)
            return self._shallow
