"""Change control: no golden, expected value or metric moves without a declared change.

    python tools/accuracy/change_control.py --base REF [--head REF] [--measured DIR] [--moved TSV]
    python tools/accuracy/change_control.py declare ID --kind KIND --calcs "A,B" --reason TEXT
                                            [--against-oracle RULING]... [--base REF]
                                            [--no-regenerate]
    python tools/accuracy/change_control.py lock --initial
    python tools/accuracy/change_control.py counts [--write]
    python tools/accuracy/change_control.py pre-push REMOTE [URL]      (git-hooks/pre-push)

The files it keeps, under tests/accuracy/change_control/:

- CHANGES.tsv: one row per declared change: id, date, kind (fix, definition,
  feature or none), calcs, analysis_version, lizard_version, changelog, reason.
- changes/<id>.moved.tsv: each golden cell the change moved (golden, path,
  handle, column, old, new), the outside oracle that judged it, the oracle's
  value, and the ruling that covers a disagreement.
- goldens.lock: the sha256 of every locked file and the change that last moved
  it. LOCKED lists what is locked: the goldens, every rulings, hand, probe,
  ground-truth, equivalence and definitions table, the retro probes, and every
  oracle and adapter.
- metric-digests.tsv: one row per (ANALYSIS_VERSION, lizard version, small
  corpus digest), with a digest over every small-corpus scored row's path,
  handle, ccn_std, ccn_mod, ccn, cognitive, nesting, nloc, params, start, end,
  CRAP at 4 dp and cov. The corpus column digests the corpus bytes, recorded
  coverage artifacts included, so a row's metric digest can only move with
  crapkit.
- test-counts.tsv: the number of accuracy test functions per packet, a
  parametrized one counted once: its cases can depend on the OS, the shells and
  the corpus a machine holds, and the function cannot.

In-tree rules hold on any one tree (test_change_control.py, and on the head of
every base-aware run):

- T1 every golden equals crapkit's current output after normalization: the corpus
  packet's tests/accuracy/corpus_goldens/test_goldens.py, which measures each set.
- T2 every locked file's sha256 equals its lock row, and every lockable file has one.
- T3 each lock row names a CHANGES id; CHANGES ids are unique and their kinds known.
- T4 each CHANGES id other than kind none appears in CHANGELOG.md.
- T5 metric-digests rows are unique and ascending; the last row is the running
  ANALYSIS_VERSION, lizard version and corpus, and its digest is the computed one.
- T6 test-counts.tsv equals the collected test functions per packet (pytest only).

Base-aware rules compare a head commit with the merge base of a base ref (pre-push:
origin/main; the CI verdict job: refs/accuracy/green; a release: the previous tag).
A change is fresh when its CHANGES id is not in the base. The base-aware rules
hold once either side has the first lock (kit-close runs `lock --initial`);
before it only the in-tree rules do, since no change can be declared yet.


- B1 the base's metric-digests.tsv is a prefix of the head's.
- B2 every row of CHANGES.tsv, bugs.tsv, triage.tsv, every retro.tsv and the
  kit's seed-changes.tsv is still there byte for byte, every (id, test) row of
  ledger.tsv is still there (a replay re-records its own row), and no rulings id
  is gone. A bug the base's bugs.tsv marks `open` waits on a fix off main, which
  lands as other commits, so its rows may change or go as long as it keeps a
  bugs.tsv row.
- B3 no floor drops, no floor key is gone, and each added survivor or equivalent
  carries evidence. Added ones are printed.
- B4 no packet's test function count drops.
- B5 a locked file that moved names a fresh change in its lock row, and a fresh
  change of another kind than none names a calc of the file's packet.
- B6 a diff that touches a module a calcs.tsv row names needs a fresh change naming
  one of that module's calcs, or a fresh kind none change; each kind none change
  gives a reason, and when every fresh change is kind none no golden cell moved.
  An analyze.py whose only edit is the ANALYSIS_VERSION line touches no calc.
- B7 each fresh fix change comes with a fresh bugs.tsv row, and each fresh bug id
  has a retro.tsv row naming its test.
- B8 a fresh definition change edits a docs file (DOCS), each of its moved.tsv
  rows names a rulings row this diff adds or changes, and each moved calc gains a
  hand or probe row with an outside source in the packet that owns the calc.
- B9 a rulings row that changes needs a fresh fix or definition change naming its calc.
- B10 the fresh changes' calcs equal the moved calcs: every moved cell's calc is
  declared, and nothing is declared that did not move. A calc no golden shows
  (the pre-commit gate, say) counts as moved when the diff touches its module.
- B11 each fresh change's moved.tsv lists exactly the cells it moved, each
  oracle value is what the oracle says now, and each disagreement names a
  ruling of that calc and oracle.

A moved cell is a golden scored.tsv or inventory.tsv cell whose value differs
between base and head, read by (path, handle); a golden table that is new or gone
moves nothing (B5 still needs a change for it). A CRAP cell whose row's ccn or
cov moved, and a remedy cell whose row's ccn, cov, crap or flag moved, follow
from those and need no calc of their own. Another golden file that moved
(a surface, mapped to its calcs by SURFACES) needs its own calc only when no
table cell of a set measured from the same corpus moved: the small, history
and session sets all measure the small corpus, and a set named after a
[member.*] of corpus.toml measures that member.

`declare` first rewrites the goldens with `python tools/accuracy/regenerate.py
goldens` (the corpus packet's regenerator), then judges each moved cell against an
outside oracle before it records anything: radon for Python ccn, complexipy for
Python cognitive, ESLint's complexity rule (classic for ccn_std, modified for
ccn_mod, the lower for ccn) and sonarjs's cognitive complexity for JS, TS and Vue
(oracles/eslint_values.cjs), kit.exact for CRAP from the row's own ccn and cov,
the coverage packet's counts table for cov, and Python's ast for a Python function
row that appears or goes (a def of that name at the row's start line, read at the
base for a row that goes). A cell the oracle disagrees with stops the declare
("crapkit now says 9, radon says 7 at src/a.py:parse"), unless --against-oracle
names a rulings row of that calc whose oracle cell names that oracle. A cell no
oracle here answers (another language, or a start line the oracle finds no
function at) is recorded with no oracle, and its packet's oracle checks judge it.
When the metric digest moves, the running ANALYSIS_VERSION must be new to
metric-digests.tsv (default A1: a move bumps it). A golden a fresh change already
relocked belongs to that change, so a second declare in the same diff (a kind none
for a refactor next to a fix) answers only for what is left.


Exit codes: 0 when every rule holds, or with one skip line when --measured
holds a failure.json from a measurement that stopped; 1 when a rule fails or a
declare is refused; 2 on a usage error.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fnmatch
from fractions import Fraction
from functools import partial
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import tomllib

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from accuracy.kit import exact, goldens, oracles, surfaces, tiers  # noqa: E402


TOOL = "python tools/accuracy/change_control.py"
HOME = "tests/accuracy/change_control"
CHANGES = f"{HOME}/CHANGES.tsv"
LOCK = f"{HOME}/goldens.lock"
DIGESTS = f"{HOME}/metric-digests.tsv"
COUNTS = f"{HOME}/test-counts.tsv"
MOVED = f"{HOME}/changes"
SEED_LOCK = "tests/accuracy/kit/fixtures/seed-goldens.lock"
SEED_CHANGES = "tests/accuracy/kit/fixtures/seed-changes.tsv"
SMALL_CORPUS = "tests/accuracy/corpus_goldens/small"
CORPUS_TOML = "tests/accuracy/corpus_goldens/corpus.toml"
# The small golden set: its own directory under goldens/, or goldens/ itself.
SMALL_GOLDENS = ("tests/accuracy/corpus_goldens/goldens/small",
                 "tests/accuracy/corpus_goldens/goldens")
CORPUS_ENV = "CRAPKIT_ACCURACY_CORPUS"
REGENERATE = "tools/accuracy/regenerate.py"
ANALYZE = "src/crapkit/analyze.py"
CHANGELOG = "CHANGELOG.md"
DOCS = ("README.md", "CONTEXT.md", "docs/agent-json.md", "docs/accuracy.md")
GOLDENS = "tests/accuracy/*/goldens/**"
LOCKED = (GOLDENS, "tests/accuracy/*/rulings.tsv", "tests/accuracy/*/hand_*.tsv",
          "tests/accuracy/*/probes/**/probes.tsv", "tests/accuracy/*/probes/**/ground_truth.tsv",
          "tests/accuracy/*/equivalence.tsv", "tests/accuracy/*/definitions.tsv",
          "tests/accuracy/suite_strength/retro/probes/R*.py", "tests/accuracy/*/oracles/**")
HAND_TABLES = LOCKED[2:7]
RULINGS = "tests/accuracy/*/rulings.tsv"
RETRO = "tests/accuracy/*/retro.tsv"
CALCS = "tests/accuracy/*/calcs.tsv"
BUGS = "tests/accuracy/suite_strength/retro/bugs.tsv"
LEDGER = "tests/accuracy/suite_strength/retro/ledger.tsv"
GROWING = (CHANGES, SEED_CHANGES, BUGS, LEDGER,
           "tests/accuracy/suite_strength/retro/triage.tsv", RETRO)
FLOORS = "tests/accuracy/**/floors.tsv"
EVIDENCED = ("tests/accuracy/suite_strength/mutation/survivors.tsv",
             "tests/accuracy/suite_strength/mutation/equivalent.tsv")
CHANGE_COLUMNS = goldens.CHANGE_COLUMNS
KINDS = goldens.CHANGE_KINDS
LOCK_COLUMNS = goldens.LOCK_COLUMNS
DIGEST_COLUMNS = ("analysis_version", "lizard_version", "corpus", "digest", "change")
MOVED_COLUMNS = ("golden", "path", "handle", "column", "old", "new", "oracle", "oracle_value",
                 "ruling")
COUNT_COLUMNS = ("packet", "tests")
ROW_TABLES = ("scored.tsv", "inventory.tsv")
METRIC_FIELDS = ("ccn_std", "ccn_mod", "ccn", "cognitive", "nesting", "nloc", "params", "start",
                 "end")
NO_SOURCE = re.compile(r"^(|crapkit|observed|v?\d+(\.\d+)+)$", re.IGNORECASE)
GIT_SECONDS = 120
ZERO = "0" * 40

SPAN = "Function discovery and spans"
CCN = "ccn_std, ccn_mod and gated ccn"
COV = ("Function coverage ratio", "coverage.py per-function coverage",
       "Istanbul attribution (vitest v8, nyc, Jest, c8)", "Coverage join",
       "Shared span and def-line floor")
COLUMN_CALCS = {
    "row": (SPAN,), "start": (SPAN,), "end": (SPAN,), "long_name": (SPAN,),
    "occurrence": (SPAN,), "scope": ("File universe and scope ownership",),
    "ccn_std": (CCN,), "ccn_mod": (CCN,), "ccn": (CCN,),
    "cognitive": ("Cognitive complexity",), "nesting": ("Nesting depth",), "nloc": ("nloc",),
    "params": ("Parameter list (params, packet.params)",), "cov": COV,
    "flag": ("Coverage flag",), "crap": ("CRAP score",), "remedy": ("Remedy label",),
}
OTHER_COLUMN = ("Inventory rows and TSV exports",)
# A reader change moves spans and ccn; its language's reader calc may declare it.
READER_COLUMNS = frozenset({"row", "start", "end", "long_name", "occurrence", "ccn_std",
                            "ccn_mod", "ccn"})
_JS = "JS/TS expression arrows and template literals"
READERS = {".py": "Python reader: spans, names, inline_body, unread-def net", ".js": _JS,
           ".jsx": _JS, ".mjs": _JS, ".cjs": _JS, ".ts": _JS, ".tsx": _JS, ".vue": _JS,
           ".rs": "Rust reader (match arms)", ".sh": "Shell reader", ".bash": "Shell reader",
           ".ps1": "PowerShell reader", ".psm1": "PowerShell reader"}
# README: crap is ccn^2 (1 - cov)^3 + ccn; the remedy reads ccn, crap and the flag.
DERIVED = {"crap": ("ccn", "cov"), "remedy": ("ccn", "cov", "crap", "flag")}
_VERDICT = ("verify gate violations", "Verdict exit code and dirty split", "Ratchet regressions",
            "Test failure classes", "Standing unmarked debt", "Baseline and run trust selection")
# A golden file other than the row tables, by name (a `.stderr` file goes with its
# output): the calcs any one of which declares its move, the first being its own.
# The first pattern that matches wins.
SURFACES = (
    ("coverage*.json", ("Coverage run summary",)),
    ("*.sarif", ("SARIF and GitHub annotations",)),
    ("verify-github.txt", ("SARIF and GitHub annotations",)),
    ("doctor.json", ("Doctor findings (unmeasured directories, nearby test)",
                     "doctor --tune knobs and lane cost")),
    ("duplication.json", ("Near-duplicate functions",)),
    ("inventory.txt", ("Inventory rows and TSV exports", "Unanalyzable files and twin-name notes")),
    ("next-item*", ("next-item ranking and empty-queue reasons", "Queue admission and floors")),
    ("report.*", ("HTML report",)),
    ("runs*.json", ("Run totals and trend rollup", "Run retention keep set")),
    ("trend*.json", ("Run totals and trend rollup",)),
    ("digest*", ("Digest deltas",)),
    ("worklist-batches.json", ("Batch split",)),
    ("worklist*", ("Worklist ranking and dormant list", "Queue admission and floors")),
    ("brief*.json", ("Brief packet fields and regrowth", "Handles and NAME resolution")),
    ("coupling.json", ("Change coupling",)),
    ("baseline.tsv", ("Baseline and run trust selection",)),
    ("changed.z", ("Changed line ranges",)),
    ("claims.json", ("Claim ownership and closing",)),
    ("cli-*.json", ("MCP tool results",)),
    ("mcp-*.json", ("MCP tool results",)),
    ("explain*.json", ("Explain history",)),
    ("gate.json", ("rescore --gate verdict",)),
    ("overrides.json", ("Audited override grant",)),
    ("pr-comment*", ("PR comment",)),
    ("ratchet-report.json", ("Burn-down, mark age and debt policy",)),
    ("refusal-name.json", ("Handles and NAME resolution",)),
    ("refusal-scope.json", ("File universe and scope ownership",)),
    ("rescore.json", ("Rescore overlay",)),
    ("seed.txt", ("Ratchet seed and prune", "Metric stamp guard")),
    ("verify*", _VERDICT),
)


class ChangeControlError(ValueError):
    """A ref that does not resolve, a git call that failed, or a refused declare."""


# --- patterns ------------------------------------------------------------------------

_TOKENS = {"**/": "(?:[^/]+/)*", "**": ".*", "*": "[^/]*", "?": "[^/]"}
# The process's caches: compiled globs, corpus.toml members, ESLint answers per file.
_COMPILED: dict[str, re.Pattern] = {}
_MEMBERS: dict[bytes, frozenset[str]] = {}
_ESLINT: dict[tuple[str, str], dict] = {}


def clear_caches() -> None:
    """Empty the process's caches, so the next call computes afresh."""
    for cache in (_COMPILED, _MEMBERS, _ESLINT):
        cache.clear()


