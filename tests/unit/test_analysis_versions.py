"""One analysis number per language and one version per coverage reader.

A change that moves Go's ccn raises the go number and nothing else, and the
analysis cache keys each file on its own language's number, so the next run
re-reads the Go files and serves every other file from the cache.
`ANALYSIS_VERSION` stays an int: the tables' revision, raised by one in any
change that raises a number. A new language enters at 13 and a new reader at
1, and neither raises anything (no existing function's score moves).
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import lizard
import pytest

import crapkit.analyze as analyze
from crapkit import coverage_format
from crapkit.analyze import analysis_version_of, analyze_files
from crapkit.cli import main
from crapkit.languages import LANGUAGE_EXTENSIONS

SRC = Path(analyze.__file__).resolve().parent

# --- the pin ----------------------------------------------------------------------

# (ANALYSIS_VERSION, ANALYSIS_VERSIONS, READER_VERSIONS) as this tree ships them.
PINNED = (13,
          {"cpp": 13, "go": 13, "java": 13, "javascript": 13, "objectivec": 13,
           "powershell": 13, "python": 13, "rust": 13, "shell": 13, "swift": 13, "tsx": 13,
           "typescript": 13, "vue": 13, "zig": 13},
          {"coveragepy": 1, "istanbul": 1})
FIRSTS = {"ANALYSIS_VERSIONS": 13, "READER_VERSIONS": 1}
RULE = ("raise the language's or reader's number and ANALYSIS_VERSION by one in one change, "
        "then update PINNED in tests/unit/test_analysis_versions.py")


def _table_breaks(name: str, old: dict, new: dict) -> list[str]:
    first = FIRSTS[name]
    dropped = [f"{name} dropped {key!r}, which a recorded stamp may still name"
               for key in sorted(old.keys() - new.keys())]
    added = [f"{name}[{key!r}] enters at {new[key]}, not {first}"
             for key in sorted(new.keys() - old.keys()) if new[key] != first]
    moved = [f"{name}[{key!r}] is {new[key]}, pinned {old[key]}"
             for key in sorted(old.keys() & new.keys()) if new[key] != old[key]]
    return dropped + added + moved


def pin_breaks(pinned: tuple, current: tuple) -> list[str]:
    """Each way `current` differs from `pinned` that the bump rule forbids, with
    the rule. A key the pin lacks passes at its table's first number."""
    revision = [] if current[0] == pinned[0] else [
        f"ANALYSIS_VERSION is {current[0]}, pinned {pinned[0]}"]
    tables = [line for name, old, new in zip(FIRSTS, pinned[1:], current[1:])
              for line in _table_breaks(name, old, new)]
    return [f"{line}: {RULE}" for line in revision + tables]


def current() -> tuple:
    return (analyze.ANALYSIS_VERSION, analyze.ANALYSIS_VERSIONS, coverage_format.READER_VERSIONS)


def test_the_tables_are_the_pinned_literal():
    assert pin_breaks(PINNED, current()) == []


def _moved(revision: int = 0, languages: dict | None = None, readers: dict | None = None,
           drop: str | None = None) -> tuple:
    version, analysis, reader = PINNED
    analysis = {**analysis, **(languages or {})}
    reader = {**reader, **(readers or {})}
    analysis.pop(drop, None)
    reader.pop(drop, None)
    return version + revision, analysis, reader


@pytest.mark.parametrize("moved", [
    _moved(languages={"kotlin": 13}),
    _moved(readers={"lcov": 1}),
    _moved(languages={"kotlin": 13, "csharp": 13}, readers={"lcov": 1, "cobertura": 1}),
], ids=["language", "reader", "both"])
def test_a_key_added_at_its_first_number_passes_with_the_revision_unchanged(moved):
    assert pin_breaks(PINNED, moved) == []


