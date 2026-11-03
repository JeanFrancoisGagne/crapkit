"""The committed ratchet: per-function high-water CRAP marks for functions above target.

Pure text <-> entries. Marks only ever tighten: an improvement lowers or drops
an entry on update; a regression NEVER raises one (it surfaces as a verdict
failure instead); brand-new debt enters only through the audited override.

Identity is (path, key name): spans drift with every edit, names survive. A key
name is the function's long_name, plus a `#N` ordinal when the file gives that
name to more than one function — see `keys`. The first twin keeps the bare name,
so a marks file written before the ordinal reads unchanged.

The file opens with a metric stamp naming the rules that produced the numbers.
Marks measured under different rules are not comparable, and without the stamp
that reads as a clean run. It has two forms: the single-number line 0.8.x wrote,
`crapkit-analysis=N lizard=X.Y.Z`, and the per-scope form, a first line naming
the file and the release that reads it, then one `#scope` line per scope.
`MetricStamp` is the stamp's one parsed form: `read_stamp`, `run_stamp` and
`running_stamp` hand it out, and every caller compares and renders that value
instead of reading the lines itself. crapkit writes only the per-scope form; a
single-number stamp comes only from parsing a 0.8.x file or run.
"""
from __future__ import annotations

import json
import math
import operator
import re
from typing import ClassVar, NamedTuple

from .invariants import check_dump, check_marks_kept
from .invocation import _self
from .keys import Key, MarkIndex, mark_key, pair_moves, pair_path_moves, rows_by_key, split_ordinal
from .score import ScoredRow, over_ceiling
from .records import decode_record, encode_record, record_lines

_HEADER = "path\tlong_name\tcrap"
KEY_VERSION = 1
_KEY_STAMP = "# crapkit-keys="


class RatchetEntry(NamedTuple):
    path: str
    long_name: str
    crap: float


class TightenRefusal(NamedTuple):
    """One mark a bouncing measurement was not allowed to pull down."""
    path: str
    long_name: str
    previous: float
    fresh: float


# The single-number line 0.8.x wrote. Only the parsers read it, and a parsed
# stamp renders it back as the line it read.
_SINGLE_LINE = "crapkit-analysis={} lizard={}"
_SINGLE_NUMBER = re.compile(r"crapkit-analysis=([0-9]+) lizard=(\S+)")
_ANALYSIS = re.compile(r"crapkit-analysis=([0-9]+)(?= |$)")


class MetricStamp(str):
    """The metric a marks file or a stored run was measured under, parsed once.

    Three members: `SingleNumberStamp`, one analysis number and the lizard
    version; `UnreadableStamp`, a stamp line crapkit cannot parse, kept as it
    was so a refusal can quote it and a write that adds no number writes it
    back; and `MetricStamp.NONE`, a file or run that recorded none, which is
    falsy. A stamp is a str of the text after the `# ` that opens its line, so
    it prints and serializes as that text, and a caller that still holds the
    text compares equal to it. Two stamps are equal when they render the same
    line: for two `SingleNumberStamp`s, the same analysis number and the same
    lizard version.
    """
    __slots__ = ()
    NONE: ClassVar[MetricStamp]

    @classmethod
    def parse(cls, text: str) -> MetricStamp:
        """`text` as the stamp it spells. A line that would not render back as
        itself is unreadable, so a write never respells a committed stamp."""
        if not text:
            return cls.NONE
        found = _SINGLE_NUMBER.fullmatch(text)
        stamp = SingleNumberStamp(int(found[1]), found[2]) if found else None
        return stamp if stamp == text else UnreadableStamp(text)

    @property
    def analysis(self) -> int | None:
        """The number after the `crapkit-analysis=` that opens the line, with or
        without a lizard field after it; None when the line opens with none."""
        found = _ANALYSIS.match(self)
        return int(found[1]) if found else None

    def render(self) -> str:
        """The stamp's line without its `# `, as a plain str; "" for no stamp."""
        return str(self)

    def language_number(self, language: str) -> int | None:
        """The analysis number `language`'s functions were read under: the one
        number every language shares here; None when the stamp records none."""
        return self.analysis


class SingleNumberStamp(MetricStamp):
    """`crapkit-analysis=N lizard=X.Y.Z`: one analysis number for every language.

    The form 0.8.x wrote. Only `MetricStamp.parse` builds one in crapkit, for
    `read_stamp` and `run_stamp`, and it renders back the line it read."""

    def __new__(cls, analysis: int, lizard: str) -> SingleNumberStamp:
        stamp = super().__new__(cls, _SINGLE_LINE.format(analysis, lizard))
        stamp._versions = (analysis, lizard)
        return stamp

    def __getnewargs__(self) -> tuple[int, str]:
        # A copy or a pickle rebuilds the stamp from its two versions, not its text.
        return self._versions

    @property
    def lizard(self) -> str:
        return self._versions[1]


class UnreadableStamp(MetricStamp):
    """A stamp line that is not one crapkit writes. It compares unequal to every
    other stamp, so verify refuses the marks instead of reading them as unstamped."""
    __slots__ = ()


class _Unstamped(MetricStamp):
    __slots__ = ()


MetricStamp.NONE = _Unstamped("")

# The per-scope form. Line 1 is what 0.8.x reads as its stamp, compares unequal
# and quotes, so it names the marks file and the release that reads it; every
# `#scope` line holds a tab and a third field that is no number, so 0.8.x reads
# it as a mark it cannot parse and refuses the file before it rewrites it.
_SCOPE = "#scope"
_SCOPES_TAIL = "holds one stamp per scope, which crapkit 0.9.0 or later reads"
_SCOPES_HEAD = re.compile(rf"crapkit-stamp=2 lizard=([^\s:]+): (.+) {re.escape(_SCOPES_TAIL)}")
_PAIR = r"[^\s=;]+=[0-9]+"
_SCOPE_BODY = re.compile(rf"analysis((?: {_PAIR})*); coverage (cc-only|no-lane|{_PAIR}(?: {_PAIR})*)")
_PAIRS = re.compile(r"([^\s=;]+)=([0-9]+)")
_NO_READER = ("cc-only", "no-lane")


def _pairs(numbers: dict) -> str:
    """` name=N` per entry, sorted by name."""
    return "".join(f" {name}={number}" for name, number in sorted(numbers.items()))


def _coverage_text(mode) -> str:
    return mode if isinstance(mode, str) else _pairs(mode)[1:]


def _coverage(text: str) -> str | dict:
    return text if text in _NO_READER else _numbers(text)


def _numbers(text: str) -> dict:
    return {name: int(number) for name, number in _PAIRS.findall(text)}


class ScopeStamp(NamedTuple):
    """One scope's rules: the analysis number of each of its languages
    ({language: N}), and its coverage mode: `cc-only`, `no-lane`, or
    {parser: reader number} for the lanes that measure it."""
    analysis: dict
    coverage: str | dict

    def render(self) -> str:
        """`analysis python=13; coverage coveragepy=1`, languages and parsers sorted."""
        return f"analysis{_pairs(self.analysis)}; coverage {_coverage_text(self.coverage)}"

    @classmethod
    def parse(cls, text: str) -> ScopeStamp | None:
        """The stamp `text` spells; None when it spells none, or one that would
        not render back as itself, so a write never respells a committed line."""
        found = _SCOPE_BODY.fullmatch(text)
        stamp = cls(_numbers(found[1]), _coverage(found[2])) if found else None
        return stamp if stamp and stamp.render() == text else None


class ScopeStamps(MetricStamp):
    """`crapkit-stamp=2`: the lizard version and one `ScopeStamp` per scope name.

    The file holds it as a first line naming the file, then one `#scope` line
    per scope, sorted by name. Its text holds the lizard version and every
    scope's line but never the file's name, so two are equal exactly when they
    write the same scope lines. `file_name` is the name the first line of the
    file it came from recorded: a write that adds no number writes that line
    back, even into the temporary file git hands the merge driver as OURS.
    """

    def __new__(cls, lizard: str, scopes: dict, file_name: str | None = None) -> ScopeStamps:
        ordered = dict(sorted(scopes.items()))
        lines = json.dumps({name: scope.render() for name, scope in ordered.items()},
                           ensure_ascii=False)
        stamp = super().__new__(cls, f"crapkit-stamp=2 lizard={lizard} {lines}")
        stamp._parts = (lizard, ordered, file_name)
        return stamp

    def __getnewargs__(self) -> tuple:
        return self._parts

    @property
    def lizard(self) -> str:
        return self._parts[0]

    @property
    def scopes(self) -> dict:
        """{scope name: ScopeStamp}, sorted by name."""
        return self._parts[1]

    def language_number(self, language: str) -> int | None:
        """The lowest number any scope records for `language`; None when none
        does. A reader that holds no config cannot place a mark in a scope."""
        return min((scope.analysis[language] for scope in self.scopes.values()
                    if language in scope.analysis), default=None)

    def head(self, file_name: str | None) -> str:
        """Line 1, naming the file its own first line named, else `file_name`."""
        name = self._parts[2] or file_name
        if not name:
            raise ValueError("a per-scope stamp's first line names its marks file, and none was given")
        return f"# crapkit-stamp=2 lizard={self.lizard}: {name} {_SCOPES_TAIL}"

    def scope_lines(self) -> list[str]:
        return [_scope_line(name, scope.render()) for name, scope in self.scopes.items()]


