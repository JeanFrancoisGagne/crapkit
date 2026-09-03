"""Universe seam: tracked file list + config in, per-scope analyzable file lists out. Pure."""
import fnmatch
from types import SimpleNamespace

import pytest

from crapkit.config import Config, Scope
from crapkit.universe import (_TEST_DIR, assign_files, exclude_matcher, excluded,
                              scan_files)


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

def _fnmatch_with_optional_prefix(lowered: str, glob: str) -> bool:
    """One glob the way the matcher reads it: a leading `**/` is zero or more
    directories, so the rest may match at the root or behind any prefix that
    ends in a slash. Everything else is fnmatch as written."""
    if glob.startswith("**/"):
        rest = glob[3:]
        return fnmatch.fnmatch(lowered, rest) or fnmatch.fnmatch(lowered, "*/" + rest)
    return fnmatch.fnmatch(lowered, glob)


def _excluded_by_fnmatch(path: str, globs: tuple[str, ...]) -> bool:
    """The fnmatch loop the regex replaced, with the one rule 0.5.0 added on
    top, kept as the oracle for the corpus test below."""
    lowered = path.lower()
    if _TEST_DIR.search(path):
        return True
    return any(_fnmatch_with_optional_prefix(lowered, g.lower()) for g in globs)


_ORACLE_GLOBS = (
    "**/node_modules/**", "deployed/**", "**/dist/**", "**/i18n/locales/**",
    "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py",
    "*.md", "src/[abc]*.ts", "src/mod?.ts", "**/*.TS",
)
# Corpus separators stay forward-slash on purpose: both callers normalize
# backslashes BEFORE the matcher sees a path, and fnmatch's Windows normcase
# would rewrite a backslash path's separators on only one side of the compare.
_TOPS = ("src", "ui", "Deployed", "packages", "dist", "node_modules", "distro")
# "deployed" and "src" appear as inner dirs too, so a root-anchored glob like
# "deployed/**" gets its chance to wrongly fire on "ui/deployed/x.ts".
_MIDS = ("", "node_modules", "dist/js", "i18n/locales", "Tests", "__tests__",
         "a.b", "c+d", "e(f)", "g[h]", "sub/node_modules/deep", "deployed", "src")
_NAMES = ("mod", "mod1", "mod.test", "MOD.Spec", "test_thing", "thing_test",
          "conftest", "weird name", "a*b", "q?x")
_EXTS = (".ts", ".TS", ".py", ".md", "")


def _path_corpus() -> list[str]:
    nested = [f"{top}/{mid}/{name}{ext}".replace("//", "/")
              for top in _TOPS for mid in _MIDS for name in _NAMES for ext in _EXTS]
    # Root-level files: the paths a `**/` glob reaches only under the
    # zero-directories reading, and the ones the old rule never matched.
    root = [f"{name}{ext}" for name in _NAMES for ext in _EXTS]
    return nested + root


def test_the_compiled_exclude_regex_decides_exactly_what_the_fnmatch_oracle_does():
    match = exclude_matcher(_ORACLE_GLOBS)
    corpus = _path_corpus()
    assert len(corpus) > 2000, "the corpus is the whole point of this test"
    disagreements = [p for p in corpus
                     if excluded(p, match) != _excluded_by_fnmatch(p, _ORACLE_GLOBS)]
    assert disagreements == []


def test_a_leading_double_star_reaches_the_repo_root():
    """`**/dist/**` used to need a directory in front of `dist`, so a repo-root
    dist/ stayed in the corpus while web/dist/ left. Zero or more directories
    now, and `distro/` is still not `dist/`."""
    match = exclude_matcher(("**/dist/**", "**/*.generated.*", "**/conftest.py"))

    assert excluded("dist/x.py", match)
    assert excluded("web/dist/x.py", match)
    assert not excluded("src/distro/x.py", match)
    assert excluded("client.generated.ts", match)
    assert excluded("api/client.generated.ts", match)
    assert excluded("conftest.py", match)
    assert not excluded("src/conftest_helpers.py", match)


def test_a_root_form_written_by_hand_still_matches_the_root_and_nothing_below():
    match = exclude_matcher(("dist/**",))

    assert excluded("dist/x.py", match)
    assert not excluded("web/dist/x.py", match), "fnmatch as written: no prefix rule here"


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


def test_the_packet_reads_the_language_arm_the_scored_row_was_assigned_by():
    """`b` claims the prefix but not this row's extension, so the universe
    scored the file under `a` and the ceiling comes from `a`. The lane and the
    scoped test command have to come from `a` too: extension-blind ownership
    put scope `b` in the same packet as scope `a`'s ceiling."""
    scopes = (Scope(name="a", paths=("src",), languages=("python",), target=5),
              Scope(name="b", paths=("src/deep",), languages=("typescript",), target=9))
    cfg = Config(target=6, scopes=scopes, exclude_globs=())
    from crapkit.cli.queue import _row_ceiling

    row = SimpleNamespace(path="src/deep/x.py", scope="a")

    assert assign_files(["src/deep/x.py"], cfg)["a"] == ["src/deep/x.py"]
    assert _packet_scope(cfg, row.path, row.scope) == "a"
    assert _row_ceiling(cfg, row) == 5


# --- dot-directories leave the corpus the way test directories do -------------
#
# `.github/`, `.cursor/` and `.specify/` carry helper scripts written in the
# repo's own languages. `crapkit init` refuses to build a scope out of a
# dot-directory, so the config it writes has nothing that can own those files,
# and every one of them came back from doctor as a tracked file matching a scope
# language with no scope path — a FAIL on the config init had just written.

def test_dot_directories_leave_the_corpus_without_a_glob():
    match = exclude_matcher(())
    assert excluded(".github/workflows/gen.py", match)
    assert excluded(".cursor/skills/skill.py", match)
    assert excluded("a/.hidden/x.py", match), "nested, not only at the root"


def test_a_dot_inside_a_name_is_not_a_dot_directory():
    match = exclude_matcher(())
    assert not excluded("src/pkg/mod.py", match)
    assert not excluded("web/a.b/mod.ts", match), "the dot has to open the component"
    assert not excluded(".eslintrc.js", match), "a dot FILE is nobody's hidden directory"


def test_a_dot_directorys_source_is_never_unclaimed():
    """What the FAIL counted. The file leaves the corpus, so it is neither a
    scope member nor a file doctor asks the reader to claim."""
    uni = scan_files([".github/workflows/gen.py", "scripts/c.py"], CFG)

    assert uni.by_scope == {"src": [], "py": ["scripts/c.py"]}
    assert uni.unclaimed == ()
