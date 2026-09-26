"""One verdict per lane per command: do its line numbers still hold, and can its measurement be reused.

Two questions, one set of reads. Whether a lane's dark lines still point at the
right lines is a question about the bytes it measured: the stamp records the
git blob id of every file under its scopes (lane_sources), and a file whose id
moved withholds only its own lines. Whether `--reuse-unchanged` may skip the
lane is a wider question: tests, crapkit.toml, the lane's table, the
environment and the crapkit version void a measurement without moving a line
number, so reuse asks for the proof the stamp recorded (`proof_builder` below).

Every reader renders these answers and none recomputes them: the
`--reuse-artifacts` warning, the dark-line note `brief`, `next-item` and
`explain` print, the report's banner and the rerun line of `--reuse-unchanged`.
They used to hold three copies of the branch between a stamp that recorded
content and one that recorded only a commit, and two of them disagreed.

A stamp crapkit 0.8.0 or older wrote records no blob ids. It is judged the old
way, by git's diff since its commit, until the next run replaces it.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
from pathlib import Path
from typing import NamedTuple

from . import __version__
from .errors import GitError
from .gitio import GitFacts, worktree_root
from .lane_outputs import config_bytes, configured_outputs, declared_outputs
from .lane_sources import declared_paths, lane_matchers, listing, moved, record
from .lane_stamps import LEGACY, STAMPS_FILE, Stamps, read
from .named import first_few
from .universe import owning_scope


# --- the proof builder: one for both lane kinds ---------------------------------

# Variables a shell or terminal keeps for its own bookkeeping. A `cd` moves
# OLDPWD and each new terminal or SSH login gets its own session ids, and no
# runner measures differently for them. Hashed, OLDPWD alone reran every lane of
# a large consumer repo on a clean, unchanged tree.
# The agent and IPC sockets a login, a tmux server or an editor terminal opens
# are session ids too: each new one moved SSH_AUTH_SOCK, TMUX or
# VSCODE_GIT_IPC_HANDLE and reran a lane on an unchanged tree. PowerShell sets
# PSModulePath for its own module search, so a run from PowerShell and one from
# Git Bash differed by it alone. PATHEXT stays in the proof: it decides what
# cmd.exe starts for a lane's first word. Names compare in upper case, the way
# Windows spells every variable.
_SESSION_VARIABLES = frozenset({
    "OLDPWD", "PWD", "SHLVL", "_",
    "WT_SESSION", "TERM_SESSION_ID", "ITERM_SESSION_ID", "TMUX_PANE", "WINDOWID",
    "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "SECURITYSESSIONID", "XDG_SESSION_ID",
    "SSH_AUTH_SOCK", "SSH_AGENT_PID", "TMUX", "VSCODE_GIT_IPC_HANDLE", "PSMODULEPATH",
})

# What each lane kind's proof leaves out, printed on the line that reuses it.
_UNPROVED = {False: "gitignored files and anything outside the repository",
             True: "gitignored files, files outside its inputs and inherited environment variables"}


class Proof(NamedTuple):
    """The key a stamp records, the named parts it hashes, and why there is no
    key: `why` is set exactly when `key` is "", and `dirty` names the
    uncommitted changes that voided it."""
    key: str
    parts: dict
    why: str
    dirty: tuple = ()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lane_bytes(lane) -> bytes:
    return json.dumps(lane, sort_keys=True).encode("utf-8")


def _environment_digests() -> dict[str, str]:
    """Each inherited variable the proof covers, as a short digest of its value.
    The stamp keeps these, never a value, so a rerun can name what changed."""
    return {name: _digest(value.encode("utf-8", "surrogatepass"))[:16]
            for name, value in os.environ.items() if name.upper() not in _SESSION_VARIABLES}


def proof_parts(root: Path, lane, commit: str) -> dict:
    """The named parts a proof hashes. Both kinds hold the commit, the lane's
    own table and the crapkit version: a crapkit that parses or scores an
    artifact differently must read it fresh. A lane without `inputs` also holds
    crapkit.toml, with CRLF read as LF so a checkout under core.autocrlf=true is
    the file it was, and the inherited environment. A lane with `inputs` is
    proved by its paths, and its own `env` rides in its table."""
    parts = {"commit": commit, "crapkit": __version__, "lane": _digest(_lane_bytes(lane))}
    if lane.inputs:
        return {**parts, "kind": "inputs"}
    return {**parts, "kind": "tree", "config": _digest(config_bytes(root).replace(b"\r\n", b"\n")),
            "env": _environment_digests()}


def _key(parts: dict) -> str:
    return _digest(json.dumps(parts, sort_keys=True).encode("utf-8"))


def measurement_proof(root: Path, lane, skip: frozenset = frozenset()) -> Proof:
    """The proof, taken now, that nothing the lane reads is uncommitted. `skip`
    names the lane outputs and by-products that are not changes: every run
    rewrites them, and each stamp proves its own by their digests."""
    try:
        commit, dirty = _dirty(root, lane, skip)
        parts = proof_parts(root, lane, commit)
    except (GitError, OSError) as exc:
        return Proof("", {}, f"nothing proves its inputs unchanged: {exc}")
    if dirty:
        return Proof("", {}, _dirty_sentence(lane, dirty), tuple(dirty))
    return Proof(_key(parts), parts, "")


def _dirty_sentence(lane, dirty: list[str]) -> str:
    where = "its inputs have" if lane.inputs else "the working tree has"
    return f"{where} {len(dirty)} uncommitted change(s): {first_few(sorted(dirty))}"


def _dirty(root: Path, lane, skip: frozenset) -> tuple[str, list[str]]:
    """HEAD, and the uncommitted changes the lane's proof counts: under its
    inputs, or anywhere in the checkout."""
    if lane.inputs:
        return GitFacts(root).head_commit(), _dirty_inputs(root, lane, skip)
    return _worktree(root, skip)


def _dirty_inputs(root: Path, lane, skip: frozenset) -> list[str]:
    from .lane_changes import ChangeReads

    with ChangeReads(root, (), lane.inputs) as reads:
        return _unless(reads.status_names(), skip)


def _worktree(root: Path, skip: frozenset[str]) -> tuple[str, list[str]]:
    """HEAD, and every uncommitted change in the whole checkout but `skip`,
    named from the checkout's top as git names them."""
    top = worktree_root(root)
    facts = GitFacts(top)
    return facts.head_commit(), _unless(facts.status_names(), _from_top(root, top, skip))