def _translated(pattern: str) -> str:
    parts = re.split(r"(\*\*/|\*\*|\*|\?)", pattern)
    return "".join(_TOKENS.get(part, re.escape(part)) for part in parts) + r"\Z"


def _regex(pattern: str) -> re.Pattern:
    if pattern not in _COMPILED:
        _COMPILED[pattern] = re.compile(_translated(pattern))
    return _COMPILED[pattern]


def matches(path: str, patterns) -> bool:
    """Whether a repo path matches a glob: `*` stays in one directory, `**/` spans any."""
    chosen = (patterns,) if isinstance(patterns, str) else patterns
    return any(_regex(pattern).match(path) for pattern in chosen)


def _bytecode(path: str) -> bool:
    return "/__pycache__/" in f"/{path}" or path.endswith(".pyc")


def packet_of(path: str) -> str:
    """The packet directory a tests/accuracy path lies in: kit, score_model, ..."""
    parts = path.split("/")
    return parts[2] if parts[:2] == ["tests", "accuracy"] and len(parts) > 3 else ""


# --- trees -----------------------------------------------------------------------------

def blob_id(data: bytes) -> str:
    """The id git gives these bytes as a blob, so trees of every kind compare."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    tiers.require_process("git")
    done = subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True,
                          timeout=GIT_SECONDS)
    if done.returncode != 0:
        error = done.stderr.decode("utf-8", "replace").strip()
        raise ChangeControlError(f"git {' '.join(args)} exited {done.returncode}: {error}")
    return done.stdout


class DictTree:
    """A tree held in memory: {repo path: bytes}."""

    def __init__(self, files: dict):
        self.files = {path: data for path, data in files.items() if data is not None}

    def paths(self) -> list[str]:
        return sorted(self.files)

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def id(self, path: str) -> str | None:
        data = self.read(path)
        return None if data is None else blob_id(data)


def _ls_tree(repo: Path, commit: str) -> dict[str, str]:
    """{path: blob id} for every blob of a commit."""
    found = {}
    for entry in git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        meta, _, path = entry.partition(b"\t")
        if meta.split(b" ")[1:2] == [b"blob"]:
            found[path.decode("utf-8")] = meta.split(b" ")[2].decode("ascii")
    return found


def _cat_blobs(repo: Path, ids: list[str]) -> dict[str, bytes]:
    """{blob id: bytes} for each id, read in one `git cat-file --batch`."""
    out = git(repo, "cat-file", "--batch", stdin="".join(f"{i}\n" for i in ids).encode("ascii"))
    blobs, at = {}, 0
    while at < len(out):
        end = out.index(b"\n", at)
        name, _, size = out[at:end].decode("ascii").split(" ")
        blobs[name] = out[end + 1:end + 1 + int(size)]
        at = end + 2 + int(size)
    return blobs


def _preloaded(path: str) -> bool:
    return path.startswith("tests/accuracy/") or path in (*DOCS, CHANGELOG, ANALYZE)


class GitTree:
    """A commit's tree, read through git: one ls-tree, then one batch read of
    every tests/accuracy blob the first time any of them is asked for."""

    def __init__(self, repo: Path, ref: str):
        self.repo = Path(repo)
        self.commit = resolve(self.repo, ref)
        self.label = f"{ref} ({self.commit[:12]})"
        self._ids = _ls_tree(self.repo, self.commit)
        self._blobs: dict[str, bytes] = {}

    def paths(self) -> list[str]:
        return sorted(self._ids)

    def id(self, path: str) -> str | None:
        return self._ids.get(path)

    def read(self, path: str) -> bytes | None:
        if path not in self._ids:
            return None
        if path not in self._blobs:
            self._load(path)
        return self._blobs[path]

    def _unread(self, name: str) -> bool:
        return _preloaded(name) and name not in self._blobs

    def _load(self, path: str) -> None:
        ids = {name: self._ids[name] for name in {*filter(self._unread, self._ids), path}}
        blobs = _cat_blobs(self.repo, sorted(set(ids.values())))
        self._blobs.update({name: blobs[blob] for name, blob in ids.items()})


def _walk(root: Path, top: str) -> list[str]:
    base = root / top
    found = (path for path in base.rglob("*") if path.is_file()) if base.is_dir() else ()
    return [path.relative_to(root).as_posix() for path in found]


class DirTree:
    """The working tree: tests/accuracy and the few root files the rules read."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def paths(self) -> list[str]:
        walked = _walk(self.root, "tests/accuracy")
        named = [name for name in (*DOCS, CHANGELOG, ANALYZE) if (self.root / name).is_file()]
        return sorted({*walked, *named} - {path for path in walked if _bytecode(path)})

    def read(self, path: str) -> bytes | None:
        target = self.root / path
        return target.read_bytes() if target.is_file() else None

    def id(self, path: str) -> str | None:
        data = self.read(path)
        return None if data is None else blob_id(data)


def resolve(repo: Path, ref: str) -> str:
    try:
        return git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").decode().strip()
    except ChangeControlError:
        raise ChangeControlError(f"{ref} names no commit here; fetch it, or pass --base with a "
                                 "ref that exists") from None


def merge_base(repo: Path, base: str, head: str) -> str:
    """Where head left base: rows base gained after that are not head's to keep."""
    return git(repo, "merge-base", resolve(repo, base), resolve(repo, head)).decode().strip()


def changed_paths(base, head) -> frozenset[str]:
    paths = set(base.paths()) | set(head.paths())
    return frozenset(path for path in paths if base.id(path) != head.id(path))


# --- tables ----------------------------------------------------------------------------

def lines(data: bytes | None) -> list[str]:
    """Non-empty physical lines, one trailing CR removed."""
    if data is None:
        return []
    return [line.removesuffix("\r") for line in data.decode("utf-8").split("\n") if line.strip()]


def rows(data: bytes | None) -> list[dict]:
    found = lines(data)
    header = found[0].split("\t") if found else []
    return [dict(zip(header, line.split("\t"))) for line in found[1:]]


def table_bytes(columns, records) -> bytes:
    body = ["\t".join(columns)] + ["\t".join(str(cell) for cell in record) for record in records]
    return ("\n".join(body) + "\n").encode("utf-8")


def _first(row: dict) -> str:
    return row.get("id") or next(iter(row.values()), "")


def keyed(data: bytes | None) -> dict[str, dict]:
    """Rows by their id column, or their first column."""
    return {_first(row): row for row in rows(data)}


def split_calcs(cell: str) -> frozenset[str]:
    """A CHANGES calcs cell: calc names separated by `;`. A calc name may hold a
    comma ("ccn_std, ccn_mod and gated ccn"), never a semicolon."""
    return frozenset(part.strip() for part in cell.split(";") if part.strip())


def _merge(pieces: list[str], known: set[str]) -> list[str]:
    names, held = [], ""
    for piece in pieces:
        held = f"{held},{piece}" if held else piece
        if held.strip() in known:
            names.append(held.strip())
            held = ""
    return names + ([held.strip()] if held.strip() else [])


def parse_calcs(text: str, known: set[str]) -> list[str]:
    """--calcs as typed: `;`-separated, or `,`-separated with the pieces of a
    known calc name that holds a comma joined back."""
    if ";" in text:
        return sorted(split_calcs(text))
    return sorted(set(_merge(text.split(","), known)))


def _modules(cell: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in cell.split(",") if part.strip())


def changes_of(tree, path: str = CHANGES) -> dict[str, dict]:
    return keyed(tree.read(path))


def lock_of(tree, path: str = LOCK) -> dict[str, tuple[str, str]]:
    return {row["path"]: (row.get("sha256", ""), row.get("change", ""))
            for row in rows(tree.read(path))}


@dataclass(frozen=True)
class CalcRow:
    packet: str
    calc: str
    test: str
    modules: tuple[str, ...]


def calcs_of(tree) -> list[CalcRow]:
    found = []
    for path in (name for name in tree.paths() if matches(name, CALCS)):
        found += [CalcRow(packet_of(path), row.get("calc", ""), row.get("independent_test", ""),
                          _modules(row.get("modules", ""))) for row in rows(tree.read(path))]
    return found


# The calcs a golden can show: those this tool maps moved cells and surfaces to.
GOLDEN_CALCS = frozenset({calc for names in COLUMN_CALCS.values() for calc in names}
                         | {calc for _, names in SURFACES for calc in names}
                         | set(READERS.values()) | set(OTHER_COLUMN))


def known_calcs(tree) -> set[str]:
    """Every calc name a declaration may use: the calcs.tsv rows and the golden calcs."""
    return {row.calc for row in calcs_of(tree)} | GOLDEN_CALCS


def raw_rows(data: bytes | None) -> list[tuple[dict, str]]:
    """(row, its physical line) for each row of a table."""
    found = lines(data)
    header = found[0].split("\t") if found else []
    return [(dict(zip(header, line.split("\t"))), line) for line in found[1:]]


def _rulings_table(path: str) -> bool:
    return matches(path, RULINGS)


def changed_rulings(before: dict, after: dict) -> dict[str, tuple[dict, dict]]:
    """{id: (base row, head row)} for each rulings row both sides hold whose line changed."""
    return {key: (before[key][1], after[key][1]) for key in sorted(set(before) & set(after))
            if before[key][2] != after[key][2]}