def _scope_line(name: str, text: str) -> str:
    """`#scope`, the name and the scope's stamp, tab-separated. A name holding a
    tab or a line break takes the record encoding, as a mark's name does:
    encode_record encodes every line that opens with `#`, so it judges the line
    without its `#`."""
    raw = f"{_SCOPE}\t{name}\t{text}"
    if encode_record((_SCOPE[1:], name, text)) == raw[1:]:
        return raw
    return encode_record((_SCOPE, name, text))


def run_stamp(tool_versions: dict) -> MetricStamp:
    """The metric a stored run was measured under: the per-scope stamp a run
    that recorded `scope_stamps` holds, else the single number 0.8.x recorded;
    NONE for a run that recorded neither."""
    scopes = tool_versions.get("scope_stamps")
    if scopes is None:
        return _run_single_number(tool_versions)
    return ScopeStamps(tool_versions["lizard"],
                       {name: ScopeStamp(**stamp) for name, stamp in scopes.items()})


def _run_single_number(tool_versions: dict) -> MetricStamp:
    """The single-number stamp a 0.8.x run's `analysis_version` and `lizard`
    spell, parsed from the line 0.8.x signed its marks with; NONE for a run
    that recorded either one not."""
    analysis, lizard = tool_versions.get("analysis_version"), tool_versions.get("lizard")
    if not (analysis and lizard):
        return MetricStamp.NONE
    return MetricStamp.parse(_SINGLE_LINE.format(analysis, lizard))


def running_stamp(cfg) -> ScopeStamps:
    """The stamp a run of this crapkit on `cfg` records: each scope's declared
    languages at their analysis number, and its coverage mode, which the
    config sets, not the lanes a run happened to execute."""
    # Imported here, not at module scope: the git merge driver runs this module
    # in a temp dir with no analysis stack, and it passes its own stamp through.
    import lizard

    from .analyze import ANALYSIS_VERSIONS
    from .coverage_format import READER_VERSIONS
    return ScopeStamps(lizard.version, {
        scope.name: ScopeStamp({language: ANALYSIS_VERSIONS[language] for language in scope.languages},
                               _coverage_mode(cfg, scope.name, READER_VERSIONS))
        for scope in cfg.scopes})


def _coverage_mode(cfg, scope: str, readers: dict) -> str | dict:
    """`cc-only` for a `coverage_optional` scope, `no-lane` for one no lane
    measures, else {parser: reader number} of the lanes that measure it."""
    if scope in cfg.coverage_optional_scopes:
        return "cc-only"
    parsers = sorted({lane.parser for lane in cfg.lanes if scope in lane.scopes})
    return {parser: readers[parser] for parser in parsers} if parsers else "no-lane"


def read_stamp(text: str) -> MetricStamp:
    """The metric a marks file was written under; NONE for one written before stamping.

    The stamp line is the first line that is neither blank, the key stamp nor a
    scope stamp, when it is a comment. With no scope stamp line it holds the
    single-number form, or a line crapkit cannot read. Scope stamp lines,
    wherever they stand, make the per-scope form, which the stamp line opens.
    A file that holds both forms, in any order, and one whose scope stamps
    cannot stand read as unreadable, and `read_ratchet` names the line.
    """
    return _file_stamp(text)[0]


_STAMP_2 = "crapkit-stamp=2"
_SINGLE_FIELD = "crapkit-analysis="


def _file_stamp(text: str) -> tuple[MetricStamp, str | None]:
    """The stamp `text` records, and the complaint a file whose scope stamp
    lines cannot stand earns; None when they stand or it holds none."""
    head = _stamp_line(text)
    # Two scans in C: a single-number file of 40k marks never walks its lines here.
    if _SCOPE not in text and _STAMP_2 not in text:
        return _single_number(head), None
    return _scoped_file_stamp(text, head)


def _single_number(head: tuple[int, str] | None) -> MetricStamp:
    return MetricStamp.parse(head[1] if head else "")


def _scoped_file_stamp(text: str, head: tuple[int, str] | None) -> tuple[MetricStamp, str | None]:
    """`_file_stamp` of a file that may hold per-scope lines. One whose only
    `crapkit-stamp=2` sits in a mark or a stray comment, with no scope stamp
    line, reads as its stamp line says."""
    rows = _scope_rows(text)
    found = head and _SCOPES_HEAD.fullmatch(head[1])
    mixed = _both_forms(text, rows)
    if mixed:
        return mixed
    if not (rows or found):
        return _single_number(head), None
    return _scope_stamps(head, found, rows)


def _both_forms(text: str, rows: list) -> tuple[MetricStamp, str] | None:
    """The unreadable stamp and the complaint of a file that holds a
    `crapkit-analysis=` line and a per-scope line (a per-scope first line or a
    readable scope stamp line), in either order; None for one that does not.
    The complaint names the later line, since a write would keep one form only."""
    comments = _comments(text)
    single, scoped = _first_single(comments), _first_scoped(comments, rows)
    if not (single and scoped):
        return None
    said = {single: f"[{single[1]}]", scoped: "per scope"}
    first, later = sorted((single, scoped))
    return UnreadableStamp(scoped[1]), (
        f"ratchet line {later[0]} stamps the file {said[later]}, but line {first[0]} stamps it "
        f"{said[first]}; a marks file holds one stamp form, single-number or per scope")


def _comments(text: str) -> list[tuple[int, str]]:
    """(line number, text after its `#`) of each comment line other than the
    key stamp and the scope stamps."""
    return [(number, line[1:].strip()) for number, line in enumerate(record_lines(text), 1)
            if comment_line(line) and not _key_stamp(line) and _scope_row(line) is None]


def _first_single(comments: list[tuple[int, str]]) -> tuple[int, str] | None:
    return next((comment for comment in comments if comment[1].startswith(_SINGLE_FIELD)), None)


def _first_scoped(comments: list[tuple[int, str]], rows: list) -> tuple[int, str] | None:
    """(line number, line) of the first per-scope first line or readable scope stamp line."""
    heads = [comment for comment in comments if _SCOPES_HEAD.fullmatch(comment[1])]
    return min(heads[:1] + _readable_rows(rows)[:1], default=None)


def _readable_rows(rows: list) -> list[tuple[int, str]]:
    """(line number, line) of each scope stamp line that spells a scope stamp."""
    return [(number, line) for number, line, fields in rows if _scope_stamp(fields)]


def _stamp_line(text: str) -> tuple[int, str] | None:
    """(line number, text after its `#`) of the stamp line; None when the first
    line that is neither blank, the key stamp nor a scope stamp is no comment."""
    for number, line in enumerate(record_lines(text), 1):
        if line.strip() and not _key_stamp(line) and _scope_row(line) is None:
            return (number, line[1:].strip()) if comment_line(line) else None
    return None


def _scope_rows(text: str) -> list[tuple[int, str, list[str]]]:
    """(line number, line, fields) of each scope stamp line, readable or not."""
    if _SCOPE not in text:  # one scan in C: a file of 40k marks and no scope line
        return []
    return [(number, line, fields) for number, line in enumerate(record_lines(text), 1)
            if (fields := _scope_row(line)) is not None]


def _scope_stamps(head, found, rows) -> tuple[MetricStamp, str | None]:
    """The per-scope stamp the stamp line and `rows` make. A file whose stamp
    line is not the per-scope one, a scope stamped twice, and a line that
    spells no scope stamp read as unreadable, quoting that line; the last one
    earns no complaint here, since read_ratchet names that line on its own."""
    if not found:
        return _unopened(head, rows)
    scopes = {}
    for number, line, fields in rows:
        stamp = _scope_stamp(fields)
        if stamp is None:
            return UnreadableStamp(line), None
        if fields[1] in scopes:
            return UnreadableStamp(line), f"ratchet line {number} stamps scope {fields[1]!r} a second time"
        scopes[fields[1]] = stamp
    return ScopeStamps(found[1], scopes, found[2]), None


