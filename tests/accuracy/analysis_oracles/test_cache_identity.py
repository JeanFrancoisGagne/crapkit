"""Analysis cache and stat index: a warm run reads what a cold run of the same bytes reads.

The oracle is a cold analysis: the same files in a fresh repo with no
.crapkit/, so no cache entry and no stat stamp exists. README ("inventory"):
one lizard pass over every in-scope file, cached by content hash. The cache
module's docstring (src/crapkit/cache.py): a fingerprint change drops the whole
cache. From 0.9.0 the fingerprint names crapkit's and lizard's versions, and
each entry's key names the analysis number of its file's language
(src/crapkit/analyze.py, ANALYSIS_VERSIONS), so a raise of one language's number
drops that language's entries and serves the rest. Through 0.8.1 the
fingerprint named the one analysis version.

A warm run must equal the cold one after every kind of change: an edit, a
touch that keeps the bytes, a rename, equal bytes under two languages, a
version bump, and an edit that lands while crapkit hashes the file
(test_cache_race_seam.py, which wraps crapkit's hash for that seam). The
version-bump check first shows the cache is read (poisoned entries under the
current fingerprint come back), so a pass is not a cache nobody consulted.
Records the reader before whole template literals wrote (R45, stamped
`cache=5`) read cold too; the nightly state machine mixes them with the other
changes and counts the steps that held.

Self-diff: after every step of the scripted walk, the cache file crapkit
streams one entry at a time equals, byte for byte, json.dumps(document,
sort_keys=True) of the same document, and holds the records that run exported.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import NamedTuple

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
import pytest

from accuracy.analysis_oracles import analysis_inventory, analysis_tables
from accuracy.corpus_goldens import surface_expect
from accuracy.kit import drive, repos, surfaces
from accuracy.kit.settings import process

pytestmark = pytest.mark.process

CACHE = Path(".crapkit") / "cache.json"
# FunctionRecord columns 4, 5, 6 and 10: ccn_std, ccn_mod, ccn, cognitive.
POISONED = (4, 5, 6, 10)
POISON = 100


class Warm:
    """One repo kept across runs, so its cache and stat stamps stay warm."""

    def __init__(self, files: dict, work: Path, spawn: bool = False):
        self.files = {"crapkit.toml": analysis_inventory.config(), **files}
        self.work, self.spawn = work, spawn
        self.root = analysis_inventory.build(self.files, work / "repo")
        self.runs = 0

    def write(self, path: str, content) -> None:
        self.files[path] = content
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)

    def move(self, old: str, new: str) -> None:
        self.write(new, self.files.pop(old))
        (self.root / old).unlink()

    def run(self):
        repos.git(self.root, "add", "-A")
        self.runs += 1
        measured = analysis_inventory.run_inventory(self.root, self.work / f"run{self.runs}.tsv",
                                                  spawn=self.spawn)
        assert measured.code == 0, measured.stderr
        return measured

    def inventory(self) -> dict:
        """`inventory --json`: the files it analyzed and how many came from the cache."""
        return json.loads(drive.Driver(self.root).run("inventory", "--json").stdout)

    def cache_hits(self) -> int:
        return self.inventory()["cache_hits"]


def cold(files: dict, work: Path, spawn: bool = False):
    measured = analysis_inventory.measure(files, work, spawn=spawn)
    assert measured.code == 0, measured.stderr
    return measured


def _cold_of(warm: Warm, work: Path):
    return cold({path: data for path, data in warm.files.items()}, work, warm.spawn)


def _rewrite_cache(root: Path, change) -> None:
    path = root / CACHE
    cache = json.loads(path.read_text(encoding="utf-8"))
    change(cache)
    path.write_text(json.dumps(cache), encoding="utf-8")


def _poison(cache: dict, language: str | None = None) -> None:
    """Every entry's records, or only those keyed under `language`'s number."""
    for key, records in cache["entries"].items():
        if language is not None and language not in _key_numbers(key):
            continue
        for record in records:
            for column in POISONED:
                record[column] += POISON


# An entry key's analysis numbers, `<language>-analysis=<number>` comma-joined,
# sit between the reader's fields and the content hash (src/crapkit/analyze.py
# _analysis_key); a key from a crapkit through 0.8.1 holds none.
NUMBER = r"(?<=[:,])({language})-analysis=(\d+)(?=[:,])"


def _key_numbers(key: str) -> dict:
    return {found[1]: int(found[2]) for found in re.finditer(NUMBER.format(language="[a-z]+"), key)}


def _older_number(cache: dict, language: str = "[a-z]+") -> None:
    """Each entry keyed under `language`'s number (every language's by default)
    as a crapkit with an older number keyed it."""
    pattern = re.compile(NUMBER.format(language=language))
    cache["entries"] = {pattern.sub(r"\1-analysis=\2-older", key): records
                        for key, records in cache["entries"].items()}


def _raise_go(cache: dict) -> None:
    """The cache a raise of the go number alone meets: the Go entries keyed under
    the older number, and poisoned, so a Go record served from it shows."""
    _poison(cache, "go")
    _older_number(cache, "go")


# --- warm equals cold on the probe files --------------------------------------------------------

@pytest.fixture(scope="module")
def probe_files():
    return analysis_tables.probe_files()


def test_warm_equals_cold_on_every_probe_file(probe_files, tmp_path):
    # More files than the pool threshold, so each run is spawned: the in-process
    # runner refuses a call that starts the analysis pool.
    warm = Warm(probe_files, tmp_path / "warm", spawn=True)
    first = warm.run()
    second = warm.run()
    # Every file the run analyzed comes from the cache, one that defines no
    # function included: java/Element.java's annotation element has no row.
    report = warm.inventory()
    assert report["cache_hits"] == report["files"] >= len({row["path"] for row in second.rows})
    assert second.rows == first.rows == _cold_of(warm, tmp_path / "cold").rows


# --- the fingerprint (R01, R12) -------------------------------------------------------------------

SMALL = {"a.py": "def a(x):\n    if x:\n        return 1\n    return 2\n",
         "b.ts": "export function b(x: number): number {\n  return x > 0 ? 1 : 2;\n}\n",
         "c.go": "package c\n\nfunc C(x int) int {\n\tif x > 0 {\n\t\treturn 1\n\t}\n\treturn 2\n}\n"}
TWO_LANGUAGES = "function f(a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\n"


def _edit(text: str, number: int) -> str:
    return text + f"\n\ndef added{number}(x):\n    if x:\n        return {number}\n    return 0\n"


@pytest.fixture
def warmed(tmp_path):
    warm = Warm(SMALL, tmp_path / "warm")
    warm.run()
    return warm


def test_the_fingerprint_names_the_analysis_and_lizard_versions(warmed):
    """The cache names the analysis version: in the fingerprint through 0.8.1
    (`analysis=N`), and from 0.9.0 per language in every entry's key."""
    cache = json.loads((warmed.root / CACHE).read_text(encoding="utf-8"))
    fp, keys = cache["fp"], list(cache["entries"])
    in_fp = re.search(r"(^|;)analysis=\d+(;|$)", fp)
    assert in_fp or (keys and all(map(_key_numbers, keys))), (fp, keys)
    assert f"lizard={metadata.version('lizard')}" in fp.split(";"), fp


