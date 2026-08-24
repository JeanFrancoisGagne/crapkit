"""The file universe's full verdict: claimed by a scope, claimed by none, or
past the byte ceiling. Pure: paths and a size lookup in, three lists out."""
from crapkit.config import Config, Scope
from crapkit.universe import assign_files, scan_files

CFG = Config(
    target=6,
    scopes=(
        Scope(name="src", paths=("src",), languages=("typescript",)),
        Scope(name="py", paths=("scripts",), languages=("python",)),
    ),
    exclude_globs=("**/node_modules/**", "deployed/**"),
)


def test_a_tracked_source_file_no_scope_path_claims_is_reported_unclaimed():
    uni = scan_files(["src/a.ts", "tools/helper.ts", "tools/notes.md"], CFG)
    assert uni.by_scope == {"src": ["src/a.ts"], "py": []}
    assert uni.unclaimed == ("tools/helper.ts",), \
        "notes.md matches no scope language, so it was never a candidate for gating"


def test_a_language_no_scope_declares_is_not_unclaimed():
    uni = scan_files(["tools/helper.swift"], CFG)
    assert uni.unclaimed == ()


def test_unclaimed_respects_the_exclude_globs_and_the_test_dirs():
    uni = scan_files(["deployed/copy.ts", "src/node_modules/x/dep.ts", "extra/tests/t.ts"], CFG)
    assert uni.unclaimed == ()


def test_unclaimed_paths_are_sorted_regardless_of_input_order():
    uni = scan_files(["tools/z.ts", "tools/a.ts"], CFG)
    assert uni.unclaimed == ("tools/a.ts", "tools/z.ts")


def test_a_file_over_max_file_bytes_is_skipped_with_its_size():
    cfg = CFG._replace(max_file_bytes=1000)
    sizes = {"src/big.ts": 4096, "src/small.ts": 12, "tools/big.ts": 9999}
    uni = scan_files(sorted(sizes), cfg, size_of=sizes.__getitem__)
    assert uni.by_scope["src"] == ["src/small.ts"]
    assert uni.oversized == (("src/big.ts", 4096), ("tools/big.ts", 9999))
    assert uni.unclaimed == (), "an oversized file is skipped, never reported unclaimed"


def test_a_file_exactly_at_the_limit_is_kept():
    cfg = CFG._replace(max_file_bytes=1000)
    uni = scan_files(["src/a.ts"], cfg, size_of=lambda path: 1000)
    assert uni.by_scope["src"] == ["src/a.ts"]
    assert uni.oversized == ()


def test_no_limit_means_the_corpus_is_never_stat_ed():
    seen = []

    def size_of(path: str) -> int:
        seen.append(path)
        return 10 ** 9

    uni = scan_files(["src/a.ts"], CFG, size_of=size_of)
    assert uni.oversized == ()
    assert seen == [], "with no max_file_bytes declared crapkit must not touch the disk"


def test_assign_files_still_returns_the_plain_per_scope_mapping():
    assert assign_files(["src/a.ts", "tools/helper.ts"], CFG) == {"src": ["src/a.ts"], "py": []}
