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
`metric_version` hand it out, and every caller compares and renders that value
instead of reading the lines itself.
"""
from __future__ import annotations

import json
import math
import re
from typing import ClassVar, NamedTuple

from .invariants import check_dump, check_marks_kept
from .invocation import _self
from .keys import MarkIndex, mark_key, rows_by_key, split_ordinal
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


def stamp_text(analysis_version: int | str, lizard_version: str) -> str:
    """The metric identity a set of marks was measured under, as one line."""
    return f"crapkit-analysis={analysis_version} lizard={lizard_version}"


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
    """`crapkit-analysis=N lizard=X.Y.Z`: one analysis number for every language."""

    def __new__(cls, analysis: int, lizard: str) -> SingleNumberStamp:
        stamp = super().__new__(cls, stamp_text(analysis, lizard))
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
    """The metric a stored run was measured under; NONE for a run that recorded none."""
    analysis, lizard = tool_versions.get("analysis_version"), tool_versions.get("lizard")
    if not (analysis and lizard):
        return MetricStamp.NONE
    return MetricStamp.parse(stamp_text(analysis, lizard))


def metric_version() -> MetricStamp:
    # Imported here, not at module scope: the git merge driver runs this module
    # in a temp dir with no analysis stack, and it passes its own stamp through.
    import lizard

    from .analyze import ANALYSIS_VERSION
    return SingleNumberStamp(ANALYSIS_VERSION, lizard.version)


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


def stamp_conflict(recorded: MetricStamp, current: MetricStamp) -> str | None:
    """The refusal when marks and the running metric disagree; None when they compare.

    An unstamped file has nothing to disagree with: the caller warns instead.
    Marks a newer crapkit wrote are sent to an upgrade: the stock
    coverage-then-seed would restamp them under this older metric, and every
    teammate on the newer release would then be refused. Marks a newer lizard
    stamped take that reseed, since the exact lizard pin admits no upgrade.
    """
    if not recorded or recorded == current:
        return None
    head = f"ratchet marks were recorded under [{recorded}] but this run measures [{current}]"
    newer = newer_tools(recorded, current)
    if newer:
        return (f"{head} - {upgrade_remedy(newer)} rather than re-seed, which would restamp the "
                "team's marks backwards and make every upgraded teammate's verify refuse them")
    return (f"{head} - CRAP scores are not comparable across metric versions; "
            f"{coverage_then_seed()}")


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


def _stamp_versions(stamp: str) -> dict[str, tuple[int, ...]]:
    """`crapkit-analysis=11 lizard=1.24.0` as {field: (11,), ...}: numbers compare
    as numbers, so lizard 1.9.0 is older than 1.24.0. A word with no `=` is left out."""
    fields = (word.partition("=") for word in stamp.split())
    return {name: tuple(int(n) for n in re.findall(r"\d+", value))
            for name, sep, value in fields if sep}


def coverage_then_seed(rebaseline: str = "re-baseline") -> str:
    """The remedy every stamp refusal prints: a fresh run, a prune, then the seed.

    Seed stamps the metric of the run it reads. Right after an upgrade the
    newest run is the older crapkit's, so seed alone kept the old stamp and
    verify refused again. A new metric can also move a function's key (a
    UTF-16 source, a name holding a byte cp1252 leaves undefined), and seed
    never drops a mark, so prune goes between them to drop the mark left under
    the old key, as the upgrade guide says.
    """
    return (f"run `{_self()} coverage`, then `{_self()} ratchet prune`, then {rebaseline} "
            f"with `{_self()} ratchet seed`")


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
    b, o, t = MarkIndex(base), MarkIndex(ours), MarkIndex(theirs)
    merged = []
    for key in sorted(b.keys() | o.keys() | t.keys()):
        crap = _merge_key(b.mark(key), o.mark(key), t.mark(key))
        if crap is not None:
            merged.append(RatchetEntry(key[0], key[1], crap))
    return merged


def seed_ratchet(prior: list[RatchetEntry], fresh: list[ScoredRow], *, target: int,
                 scope_targets: dict[str, int] | None = None) -> tuple[list[RatchetEntry], int, int]:
    """First-class mark entry: record every over-ceiling function at its current
    CRAP. A mark never rises, seeding included — an existing lower mark stays."""
    ceilings = scope_targets or {}
    marks = MarkIndex(prior).working_copy()
    added = tightened = 0
    for key, row in rows_by_key(fresh).items():
        if not over_ceiling(row.crap, ceilings.get(row.scope, target)):
            continue
        a, t = _seed_mark(marks, key, row.crap)
        added += a
        tightened += t
    seeded = sorted(marks.values(), key=lambda e: (e.path, e.long_name))
    check_marks_kept(prior, seeded, adds=True)
    return seeded, added, tightened


def _seed_mark(marks: dict, key: tuple, crap: float) -> tuple[int, int]:
    """Place one mark; returns (added, tightened) deltas. A mark never rises."""
    old = marks.get(key)
    mark = round(crap, 4)
    if old is not None and mark >= old.crap:
        return 0, 0
    marks[key] = RatchetEntry(key[0], key[1], mark)
    return (1, 0) if old is None else (0, 1)


def _repath(entries: list[RatchetEntry], dest_of) -> tuple[list[RatchetEntry], int]:
    """Rewrite paths by a per-mark chooser (None leaves a mark alone); values never move."""
    out = []
    moved = 0
    for e in entries:
        dest = dest_of(e)
        if dest is None:
            out.append(e)
            continue
        out.append(e._replace(path=dest))
        moved += 1
    return sorted(out, key=lambda e: (e.path, e.long_name)), moved


def _moved_path(path: str, old: str, new: str) -> str | None:
    """One mark's destination under an explicit move, or None when `old` misses it."""
    if old.endswith("/"):
        return f"{new.rstrip('/')}/{path[len(old):]}" if path.startswith(old) else None
    return new if path == old else None


def move_marks(entries: list[RatchetEntry], old: str,
               new: str) -> tuple[list[RatchetEntry], int]:
    """Re-path marks at their recorded values. A trailing "/" on `old` moves a whole
    directory; anything else matches one exact path."""
    return _repath(entries, lambda e: _moved_path(e.path, old, new))


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
    return _repath(prior, lambda e: _rename_target(e, present, renames))


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