def rulings_of(tree) -> dict[str, tuple[str, dict, str]]:
    """{id: (table path, row, raw line)} over every rulings.tsv."""
    found = {}
    for path in filter(_rulings_table, tree.paths()):
        found.update({_first(row): (path, row, line) for row, line in raw_rows(tree.read(path))})
    return found


# --- problems and moved cells ------------------------------------------------------------

@dataclass(frozen=True)
class Problem:
    rule: str
    text: str
    fix: str


@dataclass(frozen=True)
class Cell:
    golden: str
    path: str
    handle: str
    column: str
    old: str
    new: str

    def key(self) -> tuple:
        return self.golden, self.path, self.handle, self.column, self.old, self.new


def _table_rows(data: bytes | None) -> dict:
    if data is None:
        return {}
    return surfaces.from_tsv(data.decode("utf-8"))


def _presence(row: dict | None) -> str:
    return "absent" if row is None else "present"


def _differing(old: dict, new: dict) -> list[str]:
    return sorted(column for column in set(old) | set(new) if old.get(column) != new.get(column))


def _row_cells(golden: str, key: tuple, old: dict | None, new: dict | None) -> list[Cell]:
    if old is None or new is None:
        return [Cell(golden, *key, "row", _presence(old), _presence(new))]
    return [Cell(golden, *key, column, old.get(column, ""), new.get(column, ""))
            for column in _differing(old, new)]


def _table_cells(golden: str, old: bytes, new: bytes) -> list[Cell]:
    before, after = _table_rows(old), _table_rows(new)
    return [cell for key in sorted(set(before) | set(after))
            for cell in _row_cells(golden, key, before.get(key), after.get(key))]


def golden_tables(tree) -> list[str]:
    return [path for path in tree.paths()
            if matches(path, GOLDENS) and PurePosixPath(path).name in ROW_TABLES]


def moved_cells(base, head) -> list[Cell]:
    """Every golden table cell that differs; a table on one side only moves nothing."""
    both = sorted(set(golden_tables(base)) & set(golden_tables(head)))
    return [cell for path in both if base.id(path) != head.id(path)
            for cell in _table_cells(path, base.read(path), head.read(path))]


def _on_both(path: str, base, head) -> bool:
    return base.id(path) is not None and head.id(path) is not None


def _surface(path: str) -> bool:
    return matches(path, GOLDENS) and PurePosixPath(path).name not in ROW_TABLES


def moved_surfaces(base, head, changed: frozenset[str]) -> list[str]:
    """Golden files other than the row tables that exist on both sides and moved."""
    return sorted(path for path in filter(_surface, changed) if _on_both(path, base, head))


def acceptable(cell: Cell) -> frozenset[str]:
    """The calcs any one of which declares this cell."""
    calcs = set(COLUMN_CALCS.get(cell.column, OTHER_COLUMN))
    reader = READERS.get(PurePosixPath(cell.path).suffix.lower())
    if reader and cell.column in READER_COLUMNS:
        calcs.add(reader)
    return frozenset(calcs)


def _moved_columns(cells: list[Cell]) -> dict[tuple, set[str]]:
    columns: dict[tuple, set[str]] = {}
    for cell in cells:
        columns.setdefault((cell.golden, cell.path, cell.handle), set()).add(cell.column)
    return columns


def _follows(cell: Cell, columns: dict) -> bool:
    moved = columns[(cell.golden, cell.path, cell.handle)]
    return bool(moved.intersection(DERIVED.get(cell.column, ())))


def golden_set(path: str) -> str:
    """The set a golden file belongs to: the directory right under goldens/, or ''
    for a file directly in goldens/."""
    parts = PurePosixPath(path).parts
    after = parts[parts.index("goldens") + 1:] if "goldens" in parts else ()
    return after[0] if len(after) > 1 else ""


def _members(data: bytes | None) -> frozenset[str]:
    """The [member.*] names of a corpus.toml, none when the tree has no corpus.toml."""
    if not data:
        return frozenset()
    if data not in _MEMBERS:
        _MEMBERS[data] = frozenset(tomllib.loads(data.decode("utf-8")).get("member", {}))
    return _MEMBERS[data]


def corpus_name(tree, golden: str) -> str:
    """The corpus a golden file was measured from: the full-corpus member its set is
    named after ([member.<name>] in corpus.toml), else the small corpus, which the
    small, history and session sets all measure."""
    name = golden_set(golden)
    return name if name in _members(tree.read(CORPUS_TOML)) else "small"


def surface_calcs(path: str) -> tuple[str, ...]:
    """The calcs any one of which declares a moved golden file other than a row table."""
    name = PurePosixPath(path).name.removesuffix(".stderr")
    return next((calcs for pattern, calcs in SURFACES if fnmatch.fnmatchcase(name, pattern)),
                OTHER_COLUMN)


def unexplained(tree, cells: list[Cell], moved_surfaces_: list[str]) -> list[str]:
    """The moved surfaces of a corpus none of whose row-table cells moved: a
    moved row explains every surface measured from the same corpus."""
    explained = {corpus_name(tree, cell.golden) for cell in cells}
    return [path for path in moved_surfaces_ if corpus_name(tree, path) not in explained]


def required(tree, cells: list[Cell], moved_surfaces_: list[str]) -> list[frozenset[str]]:
    """One set of acceptable calcs per move that needs its own declaration."""
    columns = _moved_columns(cells)
    needed = {acceptable(cell) for cell in cells if not _follows(cell, columns)}
    files = {frozenset(surface_calcs(path)) for path in unexplained(tree, cells, moved_surfaces_)}
    return sorted(needed | files, key=sorted)


def allowed(cells: list[Cell], moved_surfaces_: list[str], more: set[str]) -> set[str]:
    """Every calc a declaration of this diff may name: the calcs of the moved cells
    and files, and `more` (the calcs of changed rulings rows, and the calcs no
    golden shows whose module the diff touches)."""
    found = {calc for cell in cells for calc in acceptable(cell)}
    return found | {calc for path in moved_surfaces_ for calc in surface_calcs(path)} | more


# --- oracles -----------------------------------------------------------------------------

def _functions(blocks) -> list:
    """radon's functions with their closures. radon lists a class's methods beside
    the class itself; it reads no method of a class nested in a function or class."""
    found = []
    for block in blocks:
        if hasattr(block, "closures"):
            found += [block, *_functions(block.closures)]
    return found


def _one(values: list[int]) -> str | None:
    """The oracle's value at one start line; None when it finds no function there,
    or two that disagree, so it cannot say which one the row is."""
    return str(values[0]) if values and len(set(values)) == 1 else None


def _radon_blocks(source: str) -> list:
    from radon.complexity import cc_visit
    try:
        return _functions(cc_visit(source))
    except SyntaxError:
        return []


def radon_ccn(row: dict, source: str | None) -> str | None:
    """radon's McCabe number for the function at the row's start line: its ast
    reads the source with this interpreter, so a newer syntax answers nothing."""
    blocks = _radon_blocks(source) if source is not None else []
    return _one([block.complexity for block in blocks if block.lineno == int(row["start"])])


def _complexipy_functions(source: str) -> list:
    from complexipy import code_complexity
    try:
        return code_complexity(source).functions
    except ValueError:
        return []


def complexipy_cognitive(row: dict, source: str | None) -> str | None:
    """complexipy's cognitive complexity for the function at the row's start line."""
    found = _complexipy_functions(source) if source is not None else []
    return _one([function.complexity for function in found
                 if function.line_start == int(row["start"])])



def _python_defs(source: str) -> set[tuple[int, str]] | None:
    """(line, name) of every def in a Python source; None when this interpreter's
    ast cannot read it (a newer syntax)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    return {(node.lineno, node.name) for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def ast_row(row: dict, source: str | None) -> str | None:
    """'present' when ast finds a def of the row's name at the row's start line,
    else 'absent': whether a Python function row should exist at all."""
    found = _python_defs(source) if source is not None else None
    if found is None:
        return None
    name = row["long_name"].split("(")[0].strip().rpartition(".")[2]
    return "present" if (int(row["start"]), name) in found else "absent"


ESLINT = f"{HOME}/oracles/eslint_values.cjs"
JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue")
# The ESLint rule answering each column; ccn is the lower of the two variants.
ESLINT_RULES = {"ccn_std": ("classic",), "ccn_mod": ("modified",), "ccn": ("classic", "modified"),
                "cognitive": ("cognitive",)}
NODE_SECONDS = 180


def _process(label: str, argv: list[str], seconds: float, cwd: Path | None = None,
             env: dict | None = None) -> str:
    """The text an outside process prints; ChangeControlError naming `label` with
    the end of its output when it exits nonzero."""
    tiers.require_process(label)
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=seconds)
    if done.returncode != 0:
        raise ChangeControlError(f"{label} exited {done.returncode}:\n"
                                 f"{(done.stdout + done.stderr)[-3000:]}")
    return done.stdout


def _node(script: Path, *args: str) -> str:
    return _process(f"node {script.name}", ["node", str(script), *args], NODE_SECONDS)


def eslint_values(source: str, suffix: str) -> dict:
    """ESLint's complexity (classic and modified) and sonarjs's cognitive complexity
    for every function of one JS, TS or Vue file (oracles/eslint_values.cjs), one
    node run per file."""
    if (source, suffix) not in _ESLINT:
        _ESLINT[source, suffix] = _eslint_run(source, suffix)
    return _ESLINT[source, suffix]


def _eslint_run(source: str, suffix: str) -> dict:
    modules = oracles.node_modules("push")
    if not (modules / "eslint").is_dir():
        raise ChangeControlError("oracle eslint is not installed; run: npm ci --prefix "
                                 "tools/accuracy/node/push")
    with tempfile.TemporaryDirectory(prefix="crapkit-eslint-") as work:
        target = Path(work) / f"source{suffix}"
        target.write_bytes(source.encode("utf-8"))
        return json.loads(_node(REPO / ESLINT, str(modules), str(target)))


def _in_head(heads: list, line: int) -> bool:
    return any(start <= line <= body for start, body in heads)


def _heads(found: dict, start: int) -> list[tuple[int, int]]:
    return [(first, body) for first, body in found.get("functions", ()) if first == start]


def _reported(found: dict, rule: str, heads: list) -> list[int]:
    return [value["value"] for value in found.get("values", ())
            if value["rule"] == rule and _in_head(heads, value["line"])]


def _rule_value(found: dict, start: int, rule: str) -> str | None:
    """The rule's value for the function starting at `start`, reported on a line of
    its head; sonarjs leaves out a function whose value is 0."""
    heads = _heads(found, start)
    unreported = [0] if heads and rule == "cognitive" else []
    return _one(_reported(found, rule, heads) or unreported)



def eslint_answer(row: dict, source: str | None, column: str) -> str | None:
    if source is None:
        return None
    found = eslint_values(source, PurePosixPath(row["path"]).suffix.lower())
    answers = [_rule_value(found, int(row["start"]), rule) for rule in ESLINT_RULES[column]]
    return None if None in answers else str(min(map(int, answers)))


# README, "Flags: why a coverage number is missing": cc-only and excluded score crap = ccn.
CCN_FLAGS = ("cc-only", "excluded")


def exact_crap(row: dict, source: str | None) -> str | None:
    """The README's CRAP from the row's own ccn and cov, exactly: ccn^2 (1 - cov)^3 +
    ccn, and ccn itself on a cc-only or excluded row."""
    try:
        ccn, cov = int(row["ccn"]), Fraction(float(row["cov"]))
    except (KeyError, ValueError):
        return None
    return repr(float(ccn if row.get("flag") in CCN_FLAGS else exact.crap(ccn, cov)))


COUNTS_TABLE = "accuracy.coverage_oracles.counts_table"


def counts_module():
    """The coverage packet's counts table (json.load over the recorded artifacts, no
    crapkit), or None in a tree that does not have it."""
    try:
        return importlib.import_module(COUNTS_TABLE)
    except ModuleNotFoundError as missing:
        if COUNTS_TABLE.startswith(missing.name or "-"):
            return None
        raise


def _materialized(tree) -> Path:
    """The small corpus of a git or in-memory tree, written out once per tree."""
    held = tree.__dict__.get("_corpus")
    if held is None:
        held = tree.__dict__["_corpus"] = tempfile.TemporaryDirectory(prefix="crapkit-corpus-")
        prefix = SMALL_CORPUS + "/"
        for path in (name for name in tree.paths() if name.startswith(prefix)):
            target = Path(held.name) / path[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tree.read(path))
    return Path(held.name)


def corpus_dir(tree, name: str) -> Path | None:
    """The corpus directory on disk: the working tree's own small corpus, a git or
    in-memory tree's written out, or a member under CRAPKIT_ACCURACY_CORPUS."""
    if name != "small":
        root = os.environ.get(CORPUS_ENV)
        return Path(root) / name if root else None
    return tree.root / SMALL_CORPUS if isinstance(tree, DirTree) else _materialized(tree)