def _unopened(head: tuple[int, str] | None, rows: list) -> tuple[MetricStamp, str | None]:
    """A file of scope stamp lines that the per-scope stamp line does not open.
    It reads as unreadable, and its first readable scope stamp line earns the
    complaint; an unreadable one earns none, since read_ratchet names it."""
    readable = _readable_rows(rows)
    if not readable:
        return UnreadableStamp(rows[0][1]), None
    opened = f"line {head[0]} stamps the file [{head[1]}]" if head else "no stamp line opens the file"
    return UnreadableStamp(readable[0][1]), (
        f"ratchet line {readable[0][0]} is a scope stamp, but {opened}; a marks file holds one "
        "stamp form, single-number or per scope")


def read_key_version(text: str) -> int:
    """Missing means the old start-only identity; an unknown format cannot compare."""
    stamps = _key_stamps(text)
    if not stamps:
        return 0
    if len(stamps) != 1 or stamps[0] not in ("0", str(KEY_VERSION)):
        raise ValueError("unreadable or unsupported ratchet key identity version")
    return int(stamps[0])


def _key_stamps(text: str) -> list[str]:
    return [line[len(_KEY_STAMP):] for line in record_lines(text) if _key_stamp(line)]


def _marked_group(entry: RatchetEntry, groups: set) -> tuple[str, str]:
    exact = (entry.path, entry.long_name)
    return exact if exact in groups else (entry.path, split_ordinal(entry.long_name)[0])


def parsed_marks(text: str, entries=None) -> list:
    """The marks a caller already parsed out of `text`, or a parse of it now."""
    return read_ratchet(text)[0] if entries is None else entries


def check_reader_keys(text: str, entries=None) -> None:
    """Old expression readers lost callbacks, so their anonymous ordinals lack proof.
    `entries` is `read_ratchet(text)[0]` when the caller already holds it."""
    check_reader_version(parsed_marks(text, entries), read_stamp(text))


def check_reader_version(entries, stamp: MetricStamp) -> None:
    """Admit expression keys only when their source reader enumerated every callback.

    Each JavaScript-family mark asks `stamp` for its own language's number
    (`keys.expression_language`): a single-number stamp's one number, or the
    lowest number any scope of a per-scope stamp records for that language.
    """
    from .keys import expression_group, expression_language

    stale = _stale_expression_languages(stamp)
    if not stale:
        return
    affected = sorted({entry.path for entry in entries if expression_group(entry.path, entry.long_name)
                       and expression_language(entry.path) in stale})
    if affected:
        raise ValueError("expression reader 10 changed anonymous function ordinals in "
                         f"{', '.join(affected)}; refresh coverage and reconcile any saved "
                         "marks with their original functions before reseeding")


def _stale_expression_languages(stamp: MetricStamp) -> set[str]:
    """The JavaScript-family languages whose number in `stamp` does not prove expression reader 10."""
    from .keys import EXPRESSION_LANGUAGES, expression_reader_current

    return {language for language in EXPRESSION_LANGUAGES
            if not expression_reader_current(stamp.language_number(language))}


def check_key_groups(text: str, present: set, collisions: set, entries=None) -> int:
    """Prove old marks still name the same functions before comparing or rewriting.

    An unseen mark stays intact with its old version. A known collision needs
    a reviewed mapping, because it also shifts every later ordinal of that name.
    `entries` is `read_ratchet(text)[0]` when the caller already holds it: one
    parse of a 39,496-mark file takes 71 ms.
    """
    entries = parsed_marks(text, entries)
    check_reader_keys(text, entries)
    version = read_key_version(text)
    if version == KEY_VERSION:
        return version
    known = present | collisions  # once: a union per mark copied `present` 40k times
    groups = {_marked_group(entry, known) for entry in entries}
    _refuse_ambiguous(groups & collisions)
    return KEY_VERSION if groups <= present else 0


def _refuse_ambiguous(unresolved: set) -> None:
    if unresolved:
        names = "; ".join(f"{path}: {name}" for path, name in sorted(unresolved))
        raise ValueError(f"legacy ratchet key identity is ambiguous for {names}; "
                         "preserve these marks and reconcile their function mapping "
                         "as described in docs/ratchet.md#same-line-function-identity")


def checked_key_version(text: str, rows, *, historical: set = frozenset(), entries=None) -> int:
    from .keys import ambiguous_groups

    present = {(row.path, row.long_name) for row in rows}
    return check_key_groups(text, present, ambiguous_groups(rows) | set(historical), entries)


def unseen_marks(entries, rows) -> list[RatchetEntry]:
    """The marks whose name group `rows` lacks. In a file with no key stamp they
    hold the start-only format: no row proves what their ordinals meant."""
    present = {(row.path, row.long_name) for row in rows}
    return [entry for entry in entries if _marked_group(entry, present) not in present]


def marked_collisions(entries, rows) -> set:
    """The same-line twin groups of `rows` that `entries` mark: the groups the
    start-only format cannot key, as `checked_key_version` judges them."""
    from .keys import ambiguous_groups

    present = {(row.path, row.long_name) for row in rows}
    return {_marked_group(entry, present) for entry in entries} & ambiguous_groups(rows)


# The coverage parsers 0.8.x read, each at the reader number it read them at.
# A single-number file records no coverage mode: a scope a lane of one of these
# measures now was measured by it then, and a scope whose lanes use only other
# parsers was cc-only when 0.8.x seeded it.
SINGLE_NUMBER_PARSERS = {"coveragepy": 1, "istanbul": 1}
_NOT_COMPARABLE = "CRAP scores are not comparable across metric versions"
_BACKWARDS = ("rather than re-seed, which would restamp the team's marks backwards and make "
              "every upgraded teammate's verify refuse them")


class StampMove(NamedTuple):
    """One way a marked scope's rules moved since its marks were recorded.

    `kind` is `analysis` (`what` names the language), `reader` (the parser),
    `lizard`, `coverage` (old and new mode) or `unstamped` (the scope holds
    marks and no recorded stamp, which is how a renamed scope reads)."""
    scope: str
    kind: str
    what: str = ""
    old: object = None
    new: object = None

    def render(self) -> str:
        if self.kind == "unstamped":
            return "no stamp recorded for this scope"
        name = f"{self.what} {self.kind}" if self.what else self.kind
        return f"{name} {_mode_text(self.old)} to {_mode_text(self.new)}"

    @property
    def newer(self) -> bool:
        """The marks recorded a higher number than this crapkit runs: a newer release wrote them."""
        return self.kind in ("analysis", "reader") and self.old > self.new


def _mode_text(value) -> str:
    return _coverage_text(value) if isinstance(value, dict) else str(value)


class StampConflict(NamedTuple):
    """How recorded marks and the running metric differ.

    `moved` refuses: each marked scope whose rules moved. `gone` names the
    recorded scopes the config no longer has, which refuse nothing. `inferred`
    names the marked scopes whose coverage mode a single-number file left to
    inference, for verify's warning. With neither side per scope, or a stamp
    no scope can be read from, the whole file refuses as one value. So does a
    single-number file that holds no mark (`one_value`, `as_one_value`): its
    `moved` names every running scope its number or lizard differs from."""
    recorded: MetricStamp
    running: MetricStamp
    moved: tuple = ()
    gone: tuple = ()
    inferred: tuple = ()
    one_value: bool = False

    @property
    def whole(self) -> bool:
        return self.one_value or not _weighed_per_scope(self.recorded, self.running)

    @property
    def refuses(self) -> bool:
        return self.whole or bool(self.moved)

    @property
    def scopes(self) -> frozenset:
        """The scopes a seed restamps: the moved ones, or every running scope
        for a whole-file refusal."""
        if self.moved:
            return frozenset(move.scope for move in self.moved)
        return frozenset(self.running.scopes) if isinstance(self.running, ScopeStamps) else frozenset()

    @property
    def newer(self) -> bool:
        """A newer crapkit wrote the marks: an upgrade clears them, and a seed
        would restamp them backwards."""
        if self.whole and not self.moved:
            return bool(newer_tools(self.recorded, self.running))
        return any(move.newer for move in self.moved)

    def refusal(self, file_name: str, remedy: str | None = None) -> str:
        """The text verify refuses with. `remedy` replaces the stock
        coverage, prune and seed steps, which read the run a plain seed picks."""
        head = f"ratchet marks were recorded under [{self.recorded}] but this run measures [{_label(self.running)}]"
        if not self.whole:
            head = (f"ratchet marks were recorded under [{_label(self.recorded)}] in {file_name}, and "
                    f"the rules have moved since in {_scopes_moved(self.moved)}")
        if self.newer:
            return f"{head} - {upgrade_remedy(['crapkit'])} {_BACKWARDS}"
        flips = "".join(_flip_note(move) for move in self.moved if _raise_kind(move) == "coverage flip")
        return f"{head} - {_NOT_COMPARABLE}; {remedy or coverage_then_seed(seed=self.seeds())}{flips}"

    def seeds(self, baseline: str = "") -> str:
        """`crapkit ratchet seed --scope NAME` for each scope a scoped seed
        repairs, else the plain seed; `baseline` appended to each."""
        scopes = [f" --scope {name}" for name in self._scoped_repairs]
        return ", then ".join(f"`{_self()} ratchet seed{scope}{baseline}`" for scope in scopes or [""])

    @property
    def _scoped_repairs(self) -> tuple:
        """The moved scopes, unless lizard moved: one lizard version signs every
        scope and `seed --scope` keeps the file's, so only the plain seed clears
        it. A file compared as one value holds no mark a scope's seed repairs."""
        if self.one_value or any(move.kind == "lizard" for move in self.moved):
            return ()
        return tuple(dict.fromkeys(move.scope for move in self.moved))


