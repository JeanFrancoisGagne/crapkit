"""Analysis cache and stat index: a warm run reads what a cold run of the same bytes reads.

The oracle is a cold analysis: the same files in a fresh repo with no
.crapkit/, so no cache entry and no stat stamp exists. README ("inventory"):
one lizard pass over every in-scope file, cached by content hash. The cache
module's docstring (src/crapkit/cache.py): the fingerprint bundles everything
that changes analysis output for identical content (lizard pin, crapkit
analysis version), and a fingerprint change drops the whole cache.

A warm run must equal the cold one after every kind of change: an edit, a
touch that keeps the bytes, a rename, equal bytes under two languages, a
version bump, and an edit that lands while crapkit hashes the file. The
version-bump check first shows the cache is read (poisoned entries under the
current fingerprint come back), so a pass is not a cache nobody consulted.
"""
from __future__ import annotations

from importlib import metadata
import json
import os
from pathlib import Path
import re

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
import pytest

from accuracy.analysis_oracles import analysis_inventory, analysis_tables
from accuracy.kit import drive, repos
from accuracy.kit.settings import process

pytestmark = pytest.mark.process

CACHE = Path(".crapkit") / "cache.json"
# FunctionRecord columns 4, 5, 6 and 10: ccn_std, ccn_mod, ccn, cognitive.
POISONED = (4, 5, 6, 10)
POISON = 100


class Warm:
    """One repo kept across runs, so its cache and stat stamps stay warm."""

    def __init__(self, files: dict, work: Path):
        self.files = {"crapkit.toml": analysis_inventory.config(), **files}
        self.work = work
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
        measured = analysis_inventory.run_inventory(self.root, self.work / f"run{self.runs}.tsv")
        assert measured.code == 0, measured.stderr
        return measured

    def cache_hits(self) -> int:
        return json.loads(drive.Driver(self.root).run("inventory", "--json").stdout)["cache_hits"]


def cold(files: dict, work: Path):
    measured = analysis_inventory.measure(files, work)
    assert measured.code == 0, measured.stderr
    return measured


def _cold_of(warm: Warm, work: Path):
    return cold({path: data for path, data in warm.files.items()}, work)


def _rewrite_cache(root: Path, change) -> None:
    path = root / CACHE
    cache = json.loads(path.read_text(encoding="utf-8"))
    change(cache)
    path.write_text(json.dumps(cache), encoding="utf-8")


def _poison(cache: dict) -> None:
    for records in cache["entries"].values():
        for record in records:
            for column in POISONED:
                record[column] += POISON


# --- warm equals cold on the probe files --------------------------------------------------------

@pytest.fixture(scope="module")
def probe_files():
    return analysis_tables.probe_files()


def test_warm_equals_cold_on_every_probe_file(probe_files, tmp_path):
    warm = Warm(probe_files, tmp_path / "warm")
    first = warm.run()
    second = warm.run()
    assert warm.cache_hits() == len({row["path"] for row in second.rows})
    assert second.rows == first.rows == _cold_of(warm, tmp_path / "cold").rows


# --- the fingerprint (R01, R12) -------------------------------------------------------------------

SMALL = {"a.py": "def a(x):\n    if x:\n        return 1\n    return 2\n",
         "b.ts": "export function b(x: number): number {\n  return x > 0 ? 1 : 2;\n}\n",
         "c.go": "package c\n\nfunc C(x int) int {\n\tif x > 0 {\n\t\treturn 1\n\t}\n\treturn 2\n}\n"}


@pytest.fixture
def warmed(tmp_path):
    warm = Warm(SMALL, tmp_path / "warm")
    warm.run()
    return warm


def test_the_fingerprint_names_the_analysis_and_lizard_versions(warmed):
    fp = json.loads((warmed.root / CACHE).read_text(encoding="utf-8"))["fp"]
    assert re.search(r"(^|;)analysis=\d+(;|$)", fp), fp
    assert f"lizard={metadata.version('lizard')}" in fp.split(";"), fp


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
    _rewrite_cache(warmed.root, bump)
    assert warmed.run().rows == _cold_of(warmed, tmp_path / "cold").rows
    assert warmed.cache_hits() == len(SMALL)