def _counts_of(tree, name: str) -> dict:
    """{(path, start): count rows} for one corpus of the tree, read once per tree."""
    tables = tree.__dict__.setdefault("_counts", {})
    if name not in tables:
        directory, module = corpus_dir(tree, name), counts_module()
        usable = module is not None and directory is not None and directory.is_dir()
        tables[name] = module.table(directory) if usable else {}
    return tables[name]


# README, "Flags: why a coverage number is missing": untested, excluded and no-lane
# score cov = 0.
ZERO_COV_FLAGS = ("untested", "excluded", "no-lane")


def counts_cov(tree, cell: Cell, row: dict) -> str | None:
    """The README's coverage ratio (branches, else statements, else called) from the
    counts table's row at the function's start, or 0 on a row the README scores at
    0; nothing when no row, or several (a function two lanes measure), stand there."""
    if row.get("flag") in ZERO_COV_FLAGS:
        return "0.0"
    found = _counts_of(tree, corpus_name(tree, cell.golden)).get((cell.path, int(row["start"])))
    return repr(float(counts_module().ratio(found[0]))) if found and len(found) == 1 else None



def _by_source(function):
    """An oracle that reads the source text of the cell's file."""
    return lambda tree, cell, row: function(row, corpus_source(tree, cell))


def _eslint_oracles() -> dict:
    return {(column, suffix): ("sonarjs" if column == "cognitive" else "eslint",
                               _by_source(partial(eslint_answer, column=column)))
            for column in ESLINT_RULES for suffix in JS_SUFFIXES}


def oracle_table() -> dict:
    """{(column, file suffix, or '' for any file): (oracle name, answer)}. Built on
    each call rather than at import, so a mutant of a helper that builds it is live
    in the tests (tools/accuracy/mutation.py mutates this file)."""
    return {("row", ".py"): ("ast", _by_source(ast_row)),
            ("ccn_std", ".py"): ("radon", _by_source(radon_ccn)),
            ("ccn_mod", ".py"): ("radon", _by_source(radon_ccn)),
            ("ccn", ".py"): ("radon", _by_source(radon_ccn)),
            ("cognitive", ".py"): ("complexipy", _by_source(complexipy_cognitive)),
            ("crap", ""): ("kit.exact", _by_source(exact_crap)),
            ("cov", ""): ("counts table", counts_cov),
            **_eslint_oracles()}


def oracle_for(cell: Cell):
    """(name, function) that answers this cell, or None."""
    table, suffix = oracle_table(), PurePosixPath(cell.path).suffix.lower()
    return table.get((cell.column, suffix)) or table.get((cell.column, ""))


def _close(value: str, oracle: str) -> bool:
    """A float within 4 ulp of the oracle's value; any other value equal as text."""
    try:
        new, wanted = float(value), float(oracle)
    except ValueError:
        return value == oracle
    return math.isfinite(new) and abs(new - wanted) <= 4 * math.ulp(wanted)


@dataclass(frozen=True)
class Judgement:
    cell: Cell
    oracle: str
    value: str

    @property
    def agrees(self) -> bool:
        return not self.oracle or _close(self.cell.new, self.value)


def _outside(name: str, path: str) -> bytes | None:
    root = os.environ.get(CORPUS_ENV)
    target = Path(root) / name / path if root else None
    return target.read_bytes() if target is not None and target.is_file() else None


def corpus_source(tree, cell: Cell) -> str | None:
    """The source text of the cell's file: the small corpus in the tree, or a
    full-corpus member under CRAPKIT_ACCURACY_CORPUS."""
    name = corpus_name(tree, cell.golden)
    small = name == "small"
    data = tree.read(f"{SMALL_CORPUS}/{cell.path}") if small else _outside(name, cell.path)
    return None if data is None else data.decode("utf-8", "replace")


def _row_at(tree, cell: Cell) -> dict:
    return _table_rows(tree.read(cell.golden)).get((cell.path, cell.handle), {})


def _answer(tree, cell: Cell, oracle, before=None) -> str | None:
    row = _row_at(tree, cell) or (_row_at(before, cell) if before is not None else {})
    return oracle(tree, cell, row) if row else None


def judge(tree, cell: Cell, before=None) -> Judgement:
    """The outside oracle's value for a moved cell, read at `tree`; a row gone from
    `tree` is read with its start line and name in `before`, the base."""
    found = oracle_for(cell)
    if found is None or cell.new == "":
        return Judgement(cell, "", "")
    value = _answer(tree, cell, found[1], before)
    return Judgement(cell, found[0] if value is not None else "", value or "")




# --- the report ----------------------------------------------------------------------------

def primary(cell: Cell) -> str:
    """The calc a cell's column names first: what a report calls its move."""
    return COLUMN_CALCS.get(cell.column, OTHER_COLUMN)[0]


def _count(cells: list[Cell], files: list[str]) -> str:
    counts = Counter(map(primary, cells))
    moved = Counter(surface_calcs(path)[0] for path in files)
    return ", ".join([f"{calc} ({count} cells)" for calc, count in sorted(counts.items())]
                     + [f"{calc} ({count} files)" for calc, count in sorted(moved.items())])


def _moved_line(judgement: Judgement) -> str:
    cell = judgement.cell
    oracle = f"{judgement.oracle} {judgement.value}" if judgement.oracle else "-"
    return "\t".join((cell.golden, cell.path, cell.handle, cell.column, cell.old, cell.new, oracle))


def _row_lines(tree, cells: list[Cell], before) -> list[str]:
    shown = [_moved_line(judge(tree, cell, before)) for cell in cells[:10]]
    return ([f"moved rows (first {len(shown)} of {len(cells)}):",
             "  golden\tpath\thandle\tcolumn\told\tnew\toracle"]
            + [f"  {line}" for line in shown]) if shown else []


def _file_lines(files: list[str]) -> list[str]:
    shown = [f"  {path}\t{surface_calcs(path)[0]}" for path in files[:10]]
    return ([f"moved golden files no moved row explains (first {len(shown)} of {len(files)}):"]
            + shown) if shown else []


def moved_block(tree, cells: list[Cell], moved_surfaces_: list[str] = (),
                before=None) -> list[str]:
    """The moved calcs, the first 10 moved rows with each one's oracle value (a row
    gone from `tree` read in `before`), and the first 10 moved golden files no moved
    row explains."""
    files = unexplained(tree, cells, list(moved_surfaces_))
    if not cells and not files:
        return ["moved calcs: none (no golden cell or file moved)"]
    return [f"moved calcs: {_count(cells, files)}", *_row_lines(tree, cells, before),
            *_file_lines(files)]


def next_id(changes: dict) -> str:
    numbers = [int(key[1:]) for key in changes if re.fullmatch(r"C\d+", key)]
    return f"C{max(numbers, default=0) + 1}"


def declare_command(change_id: str, kind: str, calcs) -> str:
    named = "" if kind == "none" else f' --calcs "{", ".join(sorted(calcs)) or "<calc>"}"'
    return f'{TOOL} declare {change_id} --kind {kind}{named} --reason "<why>"'


def report(problems: list[Problem], block: list[str], label: str) -> str:
    if not problems:
        return f"change control: pass ({label})"
    lines_ = [f"change control: FAIL, {len(problems)} problem(s) ({label})"]
    for problem in problems:
        lines_ += [f"{problem.rule} {problem.text}", f"  fix: {problem.fix}"]
    return "\n".join(lines_ + block)


# --- in-tree rules ---------------------------------------------------------------------------

def lockable(tree) -> dict[str, str]:
    """{path: sha256} for every file the lock covers."""
    return {path: hashlib.sha256(tree.read(path)).hexdigest() for path in tree.paths()
            if matches(path, LOCKED) and not _bytecode(path)}


def _initialized(tree) -> bool:
    return bool(changes_of(tree)) or bool(lock_of(tree))


def _uninitialized(now: dict) -> list[Problem]:
    if not now:
        return []
    return [Problem("T2", f"{len(now)} locked files wait for the first lock, the first being "
                          f"{sorted(now)[0]}", f"{TOOL} lock --initial")]


def _lock_problem(path: str, locked, now, changes: dict) -> Problem | None:
    text = goldens._lock_problem(path, locked, now, changes, "")
    if text is None:
        return None
    rule = "T3" if "which no CHANGES row declares" in text else "T2"
    return Problem(rule, text.removesuffix(": "), declare_command("<id>", "<kind>", ()))


def lock_problems(tree) -> list[Problem]:
    """T2 and T3: the lock and the files it covers agree."""
    now = lockable(tree)
    if not _initialized(tree):
        return _uninitialized(now)
    lock, changes = lock_of(tree), changes_of(tree)
    found = (_lock_problem(path, lock.get(path), now.get(path), changes)
             for path in sorted({*lock, *now}))
    return [problem for problem in found if problem]


def _change_problem(change: dict, seen: Counter) -> str | None:
    key = change.get("id", "")
    if seen[key] > 1:
        return f"{key} appears {seen[key]} times"
    if change.get("kind") not in KINDS:
        return f"{key} has kind {change.get('kind')!r}, not one of {', '.join(KINDS)}"
    return None


def changes_problems(tree) -> list[Problem]:
    """T3: CHANGES ids are unique and each kind is known."""
    found = rows(tree.read(CHANGES))
    seen = Counter(change.get("id", "") for change in found)
    texts = {_change_problem(change, seen) for change in found} - {None}
    return [Problem("T3", f"CHANGES.tsv: {text}", f"edit the new row in {CHANGES}")
            for text in sorted(texts)]


def _unlogged(key: str, change: dict, changelog: str) -> bool:
    named = re.search(rf"\b{re.escape(key)}\b", changelog)
    return change.get("kind") != "none" and not named


def changelog_problems(tree) -> list[Problem]:
    """T4: every declared change other than kind none is in CHANGELOG.md."""
    text = (tree.read(CHANGELOG) or b"").decode("utf-8", "replace")
    missing = [key for key, change in changes_of(tree).items() if _unlogged(key, change, text)]
    return [Problem("T4", f"CHANGELOG.md never names change {key}",
                    f"add a line under ## Unreleased that ends `(accuracy change {key})`")
            for key in missing]


@dataclass(frozen=True)
class Running:
    analysis: str
    lizard: str


def analysis_version(tree) -> str:
    """ANALYSIS_VERSION as src/crapkit/analyze.py assigns it, read without importing it."""
    source = tree.read(ANALYZE)
    tree_ = ast.parse(source or b"")
    for node in tree_.body:
        targets = [getattr(target, "id", "") for target in getattr(node, "targets", [])]
        if "ANALYSIS_VERSION" in targets:
            return str(ast.literal_eval(node.value))
    return ""


def running(tree, lizard: str | None = None) -> Running:
    return Running(analysis_version(tree), lizard or importlib.metadata.version("lizard"))


def small_goldens(tree) -> str | None:
    return next((path for path in SMALL_GOLDENS if tree.id(f"{path}/scored.tsv")), None)


def _metric_line(key: tuple, row: dict) -> str:
    crap = row.get("crap", "")
    rounded = str(exact.half_even(float(crap), 4)) if crap else ""
    return "\t".join([*key, *(row.get(name, "") for name in METRIC_FIELDS), rounded,
                      row.get("cov", "")])


def metric_digest(tree) -> str:
    """A digest over every small-corpus scored row's metrics, in (path, handle) order."""
    where = small_goldens(tree)
    table = _table_rows(tree.read(f"{where}/scored.tsv")) if where else {}
    text = "\n".join(_metric_line(key, table[key]) for key in sorted(table))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def corpus_digest(tree) -> str:
    """kit.corpus_run.digest over the small corpus's files, read from the tree."""
    hashed = hashlib.sha256()
    prefix = SMALL_CORPUS + "/"
    for path in sorted(name for name in tree.paths() if name.startswith(prefix)):
        data = tree.read(path)
        hashed.update(path[len(prefix):].encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return hashed.hexdigest()[:16]


def _version(text: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"[.\-]", text))