@pytest.mark.parametrize("moved, broken", [
    (_moved(languages={"kotlin": 14}), "ANALYSIS_VERSIONS['kotlin'] enters at 14, not 13"),
    (_moved(readers={"lcov": 2}), "READER_VERSIONS['lcov'] enters at 2, not 1"),
    (_moved(revision=1, languages={"kotlin": 13}), "ANALYSIS_VERSION is 14, pinned 13"),
    (_moved(languages={"go": 14}), "ANALYSIS_VERSIONS['go'] is 14, pinned 13"),
    (_moved(revision=1, languages={"go": 14}), "ANALYSIS_VERSIONS['go'] is 14, pinned 13"),
    (_moved(readers={"istanbul": 2}), "READER_VERSIONS['istanbul'] is 2, pinned 1"),
    (_moved(revision=1), "ANALYSIS_VERSION is 14, pinned 13"),
    (_moved(drop="zig"), "ANALYSIS_VERSIONS dropped 'zig'"),
    (_moved(drop="coveragepy"), "READER_VERSIONS dropped 'coveragepy'"),
], ids=["language-off-first", "reader-off-first", "added-with-a-raise", "raise-unpinned",
        "raise-with-revision-unpinned", "reader-raise-unpinned", "revision-alone", "language-dropped",
        "reader-dropped"])
def test_any_other_difference_fails_and_states_the_rule(moved, broken):
    breaks = pin_breaks(PINNED, moved)

    assert any(line.startswith(broken) for line in breaks), breaks
    assert all(line.endswith(RULE) for line in breaks), breaks


# --- completeness -----------------------------------------------------------------

def gaps(table: dict, universe) -> list[str]:
    return ([f"{key!r} has no entry" for key in sorted(set(universe) - set(table))]
            + [f"entry {key!r} names nothing" for key in sorted(set(table) - set(universe))])


def test_every_language_has_a_number_and_every_number_a_language():
    assert gaps(analyze.ANALYSIS_VERSIONS, LANGUAGE_EXTENSIONS) == [], (
        f"add the language to analyze.ANALYSIS_VERSIONS at {analyze.FIRST_ANALYSIS_VERSION}")


def test_every_coverage_reader_has_a_version_and_every_version_a_reader():
    assert gaps(coverage_format.READER_VERSIONS, coverage_format._FORMATS) == [], (
        f"add the reader to coverage_format.READER_VERSIONS at "
        f"{coverage_format.FIRST_READER_VERSION}")


def test_the_completeness_check_names_a_missing_and_a_stray_entry():
    assert gaps({"python": 13, "cobol": 13}, {"python": (".py",), "go": (".go",)}) == [
        "'go' has no entry", "entry 'cobol' names nothing"]


def test_the_first_numbers_are_the_pinned_ones():
    assert (analyze.FIRST_ANALYSIS_VERSION, coverage_format.FIRST_READER_VERSION) == (13, 1)


# --- read without importing crapkit ------------------------------------------------