def test_each_entry_key_names_its_language_number_and_the_fingerprint_none(warmed):
    cache = json.loads((warmed.root / CACHE).read_text(encoding="utf-8"))
    named = {language for key in cache["entries"] for language in _key_numbers(key)}
    languages = {analysis_inventory.SUFFIX_LANGUAGE[Path(path).suffix]
                 for path in analysis_inventory.retro_tree(SMALL)}
    assert named == languages, list(cache["entries"])
    assert all(len(_key_numbers(key)) == 1 for key in cache["entries"]), list(cache["entries"])
    assert not re.search(r"(^|;)analysis=", cache["fp"]), cache["fp"]


def test_poisoned_entries_under_the_same_fingerprint_are_served(warmed):
    """The precondition: the cache is read, so a poisoned entry shows."""
    before = warmed.run().rows
    _rewrite_cache(warmed.root, _poison)
    served = warmed.run().rows
    assert [row["ccn"] + POISON for row in before] == [row["ccn"] for row in served]


@pytest.mark.parametrize("field", ["analysis", "lizard", "crapkit"])
def test_version_bump_reads_cold(field, warmed, tmp_path):
    def bump(cache):
        _poison(cache)
        cache["fp"] = re.sub(rf"(^|;){field}=([^;]*)", rf"\g<1>{field}=\g<2>-older", cache["fp"])
        if field == "analysis":  # from 0.9.0 each entry's key holds its language's number
            _older_number(cache)
    _rewrite_cache(warmed.root, bump)
    assert warmed.run().rows == _cold_of(warmed, tmp_path / "cold").rows
    # One hit per file the repo holds: a retro replay cuts SMALL to the languages
    # its commit reads (CRAPKIT_ACCURACY_LANGUAGES), and a normal run keeps all three.
    assert warmed.cache_hits() == len(analysis_inventory.retro_tree(SMALL))