def _order(row: dict) -> tuple:
    return int(row.get("analysis_version") or 0), _version(row.get("lizard_version", ""))


def _digest_key(row: dict) -> tuple:
    return row.get("analysis_version"), row.get("lizard_version"), row.get("corpus")


def _digest_shape(found: list[dict]) -> list[str]:
    keys = Counter(map(_digest_key, found))
    texts = [f"row {'/'.join(key)} appears {count} times" for key, count in keys.items()
             if count > 1]
    orders = [_order(row) for row in found]
    return texts + (["rows are not in ascending version order"] if orders != sorted(orders) else [])


def _last_row(tree, found: list[dict], now: Running) -> list[str]:
    wanted = {"analysis_version": now.analysis, "lizard_version": now.lizard,
              "corpus": corpus_digest(tree), "digest": metric_digest(tree)}
    last = found[-1] if found else {}
    return [f"the last row has {name} {last.get(name) or '<none>'}, the tree gives {value}"
            for name, value in wanted.items() if last.get(name) != value]


def digest_problems(tree, now: Running) -> list[Problem]:
    """T5: metric-digests.tsv is ordered and its last row is this tree's."""
    if not _initialized(tree):
        return []
    found = rows(tree.read(DIGESTS))
    texts = _digest_shape(found) + _last_row(tree, found, now)
    fix = (f"declare the move with `{declare_command('<id>', '<kind>', ())}` after bumping "
           f"ANALYSIS_VERSION in {ANALYZE}; never edit an old row")
    return [Problem("T5", f"metric-digests.tsv: {text}", fix) for text in texts]


def in_tree(tree, now: Running) -> list[Problem]:
    """T2 to T5 on one tree."""
    return (changes_problems(tree) + lock_problems(tree) + changelog_problems(tree)
            + digest_problems(tree, now))


# --- base-aware rules -------------------------------------------------------------------------

@dataclass
class Diff:
    base: object
    head: object
    changed: frozenset[str]
    extra: list[Cell] = field(default_factory=list)

    def __post_init__(self):
        self.base_changes, self.head_changes = changes_of(self.base), changes_of(self.head)
        self.fresh = {key: row for key, row in self.head_changes.items()
                      if key not in self.base_changes}
        self.cells = moved_cells(self.base, self.head) + list(self.extra)
        self.surfaces = moved_surfaces(self.base, self.head, self.changed)
        self.base_rulings, self.head_rulings = rulings_of(self.base), rulings_of(self.head)
        self.calc_rows = calcs_of(self.head)
        self.touched = touched_calcs(self.calc_rows, self.changed, self.base, self.head)

    def fresh_of(self, *kinds: str) -> dict[str, dict]:
        return {key: row for key, row in self.fresh.items() if row.get("kind") in kinds}

    def declared(self) -> set[str]:
        return {calc for row in self.fresh_of("fix", "definition", "feature").values()
                for calc in split_calcs(row.get("calcs", ""))}

    def changed_rulings(self) -> dict[str, tuple[dict, dict]]:
        return changed_rulings(self.base_rulings, self.head_rulings)

    def packet_calcs(self, packet: str) -> set[str]:
        return {row.calc for row in self.calc_rows if row.packet == packet}


def rule_b1(diff: Diff) -> list[Problem]:
    old, new = lines(diff.base.read(DIGESTS)), lines(diff.head.read(DIGESTS))
    if new[:len(old)] == old:
        return []
    return [Problem("B1", f"{DIGESTS} changed a row the base had; rows only append",
                    f"git checkout {diff.base.commit if hasattr(diff.base, 'commit') else 'BASE'} "
                    f"-- {DIGESTS}, then declare the move as a new row")]


def _kept(path: str, rows_: list[str]) -> list[str]:
    """What of each row must survive: the whole line, except in the ledger, where
    `retro.py run --record` rewrites a row's cells (pending to red and pass, a new
    digest and date) and only its (id, test) key must stay."""
    if path != LEDGER:
        return rows_
    return ["\t".join(row.split("\t")[:2]) for row in rows_]


def _lost_rows(path: str, old: list[str], new: list[str]) -> Problem | None:
    lost = Counter(_kept(path, old[1:])) - Counter(_kept(path, new[1:]))
    if new[:1] != old[:1]:
        lost = Counter({f"header {old[0]}": 1})
    if not lost:
        return None
    first = next(iter(lost)).split("\t")[0]
    return Problem("B2", f"{path} lost or changed {sum(lost.values())} row(s) the base had, "
                         f"the first being {first}",
                   f"restore them from the base (git checkout <base> -- {path}) and add new "
                   "rows below them")


def _changed_matching(diff: Diff, patterns) -> list[str]:
    """The base's tables that match and changed."""
    return [path for path in diff.base.paths() if matches(path, patterns) and path in diff.changed]


def _gone_rulings(diff: Diff) -> list[Problem]:
    gone = sorted(set(diff.base_rulings) - set(diff.head_rulings))
    return [Problem("B2", f"rulings row {key} ({diff.base_rulings[key][0]}) is gone",
                    "restore it; a ruling that no longer applies moves to `fixed`")
            for key in gone]


def _open_bugs(diff: Diff) -> set[str]:
    """The bugs the base's bugs.tsv marks `open`: each waits on a fix off main."""
    return {row.get("id", "") for row in rows(diff.base.read(BUGS)) if row.get("replay") == "open"}


def _settled(old: list[str], landing: set[str]) -> list[str]:
    """The base's rows B2 holds: all but an open bug's, whose fix lands as other commits
    (a cherry-pick), so its rows get rewritten to name them."""
    return old[:1] + [row for row in old[1:] if row.split("\t")[0] not in landing]


def _unrowed(diff: Diff, landing: set[str]) -> list[Problem]:
    kept = {row.get("id") for row in rows(diff.head.read(BUGS))}
    return [Problem("B2", f"open bug {bug} lost every {BUGS} row",
                    "keep the row that names the check that catches it")
            for bug in sorted(landing - kept)]


def rule_b2(diff: Diff) -> list[Problem]:
    landing = _open_bugs(diff)
    found = [_lost_rows(path, _settled(lines(diff.base.read(path)), landing),
                        lines(diff.head.read(path)))
             for path in _changed_matching(diff, GROWING)]
    return list(filter(None, found)) + _gone_rulings(diff) + _unrowed(diff, landing)


def _floor_key(row: dict) -> tuple:
    return tuple(sorted((name, value) for name, value in row.items() if name != "floor"))


def _number(text) -> float:
    return float(text or 0)


def _floor_fell(row: dict, after: dict) -> bool:
    now = after.get(_floor_key(row))
    return now is None or _number(now) < _number(row.get("floor"))


def _floor_problem(path: str, row: dict, after: dict) -> Problem:
    now = after.get(_floor_key(row)) or "nothing"
    return Problem("B3", f"{path}: floor {_first(row)} fell from {row.get('floor')} to {now}",
                   "restore the floor; a floor only rises")


def _floor_problems(path: str, old: list[dict], new: list[dict]) -> list[Problem]:
    after = {_floor_key(row): row.get("floor", "") for row in new}
    return [_floor_problem(path, row, after) for row in old if _floor_fell(row, after)]


def _label(row: dict) -> str:
    return "/".join(list(row.values())[:3])


def _added(old: list[str], new: list[tuple[dict, str]]) -> list[dict]:
    kept = set(old)
    return [row for row, line in new if line not in kept]


def _evidence_problems(path: str, old: list[str], new: list[tuple[dict, str]]) -> list[Problem]:
    added = _added(old, new)
    for row in added:
        print(f"change control: {path} adds {_label(row)}")
    return [Problem("B3", f"{path}: {_label(row)} is added with no evidence",
                    "fill its evidence column from `python tools/accuracy/mutation.py` "
                    "(10,000 examples showing equal outputs)")
            for row in added if not row.get("evidence", "").strip()]


def _floors(diff: Diff) -> list[Problem]:
    return [problem for path in _changed_matching(diff, FLOORS) for problem in
            _floor_problems(path, rows(diff.base.read(path)), rows(diff.head.read(path)))]


def _evidenced(diff: Diff) -> list[Problem]:
    return [problem for path in _changed_matching(diff, EVIDENCED) for problem in
            _evidence_problems(path, lines(diff.base.read(path)), raw_rows(diff.head.read(path)))]


def rule_b3(diff: Diff) -> list[Problem]:
    return _floors(diff) + _evidenced(diff)


def _counts(data: bytes | None) -> dict[str, int]:
    return {row["packet"]: int(row["tests"]) for row in rows(data)}


def rule_b4(diff: Diff) -> list[Problem]:
    before, after = _counts(diff.base.read(COUNTS)), _counts(diff.head.read(COUNTS))
    dropped = [packet for packet, count in sorted(before.items()) if after.get(packet, 0) < count]
    return [Problem("B4", f"packet {packet} collects {after.get(packet, 0)} tests, the base "
                          f"{before[packet]}", "restore the tests; a check is replaced, never "
                                               "dropped")
            for packet in dropped]


def _exempt(path: str, change: dict | None, wanted: set[str]) -> bool:
    """A relock B5 leaves to other rules: no fresh change, kind none, a packet with
    no calcs, a golden (B10) or a rulings table (B9)."""
    return (change is None or change.get("kind") == "none" or not wanted
            or matches(path, (GOLDENS, RULINGS)))


def _relock_problem(diff: Diff, path: str, change_id: str) -> Problem | None:
    change = diff.fresh.get(change_id)
    wanted = diff.packet_calcs(packet_of(path))
    if _exempt(path, change, wanted) or split_calcs(change.get("calcs", "")) & wanted:
        return None
    return Problem("B5", f"{path} moved under {change_id}, which names no calc of packet "
                         f"{packet_of(path)} ({'; '.join(sorted(wanted))})",
                   f"name the calc in {change_id}'s calcs column")


def _lock_rule(diff: Diff, lock: str, changes: str) -> list[Problem]:
    base_lock, head_lock = lock_of(diff.base, lock), lock_of(diff.head, lock)
    texts = goldens.against_base(base_lock, head_lock, changes_of(diff.base, changes),
                                 changes_of(diff.head, changes))
    found = [Problem("B5", text.split(": declare it")[0], declare_command("<id>", "<kind>", ()))
             for text in texts]
    moved = [path for path, row in head_lock.items() if base_lock.get(path) != row]
    return found + list(filter(None, (_relock_problem(diff, path, head_lock[path][1])
                                      for path in moved)))


def rule_b5(diff: Diff) -> list[Problem]:
    return _lock_rule(diff, LOCK, CHANGES) + _lock_rule(diff, SEED_LOCK, SEED_CHANGES)


def _unversioned(data: bytes | None) -> bytes:
    return re.sub(rb"(?m)^ANALYSIS_VERSION\s*=.*$", b"", data or b"")


def _version_only(module: str, base, head) -> bool:
    """analyze.py with only its ANALYSIS_VERSION line moved: the bump A1 asks of any
    change that moves a metric, which changes no calc that module holds."""
    return module == ANALYZE and _unversioned(base.read(module)) == _unversioned(head.read(module))


def touched_calcs(calc_rows: list[CalcRow], changed, base, head) -> dict[str, list[str]]:
    """{changed module: the calcs it holds}, leaving out a version-only analyze.py."""
    touched: dict[str, list[str]] = {}
    for row in calc_rows:
        for module in set(row.modules) & set(changed):
            touched.setdefault(module, []).append(row.calc)
    return {module: sorted(calcs) for module, calcs in touched.items()
            if not _version_only(module, base, head)}


def unshown(touched: dict[str, list[str]]) -> set[str]:
    """The calcs of the touched modules that no golden can show: a declaration may
    name them with nothing moved."""
    return {calc for calcs in touched.values() for calc in calcs if calc not in GOLDEN_CALCS}


PRIMARIES = (frozenset(names[0] for names in COLUMN_CALCS.values())
             | {names[0] for _, names in SURFACES})


def _preferred(options: frozenset[str]) -> str:
    return min(options, key=lambda calc: (calc not in PRIMARIES, calc))


