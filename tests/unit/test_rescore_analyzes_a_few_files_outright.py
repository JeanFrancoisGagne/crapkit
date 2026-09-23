"""`rescore` of a few small files runs lizard on them and leaves the shared
analysis cache alone.

Loading the cache validated every record in it (0.41 s on a large consumer
repo's 21.8 MB file) and an edit rewrote all of it (0.17 s more), while lizard
costs 4 to 123 ms a file there, about 1.1 to 2.1 ms a KB. The pre-commit hook
settled the same trade on file count: below its pool threshold it analyzes a
commit's files outright with no cache, and rescore reuses that threshold. Large
files break the trade on bytes, so rescore also holds a byte budget: past
either limit it folds its records into the cache, and the next inventory stays
warm.
"""
import json

from cli_inproc_repo import add_knotty, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

import crapkit.analyze
from crapkit.cli import main
from crapkit.cli.scoring import _RESCORE_OUTRIGHT_BYTES
from crapkit.hook import _HOOK_POOL_THRESHOLD

AMBIGUOUS = "const f = [(x: number) => x < 2, (y: number) => y];\n"

# Every input the two paths could read differently: a BOM, CRLF endings, a cp1252
# byte in a string and in a name, two functions with one name, and two arrow
# callbacks on one line.
MIXED = (b"\xef\xbb\xbf"
         b"export class Left {\r\n"
         b"  run(x: number): number {\r\n"
         b"    return x > 0 ? x : -x;\r\n"
         b"  }\r\n"
         b"}\r\n"
         b"export class Right {\r\n"
         b"  run(x: number): number {\r\n"
         b"    const label = \"caf\xe9\";\r\n"
         b"    return label.length > x ? 1 : 0;\r\n"
         b"  }\r\n"
         b"}\r\n"
         b"export const pair = [1, 2].map((x) => x > 1 ? x : 0).filter((y) => y > 1 && y < 9 && y !== 4);\r\n"
         b"export function caf\xe9(x: number): number {\r\n"
         b"  return x;\r\n"
         b"}\r\n")

# MIXED read by hand: (function, start, end, ccn, occurrence).
MIXED_ROWS = [("(anonymous)", 12, 12, 2, 1), ("(anonymous)", 12, 12, 3, 2),
              ("café ( x )", 13, 15, 1, 1),
              ("run ( x )", 2, 4, 2, 1), ("run ( x )", 7, 10, 2, 1)]


@pytest.fixture()
def scored(repo, capsys):
    """One trusted coverage run, which also leaves the shared cache on disk."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert (repo / ".crapkit" / "cache.json").is_file()
    return repo


def cache_bytes(repo) -> bytes:
    return (repo / ".crapkit" / "cache.json").read_bytes()


def cache_entries(repo) -> set[str]:
    return set(json.loads(cache_bytes(repo))["entries"])


def small_files(repo, count: int) -> list[str]:
    """`count` new source files the cache has never seen, one function each."""
    names = []
    for i in range(count):
        rel = f"src/small{i}.ts"
        (repo / rel).write_text(f"export function f{i}(x: number): number {{\n"
                                f"  return x > {i} ? x : {i};\n}}\n", encoding="utf-8")
        names.append(rel)
    return names


def sized_files(repo, total: int) -> list[str]:
    """Two new source files of `total` bytes between them, one function each.
    A comment pads them, so lizard reads the size in one token."""
    names = []
    for i, size in enumerate((total // 2, total - total // 2)):
        rel = f"src/sized{i}.ts"
        body = f"export function sized{i}(x: number): number {{\n  return x;\n}}\n"
        (repo / rel).write_bytes((body + "//" + "x" * (size - len(body) - 3) + "\n").encode())
        assert (repo / rel).stat().st_size == size
        names.append(rel)
    return names


def refuse_the_cache(monkeypatch) -> None:
    def refuse(_path):
        raise AssertionError("rescore of a few small files read the shared cache")

    monkeypatch.setattr(crapkit.analyze, "load_cache", refuse)


def rescore(repo, capsys, files: list[str]) -> dict:
    assert main(["rescore", *files, "--json", "--repo", str(repo)]) == 0
    return json.loads(capsys.readouterr().out)


def test_an_edited_file_is_rescored_without_rewriting_the_cache(scored, capsys):
    before = cache_bytes(scored)
    add_knotty(scored)

    payload = rescore(scored, capsys, ["src/app.ts"])

    assert "knotty ( n )" in {f["function"] for f in payload["functions"]}
    assert cache_bytes(scored) == before


def test_a_few_files_are_rescored_without_reading_the_cache(scored, capsys, monkeypatch):
    refuse_the_cache(monkeypatch)
    add_knotty(scored)

    payload = rescore(scored, capsys, small_files(scored, _HOOK_POOL_THRESHOLD - 2) + ["src/app.ts"])

    assert len({f["path"] for f in payload["functions"]}) == _HOOK_POOL_THRESHOLD - 1


def test_a_few_files_under_the_byte_budget_leave_the_cache_unread(scored, capsys, monkeypatch):
    files = sized_files(scored, _RESCORE_OUTRIGHT_BYTES - 1)
    refuse_the_cache(monkeypatch)

    payload = rescore(scored, capsys, files)

    assert sorted(f["path"] for f in payload["functions"]) == files


def test_a_few_files_at_the_byte_budget_fold_into_the_cache(scored, capsys):
    """Lizard on this many bytes costs more than loading a large cache."""
    before = cache_entries(scored)

    rescore(scored, capsys, sized_files(scored, _RESCORE_OUTRIGHT_BYTES))

    assert before < cache_entries(scored), "every prior entry stays, and the new files join them"


def test_the_hooks_threshold_of_files_still_folds_into_the_cache(scored, capsys):
    before = cache_entries(scored)

    rescore(scored, capsys, small_files(scored, _HOOK_POOL_THRESHOLD))

    assert before < cache_entries(scored), "every prior entry stays, and the new files join them"


def test_files_no_reader_can_tokenize_are_named_in_one_note(scored, capsys):
    """The note the cache path prints for its misses: one count, every file under it."""
    files = []
    for i in range(2):
        rel = f"src/ambiguous{i}.ts"
        (scored / rel).write_text(f"// {i}\n" + AMBIGUOUS, encoding="utf-8")
        files.append(rel)

    assert main(["rescore", *files, "--json", "--repo", str(scored)]) == 0
    err = capsys.readouterr().err

    assert err.count("could not be tokenized") == 1, err
    assert "crapkit: 2 file(s) could not be tokenized" in err, err
    assert all(f"lizard failed on {rel}" in err for rel in files), err


def test_both_paths_score_a_file_the_same(scored, capsys):
    (scored / "src" / "mixed.ts").write_bytes(MIXED)
    many = small_files(scored, _HOOK_POOL_THRESHOLD - 1) + ["src/mixed.ts"]

    folded = [f for f in rescore(scored, capsys, many)["functions"] if f["path"] == "src/mixed.ts"]
    outright = rescore(scored, capsys, ["src/mixed.ts"])["functions"]

    assert outright == folded
    assert sorted((f["function"], f["start"], f["end"], f["ccn"], f["occurrence"])
                  for f in outright) == MIXED_ROWS