def test_a_torn_cache_file_reads_cold(warmed, tmp_path):
    path = warmed.root / CACHE
    path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])
    assert warmed.run().rows == _cold_of(warmed, tmp_path / "cold").rows


# --- a raise of one language's number -----------------------------------------------------------

def _python(rows) -> list:
    return [row for row in rows if Path(row["path"]).suffix.lower() == ".py"]


def test_a_go_only_raise_reads_the_go_file_again_and_serves_the_rest(warmed, tmp_path):
    """After a raise of the go number alone, the Python rows come from the cache
    and equal a cold run's, and the poisoned Go entry is not served."""
    _rewrite_cache(warmed.root, _raise_go)
    warm_rows = warmed.run().rows
    cold_rows = _cold_of(warmed, tmp_path / "cold").rows
    assert _python(warm_rows) == _python(cold_rows) != []
    assert warm_rows == cold_rows
    _rewrite_cache(warmed.root, _raise_go)
    files = analysis_inventory.retro_tree(SMALL)
    assert warmed.cache_hits() == len(files) - sum(Path(path).suffix == ".go" for path in files)


# Stock lizard in an interpreter that imports no crapkit, so no crapkit reader
# correction is registered: [path, start, standard ccn, modified ccn] per function.
STOCK_LIZARD = """\
import json, sys
import lizard
plain = lizard.FileAnalyzer(lizard.get_extensions([]))
modified = lizard.FileAnalyzer(lizard.get_extensions(["modified"]))
print(json.dumps([[path, std.start_line, std.cyclomatic_complexity, mod.cyclomatic_complexity]
                  for path in sys.argv[1:]
                  for std, mod in zip(plain(path).function_list, modified(path).function_list)]))
"""
# (path, start) of each small-corpus row where crapkit's reader corrects stock
# lizard (read against 1.24.0, the release pyproject.toml pins), with the calc that pins the correction: defs whose body sits on the
# colon line, which stock lizard does not list (oneline.py, grades.py's pick,
# requests' @overload stubs), and signatures that run past their first `)`,
# which stock lizard ends early at ccn 1. Every Go row agrees with stock lizard.
PYTHON_READER = "Python reader: spans, names, inline_body, unread-def net"
CORRECTIONS = {(path, start): PYTHON_READER for path, start in (
    ("src/py/grades.py", 46), ("src/py/oneline.py", 4), ("src/py/oneline.py", 7),
    ("src/py/oneline.py", 10), ("src/py/signatures.py", 9), ("src/py/signatures.py", 18),
    ("src/py/signatures.py", 34), ("vendor/requests/cookies.py", 564),
    ("vendor/requests/cookies.py", 572))}


def _stock_lizard(root: Path, paths: list[str]) -> dict:
    done = subprocess.run([sys.executable, "-I", "-c", STOCK_LIZARD, *paths], cwd=root,
                          capture_output=True, text=True, check=True)
    found: dict = {}
    for path, start, std, mod in json.loads(done.stdout):
        found.setdefault((path, start), []).append((std, mod))
    return found


def _at(row: dict) -> tuple[str, int]:
    return row["path"], int(row["start"])


def _calc_names() -> set:
    tables = Path(__file__).resolve().parents[1].glob("*/calcs.tsv")
    return {line.split("\t", 1)[0] for table in tables
            for line in table.read_text(encoding="utf-8").splitlines()[1:]}


def _go_sources(root: Path) -> list[str]:
    """The .go files the small corpus's scopes take (its exclude globs drop tests/ and recorded/)."""
    paths = (path.relative_to(root) for path in root.rglob("*.go"))
    return [path.as_posix() for path in paths if path.parts[0] in ("src", "vendor")]