def _unless(names, skip: frozenset[str]) -> list[str]:
    return [name for name in names if name not in skip]


def _from_top(root: Path, top: Path, names: frozenset[str]) -> frozenset[str]:
    """Root-relative `names` spelled from the checkout's top."""
    try:
        prefix = root.resolve().relative_to(top).as_posix()
    except ValueError:
        return frozenset()
    return frozenset(posixpath.normpath(f"{prefix}/{name}") for name in names)


def unproved(before: Proof, after: Proof) -> str:
    """What a stamp records when its proof did not hold from start to finish,
    so a rerun can say why: the changes it was measured over, or git's error."""
    if before.dirty:
        return f"it was measured with {len(before.dirty)} uncommitted change(s): {first_few(sorted(before.dirty))}"
    if before.why:
        return f"when it was measured, {before.why}"
    if after.dirty:
        return f"{first_few(sorted(after.dirty))} changed while it ran"
    return f"when it finished, {after.why}" if after.why else "something it reads changed while it ran"


def uncommitted_changes(root: Path) -> list[str]:
    """The changes that keep every lane without `inputs` from reuse: each
    uncommitted change in the checkout but the lane outputs crapkit.toml
    declares and the files the lanes' own runs wrote. Raises GitError when git
    cannot say, which the caller must not read as a clean tree."""
    return _worktree(root, configured_outputs(config_bytes(root)) | read(root).byproducts())[1]


