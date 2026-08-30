"""Universe seam: tracked file list + config in, per-scope analyzable file lists out. Pure."""
import fnmatch
from types import SimpleNamespace

import pytest

from crapkit.config import Config, Scope
from crapkit.universe import _TEST_DIR, assign_files, exclude_matcher, excluded


CFG = Config(
    target=6,
    scopes=(
        Scope(name="src", paths=("src",), languages=("typescript",)),
        Scope(name="py", paths=("scripts",), languages=("python",)),
    ),
    exclude_globs=(
        "**/node_modules/**",
        "deployed/**",
        "**/*.test.ts",
        "**/tests/**",
        "**/i18n/locales/**",
    ),
)


def test_files_land_in_their_scope_by_path_prefix_and_language_extension():
    files = ["src/a.ts", "src/b.py", "scripts/c.py", "scripts/d.ts", "other/e.ts"]
    assigned = assign_files(files, CFG)
    assert assigned == {"src": ["src/a.ts"], "py": ["scripts/c.py"]}


def test_excluded_trees_never_appear_in_any_scope():
    files = [
        "src/ok.ts",
        "src/node_modules/x/bad.ts",
        "deployed/copy.py",
        "src/thing.test.ts",
        "src/i18n/locales/fr.ts",
        "scripts/tests/test_x.py",
    ]
    assigned = assign_files(files, CFG)
    assert assigned == {"src": ["src/ok.ts"], "py": []}


def test_capitalized_Tests_dirs_are_excluded_case_insensitively():
    files = ["src/Tests/HelperTests.ts", "src/ok.ts"]
    assigned = assign_files(files, CFG)
    assert assigned["src"] == ["src/ok.ts"]


def test_output_order_is_sorted_and_deterministic_regardless_of_input_order():
    files = ["src/z.ts", "src/a.ts", "src/m.ts"]
    shuffled = ["src/m.ts", "src/z.ts", "src/a.ts"]
    assert assign_files(files, CFG) == assign_files(shuffled, CFG)
    assert assign_files(files, CFG)["src"] == ["src/a.ts", "src/m.ts", "src/z.ts"]


def test_backslash_paths_normalize_so_windows_git_output_cannot_split_scopes():
    assigned = assign_files(["src\\a.ts"], CFG)
    assert assigned["src"] == ["src/a.ts"]


def test_a_scope_path_written_with_a_trailing_slash_still_claims_its_files():
    cfg = Config(target=6, exclude_globs=(),
                 scopes=(Scope(name="src", paths=("src/",), languages=("typescript",)),))
    assert assign_files(["src/a.ts", "srcery/b.ts"], cfg) == {"src": ["src/a.ts"]}, \
        "the prefix is hoisted once per scope; a trailing slash must not change who is claimed"


# --- the exclude matcher: regex alternation vs the fnmatch loop it replaced ---

def _excluded_by_fnmatch(path: str, globs: tuple[str, ...]) -> bool:
    """The pre-regex implementation, kept as the oracle for the test below."""
    lowered = path.lower()
    if _TEST_DIR.search(path):
        return True
    return any(fnmatch.fnmatch(lowered, g.lower()) for g in globs)


_ORACLE_GLOBS = (
    "**/node_modules/**", "deployed/**", "**/dist/**", "**/i18n/locales/**",
    "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py",
    "*.md", "src/[abc]*.ts", "src/mod?.ts", "**/*.TS",
)
# Corpus separators stay forward-slash on purpose: both callers normalize
# backslashes BEFORE the matcher sees a path, and fnmatch's Windows normcase
# would rewrite a backslash path's separators on only one side of the compare.
_TOPS = ("src", "ui", "Deployed", "packages")
# "deployed" and "src" appear as inner dirs too, so a root-anchored glob like
# "deployed/**" gets its chance to wrongly fire on "ui/deployed/x.ts".
_MIDS = ("", "node_modules", "dist/js", "i18n/locales", "Tests", "__tests__",
         "a.b", "c+d", "e(f)", "g[h]", "sub/node_modules/deep", "deployed", "src")
_NAMES = ("mod", "mod1", "mod.test", "MOD.Spec", "test_thing", "thing_test",
          "conftest", "weird name", "a*b", "q?x")
_EXTS = (".ts", ".TS", ".py", ".md", "")


def _path_corpus() -> list[str]:
    return [f"{top}/{mid}/{name}{ext}".replace("//", "/")
            for top in _TOPS for mid in _MIDS for name in _NAMES for ext in _EXTS]


def test_the_compiled_exclude_regex_decides_exactly_what_the_fnmatch_loop_did():
    match = exclude_matcher(_ORACLE_GLOBS)
    corpus = _path_corpus()
    assert len(corpus) > 2000, "the corpus is the whole point of this test"
    disagreements = [p for p in corpus
                     if excluded(p, match) != _excluded_by_fnmatch(p, _ORACLE_GLOBS)]
    assert disagreements == []


def test_an_empty_glob_list_excludes_nothing_but_the_test_dirs():
    match = exclude_matcher(())
    assert not excluded("src/a.ts", match), "an empty alternation must never match"
    assert excluded("src/tests/a.ts", match)


def test_two_scopes_sharing_a_path_prefix_split_files_by_language():
    cfg = Config(
        target=6,
        scopes=(
            Scope(name="frontend", paths=("src",), languages=("typescript",)),
            Scope(name="backend", paths=("src",), languages=("python",)),
        ),
        exclude_globs=(),
    )
    assigned = assign_files(["src/app.ts", "src/server.py"], cfg)
    assert assigned == {"frontend": ["src/app.ts"], "backend": ["src/server.py"]}


# --- one owner, whoever is asking -------------------------------------------

NESTED = Config(
    target=6,
    scopes=(
        Scope(name="a", paths=("src",), languages=("python",)),
        Scope(name="b", paths=("src/deep",), languages=("python",)),
        Scope(name="hot", paths=("core/hot.py",), languages=("python",)),
    ),
    exclude_globs=(),
)


def _packet_scope(cfg, path: str, row_scope: str) -> str:
    """What `brief --json` routes a lane and a scoped test command by."""
    from crapkit.cli.queue import _packet_scope as packet_scope

    return packet_scope(cfg, SimpleNamespace(path=path, scope=row_scope))


@pytest.mark.parametrize(("path", "owner"), [
    ("src/deep/x.py", "b"),
    ("core/hot.py", "hot"),
])
def test_universe_verifying_and_the_packet_name_the_same_owner(path, owner):
    """Three readers used to answer differently for a nested scope: universe took
    the first scope declared, verifying took the deepest, and the packet mixed
    them, taking the lane and the test command from one and the ceiling from the
    other. Deepest wins, which is what README documents for test-scoped."""
    from crapkit.cli.verifying import _owning_scope

    assert assign_files([path], NESTED)[owner] == [path]
    assert _owning_scope(path, NESTED.scope_paths) == owner
    assert _packet_scope(NESTED, path, "a") == owner


def test_a_parent_scope_still_claims_what_the_nested_one_does_not_compile():
    """The deeper scope only wins where its languages claim the extension too,
    or two scopes sharing a prefix black-hole each other's files."""
    scopes = (Scope(name="a", paths=("src",), languages=("python",)),
              Scope(name="b", paths=("src/deep",), languages=("typescript",)))
    cfg = Config(target=6, scopes=scopes, exclude_globs=())

    assigned = assign_files(["src/deep/x.py", "src/deep/x.ts"], cfg)

    assert assigned == {"a": ["src/deep/x.py"], "b": ["src/deep/x.ts"]}