def _scored_after_go_raise(small_corpus, work: Path) -> tuple[Path, list[dict]]:
    root = small_corpus.private_copy(work / "repo")
    driver = drive.Driver(root, date_now=small_corpus.date_now, spawn=True)
    _rewrite_cache(root, _raise_go)
    report = json.loads(driver.run("inventory", "--json").stdout)
    assert report["cache_hits"] == report["files"] - len(_go_sources(root)), report
    scored = driver.run("coverage", "--export", (work / "scored.tsv").as_posix())
    assert scored.code == 0, scored.stderr
    return root, surfaces.read_tsv((work / "scored.tsv").read_text(encoding="utf-8"))[1]


def test_after_a_go_only_raise_the_rows_agree_with_coverage_py_and_stock_lizard(small_corpus,
                                                                                tmp_path):
    """The warm run after a raise of the go number alone, against tools that
    share no code with crapkit: each measured Python row's cov is the ratio
    coverage.py's own JSON report gives (oracles/corpus_counts.py reads it with
    json.load), and each Python and Go row's ccn_std and ccn are stock lizard's
    standard and modified counts, except the rows in CORRECTIONS."""
    root, rows = _scored_after_go_raise(small_corpus, tmp_path)

    measured = [(row, item) for row, item in zip(rows, surface_expect.per_row(rows))
                if item.flag == "measured" and row in _python(rows)]
    assert measured
    assert [(row["path"], row["start"], row["cov"], float(item.cov)) for row, item in measured
            if float(row["cov"]) != float(item.cov)] == []

    read = [row for row in rows if Path(row["path"]).suffix.lower() in (".py", ".go")]
    stock = _stock_lizard(root, sorted({row["path"] for row in read}))
    off = {_at(row) for row in read
           if (int(row["ccn_std"]), int(row["ccn"])) not in stock.get(_at(row), [])}
    shown = "\n".join(f"{row['path']}:{row['start']} {row['long_name']} ccn_std {row['ccn_std']} "
                      f"ccn {row['ccn']}, stock lizard {stock.get(_at(row))}"
                      for row in read if _at(row) in off ^ CORRECTIONS.keys())
    assert sorted(off) == sorted(CORRECTIONS), shown
    assert set(CORRECTIONS.values()) <= _calc_names()


# --- a cache from the reader before whole template literals (R45) ---------------------------------

# docs/upgrading.md, version 11: lizard ended a template at the first backtick
# inside it, so a template nested in another's `${...}` hid every function after
# it, folded into the function around it. That reader stamped its cache `cache=5`
# (the fingerprint names the cache version), and read at 89ee5b8 it lists `a`
# alone, lines 1 to 10. Records it wrote must read cold under today's reader.
NESTED = ("export function a(x) {\n  return `outer ${`inner ${x}`} tail`;\n}\n\n"
          "export function b(y) {\n  if (y > 0) {\n    return 1;\n  }\n  return 2;\n}\n")
# (name, start, end, ccn) read off NESTED; ccn is NIST SP 500-235 sec. 4.1, 1 + one if.
NESTED_HAND = [("a", 1, 3, 1), ("b", 5, 10, 2)]
END = 3  # FunctionRecord column 3: the last line


def _folded(cache: dict) -> None:
    """Each entry as the older reader wrote it: one record over the whole file."""
    for key, records in cache["entries"].items():
        cache["entries"][key] = [records[0][:END] + [records[-1][END]] + records[0][END + 1:]]


def _older_stamp(cache: dict) -> None:
    cache["fp"], count = re.subn(r"(^|;)cache=\d+", r"\g<1>cache=5", cache["fp"])
    assert count == 1, f"the fingerprint names no cache version: {cache['fp']}"


def _hand(measured) -> list:
    return [(analysis_inventory.bare(row["long_name"]), row["start"], row["end"], row["ccn"])
            for row in measured.rows]


def test_a_cache_from_the_reader_before_whole_templates_reads_cold(tmp_path):
    warm = Warm({"mod.ts": NESTED}, tmp_path / "warm")
    cold_run = warm.run()  # the repo's first run has no cache: a cold run
    assert _hand(cold_run) == NESTED_HAND
    _rewrite_cache(warm.root, _folded)
    # The precondition: a folded entry under today's stamp is served.
    assert [row[0] for row in _hand(warm.run())] == ["a"]
    _rewrite_cache(warm.root, lambda cache: (_folded(cache), _older_stamp(cache)))
    assert warm.run().rows == cold_run.rows