def _names(needed: list[frozenset[str]]) -> set[str]:
    """One calc per need, a column's own calc before a reader's."""
    return set(map(_preferred, needed))


def _suggestion(diff: Diff) -> str:
    """The declare command this diff's moves call for."""
    calcs = _names(required(diff.head, diff.cells, diff.surfaces))
    return declare_command(next_id(diff.head_changes), "fix" if calcs else "none", calcs)


def _silent(nones: dict[str, dict]) -> list[Problem]:
    return [Problem("B6", f"change {key} is kind none with no reason", f"give {key} a reason")
            for key, row in nones.items() if not row.get("reason", "").strip()]


def _none_problems(diff: Diff) -> list[Problem]:
    nones = diff.fresh_of("none")
    moved = len(nones) == len(diff.fresh) and diff.cells
    return _silent(nones) + ([Problem("B6", f"every fresh change is kind none, yet "
                                            f"{len(diff.cells)} golden cells moved",
                                      _suggestion(diff))] if moved else [])


def _unnamed(diff: Diff) -> list[str]:
    """The touched modules no fresh change names a calc of, with no fresh kind
    none change to cover them."""
    if diff.fresh_of("none"):
        return []
    named = diff.declared()
    return [module for module, calcs in sorted(diff.touched.items()) if not named & set(calcs)]


def _module_problem(diff: Diff, module: str) -> Problem:
    fix = (f"name one of them in the change's calcs, or declare the edit as a change that moves "
           f"nothing: {declare_command(next_id(diff.head_changes), 'none', ())}")
    return Problem("B6", f"{module} holds {'; '.join(diff.touched[module])} and changed with no "
                         "declared change naming one of them", fix)


def rule_b6(diff: Diff) -> list[Problem]:
    nones = _none_problems(diff) if diff.fresh else []

    return nones + [_module_problem(diff, module) for module in _unnamed(diff)]


def _retro_ids(tree) -> dict[str, str]:
    return {_first(row): row.get("test", "") for path in tree.paths() if matches(path, RETRO)
            for row in rows(tree.read(path))}


def _too_few_bugs(fixes: dict, bugs: set[str]) -> list[Problem]:
    if len(bugs) >= len(fixes):
        return []
    return [Problem("B7", f"{len(fixes)} fix change(s) ({', '.join(sorted(fixes))}) and "
                          f"{len(bugs)} new bugs.tsv row(s)",
                    f"add a row per fixed bug to {BUGS} with its before and fix commits")]


def rule_b7(diff: Diff) -> list[Problem]:
    fixes = diff.fresh_of("fix")
    bugs = set(keyed(diff.head.read(BUGS))) - set(keyed(diff.base.read(BUGS))) if fixes else set()
    retro = _retro_ids(diff.head)
    return _too_few_bugs(fixes, bugs) + [
        Problem("B7", f"bug {bug} has no retro.tsv row naming its test",
                "add `<id>\\t<test node id>` to the owning packet's retro.tsv")
        for bug in sorted(bugs) if not retro.get(bug)]


def _hand_table(path: str, packets: set[str]) -> bool:
    return matches(path, HAND_TABLES) and (not packets or packet_of(path) in packets)


def _new_rows(diff: Diff, path: str) -> list[dict]:
    old = set(lines(diff.base.read(path)))
    return [row for row, line in raw_rows(diff.head.read(path)) if line not in old]


def _hand_rows(diff: Diff, packets: set[str]) -> list[dict]:
    """Rows a hand or probe table in these packets gained or changed."""
    tables = sorted(path for path in diff.changed if _hand_table(path, packets))
    return [row for path in tables for row in _new_rows(diff, path)]


def _sourced(found: list[dict]) -> bool:
    return any(not NO_SOURCE.match(row.get("source", "").strip()) for row in found)


def _calc_packets(diff: Diff, calc: str) -> set[str]:
    return {row.packet for row in diff.calc_rows if row.calc == calc}


def _docs_problem(diff: Diff, key: str) -> list[Problem]:
    if diff.changed.intersection(DOCS):
        return []
    return [Problem("B8", f"definition {key} edits no docs file ({', '.join(DOCS)})",
                    "state the definition where users read it, in the same diff")]


def _loose_rulings(diff: Diff, key: str) -> list[Problem]:
    touched = set(diff.changed_rulings()) | (set(diff.head_rulings) - set(diff.base_rulings))
    cited = {row.get("ruling", "") for row in rows(diff.head.read(f"{MOVED}/{key}.moved.tsv"))}
    return [Problem("B8", f"definition {key}: moved rows cite ruling {ruling or '<none>'}, "
                          "which this diff neither adds nor changes",
                    "add or change the rulings row whose construct covers them")
            for ruling in sorted(cited - touched)]


def _unexercised(diff: Diff, key: str, change: dict) -> list[Problem]:
    calcs = sorted(split_calcs(change.get("calcs", "")))
    return [Problem("B8", f"definition {key}: no hand or probe row with an outside source "
                          f"exercises {calc}", "add one to that calc's packet, citing its source")
            for calc in calcs if not _sourced(_hand_rows(diff, _calc_packets(diff, calc)))]


def _definition_problems(diff: Diff, key: str, change: dict) -> list[Problem]:
    return (_docs_problem(diff, key) + _loose_rulings(diff, key)
            + _unexercised(diff, key, change))


def rule_b8(diff: Diff) -> list[Problem]:
    return [problem for key, change in sorted(diff.fresh_of("definition").items())
            for problem in _definition_problems(diff, key, change)]


def _covers(diff: Diff, calc: str) -> bool:
    return any(calc in split_calcs(row.get("calcs", ""))
               for row in diff.fresh_of("fix", "definition").values())


def _uncovered(diff: Diff, old: dict, new: dict) -> bool:
    return old.get("calc") != new.get("calc") or not _covers(diff, new.get("calc", ""))


def _ruling_recast(key: str, old: dict, new: dict) -> Problem:
    return Problem("B9", f"rulings row {key} moved from {old.get('calc')} to {new.get('calc')}; "
                         "a ruling keeps its calc",
                   f"put {key} back under {old.get('calc')} and add a new rulings row for "
                   f"{new.get('calc')}")


def _ruling_moved(diff: Diff, key: str, old: dict, new: dict) -> Problem:
    if old.get("calc") != new.get("calc"):
        return _ruling_recast(key, old, new)
    what = (f"{old.get('ruling')} to {new.get('ruling')}, crapkit {old.get('crapkit_value')} to "
            f"{new.get('crapkit_value')}")
    calc = new.get("calc", "")
    return Problem("B9", f"rulings row {key} changed ({what}) with no fresh fix or definition "
                         f"change naming {calc}",
                   declare_command(next_id(diff.head_changes), "fix", {calc}))


def rule_b9(diff: Diff) -> list[Problem]:
    return [_ruling_moved(diff, key, old, new)
            for key, (old, new) in diff.changed_rulings().items() if _uncovered(diff, old, new)]


def _ruling_calcs(diff: Diff) -> set[str]:
    return {new.get("calc", "") for _, new in diff.changed_rulings().values()}


def undeclared(tree, declared: set[str], cells: list[Cell], moved_surfaces_: list[str]) -> list:
    """Each move that needs a calc no declaration names."""
    return [options for options in required(tree, cells, moved_surfaces_)
            if not options & declared]


def overdeclared(declared: set[str], cells: list[Cell], moved_surfaces_: list[str],
                 more: set[str]) -> list[str]:
    """Each declared calc that nothing in the diff moved."""
    return sorted(declared - allowed(cells, moved_surfaces_, more))


def _drop(calc: str) -> str:
    """How to fix a calc declared with nothing moved."""
    if calc in GOLDEN_CALCS:
        return (f"drop {calc} from the change's calcs column; a fix the goldens can show moves "
                "a golden row, so add the fixed shape to the small corpus if it is missing")
    return f"drop {calc} from the change's calcs column, or change the module that holds it"


def rule_b10(diff: Diff) -> list[Problem]:
    declared = diff.declared()
    missing = [Problem("B10", f"moved calc not declared: {' or '.join(sorted(options))}",
                       _suggestion(diff))
               for options in undeclared(diff.head, declared, diff.cells, diff.surfaces)]
    more = _ruling_calcs(diff) | unshown(diff.touched)
    return missing + [Problem("B10", f"declared calc did not move: {calc}", _drop(calc))
                      for calc in overdeclared(declared, diff.cells, diff.surfaces, more)]


def _attributed(diff: Diff) -> dict[str, list[Cell]]:
    """{fresh change id: the cells of the golden files its lock rows moved}."""
    lock = lock_of(diff.head)
    found: dict[str, list[Cell]] = {}
    for cell in diff.cells:
        owner = lock.get(cell.golden, ("", ""))[1]
        if owner in diff.fresh:
            found.setdefault(owner, []).append(cell)
    return found


def _moved_row_key(row: dict) -> tuple:
    return tuple(row.get(name, "") for name in MOVED_COLUMNS[:6])


def _listing_problems(key: str, cells: list[Cell], listed: list[dict]) -> list[Problem]:
    want = {cell.key() for cell in cells}
    have = {_moved_row_key(row) for row in listed}
    if want == have:
        return []
    return [Problem("B11", f"changes/{key}.moved.tsv lists {len(have - want)} cells that did not "
                           f"move and misses {len(want - have)} that did",
                    f"rerun the declare: {TOOL} declare {key} ... writes it")]


def names_oracle(text: str, oracle: str) -> bool:
    """Whether a rulings row's oracle cell names this oracle as a word: "radon 6.0.1"
    names radon and "kit.exact half-even" names kit.exact."""
    pattern = rf"(?<![\w.]){re.escape(oracle)}(?![\w.])"
    return bool(oracle) and re.search(pattern, text or "", re.IGNORECASE) is not None


def covers(ruling: dict, cell: Cell, oracle: str) -> bool:
    """A rulings row covers a disagreement when it is of the cell's calc and names the oracle."""
    return ruling.get("calc") in acceptable(cell) and names_oracle(ruling.get("oracle", ""), oracle)


def _ruling_problem(diff: Diff, key: str, row: dict, cell: Cell) -> Problem | None:
    ruling = diff.head_rulings.get(row.get("ruling", ""), ("", {}, ""))[1]
    if covers(ruling, cell, row.get("oracle", "")):
        return None
    return Problem("B11", f"changes/{key}.moved.tsv: {cell.path}:{cell.handle} {cell.column} "
                          f"{cell.new} disagrees with {row.get('oracle')} "
                          f"{row.get('oracle_value')} and ruling {row.get('ruling') or '<none>'} "
                          "does not cover that calc and oracle",
                   "fix the code, or name the covering ruling with --against-oracle")


def _recorded(row: dict) -> tuple[str, str]:
    return row.get("oracle", ""), row.get("oracle_value", "")


def _misrecorded(key: str, row: dict, judged: Judgement) -> Problem:
    cell = judged.cell
    recorded = " ".join(filter(None, _recorded(row))) or "no oracle"
    now = f"{judged.oracle} says {judged.value}" if judged.oracle else "no oracle answers"
    return Problem("B11", f"changes/{key}.moved.tsv records {recorded} at {cell.path}:"
                          f"{cell.handle} {cell.column}; {now}", f"rerun the declare of {key}")


def _oracle_problem(diff: Diff, key: str, row: dict) -> Problem | None:
    cell = Cell(*_moved_row_key(row))
    judged = judge(diff.head, cell, diff.base)
    if (judged.oracle, judged.value) != _recorded(row):
        return _misrecorded(key, row, judged)
    return None if judged.agrees else _ruling_problem(diff, key, row, cell)


def rule_b11(diff: Diff) -> list[Problem]:
    found = []
    for key, cells in sorted(_attributed(diff).items()):
        listed = rows(diff.head.read(f"{MOVED}/{key}.moved.tsv"))
        found += _listing_problems(key, cells, listed)
        found += list(filter(None, (_oracle_problem(diff, key, row) for row in listed)))
    return found


RULES = (rule_b1, rule_b2, rule_b3, rule_b4, rule_b5, rule_b6, rule_b7, rule_b8, rule_b9,
         rule_b10, rule_b11)


def base_aware(base, head) -> bool:
    """The base-aware rules hold once either side has the first lock. Before it
    (kit-close runs `lock --initial`) no change can be declared, so a calc-module
    diff has nothing to name; an emptied CHANGES.tsv on a locked base is B2's."""
    return _initialized(base) or _initialized(head)