def _literals(path: Path, names: set[str]) -> dict:
    """Each module-level `NAME = literal` among `names`, as change control reads
    ANALYSIS_VERSION: ast.literal_eval over the source, no import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and getattr(node.targets[0], "id", None) in names}


def test_the_tables_read_as_literals_from_the_source():
    analysis = _literals(SRC / "analyze.py", {"ANALYSIS_VERSION", "ANALYSIS_VERSIONS"})
    readers = _literals(SRC / "coverage_format.py", {"READER_VERSIONS"})

    assert (analysis["ANALYSIS_VERSION"], analysis["ANALYSIS_VERSIONS"],
            readers["READER_VERSIONS"]) == PINNED


# --- which numbers a path's key holds ---------------------------------------------

@pytest.mark.parametrize("path, pairs", [
    ("src/main.go", (("go", 13),)),
    ("src/MAIN.GO", (("go", 13),)),
    ("pkg/app.py", (("python", 13),)),
    ("web/a.jsx", (("javascript", 13),)),
    ("notes.txt", ()),
    ("Makefile", ()),
])
def test_a_path_names_the_number_of_each_language_claiming_its_suffix(path, pairs):
    assert analysis_version_of(path) == pairs


def test_no_suffix_is_claimed_twice():
    suffixes = [suffix for claimed in LANGUAGE_EXTENSIONS.values() for suffix in claimed]
    assert len(suffixes) == len(set(suffixes))


def test_an_unclaimed_suffix_keeps_the_reader_and_extension_chain_it_held():
    """The key's reader, chain and type-syntax fields are the ones the key held
    before the numbers joined it; the numbers field is empty."""
    reader = lizard.get_reader_for("notes.txt") or lizard.get_reader_for("fallback.c")
    chain = int(analyze._extensions_for("notes.txt") is analyze._PREPROCESSED_EXTENSIONS)
    held = f"{reader.__module__}.{reader.__qualname__}:{chain}:0"

    assert analyze._analysis_key("notes.txt", "d1") == f"{held}::d1"


GO = "package main\n\nfunc G{n}(x int) int {{\n\tif x > 0 {{\n\t\treturn {n}\n\t}}\n\treturn 0\n}}\n"
PY = "def f{n}(x):\n    if x:\n        return {n}\n    return 0\n"


def test_a_suffix_two_languages_claim_keys_on_both_numbers(tmp_path, monkeypatch):
    monkeypatch.setitem(LANGUAGE_EXTENSIONS, "golike", (".go",))
    monkeypatch.setitem(analyze.ANALYSIS_VERSIONS, "golike", 13)
    (tmp_path / "a.go").write_text(GO.format(n=1), encoding="utf-8")
    _, _, cache = analyze_files(tmp_path, ["a.go"], cache={})

    assert analysis_version_of("a.go") == (("go", 13), ("golike", 13))
    assert ":go-analysis=13,golike-analysis=13:" in analyze._analysis_key("a.go", "d1")
    for language in ("go", "golike"):
        with monkeypatch.context() as raised:
            raised.setitem(analyze.ANALYSIS_VERSIONS, language, 14)
            _, hits, _ = analyze_files(tmp_path, ["a.go"], cache=cache)
        assert hits == 0, language
    _, hits, _ = analyze_files(tmp_path, ["a.go"], cache=cache)
    assert hits == 1


def test_a_raise_of_the_revision_alone_serves_every_entry(tmp_path, monkeypatch):
    """The revision counts raises; the numbers it counts are what keys a record."""
    (tmp_path / "a.go").write_text(GO.format(n=1), encoding="utf-8")
    cold, _, cache = analyze_files(tmp_path, ["a.go"], cache={})
    monkeypatch.setattr(analyze, "ANALYSIS_VERSION", analyze.ANALYSIS_VERSION + 1)

    warm, hits, _ = analyze_files(tmp_path, ["a.go"], cache=cache)

    assert (hits, warm) == (1, cold)


# --- inventory re-reads the raised language alone ------------------------------------

TOML = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
        'languages = ["python", "go"]\n')
PY_FILES = ["src/p1.py", "src/p2.py", "src/p3.py"]
GO_FILES = ["src/g1.go", "src/g2.go"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def mixed_repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    for number, path in enumerate(PY_FILES, 1):
        (root / path).write_text(PY.format(n=number), encoding="utf-8")
    for number, path in enumerate(GO_FILES, 1):
        (root / path).write_text(GO.format(n=number), encoding="utf-8")
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _inventory(root: Path, monkeypatch, capsys) -> tuple[int, list[str]]:
    """`inventory --json`'s cache hits, and the files it handed lizard."""
    analyzed: list[str] = []
    real = analyze.analyze_jobs

    def spy(jobs, **kwargs):
        analyzed.extend(rel for _, rel in jobs)
        return real(jobs, **kwargs)

    monkeypatch.setattr(analyze, "analyze_jobs", spy)
    code = main(["inventory", "--json", "--repo", str(root)])
    out = capsys.readouterr().out
    assert code == 0, out
    return json.loads(out)["cache_hits"], sorted(analyzed)


@pytest.mark.parametrize("language, again", [("go", GO_FILES), ("python", PY_FILES)])
def test_raising_one_language_reanalyzes_its_files_and_serves_the_rest(
        mixed_repo, monkeypatch, capsys, language, again):
    assert _inventory(mixed_repo, monkeypatch, capsys) == (0, sorted(PY_FILES + GO_FILES))
    monkeypatch.setitem(analyze.ANALYSIS_VERSIONS, language,
                        analyze.ANALYSIS_VERSIONS[language] + 1)
    monkeypatch.setattr(analyze, "ANALYSIS_VERSION", analyze.ANALYSIS_VERSION + 1)

    hits, analyzed = _inventory(mixed_repo, monkeypatch, capsys)

    assert (hits, analyzed) == (5 - len(again), sorted(again))