# The upgrade guide's section a flip refusal links. A test pins the anchor, so
# moving the section breaks that test before it breaks every printed link.
FLIP_PROCEDURE = "docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one"


def _flip_note(move: StampMove) -> str:
    """The sentence a coverage flip adds to the refusal: the scope, the parsers
    of the lanes that measure it now, and the procedure that re-marks it."""
    return (f"; {move.scope} was {move.old} and is now measured by its {' and '.join(sorted(move.new))} "
            f"lane(s), so its marks hold no coverage yet: follow {FLIP_PROCEDURE}")


def _label(stamp: MetricStamp) -> str:
    """A stamp as one refusal names it: a per-scope stamp by its form and lizard."""
    return f"{_STAMP_2} lizard={stamp.lizard}" if isinstance(stamp, ScopeStamps) else str(stamp)


def _scopes_moved(moved: tuple) -> str:
    """`2 scope(s): web (typescript analysis 13 to 14), api (lizard 1.24.0 to 1.25.0)`."""
    by_scope: dict[str, list[str]] = {}
    for move in moved:
        by_scope.setdefault(move.scope, []).append(move.render())
    named = ", ".join(f"{name} ({'; '.join(what)})" for name, what in by_scope.items())
    return f"{len(by_scope)} scope(s): {named}"


def _weighed_per_scope(recorded: MetricStamp, running: MetricStamp) -> bool:
    """Marks a scope's stamp can be read from, against a running per-scope stamp.
    Not the merge's `_per_scope`, which also takes a per-scope side against a
    single number: verify never weighs per scope without a per-scope running stamp."""
    return isinstance(running, ScopeStamps) and isinstance(recorded, (SingleNumberStamp, ScopeStamps))


def stamp_conflict(recorded: MetricStamp, running: MetricStamp,
                   marked_scopes: frozenset = frozenset()) -> StampConflict | None:
    """How `recorded` marks and the `running` metric differ; None when nothing does.

    An unstamped file has nothing to disagree with: the caller warns instead.
    Against a per-scope `running` stamp only the scopes in `marked_scopes` can
    move, and only by what their own languages, readers, lizard and coverage
    mode record. Two single-number stamps compare as one value.
    """
    if not recorded or recorded == running:
        return None
    if not _weighed_per_scope(recorded, running):
        return StampConflict(recorded, running)
    return _scoped_conflict(recorded, running, marked_scopes)


def as_one_value(recorded: MetricStamp, running: ScopeStamps) -> StampConflict | None:
    """How a file no mark of which places in a scope differs from `running`.

    A single-number file that holds no mark compares as one value: its number
    and lizard against each language and the lizard of every running scope,
    with no coverage mode to infer. Any other stamp compares as
    `stamp_conflict` compares it with no marked scope."""
    if not isinstance(recorded, SingleNumberStamp):
        return stamp_conflict(recorded, running)
    lizards = (recorded.lizard, running.lizard)
    moved = tuple(move for name, now in running.scopes.items() for move in _scope_moves(
        name, ScopeStamp(dict.fromkeys(now.analysis, recorded.analysis), now.coverage), now, lizards))
    return StampConflict(recorded, running, moved, one_value=True) if moved else None


def _scoped_conflict(recorded, running: ScopeStamps, marked_scopes) -> StampConflict | None:
    """The per-scope comparison; None when no marked scope moved, no recorded
    scope is gone and no mode was inferred."""
    marked = sorted(set(marked_scopes) & set(running.scopes))
    conflict = StampConflict(recorded, running, _moves(recorded, running, marked),
                             _gone(recorded, running), _inferred(recorded, running, marked))
    return conflict if conflict.moved or conflict.gone or conflict.inferred else None


def _moves(recorded, running: ScopeStamps, marked: list[str]) -> tuple:
    lizards = (recorded.lizard, running.lizard)
    return tuple(move for name in marked for move in _scope_moves(
        name, _recorded_scope(recorded, name, running.scopes[name]), running.scopes[name], lizards))


def _recorded_scope(recorded, name: str, now: ScopeStamp) -> ScopeStamp | None:
    """What `recorded` holds for scope `name`, which runs as `now`. A
    single-number file holds N for each language the scope runs, and the mode
    0.8.x seeded the scope under, inferred from its lanes now."""
    if isinstance(recorded, ScopeStamps):
        return recorded.scopes.get(name)
    return ScopeStamp(dict.fromkeys(now.analysis, recorded.analysis), _inferred_mode(now.coverage))


def _inferred_mode(mode) -> str | dict:
    """A scope with a lane of a parser 0.8.x read was measured by those readers
    at the number 0.8.x read them at; any other scope was cc-only."""
    if not isinstance(mode, dict):
        return "cc-only"
    readers = {parser: SINGLE_NUMBER_PARSERS[parser] for parser in mode if parser in SINGLE_NUMBER_PARSERS}
    return readers or "cc-only"


def _scope_moves(name: str, then: ScopeStamp | None, now: ScopeStamp, lizards: tuple) -> list:
    """Each way scope `name` moved from `then` to `now`. A language or a parser
    only one side runs moves nothing: an added one leaves functions unmarked
    until the next seed; a changed set of parsers moves the coverage mode."""
    if then is None:
        return [StampMove(name, "unstamped")]
    moves = [StampMove(name, "analysis", language, then.analysis[language], number)
             for language, number in now.analysis.items()
             if language in then.analysis and then.analysis[language] != number]
    if lizards[0] != lizards[1]:
        moves.append(StampMove(name, "lizard", "", *lizards))
    return moves + _coverage_moves(name, then.coverage, now.coverage)


def _coverage_moves(name: str, then, now) -> list:
    """`reader` per parser whose number moved, when both sides measure with the
    same parsers; else `coverage` when the mode differs."""
    if then == now:
        return []
    if _same_parsers(then, now):
        return [StampMove(name, "reader", parser, then[parser], number)
                for parser, number in now.items() if then[parser] != number]
    return [StampMove(name, "coverage", "", then, now)]


def _same_parsers(then, now) -> bool:
    """Both modes are readers, and of the same parsers."""
    return isinstance(then, dict) and isinstance(now, dict) and then.keys() == now.keys()


def _gone(recorded, running: ScopeStamps) -> tuple:
    """The scopes a per-scope file records that the config no longer has."""
    if not isinstance(recorded, ScopeStamps):
        return ()
    return tuple(sorted(set(recorded.scopes) - set(running.scopes)))


def _inferred(recorded, running: ScopeStamps, marked: list[str]) -> tuple:
    """The marked scopes a single-number file leaves to a guess: those a lane
    measures, which 0.8.x may have measured or scored cc-only. A scope with no
    lane had one lane-less mode then, so it is no guess."""
    if not isinstance(recorded, SingleNumberStamp):
        return ()
    return tuple(name for name in marked if isinstance(running.scopes[name].coverage, dict))


def upgrade_remedy(tools: list[str]) -> str:
    """What to do about marks a newer crapkit measured: upgrade this one, everywhere
    it runs. `tools` is what `newer_tools` returned, which names crapkit alone.
    Every caller says what its own write would have done to them instead."""
    return ("the marks come from a newer crapkit than this install; upgrade it to the "
            "version that wrote them (the CLI, the Action's `uses:` pin and the pre-commit "
            "`rev` alike)")


# The stamp fields that mark a newer release, and the tool each is the version of.
# lizard is not one: crapkit pins it exactly and moves the analysis version with
# it, so a newer lizard in a stamp comes from a release that let lizard float, and
# the reseed that moves those marks to the pinned lizard is their remedy.
_STAMP_TOOLS = (("crapkit-analysis", "crapkit"),)