def verdict(base, head, now: Running, extra: list[Cell] = ()) -> tuple[list[Problem], Diff]:
    """Every problem between two trees: the in-tree rules on the head, then B1 to B11."""
    diff = Diff(base, head, changed_paths(base, head), list(extra))
    rules = RULES if base_aware(base, head) else ()
    return in_tree(head, now) + [problem for rule in rules for problem in rule(diff)], diff



# --- check -------------------------------------------------------------------------------------

def measurement_stopped(measured: Path | None) -> str | None:
    """The skip line when a measure side handed off failure.json instead of a wheel."""
    for side in ("base", "candidate"):
        failure = measured / side / "failure.json" if measured else None
        if failure is not None and failure.is_file():
            record = json.loads(failure.read_text(encoding="utf-8"))
            return (f"change control: skipped, the {side} measurement stopped in "
                    f"{record.get('phase', '?')}: {record.get('error', '?')}")
    return None


def read_moved(path: Path | None) -> list[Cell]:
    """Cells from another tool's moved-row map (wheel_diff), in moved.tsv's columns."""
    if path is None:
        return []
    return [Cell(*_moved_row_key(row)) for row in rows(path.read_bytes())]


def check(repo: Path, base: str, head: str = "HEAD", moved: Path | None = None,
          lizard: str | None = None) -> tuple[int, str]:
    """(exit code, report) for the head commit against the merge base with `base`."""
    head_tree = GitTree(repo, head)
    base_tree = GitTree(repo, merge_base(repo, base, head))
    base_tree.label = f"{base} at {base_tree.commit[:12]}"
    problems, diff = verdict(base_tree, head_tree, running(head_tree, lizard),
                             extra=read_moved(moved))
    text = report(problems, moved_block(head_tree, diff.cells, diff.surfaces, base_tree),
                  f"{base_tree.label} to {head_tree.label}")
    return (1 if problems else 0), text


def _check_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="change_control.py", description="Judge a diff.")
    parser.add_argument("--base", required=True, help="the ref to compare with (merge base)")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--measured", type=Path, help="the CI hand-off directory")
    parser.add_argument("--moved", type=Path, help="a moved-row map in moved.tsv's columns")
    return parser


def _check_main(argv: list[str]) -> int:
    args = _check_parser().parse_args(argv)
    skipped = measurement_stopped(args.measured)
    if skipped:
        print(skipped)
        return 0
    code, text = check(args.repo, args.base, args.head, args.moved)
    print(text)
    return code


# --- declare ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    id: str
    kind: str
    calcs: tuple[str, ...]
    reason: str
    against: tuple[str, ...] = ()
    today: str = ""


def _request_problems(request: Request, changes: dict, known: set[str]) -> list[str]:
    unknown = sorted(set(request.calcs) - known)
    checks = [
        (re.fullmatch(r"[A-Z][A-Za-z0-9_-]*", request.id) is None,
         f"id {request.id!r} is not a word starting with a capital, such as {next_id(changes)}"),
        (request.id in changes, f"{request.id} is already declared; the next free id is "
                                f"{next_id(changes)}"),
        (request.kind not in KINDS, f"kind {request.kind!r} is not one of {', '.join(KINDS)}"),
        (not request.reason.strip(), "a declared change gives a --reason"),
        (bool(unknown), "no calc is named " + ", ".join(map(repr, unknown))),
    ]
    return [text for failed, text in checks if failed]


def _ruling_row(rulings: dict, key: str) -> dict:
    return rulings.get(key, ("", {}, ""))[1]


def _covering(rulings: dict, judgement: Judgement, against: tuple[str, ...]) -> str:
    """The first named ruling of this cell's calc and oracle, or ''."""
    return next((key for key in against
                 if covers(_ruling_row(rulings, key), judgement.cell, judgement.oracle)), "")


def _definition_ruling(rulings: dict, cell: Cell, request: Request) -> str:
    """For a definition, the first named ruling of the cell's calc: the construct
    every moved row of a definition cites (B8)."""
    if request.kind != "definition":
        return ""
    return next((key for key in request.against
                 if _ruling_row(rulings, key).get("calc") in acceptable(cell)), "")


def _refusal(judgement: Judgement) -> str:
    cell = judgement.cell
    return (f"crapkit now says {cell.new}, {judgement.oracle} says {judgement.value} at "
            f"{cell.path}:{cell.handle} ({cell.column}): this looks like a regression, fix the "
            "code; if crapkit is right to differ, name the rulings row that covers it with "
            "--against-oracle <ruling-id>")


def _judge_one(base, head, cell: Cell, rulings: dict, request: Request) -> tuple:
    """(judgement, the ruling recorded for it, the refusal or None)."""
    judgement = judge(head, cell, base)
    if judgement.agrees:
        return judgement, _definition_ruling(rulings, cell, request), None
    ruling = _covering(rulings, judgement, request.against)
    return judgement, ruling, None if ruling else _refusal(judgement)


def _first_ten(refusals: list[str]) -> list[str]:
    more = len(refusals) - 10
    return refusals[:10] + ([f"... and {more} more moved cells an oracle disagrees with"]
                            if more > 0 else [])


def _judged(base, head, cells: list[Cell], request: Request) -> tuple[list, list[str]]:
    """([(judgement, ruling)], the first 10 refusals) for every moved cell."""
    rulings = rulings_of(head)
    triples = [_judge_one(base, head, cell, rulings, request) for cell in cells]
    return ([(judgement, ruling) for judgement, ruling, _ in triples],
            _first_ten([refusal for *_, refusal in triples if refusal]))


def _none_moves(cells: list[Cell], moved_surfaces_: list[str]) -> list[str]:
    moved = len(cells) + len(moved_surfaces_)
    return [f"{moved} golden cells or files moved; kind none declares a change that moves "
            "nothing"] if moved else []


@dataclass(frozen=True)
class Moves:
    """What a declaration answers for: the moved cells and files no fresh change
    declared yet, and the calcs it may name with nothing moved (changed rulings
    rows, and calcs no golden shows whose module the working tree changed)."""
    cells: list[Cell]
    surfaces: list[str]
    more: set[str]


def _calc_texts(head, declared: set[str], moves: Moves) -> list[str]:
    missing = undeclared(head, declared, moves.cells, moves.surfaces)
    extra = overdeclared(declared, moves.cells, moves.surfaces, moves.more)
    return ([f"moved calc not declared: {' or '.join(sorted(options))}" for options in missing]
            + [f"declared calc did not move: {calc}" for calc in extra])


def _declaration_problems(request: Request, head, moves: Moves) -> list[str]:
    if request.kind == "none":
        return _none_moves(moves.cells, moves.surfaces)
    return _calc_texts(head, set(request.calcs), moves)


def _moved_locks(lock: dict, now: dict) -> list[str]:
    return sorted(path for path in {*lock, *now} if lock.get(path, ("", ""))[0] != now.get(path))


def _relock(head, change_id: str) -> tuple[dict, list[str]]:
    """The head's lock with every moved lockable file relocked under change_id."""
    lock, now = lock_of(head), lockable(head)
    moved = _moved_locks(lock, now)
    kept = {path: lock[path] for path in now.keys() & lock.keys()}
    kept.update({path: (now[path], change_id) for path in now.keys() & set(moved)})
    return kept, moved


def _new_digest_row(head, now: Running, change_id: str) -> dict:
    return {"analysis_version": now.analysis, "lizard_version": now.lizard,
            "corpus": corpus_digest(head), "digest": metric_digest(head), "change": change_id}


def _last(found: list[dict]) -> dict:
    return found[-1] if found else {}


def _same_metrics(found: list[dict], row: dict) -> bool:
    last = _last(found)
    return all(last.get(name) == row[name] for name in DIGEST_COLUMNS[:4])


def _digest_refusal(found: list[dict], row: dict, now: Running) -> list[str]:
    last = _last(found)
    if _digest_key(row) in set(map(_digest_key, found)):
        return [f"the metric digest moved from {last.get('digest')} to {row['digest']} under "
                f"analysis {now.analysis}, lizard {now.lizard}: bump ANALYSIS_VERSION in "
                f"{ANALYZE} to {int(now.analysis or 0) + 1} (A1), then rerun this declare"]
    if found and _order(row) < _order(last):
        return [f"analysis {now.analysis}, lizard {now.lizard} is older than the last "
                f"metric-digests row ({last.get('analysis_version')}, "
                f"{last.get('lizard_version')})"]
    return []


def _digest_row(head, now: Running, change_id: str) -> tuple[dict | None, list[str]]:
    """(the metric-digests row this declare appends or None, refusals)."""
    found = rows(head.read(DIGESTS))
    row = _new_digest_row(head, now, change_id)
    if _same_metrics(found, row):
        return None, []
    return row, _digest_refusal(found, row, now)


@dataclass
class Plan:
    request: Request
    judged: list
    lock: dict
    relocked: list[str]
    digest: dict | None
    base_analysis: str


def _golden_paths(base, head) -> set[str]:
    return {path for tree in (base, head) for path in tree.paths() if matches(path, GOLDENS)}


def _changed_goldens(base, head) -> frozenset[str]:
    return frozenset(path for path in _golden_paths(base, head) if base.id(path) != head.id(path))


def _nothing_moved(request: Request, moves: Moves) -> list[str]:
    """A change of a kind other than none must move a golden or name a calc whose
    rulings row or module changed; a relocked hand table alone is kind none."""
    if request.kind == "none" or moves.cells or moves.surfaces or set(request.calcs) & moves.more:
        return []
    return ["nothing moved since the lock; a change that moves nothing is kind none"]


def _owned(head, lock: dict, path: str, fresh: set[str]) -> bool:
    """A golden already relocked under a fresh change and unchanged since."""
    digest, owner = lock.get(path, ("", ""))
    return owner in fresh and digest == hashlib.sha256(head.read(path) or b"").hexdigest()


def moves_of(base, head, changed: frozenset[str]) -> Moves:
    """The moves a declaration of `head` against `base` answers for: a golden a
    fresh change already relocked belongs to that change, so a kind none change
    can follow a fix in the same diff."""
    fresh, lock = set(changes_of(head)) - set(changes_of(base)), lock_of(head)
    cells = [cell for cell in moved_cells(base, head)
             if not _owned(head, lock, cell.golden, fresh)]
    files = [path for path in moved_surfaces(base, head, _changed_goldens(base, head))
             if not _owned(head, lock, path, fresh)]
    return Moves(cells, files, _more(base, head, changed))


def _more(base, head, changed: frozenset[str]) -> set[str]:
    edited = changed_rulings(rulings_of(base), rulings_of(head))
    rulings = {new.get("calc", "") for _, new in edited.values()}
    return rulings | unshown(touched_calcs(calcs_of(head), changed, base, head))



def _uninitialized_refusal(head) -> list[str]:
    return [] if _initialized(head) else [f"the lock is not initialized: run {TOOL} lock "
                                          "--initial first"]


def plan_declare(base, head, request: Request, now: Running,
                 changed: frozenset[str] = frozenset()) -> Plan:
    """Judge a declaration of every move between base and head (`changed` holds
    the paths the working tree changed); refuse with every reason at once, or
    return what to write."""
    refusals = (_request_problems(request, changes_of(head), known_calcs(head))
                + _uninitialized_refusal(head))
    moves = moves_of(base, head, changed)
    judged, bad = _judged(base, head, moves.cells, request)
    lock, relocked = _relock(head, request.id)
    digest, stale = _digest_row(head, now, request.id)
    refusals += (bad + _declaration_problems(request, head, moves)
                 + _nothing_moved(request, moves) + stale)
    if refusals:
        raise ChangeControlError("declare refused:\n" + "\n".join(f"  {text}" for text in refusals)
                                 + "\n" + "\n".join(moved_block(head, moves.cells, moves.surfaces,
                                                                 base)))
    return Plan(request, judged, lock, relocked, digest, analysis_version(base))



def _append(path: Path, columns, record) -> None:
    data = path.read_bytes() if path.is_file() else b""
    header = b"" if data.strip() else ("\t".join(columns) + "\n").encode("utf-8")
    data = data if not data or data.endswith(b"\n") else data + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data + header + ("\t".join(record) + "\n").encode("utf-8"))