# --- reuse ------------------------------------------------------------------------

class ReuseVerdict(NamedTuple):
    """`commit` is the commit the lane's artifact was built at when reuse is
    proved, else "", and then `reason` says why the lane reruns."""
    commit: str
    reason: str


_PART_NAMES = (("config", "crapkit.toml"), ("lane", "its lane table"),
               ("crapkit", "the crapkit version"))
_UNNAMED = {False: ("crapkit.toml, its lane table or the environment changed, and its stamp "
                    "does not record which"),
            True: "its lane table or env differs from the one it was measured with"}


def _moved_parts(lane, now: dict, then) -> str:
    """Which recorded part of a proof moved, named."""
    moved = _moved_names(now, then) if isinstance(then, dict) and then else []
    return "; ".join(moved) if moved else _UNNAMED[bool(lane.inputs)]


def _moved_names(now: dict, then: dict) -> list[str]:
    moved = [f"{name} changed" for key, name in _PART_NAMES if now.get(key) != then.get(key)]
    env = _environment_moved(now.get("env", {}), then.get("env"))
    return moved + ([f"{len(env)} environment variable(s) changed: {first_few(sorted(env))}"] if env else [])


def _environment_moved(now: dict, then) -> list[str]:
    then = then if isinstance(then, dict) else {}
    return sorted(name for name in now.keys() | then.keys() if now.get(name) != then.get(name))


# --- line freshness ------------------------------------------------------------------

def _git_unanswered(commit: str, exc: GitError) -> str:
    return f"git cannot say which files in its scopes changed since {commit[:11]} ({exc})"


def _scope_drift(facts, lane, scope_paths: dict, commit: str) -> str:
    """The files under this lane's scopes that moved since the commit, named, or
    why git cannot say; "" when none did. A legacy stamp's answer."""
    matchers = lane_matchers(lane, scope_paths)
    try:
        changed = _owned_changes(facts, matchers, commit)
    except GitError as exc:
        return _git_unanswered(commit, exc)
    if not changed:
        return ""
    return (f"{len(changed)} file(s) in its scopes changed since {commit[:11]} "
            f"({first_few(sorted(changed))}), uncommitted edits included")


def _owned_changes(facts, matchers, commit: str) -> list[str]:
    """Committed or working-tree changes since the commit that the lane's
    scopes own; none, and no git read, for a lane whose scopes declare no path."""
    if not matchers:
        return []
    changed = set(facts.diff_names_since(commit)) | set(facts.status_names())
    return sorted(path for path in changed if owning_scope(path, matchers))


class _Lane:
    """One lane's memoized answers inside a Freshness."""

    def __init__(self) -> None:
        self.lines: str | None = None
        self.reuse: ReuseVerdict | None = None
        self.changed: frozenset | GitError | None = None