def newer_tools(recorded: MetricStamp, current: MetricStamp) -> list[str]:
    """`["crapkit"]` when `recorded` names a newer analysis version than `current`
    does, the one field that marks a newer release. [] when it is not newer, or
    when a stamp does not read as `crapkit-analysis=N lizard=X.Y.Z`."""
    then, now = _stamp_versions(recorded), _stamp_versions(current)
    return [tool for field, tool in _STAMP_TOOLS if _is_newer(then.get(field), now.get(field))]


def _is_newer(then: tuple | None, now: tuple | None) -> bool:
    return bool(then and now) and then > now


def _stamp_versions(stamp: MetricStamp) -> dict[str, tuple[int, ...]]:
    """`crapkit-analysis=11 lizard=1.24.0` as {field: (11,), ...}: numbers compare
    as numbers, so lizard 1.9.0 is older than 1.24.0. A word with no `=` is left out."""
    fields = (word.partition("=") for word in stamp.split())
    return {name: tuple(int(n) for n in re.findall(r"\d+", value))
            for name, sep, value in fields if sep}


def coverage_then_seed(rebaseline: str = "re-baseline", seed: str = "") -> str:
    """The remedy every stamp refusal prints: a fresh run, a prune, then the seed.

    Seed stamps the metric of the run it reads. Right after an upgrade the
    newest run is the older crapkit's, so seed alone kept the old stamp and
    verify refused again. A new metric can also move a function's key (a
    UTF-16 source, a name holding a byte cp1252 leaves undefined), and seed
    never drops a mark, so prune goes between them to drop the mark left under
    the old key, as the upgrade guide says. `seed` names the seed commands
    when they are not the plain one.
    """
    return (f"run `{_self()} coverage`, then `{_self()} ratchet prune`, then {rebaseline} "
            f"with {seed or f'`{_self()} ratchet seed`'}")


def comment_line(line: str) -> bool:
    """A format comment: the metric stamp line, the key stamp, or a scope stamp
    line crapkit can read. Any other `#` line that holds a tab is a legacy raw
    mark whose path starts with `#`, not a comment."""
    return line.startswith("#") and ("\t" not in line or _readable_scope(line))


def _key_stamp(line: str) -> bool:
    return comment_line(line) and line.startswith(_KEY_STAMP)


def _is_skippable(line: str) -> bool:
    """Blank lines, the header and the stamp lines carry no mark: the comment
    lines, and a readable scope stamp line in the record encoding."""
    return (not line.strip() or line == _HEADER or comment_line(line)
            or (line.startswith("@") and _readable_scope(line)))


def _scope_row(line: str) -> list[str] | None:
    """The fields of a scope stamp line, raw or in the record encoding: its
    first field is `#scope` and its third is no finite number. The rule reads
    the line alone, so a reader of whole revisions and one of single rows
    agree, and a legacy mark whose path is `#scope` still reads as a mark."""
    if not line.startswith((_SCOPE + "\t", "@")):
        return None
    try:
        fields = decode_record(line)
    except ValueError:
        return None
    return None if fields[:1] != [_SCOPE] or _number_in(fields[2:3]) else fields


def _number_in(fields: list[str]) -> bool:
    """True when `fields` holds one finite number."""
    try:
        return bool(fields) and math.isfinite(float(fields[0]))
    except ValueError:
        return False


def _scope_stamp(fields: list[str]) -> ScopeStamp | None:
    """The scope's stamp a scope stamp line's fields spell; None for none."""
    return ScopeStamp.parse(fields[2]) if len(fields) == 3 else None


def _readable_scope(line: str) -> bool:
    fields = _scope_row(line)
    return fields is not None and _scope_stamp(fields) is not None


def _finite_mark(text: str) -> float:
    """The mark as the file holds it, at four decimals: a hand-typed mark with more
    reads as the number the next write gives it (a tie rounds as round() rounds
    the double), so every mark in memory is one `dump_ratchet` writes unchanged."""
    mark = float(text)
    if not math.isfinite(mark):
        raise ValueError("ratchet mark must be finite")
    return float(f"{mark:.4f}")


def _read_mark(line: str, number: int) -> RatchetEntry:
    try:
        parts = decode_record(line)
    except ValueError as exc:
        raise ValueError(f"ratchet line {number} has an unreadable record: {exc}") from exc
    if len(parts) != 3:
        raise ValueError(f"ratchet line {number} has {len(parts)} fields, expected 3: {line!r}")
    try:
        return RatchetEntry(parts[0], parts[1], _finite_mark(parts[2]))
    except ValueError as exc:
        what = "scope stamp" if parts[0] == _SCOPE else "mark"
        raise ValueError(f"ratchet line {number} has an unreadable {what}: {line!r}") from exc


def read_ratchet(text: str) -> tuple[list[RatchetEntry], list[str]]:
    """The marks, and one complaint per line that carries none.

    Split out from `load_ratchet` so a READ-ONLY caller can salvage the file. A
    mark is one optional field of what `explain` and `brief` answer, so a single
    hand-edited line used to cost the whole answer: a ValueError traceback where
    a trajectory, a source listing and a churn count were all available. Two
    shapes reach here, and a hand edit or a botched merge produces both: too few
    fields, and three fields whose mark is empty or is not a number. Dropping a
    line can only take a ceiling away from a gate, never raise one, so the read
    side is safe to salvage. The write side is not, and keeps `load_ratchet`.
    """
    entries, complaints = [], []
    for i, line in enumerate(record_lines(text)):
        if _is_skippable(line):
            continue
        try:
            entries.append(_read_mark(line, i + 1))
        except ValueError as exc:
            complaints.append(str(exc))
    return entries, complaints + _stamp_complaints(text)


def _stamp_complaints(text: str) -> list[str]:
    """The complaint a file whose stamp lines cannot stand earns: one that
    holds both stamp forms, or stamps a scope twice. A history reader parses
    one row at a time, so a row with neither form's marker skips the walk."""
    complaint = _file_stamp(text)[1] if _SCOPE in text or _STAMP_2 in text else None
    return [complaint] if complaint else []


def load_ratchet(text: str) -> list[RatchetEntry]:
    """Every mark, refusing a file that holds a line carrying none. What every
    caller that REWRITES the file reads with: a line skipped there would delete
    a mark the repo signed for."""
    read_key_version(text)
    entries, complaints = read_ratchet(text)
    if complaints:
        raise ValueError(complaints[0])
    return entries


def dump_ratchet(entries: list[RatchetEntry], *, stamp: MetricStamp, key_version: int = 0,
                 file_name: str | None = None) -> str:
    """`stamp` is written as the lines it renders, and NONE writes none. No
    default: a writer that stamped by omission relabeled marks another metric
    recorded, so the choice belongs to `ratchetfile.RatchetFile`'s stamp
    rules. A per-scope stamp writes its first line above the key stamp and its
    scope lines below it, and that first line names `file_name` unless the
    stamp was read from a file whose first line named one. Every mark must
    read back as the number it is (`invariants.check_dump`): the file holds
    four decimals."""
    check_dump(entries)
    head, scopes = _stamp_lines(stamp, file_name)
    keys = [f"{_KEY_STAMP}{key_version}"] if key_version else []
    lines = [*head, *keys, *scopes, _HEADER]
    lines += [encode_record((e.path, e.long_name, f"{e.crap:.4f}"))
              for e in sorted(entries, key=lambda e: (e.path, e.long_name))]
    return "\n".join(lines) + "\n"


def _stamp_lines(stamp: MetricStamp, file_name: str | None) -> tuple[list[str], list[str]]:
    """The stamp's lines above the key stamp, and those between it and the header."""
    if isinstance(stamp, ScopeStamps):
        return [stamp.head(file_name)], stamp.scope_lines()
    return [f"# {stamp}"] if stamp else [], []


def _merge_key(b: float | None, o: float | None, t: float | None) -> float | None:
    """git 3-way semantics per key: the changed side wins over the unchanged one.
    Both changed: keep the mark (it can only fall; prune is re-runnable) at min."""
    if o == t:
        return o
    if o == b:
        return t
    if t == b:
        return o
    # both changed differently; both-None is impossible past the o == t check
    return min(x for x in (o, t) if x is not None)


def merge_ratchets(base: list[RatchetEntry], ours: list[RatchetEntry],
                   theirs: list[RatchetEntry]) -> list[RatchetEntry]:
    """git's 3-way merge of the marks, one mark at a time by `_merge_key`.

    A key a side dropped and a key it added that keys pairs (`_mark_moves`) are one
    mark: the merged line takes the key the moving side gave it, or both keys
    when the two sides moved it apart. Two marks that land on one key keep the
    lower, since a mark can only fall.
    """
    merged: dict[Key, float] = {}
    for landing, crap in _merged_marks(base, ours, theirs):
        for key in landing:
            merged[key] = min(crap, merged.get(key, crap))
    return [RatchetEntry(path, name, crap) for (path, name), crap in sorted(merged.items())]