def test_a_torn_cache_file_reads_cold(warmed, tmp_path):
    path = warmed.root / CACHE
    path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])
    assert warmed.run().rows == _cold_of(warmed, tmp_path / "cold").rows


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

TWO_LANGUAGES = "function f(a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\n"


def _edit(text: str, number: int) -> str:
    return text + f"\n\ndef added{number}(x):\n    if x:\n        return {number}\n    return 0\n"


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


def test_warm_equals_cold_after_every_step(tmp_path):
    warm = Warm(SMALL, tmp_path / "warm")
    warm.run()
    for step, (name, change) in enumerate(_walk(warm)):
        change()
        assert warm.run().rows == _cold_of(warm, tmp_path / f"cold{step}").rows, name


# --- an edit inside the hash call (R30) ------------------------------------------------------------

def test_edit_during_analysis_publishes_the_new_digest(monkeypatch, tmp_path):
    """The seam is crapkit's content hash: the wrapper hashes the old bytes and
    then writes new ones, as an editor saving mid-run would. Whatever the raced
    run answers, the next run must read the new bytes, as a cold run does."""
    import crapkit.analyze as analyze

    warm = Warm(SMALL, tmp_path / "warm")
    warm.run()
    edited = _edit(SMALL["a.py"], 7)
    hashed = analyze.content_hash

    def racing(path):
        digest = hashed(path)
        if path.name == "a.py" and path.read_text(encoding="utf-8") != edited:
            path.write_text(edited, encoding="utf-8")
        return digest

    warm.write("a.py", SMALL["a.py"] + "\n")
    monkeypatch.setattr(analyze, "content_hash", racing)
    raced = analysis_inventory.run_inventory(warm.root, tmp_path / "raced.tsv")
    monkeypatch.undo()
    assert (warm.root / "a.py").read_text(encoding="utf-8") == edited, "the race never ran"
    warm.files["a.py"] = edited
    after = cold(dict(warm.files), tmp_path / "cold")
    before = cold({**warm.files, "a.py": SMALL["a.py"] + "\n"}, tmp_path / "before")
    assert raced.code != 0 or raced.rows in (before.rows, after.rows)
    assert warm.run().rows == after.rows


# --- the state machine (nightly) --------------------------------------------------------------------

PATHS = ("a.py", "b.ts", "c.go", "d.js", "d.ts", "e/f.py")


class CacheMachine(RuleBasedStateMachine):
    """Edits, touches, renames, equal bytes in two languages and version bumps,
    in any order; after each, the warm repo's rows equal a cold run's."""

    def __init__(self):
        super().__init__()
        self.base = Path(os.environ["CRAPKIT_CACHE_MACHINE_DIR"])
        self.base.mkdir(parents=True, exist_ok=True)
        self.number = len(list(self.base.iterdir()))
        self.warm = Warm(dict(SMALL), self.base / f"m{self.number}")
        self.warm.run()
        self.steps = 0

    @rule(number=st.integers(0, 9))
    def edit(self, number):
        self.warm.write("a.py", _edit(self.warm.files["a.py"], number))

    @rule(path=st.sampled_from(PATHS))
    def touch(self, path):
        if path in self.warm.files:
            self.warm.write(path, self.warm.files[path])

    @rule(target_path=st.sampled_from(("moved/c.go", "c.go", "e/c.go")))
    def rename(self, target_path):
        current = next((path for path in self.warm.files if path.endswith("c.go")), None)
        if current is not None and current != target_path:
            self.warm.move(current, target_path)

    @rule()
    def two_languages(self):
        self.warm.write("d.js", TWO_LANGUAGES)
        self.warm.write("d.ts", TWO_LANGUAGES)

    @rule()
    def version_bump(self):
        _rewrite_cache(self.warm.root, _bump)

    @invariant()
    def warm_equals_cold(self):
        self.steps += 1
        cold_rows = _cold_of(self.warm, self.base / f"m{self.number}-cold{self.steps}").rows
        assert self.warm.run().rows == cold_rows


CacheMachine.TestCase.settings = process


@pytest.mark.nightly
def test_cache_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_CACHE_MACHINE_DIR", str(tmp_path))
    CacheMachine.TestCase().runTest()