# --- equal bytes under two readers (R30) --------------------------------------------------------

# An unparenthesized arrow body with `<` before a comma: a comparison in
# JavaScript, and the TypeScript readers' documented refusal
# (src/crapkit/lizardtypescript.py module text).
AMBIGUOUS = "export const pair = [(a) => a < 1, (b) => b];\n"


@pytest.mark.parametrize("first, second", [("a.jsx", "b.tsx"), ("a.js", "b.ts"),
                                           ("a.tsx", "b.jsx")])
def test_jsx_warm_record_cannot_bypass_tsx_refusal(first, second, tmp_path):
    warm = Warm({first: AMBIGUOUS}, tmp_path / "warm")
    warm.run()
    warm.write(second, AMBIGUOUS)
    got = warm.run()
    alone = cold({second: AMBIGUOUS}, tmp_path / "alone")
    assert got.rows == _cold_of(warm, tmp_path / "cold").rows
    assert got.in_file(second) == alone.in_file(second)


def test_the_two_readers_read_the_ambiguous_bytes_differently(tmp_path):
    """The precondition: the bytes above are read differently by the two
    readers, so the check above would see a record served across them."""
    both = cold({"a.jsx": AMBIGUOUS, "b.tsx": AMBIGUOUS}, tmp_path)
    assert (len(both.in_file("a.jsx")), len(both.in_file("b.tsx"))) == (2, 0)


# --- a scripted walk through every change kind (push) -----------------------------------------------

def _bump(cache: dict) -> None:
    cache["fp"] += "-older"


def _walk(warm: Warm) -> list:
    """Each step's change, applied in order; returned for the failure message."""
    return [("edit", lambda: warm.write("a.py", _edit(warm.files["a.py"], 1))),
            ("touch", lambda: os.utime(warm.root / "b.ts", ns=(1, 2_000_000_000_000_000_000))),
            ("rename", lambda: warm.move("c.go", "moved/c.go")),
            ("two languages", lambda: (warm.write("d.js", TWO_LANGUAGES),
                                       warm.write("d.ts", TWO_LANGUAGES))),
            ("version bump", lambda: _rewrite_cache(warm.root, _bump)),
            ("revert", lambda: warm.write("a.py", SMALL["a.py"]))]


class Step(NamedTuple):
    name: str
    rows: tuple  # the warm run's
    cold_rows: tuple
    save: bytes  # .crapkit/cache.json after the warm run
    digests: dict  # path -> SHA-256 of the bytes it holds


def _step(name: str, warm: Warm, measured, cold_run) -> Step:
    digests = {path: hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data)
               .hexdigest() for path, data in warm.files.items() if path != "crapkit.toml"}
    return Step(name, measured.rows, cold_run.rows, (warm.root / CACHE).read_bytes(), digests)


@pytest.fixture(scope="module")
def walked(tmp_path_factory):
    """Every step of the walk, the repo's first run (a cold one) included."""
    work = tmp_path_factory.mktemp("walk")
    warm = Warm(SMALL, work / "warm")
    first = warm.run()
    steps = [_step("first run", warm, first, first)]
    for number, (name, change) in enumerate(_walk(warm)):
        change()
        measured = warm.run()
        steps.append(_step(name, warm, measured, _cold_of(warm, work / f"cold{number}")))
    return steps


def test_warm_equals_cold_after_every_step(walked):
    for step in walked:
        assert step.rows == step.cold_rows, step.name


# --- self-diff: the streamed save against the whole-document dump -------------------------------

def _document_dump(raw: bytes) -> bytes:
    """What json.dumps(document, sort_keys=True) writes for the same document:
    the whole-document form the streamed writer must reproduce byte for byte
    (src/crapkit/analyze.py _write_entries)."""
    return json.dumps(json.loads(raw.decode("utf-8")), sort_keys=True).encode("utf-8")


# FunctionRecord's first twelve columns: the export's columns after `scope`, in its order.
RECORD = ("path", "long_name", "start", "end", "ccn_std", "ccn_mod", "ccn", "nloc", "params",
          "nesting", "cognitive", "occurrence")