def _moved_record(judgement: Judgement, ruling: str) -> list[str]:
    return [*judgement.cell.key(), judgement.oracle, judgement.value, ruling]


def _write_moved(root: Path, plan: Plan) -> None:
    if not plan.judged:
        return
    (root / MOVED).mkdir(parents=True, exist_ok=True)
    (root / f"{MOVED}/{plan.request.id}.moved.tsv").write_bytes(table_bytes(
        MOVED_COLUMNS, (_moved_record(*pair) for pair in plan.judged)))


def _write_digest(root: Path, plan: Plan) -> None:
    if plan.digest:
        _append(root / DIGESTS, DIGEST_COLUMNS, [plan.digest[name] for name in DIGEST_COLUMNS])


def write_plan(root: Path, plan: Plan, now: Running) -> None:
    request = plan.request
    _append(root / CHANGES, CHANGE_COLUMNS, [request.id, request.today, request.kind,
                                             "; ".join(request.calcs), now.analysis, now.lizard,
                                             "#unreleased", request.reason])
    (root / LOCK).write_bytes(table_bytes(LOCK_COLUMNS, ((path, *plan.lock[path])
                                                         for path in sorted(plan.lock))))
    _write_moved(root, plan)
    _write_digest(root, plan)


def _covered_text(plan: Plan) -> str:
    under = sorted({ruling for _, ruling in plan.judged if ruling})
    return f", ruling(s) {', '.join(under)} cover a difference" if under else ""


def _judged_line(plan: Plan) -> str:
    request = plan.request
    answered = sum(1 for judgement, _ in plan.judged if judgement.oracle)
    return (f"declared {request.id} ({request.kind}: {'; '.join(request.calcs) or 'no calc'}): "
            f"{len(plan.relocked)} locked files relocked, {len(plan.judged)} golden cells moved, "
            f"{answered} judged by an oracle{_covered_text(plan)}")


def _unanswered_line(plan: Plan) -> list[str]:
    count = sum(1 for judgement, _ in plan.judged if not judgement.oracle)
    return [f"{count} moved cells have no oracle here; their packet's oracle checks judge "
            "them"] if count else []


def _digest_line(plan: Plan, now: Running) -> list[str]:
    return [f"metric-digests: new row for analysis {now.analysis}, lizard {now.lizard} "
            f"(ANALYSIS_VERSION was {plan.base_analysis} at the base)"] if plan.digest else []


def _changelog_lines(request: Request) -> list[str]:
    return [f"add to {CHANGELOG} under ## Unreleased:",
            f"- {request.reason.rstrip('.')}. (accuracy change {request.id})"
            ] if request.kind != "none" else []


def summary(plan: Plan, now: Running) -> str:
    return "\n".join([_judged_line(plan), *_unanswered_line(plan), *_digest_line(plan, now),
                      *_changelog_lines(plan.request)])


def regenerate(root: Path) -> str:
    """Rewrite the goldens with the corpus packet's regenerator, `python
    tools/accuracy/regenerate.py goldens`; a note when this tree has none."""
    script = root / REGENERATE
    if not script.is_file():
        return f"{REGENERATE} is not in this tree; the goldens are judged as they are"
    _process(f"{REGENERATE} goldens", [sys.executable, str(script), "goldens"],
             GIT_SECONDS * 15, root, _tests_env(root, {}))
    return ""


def worktree_changes(root: Path, base: str) -> frozenset[str]:
    """Every path the working tree changed since `base`: edits, deletions and new files."""
    tracked = git(root, "diff", "--name-only", "-z", resolve(root, base), "--")
    new = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return frozenset(name for name in (tracked + new).decode("utf-8").split("\0") if name)


RESTORE = ("the regenerated goldens stay in the working tree to read; `git checkout -- "
           "tests/accuracy/corpus_goldens/goldens` puts the committed ones back")


def _planned(root: Path, request: Request, base: str, now: Running, regenerated: bool) -> Plan:
    try:
        return plan_declare(GitTree(root, base), DirTree(root), request, now,
                            worktree_changes(root, base))
    except ChangeControlError as refused:
        raise ChangeControlError(f"{refused}\n{RESTORE}" if regenerated else str(refused)) from None


def declare(root: Path, request: Request, base: str = "HEAD", regenerate_goldens: bool = True,
            lizard: str | None = None) -> str:
    """Regenerate, judge and record one change; the summary, or ChangeControlError."""
    note = regenerate(root) if regenerate_goldens else ""
    now = running(DirTree(root), lizard)
    plan = _planned(root, request, base, now, regenerate_goldens and not note)
    write_plan(root, plan, now)
    return "\n".join(filter(None, (note, summary(plan, now))))



def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _declare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="change_control.py declare",
                                     description="Judge and record a change that moves goldens.")
    parser.add_argument("id")
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--calcs", default="", help="the moved calcs, `;` or `,` separated")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--against-oracle", action="append", default=[], metavar="RULING")
    parser.add_argument("--base", default="HEAD", help="where the old goldens are (default HEAD)")
    parser.add_argument("--no-regenerate", action="store_true",
                        help="judge the goldens as they are; do not remeasure the corpora")
    parser.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    return parser


def _declare_main(argv: list[str]) -> int:
    args = _declare_parser().parse_args(argv)
    calcs = parse_calcs(args.calcs, known_calcs(DirTree(args.repo)))
    request = Request(args.id, args.kind, tuple(calcs), args.reason, tuple(args.against_oracle),
                      _today())
    print(declare(args.repo, request, args.base, not args.no_regenerate))
    return 0


# --- lock --initial and counts -------------------------------------------------------------

def _tests_env(root: Path, extra: dict) -> dict:
    paths = (str(root / "tests"), os.environ.get("PYTHONPATH"))
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(filter(None, paths)), **extra}


def packet_counts(node_ids: list[str]) -> dict[str, int]:
    """{packet: test functions}: a parametrized function counts once, whatever
    cases this machine gives it."""
    functions = {node.split("[", 1)[0] for node in node_ids}
    return dict(sorted(Counter(packet_of(node.split("::")[0]) for node in functions).items()))


def collect_counts(root: Path) -> dict[str, int]:
    """{packet: test functions} over tests/accuracy, every tier and platform."""
    argv = [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q",
            "-p", "no:randomly", "-p", "no:cacheprovider", "tests/accuracy"]
    out = _process("pytest --collect-only tests/accuracy", argv, GIT_SECONDS * 5, root,
                   _tests_env(root, {tiers.COLLECT_ALL_ENV: "1"}))
    return packet_counts([line.strip() for line in out.splitlines() if "::" in line])


def counts_bytes(counts: dict[str, int]) -> bytes:
    return table_bytes(COUNT_COLUMNS, sorted(counts.items()))


def lock_initial(root: Path, now: Running, today: str, counts: dict[str, int]) -> str:
    """The first lock: C1 (kind none), every lockable file under it, the first
    metric-digests row and the test counts."""
    tree = DirTree(root)
    if _initialized(tree):
        raise ChangeControlError(f"the lock is already initialized; declare each change with "
                                 f"`{TOOL} declare`")
    _append(root / CHANGES, CHANGE_COLUMNS, ["C1", today, "none", "", now.analysis, now.lizard, "",
                                             "the first lock over every golden and "
                                             "expected-value file"])
    files = lockable(tree)
    (root / LOCK).write_bytes(table_bytes(LOCK_COLUMNS, ((path, files[path], "C1")
                                                         for path in sorted(files))))
    _append(root / DIGESTS, DIGEST_COLUMNS, [now.analysis, now.lizard, corpus_digest(tree),
                                             metric_digest(tree), "C1"])
    (root / COUNTS).write_bytes(counts_bytes(counts))
    return f"locked {len(files)} files under C1; metric-digests and test counts written"


def _lock_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="change_control.py lock")
    parser.add_argument("--initial", action="store_true", required=True)
    parser.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    return parser


def _lock_main(argv: list[str]) -> int:
    args = _lock_parser().parse_args(argv)
    now = running(DirTree(args.repo))
    print(lock_initial(args.repo, now, _today(), collect_counts(args.repo)))
    return 0


def count_problems(committed: bytes | None, counts: dict[str, int]) -> list[Problem]:
    """T6: test-counts.tsv holds the collected test function counts."""
    table = {row["packet"]: int(row["tests"]) for row in rows(committed)}
    wrong = sorted(packet for packet in set(table) | set(counts)
                   if table.get(packet) != counts.get(packet))
    return [Problem("T6", f"packet {packet} collects {counts.get(packet, 0)} tests, "
                          f"{COUNTS} says {table.get(packet, 0)}", f"{TOOL} counts --write")
            for packet in wrong]


def _counts_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="change_control.py counts")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    return parser


def _counts_main(argv: list[str]) -> int:
    args = _counts_parser().parse_args(argv)
    counts = collect_counts(args.repo)
    if args.write:
        (args.repo / COUNTS).write_bytes(counts_bytes(counts))
    problems = count_problems(DirTree(args.repo).read(COUNTS), counts)
    print(report(problems, [], "test counts"))
    return 1 if problems else 0


# --- pre-push ----------------------------------------------------------------------------------

IN_TREE_TEST = f"{HOME}/test_change_control.py"


def _fetched(repo: Path, ref: str) -> bool:
    try:
        resolve(repo, ref)
    except ChangeControlError:
        return False
    return True


def remote_main(repo: Path, remote: str) -> str:
    """The pushed-to remote's main, else origin's."""
    for ref in (f"refs/remotes/{remote}/main", "refs/remotes/origin/main"):
        if _fetched(repo, ref):
            return ref
    raise ChangeControlError(f"neither {remote}/main nor origin/main is fetched; run "
                             f"`git fetch {remote}` first")


def pushed_heads(stdin: str) -> list[str]:
    """The local commits a push sends: one per stdin line that deletes nothing."""
    parts = [line.split() for line in stdin.splitlines()]
    return [fields[1] for fields in parts if len(fields) == 4 and fields[1] != ZERO]


def touched_tests(repo: Path, base: str) -> list[str]:
    """The independent tests of every calc whose module the branch changed, and
    the in-tree change-control rules when there is any."""
    start = merge_base(repo, base, "HEAD")
    changed = git(repo, "diff", "--name-only", "-z", start, "HEAD").decode("utf-8").split("\0")
    tests = {row.test for row in calcs_of(DirTree(repo)) if set(row.modules) & set(changed)}
    rules = {IN_TREE_TEST} if tests and (repo / IN_TREE_TEST).is_file() else set()
    return sorted(tests | rules)


def run_tests(repo: Path, node_ids: list[str]) -> int:
    if not node_ids:
        print("change control: the push changes no calc module; no accuracy check to run")
        return 0
    argv = [sys.executable, "-m", "pytest", *node_ids, "-q", "-p", "no:cacheprovider",
            "-p", "no:randomly"]
    return subprocess.run(argv, cwd=repo, env=_tests_env(repo, {tiers.TIER_ENV: "push"})).returncode


def pre_push(repo: Path, remote: str, stdin: str) -> int:
    """git-hooks/pre-push: judge each pushed commit against the remote's main,
    then run the accuracy checks of the calcs the checked-out branch touches."""
    base, heads = remote_main(repo, remote), pushed_heads(stdin)
    codes = []
    for head in heads:
        code, text = check(repo, base, head)
        print(text)
        codes.append(code)
    if resolve(repo, "HEAD") in heads:
        codes.append(run_tests(repo, touched_tests(repo, base)))
    return max(codes, default=0)


def _pre_push_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="change_control.py pre-push")
    parser.add_argument("remote")
    parser.add_argument("url", nargs="?")
    return parser


def _pre_push_main(argv: list[str]) -> int:
    args = _pre_push_parser().parse_args(argv)
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    return pre_push(repo, args.remote, sys.stdin.read())


COMMANDS = {"declare": _declare_main, "lock": _lock_main, "counts": _counts_main,
            "pre-push": _pre_push_main}


def _console() -> None:
    """A path a Windows code page cannot spell prints escaped instead of stopping
    the report with UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    _console()
    args = list(sys.argv[1:] if argv is None else argv)
    command = COMMANDS.get(args[0]) if args else None

    try:
        return command(args[1:]) if command else _check_main(args)
    except ChangeControlError as refused:
        print(f"change control: {refused}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