def split_moves(base: list[RatchetEntry], ours: list[RatchetEntry],
                theirs: list[RatchetEntry]) -> list[tuple[Key, ...]]:
    """Each mark OURS and THEIRS moved to different keys: the keys the merge keeps it under."""
    return [landing for landing, _ in _merged_marks(base, ours, theirs) if len(landing) > 1]


def changed_marks(base: list[RatchetEntry], side: list[RatchetEntry]) -> int:
    """How many marks `side` holds otherwise than `base`: added, dropped, set to
    another value or moved to another key. A moved mark counts once."""
    b = MarkIndex(base)
    held = _by_mark(b, MarkIndex(side))
    return sum(held.get(mark) != (mark, b.mark(mark)) for mark in b.keys() | held.keys())


# The merge names each mark by its BASE key, or by its own key when it is new.
# _ABSENT is the (key, value) of a mark a side does not hold.
_ABSENT = (None, None)


def _merged_marks(base: list, ours: list, theirs: list):
    """(the keys a merged mark lands on, its value) for each mark that survives the merge."""
    b = MarkIndex(base)
    o, t = (_by_mark(b, MarkIndex(side)) for side in (ours, theirs))
    for mark in sorted(b.keys() | o.keys() | t.keys()):
        (o_key, o_crap), (t_key, t_crap) = o.get(mark, _ABSENT), t.get(mark, _ABSENT)
        crap = _merge_key(b.mark(mark), o_crap, t_crap)
        if crap is not None:
            yield _landing(mark, o_key, t_key), crap


def _landing(mark: Key, *sides: Key | None) -> tuple[Key, ...]:
    """Each key a side moved MARK to, sorted, or MARK itself when no side moved it."""
    moved = sorted({key for key in sides if key not in (None, mark)})
    return tuple(moved) or (mark,)


def _by_mark(base: MarkIndex, side: MarkIndex) -> dict[Key, tuple[Key, float]]:
    """SIDE's (key, value) by the mark it holds: a moved key under the BASE key it left."""
    left = {new: old for old, new in _mark_moves(base, side)}
    return {left.get(key, key): (key, side.mark(key)) for key in side.keys()}


def _mark_moves(base: MarkIndex, side: MarkIndex) -> list[tuple[Key, Key]]:
    """Each (BASE key, SIDE key) pair SIDE moved: a re-key inside one file by
    keys.pair_moves, then a path move of the keys left by keys.pair_path_moves."""
    dropped, added = base.keys() - side.keys(), side.keys() - base.keys()
    pairs = _file_moves(dropped, added)
    paired = {key for pair in pairs for key in pair}
    return pairs + pair_path_moves(_marks_of(base, dropped - paired), _marks_of(side, added - paired))


def _file_moves(dropped: set, added: set) -> list[tuple[Key, Key]]:
    """keys.pair_moves's pairs in each file that both lost and gained a key."""
    paths = {key[0] for key in dropped} & {key[0] for key in added}
    return [pair for path in sorted(paths) for pair in pair_moves(path, dropped, added).pairs]


def _marks_of(index: MarkIndex, keys: set) -> dict[Key, float]:
    return {key: index.mark(key) for key in keys}


# --- the stamps of a 3-way merge ------------------------------------------------
# The merge driver runs with no config, so it cannot tell which scope a mark sits
# in. Each scope's stamp and the lizard version merge as a mark does; the CLI
# names a scope one side's stamp came from when the other side changed marks.

_SIDES = ("OURS", "THEIRS")
_OTHER = {"OURS": "THEIRS", "THEIRS": "OURS"}


class StampMerge(NamedTuple):
    """The stamp a merged marks file records, and each (scope, side) whose stamp
    the merge took from that side, OURS or THEIRS, while the other recorded another."""
    stamp: MetricStamp
    came_from: tuple[tuple[str, str], ...] = ()


def merge_stamps(base: MetricStamp, ours: MetricStamp, theirs: MetricStamp) -> StampMerge:
    """The stamp of a 3-way merge, scope by scope, read from the three stamps alone.

    Each scope's stamp and the lizard version merge as a mark does: the side that
    moved from BASE wins, a scope one side removed drops unless the other moved
    it, and moves that differ raise ValueError naming every scope they touch. A
    single-number side stands for every scope at its one number, with no coverage
    mode. NONE and an unreadable stamp merge only with an equal stamp.
    """
    if ours == theirs:
        return StampMerge(ours)
    if _per_scope(ours, theirs):
        return _merge_scopes({"BASE": base, "OURS": ours, "THEIRS": theirs})
    return StampMerge(_merge_whole(base, ours, theirs))


def _per_scope(ours: MetricStamp, theirs: MetricStamp) -> bool:
    """OURS or THEIRS is per scope, and neither is unstamped or unreadable."""
    kinds = {type(ours), type(theirs)}
    return ScopeStamps in kinds and kinds <= {ScopeStamps, SingleNumberStamp}


def _merge_whole(base: MetricStamp, ours: MetricStamp, theirs: MetricStamp) -> MetricStamp:
    """Two different stamps of which neither is per scope merge as one value."""
    if not (isinstance(ours, SingleNumberStamp) and isinstance(theirs, SingleNumberStamp)):
        raise ValueError(_whole_refusal(ours, theirs, "the two stamps do not compare"))
    if base in (ours, theirs):
        return theirs if base == ours else ours
    raise ValueError(_whole_refusal(ours, theirs, "each side moved the stamp away from BASE's"))


def _whole_refusal(ours: MetricStamp, theirs: MetricStamp, why: str) -> str:
    return (f"ratchet merge refused: OURS records [{ours.render() or 'unstamped'}] and THEIRS records "
            f"[{theirs.render() or 'unstamped'}], and {why}; {_merge_remedy(ours, theirs)}, then merge "
            "again; OURS was left unchanged")


def _merge_remedy(ours: MetricStamp, theirs: MetricStamp) -> str:
    """Re-seed one branch under the newer side's metric: a seed under the older
    release stamps its own older metric, and the next merge refused again."""
    if newer_tools(theirs, ours):
        return (f"THEIRS is newer, so on this branch, with a crapkit that measures [{theirs.render()}], "
                f"{coverage_then_seed()}")
    if newer_tools(ours, theirs):
        return (f"OURS is newer, so on the branch you merge, with a crapkit that measures "
                f"[{ours.render()}], {coverage_then_seed()}")
    return f"on one branch, {coverage_then_seed()}"


class _Pick(NamedTuple):
    """One scope's view on each side ({BASE, OURS, THEIRS: view}), and the side
    the merge keeps it from; None when both sides moved it differently."""
    views: dict
    side: str | None


def _merge_scopes(sides: dict) -> StampMerge:
    """`merge_stamps` once OURS or THEIRS is per scope; the result is per scope."""
    lizard_side = _kept_side({side: _lizard(stamp) for side, stamp in sides.items()}, operator.eq)
    picks = {name: _scope_pick(sides, name) for name in _scope_names(sides)}
    clashes = _clashes(picks, lizard_side)
    if lizard_side is None or clashes:
        raise ValueError(_scopes_refusal(clashes, sides, lizard_side))
    stamp = ScopeStamps(sides[lizard_side].lizard, _merged_scopes(picks), _file_name(sides))
    return StampMerge(stamp, _came_from(stamp, sides))


def _lizard(stamp: MetricStamp) -> str | None:
    return stamp.lizard if isinstance(stamp, (SingleNumberStamp, ScopeStamps)) else None


def _scope_names(sides: dict) -> list[str]:
    return sorted({name for stamp in sides.values() if isinstance(stamp, ScopeStamps) for name in stamp.scopes})


def _view(stamp: MetricStamp, name: str):
    """What `stamp` records for scope `name`: its ScopeStamp or None, or a
    single-number stamp's analysis number, which stands for every scope. The
    lizard version merges on its own, so no view holds it."""
    if isinstance(stamp, ScopeStamps):
        return stamp.scopes.get(name)
    return stamp.analysis if isinstance(stamp, SingleNumberStamp) else None


def _scope_pick(sides: dict, name: str) -> _Pick:
    views = {side: _view(stamp, name) for side, stamp in sides.items()}
    return _Pick(views, _kept_side(views, _same))


def _kept_side(views: dict, same) -> str | None:
    """The side a 3-way merge keeps the value from; None when both sides moved
    it differently. Equal sides keep the one that records a scope stamp."""
    if same(views["OURS"], views["THEIRS"]):
        return "THEIRS" if isinstance(views["OURS"], int) else "OURS"
    kept = _kept_moves(views, same)
    return kept[0] if len(kept) == 1 else None