def _entries_as_rows(save: bytes, digests: dict) -> list:
    """Every cached record, after checking its entry key ends in the SHA-256 of
    the bytes its path holds (README: cached by content hash)."""
    rows = []
    for key, records in json.loads(save)["entries"].items():
        for record in records:
            assert key.endswith(":" + digests[record[0]]), (key, record[0])
            rows.append(tuple(record[: len(RECORD)]))
    return sorted(rows)


def _distinct_inputs(digests: dict) -> int:
    """One entry per reader and content: the README language a suffix names,
    with the bytes' hash."""
    return len({(analysis_inventory.SUFFIX_LANGUAGE[Path(path).suffix], digest)
                for path, digest in digests.items()})


def test_the_streamed_cache_equals_the_whole_document_dump(walked):
    """After every step of the walk, the cache crapkit wrote one entry at a time
    is the dump's exact bytes, holds one entry per reader and content, and
    holds the records that run exported."""
    for step in walked:
        exported = sorted(tuple(row[column] for column in RECORD) for row in step.rows)
        assert step.save == _document_dump(step.save), step.name
        assert len(json.loads(step.save)["entries"]) == _distinct_inputs(step.digests), step.name
        assert _entries_as_rows(step.save, step.digests) == exported, step.name


# --- the state machine (nightly) --------------------------------------------------------------------

PATHS = ("a.py", "b.ts", "c.go", "d.js", "d.ts", "e/f.py")


KINDS = ("edit", "touch", "rename", "two languages", "version bump", "older template cache",
         "warm equals cold")


class CacheMachine(RuleBasedStateMachine):
    """Edits, touches, renames, equal bytes in two languages, version bumps and
    records from the reader before whole template literals, in any order; after
    each, the warm repo's rows equal a cold run's. `seen` counts each rule and
    each step that held, across every example of a run."""

    seen: dict = {}

    def __init__(self):
        super().__init__()
        self.base = Path(os.environ["CRAPKIT_CACHE_MACHINE_DIR"])
        self.base.mkdir(parents=True, exist_ok=True)
        self.number = len(list(self.base.iterdir()))
        self.warm = Warm(dict(SMALL), self.base / f"m{self.number}")
        self.warm.run()
        self.steps = 0

    def _saw(self, kind: str) -> None:
        CacheMachine.seen[kind] = CacheMachine.seen.get(kind, 0) + 1

    @rule(number=st.integers(0, 9))
    def edit(self, number):
        self._saw("edit")
        self.warm.write("a.py", _edit(self.warm.files["a.py"], number))

    @rule(path=st.sampled_from(PATHS))
    def touch(self, path):
        self._saw("touch")
        if path in self.warm.files:
            self.warm.write(path, self.warm.files[path])

    @rule(target_path=st.sampled_from(("moved/c.go", "c.go", "e/c.go")))
    def rename(self, target_path):
        self._saw("rename")
        current = next((path for path in self.warm.files if path.endswith("c.go")), None)
        if current is not None and current != target_path:
            self.warm.move(current, target_path)

    @rule()
    def two_languages(self):
        self._saw("two languages")
        self.warm.write("d.js", TWO_LANGUAGES)
        self.warm.write("d.ts", TWO_LANGUAGES)

    @rule()
    def version_bump(self):
        self._saw("version bump")
        _rewrite_cache(self.warm.root, _bump)

    @rule()
    def older_template_cache(self):
        self._saw("older template cache")
        _rewrite_cache(self.warm.root, lambda cache: (_folded(cache), _older_stamp(cache)))

    @invariant()
    def warm_equals_cold(self):
        self.steps += 1
        cold_rows = _cold_of(self.warm, self.base / f"m{self.number}-cold{self.steps}").rows
        assert self.warm.run().rows == cold_rows
        self._saw("warm equals cold")


CacheMachine.TestCase.settings = process


@pytest.mark.nightly
def test_cache_machine(tmp_path, monkeypatch):
    """Prints each rule's count and the steps that held (-s shows it)."""
    monkeypatch.setenv("CRAPKIT_CACHE_MACHINE_DIR", str(tmp_path))
    CacheMachine.seen = {}
    CacheMachine.TestCase().runTest()
    print(f"cache machine: {json.dumps(CacheMachine.seen, sort_keys=True)}")
    assert sorted(CacheMachine.seen) == sorted(KINDS)
