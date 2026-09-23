"""Analysis shell: run lizard once over explicit file lists and read both ccn columns off it.

Lizard is always fed explicit files, never directories: its own walker descends
nested node_modules (measured hang on the first consumer repo).
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    from lizard_languages import get_reader_for as _lizard_reader_for
    from lizard_languages.python import PythonReader as _PythonReader

    from .lizardpowershell import register as _register_powershell
    from .lizardpython import register as _register_python
    from .lizardrust import register as _register_rust
    from .lizardshell import register as _register_shell
    from .lizardtypescript import LizardExtension as _TypeScriptExpressions
    from .lizardtypescript import mask_templates, reads_templates, uses_type_syntax

from .cache import partition_by_cache, updated_cache
from .errors import ToolError
from .lizardcognitive import LizardExtension as _Cognitive
from .merge import FunctionRecord, UnanalyzableFile
from .keys import bare_name

# lizard picks a reader by extension off a hardcoded list, and none of these is
# on it: `.rs` resolves to a reader that counts no `match` arm (lizard #494),
# `.py` to one that ends a def inside a signature that runs past its first `)`
# (crapkit #72), and `.sh` and `.ps1` resolve to nothing at all, which lizard
# answers with CLikeReader rather than a failure. All four belong HERE, at the
# module scope of the module a ProcessPoolExecutor child imports, or spawned
# workers measure with the readers lizard shipped and report plausible wrong
# numbers.
#
# lizardshell and lizardpowershell already register themselves on import, and
# lizardrust and lizardpython deliberately do not (rebinding a name in another
# package's namespace is not something an import should do quietly). Calling
# all four keeps the wiring readable in one place and costs nothing: each is
# idempotent.
_register_rust()
_register_shell()
_register_powershell()
_register_python()

_POOL_THRESHOLD = 16

# Bump whenever analysis semantics change (merge rules, extension set, record
# extraction): the fingerprint must invalidate cached records produced by older
# logic even when file content and tool versions are identical.
ANALYSIS_VERSION = 11  # A Python def is named by its name token and names each enclosing def once.
#                       A Python def whose body sits on its colon line is listed and ends
#                       with that logical line, so the lines after it go back to its
#                       parent and a later def no longer carries its name. A file that
#                       ends inside a def's signature is refused under either reader.
#                       The cognitive pass starts a Python body after the colon that
#                       ends the signature, not at the def's first newline, so a body
#                       on the colon line counts toward cognitive and nesting and a
#                       signature's continuation lines no longer do. Measured over
#                       5,746 stdlib, site-packages and application files: 19 of
#                       123,320 rows move cognitive, 2 of them nesting too.
#                       A record marks a def whose body starts on its colon's line
#                       (inline_body); the cache's own `cache=5` drops records
#                       read before the mark existed. A JavaScript-family
#                       template literal ends at its own closing backtick; the
#                       cache's `cache=6` drops records read before that.
# 10: separate sibling JavaScript/TypeScript expression arrows.
# 9: a Python row's nesting is the depth the cognitive
#                          pass measured, not lizard's ND count of structures,
#                          so every cached .py record carries a count under the
#                          depth's name (a flat seven-`if` function read 7).
#                          The pass also reads a Python token's owner after
#                          lizard has, so the first token of the line that
#                          dedents out of a function is no longer charged to
#                          it: `cognitive` moves too for an outer function
#                          resuming after a nested `def`, for a last function
#                          followed by a module-level `if __name__`, and for
#                          one called at import right after its `def` (read as
#                          recursion). Measured on crapkit's own tree: 6 of
#                          5,258 rows. Python is the only language whose
#                          stored values move; ccn is untouched everywhere.
#                       8: shell blocks nest. `fi`, `done` and `esac` close what
#                          `if`, a loop keyword or `case` opened, `do`/`then`/`in`
#                          are free, and a bare `break` is not a labeled one, so
#                          every cached .sh and .bash record carries a cognitive
#                          score measured flat. Shell is the only language whose
#                          stored values move; ccn is untouched everywhere,
#                          shell included.
#                       7: a Rust `match` is a cognitive condition (+1 and the
#                          nesting it sits in), so every cached .rs record
#                          carries a cognitive score measured without it. Rust
#                          is the only language whose stored values move; ccn is
#                          untouched in every language, Rust included.
#                       6: five more C-family extension sets and .ps1/.psm1 are
#                          admitted, a C++ rvalue reference is no longer a
#                          cognitive condition, and source bytes decode utf-8
#                          then cp1252 instead of by machine locale

# The three tokens lizard's modified rule reacts to. Membership is checked before
# anything else runs, so the common token pays one frozenset lookup.
_SWITCH_TOKENS = frozenset({"switch", "match", "case"})


def _switch_delta(token: str, reader) -> int:
    """+1 for a switch-like block opener, -1 for one of its arms.

    `match` and `case` are soft keywords in Python: the same spelling is an
    identifier elsewhere, so the reader's own flags decide, exactly as lizard's
    modified extension decides.
    """
    if token == "case":
        return -int("case" in reader.conditions or getattr(reader, "_keyword_case", False))
    if token == "switch":
        return 1
    return int(getattr(reader, "_keyword_match", False))


class _ModifiedDelta:
    """ccn_mod minus ccn_std, counted inside the standard pass.

    lizard's modified extension does one thing: add_condition(+1) on a
    switch/match opener and add_condition(-1) on each arm. It filters no tokens
    and reads nothing else, so the two columns differ by that sum and by nothing
    else, which made the second full tokenization of every file pure waste.
    """

    FUNCTION_INFO = {"modified_delta": {"caption": " Mod "}}

    def __call__(self, tokens, reader):
        context = reader.context
        for token in tokens:
            if token in _SWITCH_TOKENS:
                fn = context.current_function
                delta = _switch_delta(token, reader)
                fn.modified_delta = getattr(fn, "modified_delta", 0) + delta
            yield token


class _CreationOrder:
    """Keep declaration order when nested functions finish before their parents."""

    def __call__(self, tokens, reader):
        context = reader.context
        original = context.try_new_function
        sequence = 0

        def create(name):
            nonlocal sequence
            original(name)
            sequence += 1
            context.current_function.crapkit_creation = sequence

        context.try_new_function = create
        try:
            yield from tokens
        finally:
            context.try_new_function = original


# --- a Python def no reader finished (#72) -----------------------------------------
#
# lizard 1.24.0's PythonReader ended a def inside its own signature when the
# signature ran past its first `)`: a return annotation opened on the def line,
# or a line break after a default such as `()`. The def read as two lines at ccn
# 1 whatever its body held and passed every gate on that reading.
# crapkit.lizardpython reads those signatures to the body's colon. Measured on
# 6,866 stdlib, site-packages and consumer files: all 59 defs lizard cut off read
# their full span, and of the defs lizard read whole only the 20 nested inside a
# cut-off def changed, each gaining its parent's name as a prefix (one of them
# also scores 1 lower on cognitive).
#
# This check stays as the net under that reader. A def read to its body has a
# `:` at bracket depth 0 with a token after it; one cut off in its signature has
# not. A def the reader still cannot finish is refused, never scored at ccn 1.
#
# The net counts from the `def` keyword, a token no reader renames, so where it
# starts does not depend on how the reader names the def. It has to hold under
# lizard's stock reader too, which is what runs once crapkit.lizardpython
# retires: that reader names a PEP 695 def after the `]` or `:` before its `(`,
# so a count started at the function's first token began at depth -1 or on a
# colon.
#
# A file that ends inside a signature leaves that def pending, and lizard lists
# a pending def only when an enclosing def is popped at the end of the file. A
# nested one was refused that way; a top-level one, or a method, was never
# listed, and the file scored as if the def were not there. The net lists the
# def whose signature is still open when the tokens run out.
_DEPTH_CHANGE = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}


class _DefSignatures:
    """One file's walk from each `def` keyword to its body colon.

    `depth` is None between signatures and counts the brackets open since the
    `def` inside one. The function current at the depth-0 colon has reached
    its body only if it also owns the next token: one the reader cut off has
    ended by then, and the token belongs to its parent.

    `crapkit_body` turns False on a function first seen inside a signature
    and True once its body is reached. A function already read to its body
    keeps True when the rest of a cut-off child's signature is charged to it.
    A function some other reader produced never gets the attribute, which is
    what keeps `_unread_defs` to Python.

    `crapkit_inline_body` is True on a def whose first body token sits on the
    colon's line, or behind a backslash that joins the colon's line to the
    next: coverage.py reads such a body as the `def` statement itself
    (FunctionRecord.inline_body).

    `signature_owner` is the last def current at a signature token. A file
    that ends before that signature's colon leaves its def pending, and lizard
    lists a pending def only when an enclosing def is popped at the end of the
    file. `finish` lists it, so the net names it either way. The stock reader
    charges the rest of a top-level def's cut-off signature to the file's
    global pseudo function, which is no def and never becomes the owner: the
    def it cut off is listed already, and the net names that one.
    """

    def __init__(self, context):
        self.context = context
        self.depth = None
        self.colon_owner = None
        self.colon_line = 0
        self.signature_owner = None

    def step(self, token: str) -> None:
        fn = self.context.current_function
        if self.colon_owner is not None:
            self._settle_colon(fn, token)
        if self.depth is not None:
            self._signature(fn, token)
        elif token == "def":
            self.depth, self.signature_owner = 0, None

    def finish(self) -> None:
        """List the def the file ended inside of its signature, if lizard did not."""
        listed = self.context.fileinfo.function_list
        if self.depth is not None and self.signature_owner is not None and self.signature_owner not in listed:
            listed.append(self.signature_owner)

    def _settle_colon(self, fn, token: str) -> None:
        if fn is self.colon_owner:
            fn.crapkit_body = True
            fn.crapkit_inline_body = token == "\\\n" or self.context.current_line == self.colon_line
        self.colon_owner = None

    def _signature(self, fn, token: str) -> None:
        # A parent the stock reader hands the rest of a cut-off signature to
        # was read to its body already, and keeps that.
        fn.crapkit_body = getattr(fn, "crapkit_body", False)
        if fn is not self.context.global_pseudo_function:
            self.signature_owner = fn
        if token == ":" and self.depth == 0:
            self.colon_owner, self.depth = fn, None
            self.colon_line = self.context.current_line
        else:
            self.depth += _DEPTH_CHANGE.get(token, 0)


class _PythonBodies:
    """Mark every Python function whose body lizard reached.

    Reads a token's owner after lizard has, the way the cognitive pass does:
    the name token is what creates the function, and the line that ends one
    was charged to its parent by `preprocess`, upstream of here, before the
    token arrived. Sits behind `line_counter`, so no whitespace or newline
    token reaches it and any token after the body colon is body. The cognitive
    pass starts a body at that same token but keeps its own count
    (lizardcognitive._signature_token): it sits ahead of `preprocess`, whose
    soft-keyword lookahead hands it the tokens of a `match` line before they
    reach here.
    """

    def __call__(self, tokens, reader):
        if not isinstance(reader, _PythonReader):
            yield from tokens
            return
        signatures = _DefSignatures(reader.context)
        for token in tokens:
            yield token
            signatures.step(token)
        signatures.finish()  # after lizard's own end-of-file pops, which run upstream


def _chain(cognitive_index: int) -> list:
    """lizard's standard extensions with cognitive spliced in at one index.

    Index 0 puts cognitive ahead of lizard's own `preprocessing`, which is where
    it has to sit for Python: `preprocessing` strips the whitespace tokens the
    python indent rules read, and behind it a 6-branch function scores 6 instead
    of 10. The delta comes last either way, where the modified pass used to sit.
    """
    extensions = lizard.get_extensions(["ND"])
    extensions.insert(cognitive_index, _Cognitive())
    return [_TypeScriptExpressions(), *extensions, _ModifiedDelta(), _PythonBodies(), _CreationOrder()]


# Two chains, built once per process each, not once per file: 14k files paid 14k
# chain builds. Every extension keeps its state in the generator frame __call__
# opens, so one chain serves every file of its kind.
_EXTENSIONS = _chain(0)
_PREPROCESSED_EXTENSIONS = _chain(1)

# lizard's SwiftReplaceLabel.preprocess RETURNS a list where the other seven
# preprocessors YIELD: it runs list() over its input, so every extension AHEAD of
# `preprocessing` is drained to exhaustion before lizard has split the file into
# functions. An extension at index 0 counts the whole file against one
# placeholder FunctionInfo, and every real function comes out at 0. SwiftReader
# and KotlinReader are the only two of lizard's 27 readers that inherit that
# preprocessor, so only these suffixes take the second chain.
_DRAINED_READER_SUFFIXES = (".swift", ".kt", ".kts")


def _extensions_for(rel_path: str) -> list:
    if rel_path.lower().endswith(_DRAINED_READER_SUFFIXES):
        return _PREPROCESSED_EXTENSIONS
    return _EXTENSIONS


# The suffix lizard routes to PythonReader (`PythonReader.ext`), and the one
# language whose `nesting` is not lizard's. lizard's ND extension counts
# nesting STRUCTURES for Python rather than depth: a flat function of seven
# `if`s read 7 and a three-deep one read 3, the same number for opposite
# shapes. The cognitive pass keeps a per-function stack of open blocks for the
# Sonar nesting increment, and the deepest it gets is the depth. Brace
# languages keep lizard's column, which reads their braces.
_PYTHON_SUFFIXES = (".py",)


def _nesting_depth(rel_path: str, fn) -> int:
    if rel_path.lower().endswith(_PYTHON_SUFFIXES):
        return getattr(fn, "cognitive_nesting", 0) or 0
    return getattr(fn, "max_nesting_depth", 0) or 0


def _record(rel_path: str, fn, occurrence: int = 0) -> FunctionRecord:
    std = fn.cyclomatic_complexity
    mod = std + (getattr(fn, "modified_delta", 0) or 0)
    return FunctionRecord(
        path=rel_path,
        long_name=fn.long_name,
        start=fn.start_line,
        end=fn.end_line,
        ccn_std=std,
        ccn_mod=mod,
        ccn=min(std, mod),
        nloc=fn.nloc,
        params=len(fn.parameters),
        nesting=_nesting_depth(rel_path, fn),
        cognitive=getattr(fn, "cognitive_complexity", 0) or 0,
        occurrence=occurrence,
        inline_body=int(getattr(fn, "crapkit_inline_body", False)),
    )


# How many colliding names one warning prints before it stops. A generated file
# can hold hundreds; the point is that the file needs looking at, not the list.
_NAMES_SHOWN = 5


def _colliding_names(records: list[FunctionRecord]) -> list[str]:
    """Names this file gives to more than one function, in first-seen order.

    Anonymous functions are exempt from the line. lizard calls every one of them
    `(anonymous)`, so a file with two arrow callbacks collides by construction
    and the line would name nothing anyone could act on. They take the same
    ordinal keys as any other twin; `keys.handles` already addresses them as
    `(anonymous)#N`.
    """
    seen: set[str] = set()
    colliding: dict[str, None] = {}
    for record in records:
        if bare_name(record.long_name) and record.long_name in seen:
            colliding[record.long_name] = None
        seen.add(record.long_name)
    return list(colliding)


def _note_twin_keys(rel_path: str, records: list[FunctionRecord]) -> None:
    """One stderr line for a file that gives one name to more than one function.

    Information, not a warning. Until 0.4.2 it was the second: a mark keyed on
    (path, long_name) meant one twin owned the key and the rest were neither
    marked nor gated, which is the loss this announced. `keys` ends that by
    giving each twin its own ordinal, so the line now says what a reader will
    see in `crapkit-ratchet.tsv` and nothing is lost.

    Still printed, because a `#2` appearing in a committed marks file is
    otherwise unexplained. C makes the shape ordinary — both arms of an `#ifdef`
    fork are textually present — and so does Python, whose method long_names
    carry no class.

    Printed by the parent process only. A pool worker's stderr is its own,
    unreconfigured stream (#31), so workers return records and stay silent.
    """
    names = _colliding_names(records)
    if not names:
        return
    print(f"crapkit: {rel_path} defines {_listed(names)} more than once; each one takes "
          f"its own ratchet key — the first as written, later ones suffixed #2, #3 in "
          f"file order", file=sys.stderr)


def _listed(names: list[str]) -> str:
    if len(names) <= _NAMES_SHOWN:
        return ", ".join(names)
    return f"{', '.join(names[:_NAMES_SHOWN])} and {len(names) - _NAMES_SHOWN} more name(s)"


def _file_records(rel_path: str, functions) -> list[FunctionRecord]:
    """Pure, and silent: this runs inside pool workers, whose stderr is not the
    parent's. The twin-key note is the caller's to print."""
    counts, occurrences = {}, {}
    for fn in sorted(functions, key=lambda fn: fn.crapkit_creation):
        counts[fn.start_line] = counts.get(fn.start_line, 0) + 1
        occurrences[id(fn)] = counts[fn.start_line]
    return [_record(rel_path, fn, occurrences[id(fn)]) for fn in functions]


def _unread_defs(functions) -> list:
    """The Python functions `_PythonBodies` never saw a body token for."""
    return [fn for fn in functions if getattr(fn, "crapkit_body", True) is False]


def _unread_reason(rel_path: str, unread: list) -> str:
    named = ", ".join(f"{rel_path}:{fn.start_line} {fn.long_name}" for fn in unread)
    return (f"{rel_path}: the Python reader reached no body for {len(unread)} def(s): {named}; "
            f"a def read no further than its signature would score ccn 1 whatever its body "
            f"holds, so the file is not scored. Check that the file parses (a signature cut "
            f"off at the end of the file reads this way); if it does, report the signature at "
            f"https://github.com/JeanFrancoisGagne/crapkit/issues")


def _trusted_records(rel_path: str, functions) -> list[FunctionRecord]:
    """Records for a file every function of which was read to its body.

    A file with a def the reader never finished takes the unanalyzable road,
    named on every run and scored as zero functions, not a ccn-1 reading of
    that def: scoring it would pass the gate on a number that means nothing,
    and ending the run over one file is what 0.7.1 stopped (_note_unanalyzable).
    Every such def in the file is named at once.
    """
    unread = _unread_defs(functions)
    if unread:
        return UnanalyzableFile(_unread_reason(rel_path, unread))
    return _file_records(rel_path, functions)


# --- how a source file's bytes become text -------------------------------------
#
# lizard opens a source file with `io.open(path, 'r')` and no encoding, so the
# MACHINE'S LOCALE decides what a repo scores. Measured on one function,
# `function Write-Café`, written once as UTF-8 and once as cp1252, read on a
# cp1252 interpreter and on a UTF-8 one:
#
#     source     cp1252 reader        utf-8 reader
#     utf-8      no function at all   Write-Café
#     cp1252     Write-Café           Write-Caf
#
# The empty cell is not a rounding error: `é` arrives as `Ã` plus `©`, the `©`
# is no word character, and the declaration stops being one. The ratchet keys on
# path::long_name, so those are three different rows for one commit, and a
# Windows developer and a Linux CI cannot see each other's baseline.
#
# utf-8 first, cp1252 second, replacement for the five bytes cp1252 leaves
# undefined. That is the whole rule and it is fixed rather than environmental:
# all four cells above read `Write-Café`. The fallback is cp1252 rather than
# latin-1 because Windows PowerShell 5.1 writes cp1252, and because latin-1
# decodes every byte and so can never say it was wrong.
#
# UTF-16 is NOT handled. `Out-File` and the ISE write it, and such a file
# decodes here as NUL-separated cp1252 text that reports no function; it
# reported none before this change either, so nothing regressed and the narrow
# rule stays narrow.


def _characters(raw: bytes) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def decode_source(raw: bytes) -> str:
    """Source bytes as text, decoded by content rather than by machine locale.

    The single rule for every path into lizard: a file on disk, and a staged
    blob the pre-commit gate never writes down. Those two must agree or the
    gate judges different content than the inventory scores.

    Line endings are normalized the way `io.open(path, 'r')` normalized them,
    because that is what lizard did and every recorded line number and NLOC in
    every cache depends on it: a lone `\\r` left in the stream is one more
    whitespace token, not one more line.
    """
    return _characters(raw).replace("\r\n", "\n").replace("\r", "\n")


def read_source(path: str) -> str:
    return decode_source(Path(path).read_bytes())


def _install_decoder() -> None:
    """Point lizard's own file read at `read_source`. Idempotent.

    A rebind rather than a replacement for `FileAnalyzer.__call__`, so lizard
    keeps the IOError branch that answers a file deleted mid-run with an empty
    result instead of a traceback, and keeps reading each file exactly once.
    `lizard.py` binds `auto_read` into its own module namespace at import, and
    `FileAnalyzer.__call__` resolves it there on every call.

    Raises when that name is gone, which is what a lizard release that reads
    source some other way would look like. Loud beats an attribute nobody
    reads and a decode that silently went back to the locale's.
    """
    if not hasattr(lizard, "auto_read"):
        raise RuntimeError(
            f"crapkit.analyze._install_decoder() found no lizard.auto_read to "
            f"rebind: lizard {lizard.version} reads source some other way. "
            f"Rewrite this against the new mechanism; leaving it undone makes "
            f"every non-ASCII file score by the machine's locale.")
    lizard.auto_read = read_source


def _reader_for(path: str):
    """Lizard's extension regex cannot cross LF; only its selector needs an alias.

    File reads, parser context and returned records retain the original path.
    The reader class also owns cache identity, so prior fallback rows miss.
    """
    return _lizard_reader_for(path.replace("\n", "\ufffd"))


_install_decoder()
lizard.get_reader_for = _reader_for


class _Analyzer(lizard.FileAnalyzer):
    """lizard's FileAnalyzer, handing a JavaScript-family reader its source with
    the template-literal characters its tokenizer misreads blanked.

    Every path into lizard comes through here: a file on disk
    (`FileAnalyzer.__call__` reads it and calls this method), a staged blob and
    a verified worker input.
    """

    def analyze_source_code(self, filename, code):
        if reads_templates(lizard.get_reader_for(filename)):
            code = mask_templates(code)
        return super().analyze_source_code(filename, code)


def analyze_one(args: tuple[str, str]) -> tuple[str, list[FunctionRecord]]:
    abs_path, rel_path = args
    try:
        analysis = _Analyzer(_extensions_for(rel_path))(abs_path)
        return rel_path, _trusted_records(rel_path, analysis.function_list)
    except Exception as exc:  # loud and counted, never fatal: see _note_unanalyzable
        return rel_path, UnanalyzableFile(f"lizard failed on {rel_path}: {exc}")


def analyze_source(rel_path: str, code: str, *, note: bool = True) -> list[FunctionRecord]:
    """The records analyze_one would produce for a file holding `code`.

    analyze_source_code is what FileAnalyzer.__call__ runs once it has read the
    file, so nothing about the analysis depends on whether the source arrived
    from the disk or from a git blob the caller already holds; rel_path picks
    the language, and with it the extension chain, exactly as the path on disk did.
    NOTE=False leaves an unreadable file unannounced, for a caller that reads a
    half-typed edit on purpose.
    """
    try:
        analyzer = _Analyzer(_extensions_for(rel_path))
        analysis = analyzer.analyze_source_code(rel_path, code)
        records = _trusted_records(rel_path, analysis.function_list)
    except Exception as exc:  # per-file, exactly as in analyze_one; the hook keeps going
        records = UnanalyzableFile(f"lizard failed on {rel_path}: {exc}")
    if isinstance(records, UnanalyzableFile):
        if note:
            _note_unanalyzable({rel_path: records})
        return records
    _note_twin_keys(rel_path, records)
    return records


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint() -> str:
    """cache=6: a JavaScript-family template literal ends at its own closing backtick,
    which a cache=5 record's reader did not do; cache=5 added inline_body."""
    from . import __version__
    return f"crapkit={__version__};analysis={ANALYSIS_VERSION};lizard={lizard.version};cache=6"


def _analysis_key(path: str, digest: str) -> str:
    """Bytes are reusable only under the same reader and extension chain."""
    reader = lizard.get_reader_for(path) or lizard.get_reader_for("fallback.c")
    chain = int(_extensions_for(path) is _PREPROCESSED_EXTENSIONS)
    return f"{reader.__module__}.{reader.__qualname__}:{chain}:{int(uses_type_syntax(path))}:{digest}"


_CACHED_TYPES = (str, str) + (int,) * (len(FunctionRecord._fields) - 2)


def _cached_record(values) -> FunctionRecord:
    if not isinstance(values, list) or len(values) != len(_CACHED_TYPES):
        raise ValueError("cached function fields must be a record list")
    if any(type(value) is not expected for value, expected in zip(values, _CACHED_TYPES)):
        raise ValueError("cached function fields have invalid types")
    return _in_range(FunctionRecord(*values))


def _in_range(record: FunctionRecord) -> FunctionRecord:
    if record.occurrence < 0:
        raise ValueError("cached function occurrence must be nonnegative")
    if record.inline_body not in (0, 1):
        raise ValueError("cached function inline_body must be 0 or 1")
    return record


def _cached_rows(rows) -> list[FunctionRecord]:
    if not isinstance(rows, list):
        raise ValueError("cached functions must be a list")
    return [_cached_record(values) for values in rows]


def _drained_records(entries: dict) -> dict[str, list[FunctionRecord]]:
    """Turn parsed rows into records, freeing each row list as it is consumed.

    A comprehension over `entries` would hold the whole parsed list-of-lists
    alongside the records built from it; on a large corpus that doubling is tens
    of MB for no reason. Draining leaves the caller's parsed dict empty, which is
    fine: nothing reads it afterwards.
    """
    if not isinstance(entries, dict):
        raise ValueError("cached entries must be an object")
    records: dict[str, list[FunctionRecord]] = {}
    while entries:
        h, rows = entries.popitem()
        records[h] = _cached_rows(rows)
    return records


def load_cache(path: Path) -> dict:
    """A cache is disposable: corrupt or truncated content reads as cold, never as a crash.

    save_cache is not atomic, so a killed process can leave a torn file; the
    cache keys on raw bytes, so cross-machine line-ending settings just miss.
    """
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return {"fp": raw.get("fp"), "entries": _drained_records(raw.get("entries", {}))}
    except (OSError, TypeError, ValueError, AttributeError):
        return {}


def save_cache(path: Path, cache: dict, *, prior: dict | None = None) -> None:
    """Rewrite the cache file, unless `prior` says the bytes would not move.

    Callers hand back what load_cache gave them. A fully-warm run rebuilds an
    entry map equal to the one already on disk, and serializing it is the most
    expensive thing such a run does; equal maps serialize identically because
    the dump sorts its keys.
    """
    if cache == prior:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        _write_entries(fh, cache["entries"])
        fh.write(f", {json.dumps('fp')}: {json.dumps(cache['fp'])}}}")


def _write_entries(fh, entries: dict) -> None:
    """The `entries` object, one entry serialized at a time.

    Byte-for-byte what json.dumps(document, sort_keys=True) wrote: keys sorted,
    the two-character separators json uses by default, and `entries` ahead of
    `fp` because sorted keys put it there. Building the document first meant the
    rebuilt lists, the whole 18.6 MB string and its encoding were all live at
    once; this way one entry is.
    """
    fh.write('{"entries": {')
    lead = ""
    for h in sorted(entries):
        fh.write(f"{lead}{json.dumps(h)}: {json.dumps([list(r) for r in entries[h]])}")
        lead = ", "
    fh.write("}")


_STAMPS_NAME = "stat-stamps.json"

# A stamp is only recorded once the file has held still this long. Windows moves
# a file time on a ~15 ms clock tick, so two writes inside one tick can land on
# one mtime; a file written moments ago is not evidence that it is unchanged.
_STAMP_SETTLE_NS = 2_000_000_000


def _stamps_path(root: Path) -> Path:
    return root / ".crapkit" / _STAMPS_NAME


def _load_stamps(path: Path) -> dict:
    """rel path -> [mtime_ns, size, content hash], or nothing at all.

    An index of what the last run saw. Disposable exactly like the analysis
    cache: unreadable, torn, or written by another format reads as no index,
    which costs a full hashing pass and never a wrong answer.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return _stamp_entries(raw["stamps"]) if raw.get("v") == 1 else {}
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError, AttributeError):
        return {}


def _valid_stamp(stamp) -> bool:
    if not isinstance(stamp, list) or len(stamp) != 3:
        return False
    return (type(stamp[0]) is int and type(stamp[1]) is int
            and isinstance(stamp[2], str))


def _stamp_entries(stamps) -> dict:
    if not isinstance(stamps, dict):
        return {}
    return {path: stamp for path, stamp in stamps.items()
            if isinstance(path, str) and _valid_stamp(stamp)}


def _save_stamps(path: Path, stamps: dict, prior: dict) -> None:
    if stamps == prior:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "stamps": stamps}, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # an index nobody can write is a slower run, never a failed one


def _stat_of(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _unmoved(stat: tuple[int, int] | None, prior) -> bool:
    """True when this file's stat is exactly what it was when we hashed it."""
    return bool(prior) and stat is not None and stat[0] == prior[0] and stat[1] == prior[1]


def _stamp(fresh: dict, rel: str, stat: tuple[int, int] | None, digest: str, now: int) -> None:
    if stat is not None and now - stat[0] >= _STAMP_SETTLE_NS:
        fresh[rel] = [stat[0], stat[1], digest]


def _path_hash(path: Path, prior) -> tuple[str, tuple[int, int] | None]:
    stat = _stat_of(path)
    if _unmoved(stat, prior):
        return prior[2], stat
    digest = content_hash(path)
    return digest, stat if stat == _stat_of(path) else None


def _hash_paths(root: Path, rel_paths: list[str], stamps: dict) -> tuple[dict[str, str], dict]:
    """Content hash per path, plus the stat index the next run should keep.

    The cache identity includes the content hash; (mtime_ns, size) only decides
    whether that hash has to be recomputed. A file that has not moved
    since the run that hashed it keeps that hash without being opened, which is
    the difference between reading 14k files and stat-ing them.

    Its one blind spot: content rewritten to the same length under a deliberately
    restored mtime. Any real write moves the mtime, and a write too close to the
    last one is refused a stamp, so the next genuine change corrects it.
    """
    now = time.time_ns()
    hashes: dict[str, str] = {}
    fresh: dict = {}
    for rel in rel_paths:
        hashes[rel], stat = _path_hash(root / rel, stamps.get(rel))
        _stamp(fresh, rel, stat, hashes[rel], now)
    return hashes, fresh


def _kept_stamps(prior: dict, fresh: dict, visited: set) -> dict:
    """Paths this run looked at are described by this run alone: a file too
    recently written to stamp must not keep an older run's stamp. Paths it never
    looked at keep theirs, or a rescore of four files would blind the next
    inventory of fourteen thousand."""
    kept = {rel: stamp for rel, stamp in prior.items() if rel not in visited}
    kept.update(fresh)
    return kept


def _restamped(hits: dict[str, list[FunctionRecord]]) -> dict[str, list[FunctionRecord]]:
    # An entry keys on reader and content; re-stamp the path so a moved file cannot
    # carry its old location into the snapshot. Almost nothing moves between two
    # runs, and rebuilding 140k namedtuples to write back the path they already
    # hold is the most expensive thing a fully-warm run does.
    return {path: _rows_for(path, rows) for path, rows in hits.items()}


def _rows_for(path: str, rows: list[FunctionRecord]) -> list[FunctionRecord]:
    """One entry's rows all carry one path, because analyze_one stamps every
    record it emits with the single path it was handed. The first row answers
    for all of them.

    An empty list comes back as it went in: there is no path to re-key, and
    rebuilding it drops the UnanalyzableFile a refusal travels in, which is what
    keeps that file out of the cache and names it again on the next run.
    """
    if not rows or rows[0].path == path:
        return rows
    return [r._replace(path=path) for r in rows]


def _analyze_verified(job: tuple[str, str, str]) -> tuple[str, list[FunctionRecord]]:
    """Read one worker-owned input, verify its identity, then parse those bytes."""
    absolute, relative, expected = job
    try:
        raw = Path(absolute).read_bytes()
    except OSError as exc:
        raise ToolError(f"{relative}: source cannot be read; rerun analysis: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ToolError(f"{relative}: source changed during analysis; rerun analysis")
    try:
        analyzer = _Analyzer(_extensions_for(relative))
        analysis = analyzer.analyze_source_code(relative, decode_source(raw))
        return relative, _trusted_records(relative, analysis.function_list)
    except Exception as exc:  # a parse refusal, unlike the read and hash above, is per-file
        return relative, UnanalyzableFile(f"lizard failed on {relative}: {exc}")


def _job_inputs(jobs: list, hashes: dict[str, str] | None):
    if hashes is None:
        return analyze_one, jobs
    return _analyze_verified, [(absolute, relative, hashes[relative]) for absolute, relative in jobs]


def analyze_jobs(
    jobs: list[tuple[str, str]],
    *,
    workers: int | None = None,
    pool_threshold: int = _POOL_THRESHOLD,
    chunksize: int = 32,
    hashes: dict[str, str] | None = None,
    worker_budget: int = 0,
) -> dict[str, list[FunctionRecord]]:
    """Run lizard over (abs_path, rel_path) jobs, pooled once there are enough.

    The two knobs exist because the inventory and the pre-commit hook sit at
    different scales: an inventory feeds thousands of files and wants fat
    chunks, a hook feeds a commit's worth and needs each job dealt to a
    different worker (a chunksize above the job count leaves one worker doing
    all of them, serially, after paying for the pool).
    """
    worker, inputs = _job_inputs(jobs, hashes)
    with _pool_for(jobs, pool_threshold, workers, worker_budget, chunksize) as pool:
        rows = pool.map(worker, inputs, chunksize=chunksize) if pool else map(worker, inputs)
        fresh = dict(rows)
    # The parent says things; a worker only measures. A spawned child's stderr
    # never saw `_reconfigure_streams`, so a note printed from analyze_one
    # reached a UTF-8 reader in the legacy codepage on Windows (#31).
    for rel_path, records in fresh.items():
        _note_twin_keys(rel_path, records)
    _note_unanalyzable(fresh)
    return fresh


_UNANALYZABLE_NAMED = 5


def _note_unanalyzable(fresh: dict[str, list[FunctionRecord]]) -> None:
    """Name the files no reader could tokenize, and let the run continue.

    Through 0.7.0 the first refusal in a corpus raised, so one ambiguous arrow
    among 21,327 files failed `coverage`, which left the ratchet unseeded, which
    refused every commit in the repo in every language. Refusing an ambiguous
    arrow is specified, tested behaviour; ending the run over it was not. The
    file is now scored as zero functions, which is what an unreadable file
    honestly holds, and stays uncached so every run names it again.
    """
    refused = [(path, rows.reason) for path, rows in sorted(fresh.items())
               if isinstance(rows, UnanalyzableFile)]
    if not refused:
        return
    print(f"crapkit: {len(refused)} file(s) could not be tokenized; "
          f"each is scored as zero functions and stays unranked:", file=sys.stderr)
    for path, reason in refused[:_UNANALYZABLE_NAMED]:
        print(f"crapkit:   {reason}", file=sys.stderr)
    if len(refused) > _UNANALYZABLE_NAMED:
        print(f"crapkit:   ... and {len(refused) - _UNANALYZABLE_NAMED} more", file=sys.stderr)


def _pool_for(jobs: list, threshold: int, workers, worker_budget: int, chunksize: int):
    if len(jobs) < threshold or workers == 1:
        return nullcontext(None)
    if chunksize < 1:
        raise ValueError("chunksize must be >=1.")
    chunks = (len(jobs) + chunksize - 1) // chunksize
    if chunks <= 1:
        return nullcontext(None)
    requested = _requested_workers(workers, chunks, jobs)
    if requested == 1:
        return nullcontext(None)
    from ._analysis_pool import analysis_pool
    return analysis_pool(workers=requested, worker_budget=worker_budget)


def _requested_workers(workers, chunks: int, jobs: list) -> int:
    if workers:
        return min(workers, chunks)
    from .resources import DEFAULT_SOURCE_BYTES_PER_WORKER, default_chunks_per_worker
    quantum = default_chunks_per_worker()
    requested = (chunks + quantum - 1) // quantum
    if quantum > 1:
        work = _source_bytes(jobs)
        requested = max(requested, (work + DEFAULT_SOURCE_BYTES_PER_WORKER - 1) // DEFAULT_SOURCE_BYTES_PER_WORKER)
    return max(1, min(requested, chunks))


def _source_bytes(jobs: list) -> int:
    return sum(_job_size(path) for path, _ in jobs)


def _job_size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0  # The reader retains responsibility for its exact path refusal.


def _miss_origins(misses: list[str], identities: dict[str, str]) -> dict[str, str]:
    """Each cold path's first equivalent reader/content input, in path order."""
    origins: dict[str, str] = {}
    return {path: origins.setdefault(identities[path], path) for path in misses}


def _analyze_misses(root: Path, misses: list[str], identities: dict, hashes: dict,
                    workers, worker_budget: int) -> dict:
    origins = _miss_origins(misses, identities)
    jobs = [(str(root / path), path) for path in dict.fromkeys(origins.values())]
    parsed = analyze_jobs(jobs, workers=workers, hashes=hashes, worker_budget=worker_budget)
    records = {path: _rows_for(path, parsed[origin]) for path, origin in origins.items()}
    for path, origin in origins.items():
        if path != origin:
            _note_twin_keys(path, records[path])
    return records


def analyze_files(
    root: Path, rel_paths: list[str], *, cache: dict, workers: int | None = None,
    worker_budget: int = 0,
) -> tuple[dict[str, list[FunctionRecord]], int, dict]:
    fp = fingerprint()
    stamps_path = _stamps_path(root)
    prior_stamps = _load_stamps(stamps_path)
    hashes, fresh_stamps = _hash_paths(root, rel_paths, prior_stamps)
    kept = _kept_stamps(prior_stamps, fresh_stamps, set(rel_paths))
    _save_stamps(stamps_path, kept, prior_stamps)
    identities = {rel: _analysis_key(rel, digest) for rel, digest in hashes.items()}
    hits, misses = partition_by_cache(identities, cache, fingerprint=fp)
    hits = _restamped(hits)

    fresh = _analyze_misses(root, misses, identities, hashes, workers, worker_budget)

    all_records = {**hits, **fresh}
    new_cache = updated_cache(identities, all_records, fingerprint=fp)
    return all_records, len(hits), new_cache