def _kept_moves(views: dict, same) -> list[str]:
    """The sides that moved the value from BASE's. Of two, one that removed it
    gives way to one that moved it; a removal alone is a move that drops it."""
    moved = [side for side in _SIDES if not same(views[side], views["BASE"])]
    return [*filter(views.get, moved)] or moved


def _same(a, b) -> bool:
    """Two views of one scope record the same rules. A single-number view
    records no coverage mode, so it matches a scope stamp on its numbers alone."""
    if isinstance(a, int):
        a, b = b, a
    return a == _seen_as(b, a)


def _seen_as(view, other):
    """`view` beside `other`: a single-number view read as a ScopeStamp holds
    `other`'s languages at its one number and `other`'s coverage mode."""
    if isinstance(view, int) and isinstance(other, ScopeStamp):
        return ScopeStamp(dict.fromkeys(other.analysis, view), other.coverage)
    return view


def _clashes(picks: dict, lizard_side: str | None) -> list[str]:
    """The scopes both sides moved differently: every scope when the lizard version is one."""
    return list(picks) if lizard_side is None else [name for name, pick in picks.items() if pick.side is None]


def _merged_scopes(picks: dict) -> dict:
    """{scope: the ScopeStamp the merge keeps}, less the scopes it drops."""
    scopes = {name: _kept_scope(pick) for name, pick in picks.items()}
    return {name: scope for name, scope in scopes.items() if scope is not None}


def _kept_scope(pick: _Pick):
    """The kept side's view. A single-number view that won takes the languages and
    coverage mode of the scope stamp it was compared with: the other side's, else BASE's."""
    other = pick.views[_OTHER[pick.side]]
    return _seen_as(pick.views[pick.side], other if isinstance(other, ScopeStamp) else pick.views["BASE"])


def _file_name(sides: dict) -> str | None:
    """The marks file the first per-scope line read names: OURS's, else THEIRS's, else BASE's."""
    return next((sides[side]._parts[2] for side in ("OURS", "THEIRS", "BASE")
                 if isinstance(sides[side], ScopeStamps) and sides[side]._parts[2]), None)


def _came_from(stamp: ScopeStamps, sides: dict) -> tuple[tuple[str, str], ...]:
    """(scope, the side its merged stamp came from) for each scope the other side
    records otherwise: under another lizard, or with another scope stamp or none."""
    return tuple((name, _OTHER[side]) for name in stamp.scopes for side in _SIDES
                 if not (_lizard(sides[side]) == stamp.lizard
                         and _same(_view(sides[side], name), stamp.scopes[name])))


def _scopes_refusal(names: list[str], sides: dict, lizard_side: str | None) -> str:
    """Names each clashing scope and its seed. Sides that moved lizard apart
    get the plain seed: one lizard version signs every scope and `seed --scope`
    keeps the file's, so a scoped seed refuses that file."""
    said = "; ".join(f"scope {name}: OURS records [{scope_record(sides['OURS'], name)}] and THEIRS "
                     f"records [{scope_record(sides['THEIRS'], name)}]" for name in names)
    scoped = [f" --scope {name}" for name in names] if lizard_side else [""]
    steps = [f"`{_self()} coverage`", *(f"`{_self()} ratchet seed{scope}`" for scope in scoped)]
    return (f"ratchet merge refused: OURS and THEIRS moved a stamp differently - {said} - run "
            f"{', '.join(steps[:-1])} and {steps[-1]} on one branch, then merge again; OURS was left unchanged")


def scope_record(stamp: MetricStamp, name: str) -> str:
    """What `stamp` records for scope `name`, as the refusal quotes it."""
    if isinstance(stamp, SingleNumberStamp):
        return stamp.render()
    scope = stamp.scopes.get(name)
    return f"lizard={stamp.lizard} {scope.render() if scope else 'and no line for this scope'}"


# Whether a seed lets a scope's marks take the run's values, above their old
# marks too, by how the scope's stamp moved since the marks were recorded:
# only the flip from cc-only or no-lane to a coverage reader. Every other move
# keeps "marks only fall": verify fails the rise at exit 7, `verify --override`
# grants no ratchet regression, and the mark is raised by hand.
SEED_RAISES = {"coverage flip": True, "coverage": False, "analysis": False, "reader": False,
               "lizard": False, "unstamped": False}


def _raise_kind(move: StampMove) -> str:
    """The `SEED_RAISES` row a move reads: a coverage move from no reader to readers is the flip."""
    flip = move.kind == "coverage" and move.old in _NO_READER and isinstance(move.new, dict)
    return "coverage flip" if flip else move.kind


def rising_scopes(conflict: StampConflict | None) -> frozenset[str]:
    """The moved scopes whose marks a seed lets rise: those with a move whose
    `SEED_RAISES` row says so. None, a stamp that did not move, raises nothing."""
    moved = conflict.moved if conflict else ()
    return frozenset(move.scope for move in moved if SEED_RAISES[_raise_kind(move)])


def rescoped(recorded: MetricStamp, run: ScopeStamps, name: str) -> ScopeStamps:
    """`recorded` with scope `name` taking `run`'s line, the stamp `seed --scope`
    signs. Every other scope keeps what the file recorded, and so does the
    lizard version: a single-number file's scopes read at its one number, as
    `stamp_conflict` reads them; an unstamped or unreadable file records none."""
    scopes = _recorded_scopes(recorded, run)
    lizard = recorded.lizard if isinstance(recorded, (ScopeStamps, SingleNumberStamp)) else run.lizard
    file_name = recorded._parts[2] if isinstance(recorded, ScopeStamps) else None
    return ScopeStamps(lizard, {**scopes, name: run.scopes[name]}, file_name)


def _recorded_scopes(recorded: MetricStamp, run: ScopeStamps) -> dict:
    """{scope: ScopeStamp} the file records, a single-number one for each scope `run` holds."""
    if isinstance(recorded, ScopeStamps):
        return dict(recorded.scopes)
    if isinstance(recorded, SingleNumberStamp):
        return {scope: _recorded_scope(recorded, scope, now) for scope, now in run.scopes.items()}
    return {}


def _every_path(path: str) -> bool:
    return True


def _no_path(path: str) -> bool:
    return False


def seed_ratchet(prior: list[RatchetEntry], fresh: list[ScoredRow], *, target: int,
                 scope_targets: dict[str, int] | None = None, scope=None,
                 raises=None) -> tuple[list[RatchetEntry], int, int]:
    """First-class mark entry: record every over-ceiling function at its current CRAP.

    `scope` and `raises` are path predicates. Rows outside `scope` are left out
    and the marks there stay as they are (None takes every path). A mark never
    rises, seeding included, except where `raises` admits its path: there the
    mark takes the run's value. `raised_marks` lists the rises."""
    rises = raises or _no_path
    marks = MarkIndex(prior).working_copy()
    added = tightened = 0
    for key, row in _over_ceiling(fresh, target, scope_targets or {}, scope or _every_path):
        a, t = _place_mark(marks, key, row, rises)
        added += a
        tightened += t
    seeded = sorted(marks.values(), key=lambda e: (e.path, e.long_name))
    check_marks_kept(_held(prior, rises), _held(seeded, rises), adds=True)
    return seeded, added, tightened


def _over_ceiling(fresh: list[ScoredRow], target: int, ceilings: dict, admits) -> list[tuple]:
    """(key, row) for each row whose path `admits` takes and whose CRAP is over its scope's ceiling."""
    return [(key, row) for key, row in rows_by_key(fresh).items()
            if admits(row.path) and over_ceiling(row.crap, ceilings.get(row.scope, target))]


def _place_mark(marks: dict, key: tuple, row: ScoredRow, rises) -> tuple[int, int]:
    """Mark `row` at the run's value where `rises` admits its path, else only lower; (added, tightened)."""
    place = _raise_mark if rises(row.path) else _seed_mark
    return place(marks, key, row.crap)


def _held(entries: list[RatchetEntry], rises) -> list[RatchetEntry]:
    """The marks a seed may not raise."""
    return [entry for entry in entries if not rises(entry.path)]


def _seed_mark(marks: dict, key: tuple, crap: float) -> tuple[int, int]:
    """Place one mark; returns (added, tightened) deltas. A mark never rises."""
    old = marks.get(key)
    mark = round(crap, 4)
    if old is not None and mark >= old.crap:
        return 0, 0
    marks[key] = RatchetEntry(key[0], key[1], mark)
    return (1, 0) if old is None else (0, 1)