class Freshness:
    """Every lane's freshness for one command: the stamp file read once, and
    each lane's answers computed once and kept.

    `git` is the command's own GitFacts when it has one. Without it, the reads
    a stamp from crapkit 0.8.0 or older needs start together on first use
    (lane_changes.ChangeReads), narrowed to those lanes' scope paths; `close`
    waits for any nobody collected.
    """

    def __init__(self, root: Path, lanes, scope_paths: dict | None = None, *, git=None,
                 stamps: Stamps | None = None) -> None:
        self.root = root
        self.lanes = tuple(lanes)
        self.scope_paths = scope_paths or {}
        self.stamps = stamps if stamps is not None else read(root)
        self.git = git
        self._reads = None
        self._memo: dict = {}

    def __enter__(self) -> Freshness:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        """Wait for any legacy read nobody collected; a GitFacts has none."""
        close = getattr(self._reads, "close", None)
        if close is not None:
            close()

    def _of(self, lane) -> _Lane:
        return self._memo.setdefault(lane, _Lane())

    # --- what the stamp can vouch for ------------------------------------------

    def commit(self, lane) -> str:
        """The recorded commit of an artifact on disk that no refusal holds."""
        if not (self.root / lane.artifact).is_file() or self.stamps.refusal(lane.artifact).kind:
            return ""
        return self.stamps.commit(lane.artifact)

    def recorded(self, lane) -> dict | None:
        """The blob ids the stamp of the artifact on disk recorded, or None when
        it holds none: no artifact, a refused one, no stamp, or a legacy stamp."""
        blobs = self.stamps.blobs(lane.artifact)
        return blobs if blobs is not None and self.commit(lane) else None

    def blackout(self, lane) -> bool:
        """Whether a stale verdict withholds every file's lines: true unless
        the stamp answers per file."""
        return self.recorded(lane) is None

    def no_commit(self, lane) -> str:
        """Why no stamp vouches for the artifact on disk."""
        if not (self.root / lane.artifact).is_file():
            return f"no artifact at {lane.artifact}"
        refusal = self.stamps.refusal(lane.artifact)
        if refusal.kind == "leftover":
            return refusal.cause(lane.artifact)
        unread = f" ({STAMPS_FILE}: {refusal.why})" if refusal.why else ""
        return f"no stamp records the commit {lane.artifact} was built at{unread}"

    # --- line numbers ------------------------------------------------------------

    def lines(self, lane) -> str:
        """Why this artifact's line locations may be stale, or "" when every
        file under its scopes holds the bytes its run measured.

        Each answer names its cause: no stamp to vouch for the file, the files
        that moved, a legacy stamp commit HEAD does not descend from, or the
        git failure that left the question open."""
        memo = self._of(lane)
        if memo.lines is None:
            memo.lines = self._lines(lane)
        return memo.lines

    def _lines(self, lane) -> str:
        commit = self.commit(lane)
        if not commit:
            return self.no_commit(lane)
        recorded = self.stamps.blobs(lane.artifact)
        if recorded is not None:
            return self._record_drift(lane, recorded)
        return self._commit_drift(lane, commit)

    def _record_drift(self, lane, recorded: dict) -> str:
        """The files under the lane's scopes whose blob ids differ from the ones
        its run measured, named; new files and deleted ones count."""
        declared = declared_paths(lane, self.scope_paths)
        try:
            changed = moved(self.root, recorded,
                            listing(self.root, lane, self.scope_paths, self.outputs(lane)), declared)
        except GitError as exc:
            return f"git cannot say which files in its scopes changed since it measured them ({exc})"
        if not changed:
            return ""
        return f"{len(changed)} file(s) in its scopes changed since it measured them ({first_few(sorted(changed))})"

    def _commit_drift(self, lane, commit: str) -> str:
        """A legacy stamp's verdict: git's diff since its commit, which needs that
        commit behind HEAD. The next `crapkit coverage` writes a stamp that needs
        neither."""
        facts = self.facts()
        try:
            behind = facts.is_ancestor(commit)
        except GitError as exc:
            return _git_unanswered(commit, exc)
        if not behind:
            return _not_behind(self.root, commit)
        return _scope_drift(facts, lane, self.scope_paths, commit)

    def warning(self, lane) -> str:
        """What the `--reuse-artifacts` warning says, "" for nothing: a lane
        with no stamp to compare against is not called stale on reuse."""
        return self.lines(lane) if self.commit(lane) else ""

    def file_note(self, path: str) -> str:
        """Why `path`'s line numbers are stale, from the first lane whose record
        holds it and whose record says it moved; "" otherwise. A file no record
        holds is not judged here: no lane measured it."""
        for lane in self.lanes:
            recorded = self.recorded(lane)
            note = self._file_note(lane, recorded, path) if recorded and path in recorded else ""
            if note:
                return note
        return ""

    def _file_note(self, lane, recorded: dict, path: str) -> str:
        from .invocation import _self

        changed = self._changed(lane, recorded)
        if isinstance(changed, GitError):
            return (f"lane {lane.name!r}: git cannot say whether {path} changed since "
                    f"{lane.artifact} measured it ({changed}), so its line numbers there are "
                    f"withheld - rerun `{_self()} coverage` once git answers")
        if path not in changed:
            return ""
        return (f"lane {lane.name!r}: {path} changed since {lane.artifact} measured it, so its "
                f"line numbers there are stale - rerun `{_self()} coverage` to measure it again")

    def _changed(self, lane, recorded: dict):
        """The recorded files whose blob id moved, or the GitError that kept git
        from saying: asked once per lane, whatever the number of files asked about."""
        memo = self._of(lane)
        if memo.changed is None:
            try:
                now = record(self.root, recorded, declared_paths(lane, self.scope_paths))
                memo.changed = frozenset(p for p, blob in recorded.items() if now.get(p) != blob)
            except GitError as exc:
                memo.changed = exc
        return memo.changed

    def facts(self):
        """The git answers a legacy stamp is judged by: the command's own, or
        reads started together for every legacy stamp commit on first use."""
        if self.git is not None:
            return self.git
        if self._reads is None:
            self._reads = self._legacy_reads()
        return self._reads

    def _legacy_reads(self):
        from .lane_changes import ChangeReads

        legacy = self._legacy_lanes()
        commits = tuple(dict.fromkeys(self.commit(lane) for lane in legacy))
        paths = dict.fromkeys(p for lane in legacy for p in declared_paths(lane, self.scope_paths))
        try:
            return ChangeReads(self.root, commits, tuple(paths))
        except GitError:
            return GitFacts(self.root)

    def _legacy_lanes(self) -> list:
        """The lanes whose stamp git has to judge: one that records a commit and
        no blob ids."""
        return [lane for lane in self.lanes
                if self.stamps.state(lane.artifact) == LEGACY and self.commit(lane)]

    # --- reuse -----------------------------------------------------------------------

    def outputs(self, lane) -> frozenset[str]:
        """Every declared artifact and results file, this lane's own included,
        and every untracked file a stamp says its lane's run wrote."""
        return declared_outputs(self.root, lane) | self.stamps.byproducts()

    def reuse(self, lane) -> ReuseVerdict:
        """Whether automatic reuse is proved for this lane, and the first
        condition that fails when it is not.

        A lane command can read tests, configuration or any other repository
        input, and source ownership cannot prove that a changed path leaves its
        measurement intact. So a lane that declares nothing is reused only at
        the same clean repository. A lane that declares `inputs` is reused while
        no committed, staged, unstaged or untracked change touches those paths
        between its commit's tree and the working tree. Either way the stamp's
        `proof` has to equal the key the lane would stamp now and the artifact
        bytes have to match the stamp. A stamp without a `proof` is never reused
        automatically. Explicit artifact reuse remains a separate request.
        """
        memo = self._of(lane)
        if memo.reuse is None:
            memo.reuse = self._reuse(lane)
        return memo.reuse

    def _reuse(self, lane) -> ReuseVerdict:
        stamp = self.stamps.entry(lane.artifact)
        commit = self.commit(lane)
        reason = (self._stamp_gap(lane, stamp, commit) or self._proof_gap(lane, stamp, commit)
                  or _artifact_gap(self.root, lane, stamp))
        return ReuseVerdict("" if reason else commit, reason)

    def leaves_out(self, lane) -> str:
        """What this lane's reuse proof does not cover, which the line that
        reuses it names: an edit there reuses the artifact, by design."""
        return _UNPROVED[bool(lane.inputs)]

    def _stamp_gap(self, lane, stamp: dict, commit: str) -> str:
        """Why the stamp itself cannot vouch for the artifact, or ""."""
        if not commit:
            return self.no_commit(lane)
        if stamp.get("proof"):
            return ""
        why = stamp.get("unproved")
        return ("its stamp holds no proof: "
                + (why if isinstance(why, str) and why else
                   "it was measured with uncommitted changes, or by a crapkit that recorded none"))

    def _proof_gap(self, lane, stamp: dict, commit: str) -> str:
        if lane.inputs:
            return self._inputs_gap(lane, stamp, commit)
        return self._whole_tree_gap(lane, stamp, commit)

    def _whole_tree_gap(self, lane, stamp: dict, commit: str) -> str:
        proof = measurement_proof(self.root, lane, self.outputs(lane))
        if proof.why:
            return proof.why
        if proof.parts["commit"] != commit:
            return f"HEAD is {proof.parts['commit'][:11]} and its artifact was built at {commit[:11]}"
        if proof.key == stamp["proof"]:
            return ""
        return _moved_parts(lane, proof.parts, stamp.get("proof_parts"))

    def _inputs_gap(self, lane, stamp: dict, commit: str) -> str:
        """The lane's own parts at the stamp's commit, then nothing under the
        inputs moved since it. git reads the inputs as a pathspec, so an
        untracked file outside them costs nothing and blocks nothing."""
        parts = proof_parts(self.root, lane, commit)
        if _key(parts) != stamp["proof"]:
            return _moved_parts(lane, parts, stamp.get("proof_parts"))
        return self._inputs_moved(lane, commit)

    def _inputs_moved(self, lane, commit: str) -> str:
        """The changes under the inputs between the stamp's commit and the
        working tree, whatever the history between them. An amend, a rebase or
        a branch switch that leaves the inputs' tree as it was reruns nothing;
        the commit only has to be in this clone for git to compare its tree."""
        from .gitio import has_commit
        from .lane_changes import ChangeReads

        try:
            if not has_commit(self.root, commit):
                return _commit_absent(self.root, commit)
            with ChangeReads(self.root, (), lane.inputs) as reads:
                changed = _unless(reads.changed_since(commit), self.outputs(lane))
        except (GitError, OSError) as exc:
            return f"nothing proves its inputs unchanged: {exc}"
        if not changed:
            return ""
        return f"{len(changed)} change(s) under its inputs since {commit[:11]}: {first_few(sorted(changed))}"


