"""`rescore` of a commit's worth of files runs lizard on them and leaves the
shared analysis cache alone.

Loading the cache validated every record in it (0.41 s on a large consumer
repo's 21.8 MB file) and an edit rewrote all of it (0.17 s more), while lizard
costs tens of milliseconds a file. The pre-commit hook settled the same trade:
below its pool threshold it analyzes a commit's files outright with no cache,
and rescore reuses that threshold rather than a second number. At or above it,
rescore keeps folding its records into the cache, so the next inventory stays
warm.
"""
import json

from cli_inproc_repo import add_knotty, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

import crapkit.analyze
from crapkit.cli import main
from crapkit.hook import _HOOK_POOL_THRESHOLD


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


def small_files(repo, count: int) -> list[str]:
    """`count` new source files the cache has never seen, one function each."""
    names = []
    for i in range(count):
        rel = f"src/small{i}.ts"
        (repo / rel).write_text(f"export function f{i}(x: number): number {{\n"
                                f"  return x > {i} ? x : {i};\n}}\n", encoding="utf-8")
        names.append(rel)
    return names


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
    def refuse(_path):
        raise AssertionError("rescore of a few files read the shared cache")

    monkeypatch.setattr(crapkit.analyze, "load_cache", refuse)
    add_knotty(scored)

    payload = rescore(scored, capsys, small_files(scored, _HOOK_POOL_THRESHOLD - 2) + ["src/app.ts"])

    assert len({f["path"] for f in payload["functions"]}) == _HOOK_POOL_THRESHOLD - 1


def test_the_hooks_threshold_of_files_still_folds_into_the_cache(scored, capsys):
    before = cache_bytes(scored)

    rescore(scored, capsys, small_files(scored, _HOOK_POOL_THRESHOLD))

    assert cache_bytes(scored) != before


def test_both_paths_score_a_file_the_same(scored, capsys):
    many = small_files(scored, _HOOK_POOL_THRESHOLD)

    folded = rescore(scored, capsys, many)["functions"]
    outright = rescore(scored, capsys, [many[3]])["functions"]

    assert outright == [f for f in folded if f["path"] == many[3]]