def _raise_mark(marks: dict, key: tuple, crap: float) -> tuple[int, int]:
    """Place one mark at the run's value, above the old one too; returns (added, tightened)."""
    old = marks.get(key)
    marks[key] = RatchetEntry(key[0], key[1], round(crap, 4))
    if old is None:
        return 1, 0
    return 0, int(marks[key].crap < old.crap)


def raised_marks(prior: list[RatchetEntry], seeded: list[RatchetEntry]) -> list[tuple[RatchetEntry, float]]:
    """(mark, the mark it replaced) for each mark of `seeded` above `prior`'s, in file order."""
    before = MarkIndex(prior)
    olds = ((entry, before.mark(mark_key(entry))) for entry in seeded)
    return [(entry, old) for entry, old in olds if old is not None and entry.crap > old]


def _repath(entries: list[RatchetEntry], dest_of) -> tuple[list[RatchetEntry], int]:
    """Rewrite whole keys by a per-mark chooser that answers a (path, key name)
    or None to leave the mark alone. The one place a mark's key changes: the
    value never moves, so the file writes the same digits under the new key."""
    out = []
    moved = 0
    for e in entries:
        dest = dest_of(e)
        if dest is None:
            out.append(e)
            continue
        out.append(e._replace(path=dest[0], long_name=dest[1]))
        moved += 1
    return sorted(out, key=lambda e: (e.path, e.long_name)), moved


def _same_name(entry: RatchetEntry, path: str | None) -> tuple[str, str] | None:
    """ENTRY's key name at PATH, or None when no path was chosen."""
    return None if path is None else (path, entry.long_name)


def rekey_marks(entries: list[RatchetEntry], moves) -> tuple[list[RatchetEntry], int]:
    """The marks with each (old key, new key) move of MOVES applied, and how many
    moved. A key is (path, key name). An old key that holds no mark moves
    nothing and takes no key. Moving a mark onto a key that holds a mark, or
    that another mark's move already took, raises ValueError naming both keys.
    Sorted by path, then key name."""
    dest = dict(moves)
    held = MarkIndex(entries)
    taken = set(held.keys())
    for old, new in sorted(dest.items()):
        if old not in held:
            continue
        if new in taken:
            target = f"{new[0]} {new[1]}"
            why = f"{target} already holds a mark" if new in held else f"another mark moves to {target}"
            raise ValueError(f"cannot move the mark of {old[0]} {old[1]} to {target}: {why}")
        taken.add(new)
    return _repath(entries, lambda e: dest.get(mark_key(e)))


def _moved_path(path: str, old: str, new: str) -> str | None:
    """One mark's destination under an explicit move, or None when `old` misses it."""
    if old.endswith("/"):
        return f"{new.rstrip('/')}/{path[len(old):]}" if path.startswith(old) else None
    return new if path == old else None


def move_marks(entries: list[RatchetEntry], old: str,
               new: str) -> tuple[list[RatchetEntry], int]:
    """Re-path marks at their recorded values. A trailing "/" on `old` moves a whole
    directory; anything else matches one exact path."""
    return _repath(entries, lambda e: _same_name(e, _moved_path(e.path, old, new)))


def _rename_target(entry: RatchetEntry, present: set, renames: dict[str, str]) -> str | None:
    """Where a mark should follow a renamed file, or None to leave it alone.

    Three conditions, all required: the function is gone from its recorded path,
    git calls that path renamed, and the SAME key name exists at the new one. A
    copy fails the first (the source survives), so its mark never travels.
    """
    if mark_key(entry) in present:
        return None
    dest = renames.get(entry.path)
    if dest is None or (dest, entry.long_name) not in present:
        return None
    return dest


def follow_renames(prior: list[RatchetEntry], fresh: list[ScoredRow],
                   renames: dict[str, str]) -> tuple[list[RatchetEntry], int]:
    """Marks for renamed files, re-pathed. Runs BEFORE prune so a rename reads as a
    move, not as code that left the repo and forfeits its high-water mark."""
    present = set(rows_by_key(fresh))
    return _repath(prior, lambda e: _same_name(e, _rename_target(e, present, renames)))


def prune_ratchet(prior: list[RatchetEntry],
                  fresh: list[ScoredRow]) -> tuple[list[RatchetEntry], int]:
    """Deliberate mark exit: drop entries whose function is absent from the run.
    The automatic update keeps them (an exclude glob or a lane outage also removes
    rows); prune is the human confirming the code is really gone."""
    present = set(rows_by_key(fresh))
    kept = [e for e in prior if mark_key(e) in present]
    return kept, len(prior) - len(kept)


def _jumped(previous: float, fresh: float, factor: float) -> bool:
    """True when two measurements of one commit differ by more than `factor`.

    Written as a multiplication, not a ratio: a division would have to special-case
    a zero, and the comparison is the same one either way.
    """
    low, high = sorted((previous, fresh))
    return high > low * factor


def unstable_marks(prior: list[RatchetEntry], fresh: list[ScoredRow],
                   previous: dict[tuple[str, str], float], *,
                   max_jump: float) -> list[TightenRefusal]:
    """Marks whose measurement moved too far between two runs of the SAME commit.

    A tighten claims the code improved. One commit measured twice cannot have
    improved, so a score that jumped past `max_jump` in either direction is
    reporting the measurement's own noise: the lucky half lowers the mark and
    the unlucky half then fails it, and the gate becomes a coin flip on an
    unchanged tree. Only marked functions can be tightened, so only they are
    walked; a key the earlier run never scored has nothing to disagree with.
    """
    worst = rows_by_key(fresh)
    refusals = []
    for entry in MarkIndex(prior).entries():
        key = mark_key(entry)
        row, was = worst.get(key), previous.get(key)
        if row is None or was is None or not _jumped(was, round(row.crap, 4), max_jump):
            continue
        refusals.append(TightenRefusal(entry.path, entry.long_name, was, round(row.crap, 4)))
    return refusals


def update_ratchet(prior: list[RatchetEntry], fresh: list[ScoredRow], *, target: int,
                   scope_targets: dict[str, int] | None = None,
                   hold: frozenset[tuple[str, str]] = frozenset()) -> list[RatchetEntry]:
    """`hold` names keys whose mark this run may not move — `unstable_marks`
    picks them. A held mark keeps its recorded value, drop included: leaving the
    file is the deepest tighten there is. No mark rises and none is added
    (`invariants.check_marks_kept`).

    The update rewrites lines, not keys: a key the file lists twice keeps both
    lines in the order they came, each tightened or dropped against the same
    fresh row. The first line stays the one `keys.MarkIndex` answers, and
    `ratchet_delta` counts the key once."""
    fresh_by_key = rows_by_key(fresh)
    ceilings = scope_targets or {}
    updated = []
    for entry in prior:
        key = mark_key(entry)
        kept = _updated_mark(entry, fresh_by_key.get(key), key in hold,
                             lambda scope: ceilings.get(scope, target))
        if kept is not None:
            updated.append(kept)
    updated.sort(key=lambda e: (e.path, e.long_name))
    check_marks_kept(prior, updated, adds=False)
    return updated


def _updated_mark(entry: RatchetEntry, row: ScoredRow | None, held: bool,
                  ceiling_of) -> RatchetEntry | None:
    """One mark after the update, or None when the function now sits at or
    under its scope's ceiling and needs no mark."""
    if row is None or held:
        # Two ways a mark passes through untouched. Absent from the scored
        # rows is NOT proof the code is gone — an exclude glob or a lane
        # outage also removes it, and dropping the entry would erase an
        # audited override's only diff-visible record; stale entries are
        # inert (verify checks only present functions). Held is a
        # measurement this run cannot vouch for.
        return entry
    if not over_ceiling(row.crap, ceiling_of(row.scope)):
        return None  # fixed for real: below the scope's ceiling needs no mark
    return RatchetEntry(entry.path, entry.long_name, min(entry.crap, round(row.crap, 4)))


class RatchetDelta(NamedTuple):
    """What one update did to the marks: how many left the file, how many fell."""
    dropped: int
    tightened: int


def _mark_move(entry: RatchetEntry, fresh: float | None) -> str:
    """One mark's move under an update: dropped, tightened or kept."""
    if fresh is None:
        return "dropped"
    return "tightened" if fresh < entry.crap else "kept"


def ratchet_delta(prior: list[RatchetEntry], updated: list[RatchetEntry]) -> RatchetDelta:
    """The two counts a green verify reports. A mark never rises and an update
    adds none, so dropped plus tightened is the whole difference between the
    two lists; a mark that kept its value counts as neither. Each key counts
    once, under the mark `keys.MarkIndex` answers for it."""
    after = MarkIndex(updated)
    moves = [_mark_move(e, after.mark(mark_key(e))) for e in MarkIndex(prior).entries()]
    return RatchetDelta(moves.count("dropped"), moves.count("tightened"))