def _not_behind(root: Path, commit: str) -> str:
    """git answers "not an ancestor" for a commit this clone does not hold, the
    way a shallow CI checkout with .crapkit/ restored arrives; that is not a
    rewritten history, and it moved no file."""
    from .gitio import has_commit, shallow_fix

    try:
        held = has_commit(root, commit)
    except GitError as exc:
        return _git_unanswered(commit, exc)
    if held:
        return f"its artifact was built at {commit[:11]}, which is not behind HEAD"
    return (f"git cannot say which files in its scopes changed since {commit[:11]}, which this "
            f"clone does not hold{shallow_fix(root)}")


def _commit_absent(root: Path, commit: str) -> str:
    from .gitio import shallow_fix

    return (f"its artifact was built at {commit[:11]}, which this clone does not hold, so git "
            f"cannot compare its inputs{shallow_fix(root)}")


def _artifact_gap(root: Path, lane, stamp: dict) -> str:
    """Each declared file that no longer holds its stamped bytes, grouped by why."""
    from .lane_outputs import declared_files

    expected = stamp.get("artifacts")
    if not isinstance(expected, dict):
        return "its stamp records no digest of its artifact"
    by_state: dict[str, list[str]] = {}
    for name in declared_files(lane):
        state = _file_state(root / name, expected.get(name))
        if state:
            by_state.setdefault(state, []).append(name)
    return "; ".join(f"{first_few(sorted(names))}: {state}" for state, names in by_state.items())


def _file_state(path: Path, expected) -> str:
    """"" when the file holds the bytes its stamp recorded, else what stands in
    the way. A file that is gone has no digest to compare, so it reads
    missing, never a byte difference."""
    try:
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable ({exc.strerror or type(exc).__name__})"
    return "" if digest == expected else "bytes differ from its stamp"
