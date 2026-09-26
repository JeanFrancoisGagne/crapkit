"""File universe and scope ownership, against the rules docs/configuration.md states.

owner() below is written from docs/configuration.md, not from crapkit's
universe module:
- "[[scope]]": `paths = ["."]` claims the repo root, loose files included; a
  bare path also matches that exact file; `./src`, `src\\` and `src` are one
  path; a path holding `..` or a drive letter is refused.
- "Scope matching": paths are tried longest first, and the first whose path
  prefix and extension both match owns the file; declaration order breaks a
  tie between two paths of the same length; a prefix-only match does not stop
  the search.
- "[exclude]": test directories (`test`, `tests`, `__tests__`, any case) and
  dot-directories leave first; each glob matches the whole path,
  case-insensitively; a leading `**/` matches zero or more directories and the
  rest is read the way fnmatch reads it; `max_file_bytes` drops larger files.

The hand table's owners are worked from those lines too, so a test fails when
crapkit's inventory disagrees with either. Every file holds one function, so a
file's owner is the `scope` column of its one inventory row, and a file with no
row was left out of the corpus.
"""
from __future__ import annotations

import fnmatch
import json

from hypothesis import given, strategies as st
import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive
from accuracy.kit.settings import process

pytestmark = pytest.mark.process

CONFIG_DOC = "docs/configuration.md"
# README "Languages": the two languages these layouts use.
EXTENSIONS = {"python": (".py",), "typescript": (".ts",)}
TEST_DIRS = ("test", "tests", "__tests__")
# The default set `crapkit init` writes (docs/configuration.md "[exclude]").
INIT_GLOBS = ("**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**",
              "**/generated/**", "**/__generated__/**", "**/*.generated.*", "**/*.test.*",
              "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py", "**/*_test.go",
              "**/*.config.ts", "**/*.config.js", "**/*.config.mts")


# --- the model, from docs/configuration.md -----------------------------------------------

def scope_path(declared: str) -> str:
    """A declared path the way git writes one; "" is the repo root."""
    path = declared.replace(chr(92), "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.strip("/")
    return "" if path == "." else path


def glob_matches(path: str, glob: str) -> bool:
    """The whole lowered path against one lowered glob. A leading `**/` is zero
    or more directories: the rest matches the path or any tail after a `/`."""
    path, glob = path.lower(), glob.lower()
    if not glob.startswith("**/"):
        return fnmatch.fnmatchcase(path, glob)
    tails = [path] + [path[at + 1:] for at, char in enumerate(path) if char == "/"]
    return any(fnmatch.fnmatchcase(tail, glob[3:]) for tail in tails)


def left_out(path: str, globs) -> bool:
    folders = path.split("/")[:-1]
    if any(folder.lower() in TEST_DIRS or folder.startswith(".") for folder in folders):
        return True
    return any(glob_matches(path, glob) for glob in globs)


def _claims(path: str, prefix: str) -> bool:
    return prefix == "" or path == prefix or path.startswith(prefix + "/")


def _longest_first(scopes) -> list:
    claims = [(scope_path(declared), name, languages) for name, paths, languages in scopes
              for declared in paths]
    return sorted(claims, key=lambda claim: -len(claim[0]))


def _suffixes(languages) -> tuple:
    return tuple(ext for language in languages for ext in EXTENSIONS[language])


def owner(path: str, scopes, globs=()) -> str | None:
    """The scope that owns `path`, or None when it leaves the corpus or no scope
    claims it. `scopes` is [(name, declared paths, languages)] in declaration order."""
    if left_out(path, globs):
        return None
    for prefix, name, languages in _longest_first(scopes):
        if _claims(path, prefix) and path.endswith(_suffixes(languages)):
            return name
    return None


# --- building a layout ----------------------------------------------------------------------

def source(path: str, index: int) -> str:
    if path.endswith(".ts"):
        return f"export function f{index}(): number {{\n  return {index};\n}}\n"
    return f"def f{index}():\n    return {index}\n"


def config(scopes, globs=(), extra: str = "") -> str:
    """crapkit.toml for `scopes`; TOML literal strings keep a backslash as written."""
    lines = ["[crapkit]", "target = 6", extra]
    for name, paths, languages in scopes:
        declared = ", ".join(f"'{path}'" for path in paths)
        lines += ["", "[[scope]]", f'name = "{name}"', f"paths = [{declared}]",
                  f"languages = {json.dumps(list(languages))}", "coverage_optional = true"]
    lines += ["", "[exclude]", f"globs = {json.dumps(list(globs))}", ""]
    return "\n".join(lines)


def tree(paths, scopes, globs=(), extra: str = "") -> dict:
    files = {path: source(path, index) for index, path in enumerate(paths)}
    return {**files, "crapkit.toml": config(scopes, globs, extra)}


def owners(measured) -> dict:
    """{path: scope} from crapkit's inventory; each file holds one function."""
    return {row["path"]: row["scope"] for row in measured.rows}


def crapkit_owners(tmp_path, paths, scopes, globs=()) -> dict:
    measured = analysis_inventory.measure(tree(paths, scopes, globs), tmp_path)
    assert measured.code == 0, measured.stderr
    return owners(measured)


def expected(assigned: dict) -> dict:
    return {path: scope for path, scope in assigned.items() if scope is not None}


# --- the hand table ---------------------------------------------------------------------------

PY = ("python",)
TS = ("typescript",)
LAYOUTS = {
    # "[[scope]]": `paths = ["."]` claims the repo root, including loose source files.
    "root-owns-loose-files": ([("root", ["."], PY)], (),
                              {"a.py": "root", "pkg/b.py": "root", "pkg/deep/c.py": "root"}),
    # "Scope matching": the deepest declared path wins, whatever the declaration order.
    "deepest-wins": ([("a", ["src"], PY), ("hot", ["src/hot"], PY)], (),
                     {"src/b.py": "a", "src/hot/c.py": "hot"}),
    "deepest-wins-declared-last": ([("hot", ["src/hot"], PY), ("a", ["src"], PY)], (),
                                   {"src/b.py": "a", "src/hot/c.py": "hot"}),
    # "[[scope]]": `paths` are path prefixes; `srcx/` is not under `src/`.
    "prefix-is-a-path-boundary": ([("a", ["src"], PY), ("root", ["."], PY)], (),
                                  {"src/a.py": "a", "srcx/d.py": "root", "src.py": "root"}),
    # "Scope matching": a prefix-only match does not stop the search.
    "shared-prefix-splits-by-language": ([("web", ["src"], TS), ("api", ["src"], PY)], (),
                                         {"src/a.ts": "web", "src/b.py": "api"}),
    "nested-scope-of-another-language": ([("a", ["src"], PY), ("b", ["src/deep"], TS)], (),
                                         {"src/deep/x.py": "a", "src/deep/y.ts": "b"}),
    # "[[scope]]": a bare path also matches that exact file.
    "file-path-scope": ([("core", ["core"], PY), ("hot", ["core/hot.py"], PY)], (),
                        {"core/hot.py": "hot", "core/cold.py": "core"}),
    # "Scope matching": declaration order breaks a tie between same-length paths.
    "same-length-tie": ([("first", ["src"], PY), ("second", ["src"], PY)], (),
                        {"src/a.py": "first"}),
    # "[[scope]]": a scope path spelled the way git would not still claims its files.
    "spelling-dot-slash": ([("s", ["./src"], PY)], (), {"src/a.py": "s", "lib/b.py": None}),
    "spelling-trailing-slash": ([("s", ["src/"], PY)], (), {"src/a.py": "s"}),
    "spelling-backslash": ([("s", ["src" + chr(92)], PY)], (), {"src/a.py": "s"}),
    "spelling-leading-slash": ([("s", ["/src"], PY)], (), {"src/a.py": "s"}),
    "spelling-nested-backslash": ([("s", ["src" + chr(92) + "hot"], PY), ("r", ["."], PY)], (),
                                  {"src/hot/a.py": "s", "src/b.py": "r"}),
    # "[exclude]": the init default set reaches the root and every nested copy.
    "init-globs-root-and-nested": (
        [("root", ["."], PY)], INIT_GLOBS,
        {"dist/a.py": None, "web/dist/b.py": None, "src/distro/c.py": "root",
         "conftest.py": None, "pkg/conftest.py": None, "test_d.py": None, "pkg/test_e.py": None,
         "f_test.py": None, "build/g.py": None, "pkg/vendor/h.py": None,
         "generated/i.py": None, "j.generated.py": None, "k.py": "root"}),
    # "[exclude]": test directories and dot-directories leave the corpus; a dot file stays.
    "test-and-dot-directories": (
        [("root", ["."], PY)], (),
        {"tests/a.py": None, "Tests/b.py": None, "__tests__/c.py": None, "pkg/test/d.py": None,
         ".github/e.py": None, "pkg/.cache/f.py": None, ".hidden.py": "root",
         "testing/g.py": "root", "latest/h.py": "root"}),
    # "[exclude]": a root form is read the way fnmatch reads it, the root only.
    "root-form-glob": ([("root", ["."], PY)], ("dist/**",),
                       {"dist/a.py": None, "web/dist/b.py": "root"}),
    # "[exclude]": globs match case-insensitively.
    "glob-case": ([("root", ["."], PY)], ("**/DIST/**",), {"Dist/a.py": None, "b.py": "root"}),
}


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_the_model_reads_the_hand_table(name):
    scopes, globs, hand = LAYOUTS[name]
    assert {path: owner(path, scopes, globs) for path in hand} == hand


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_crapkit_assigns_the_hand_owners(name, tmp_path):
    scopes, globs, hand = LAYOUTS[name]
    assert crapkit_owners(tmp_path, hand, scopes, globs) == expected(hand)


def _named(name: str, tmp_path):
    scopes, globs, hand = LAYOUTS[name]
    assert crapkit_owners(tmp_path, hand, scopes, globs) == expected(hand)


def test_root_scope_owns_loose_files(tmp_path):
    _named("root-owns-loose-files", tmp_path)


def test_scope_prefix_is_a_path_boundary(tmp_path):
    _named("prefix-is-a-path-boundary", tmp_path)
    _named("shared-prefix-splits-by-language", tmp_path / "shared")


@pytest.mark.parametrize("name", [key for key in sorted(LAYOUTS) if key.startswith("spelling")])
def test_scope_spellings_claim_the_same_files(name, tmp_path):
    _named(name, tmp_path)


def test_default_excludes_at_root_and_nested(tmp_path):
    _named("init-globs-root-and-nested", tmp_path)


@pytest.mark.parametrize("declared", ["../src", "src/../lib", "C:/src", "c:" + chr(92) + "src"])
def test_a_path_no_tracked_file_can_match_is_refused(declared, tmp_path):
    measured = analysis_inventory.measure(tree(["src/a.py"], [("s", [declared], PY)]), tmp_path)
    assert measured.code == 3 and "'s'" in measured.stderr, measured.stderr


def test_max_file_bytes_drops_larger_files_and_counts_them(tmp_path):
    files = {"a.py": "def a():\n    return 1\n", "b.py": "def b():\n    return 1\n" + "#" * 40,
             "c.py": "def c():\n    return 1\n" + "#" * 80,
             "crapkit.toml": config([("root", ["."], PY)], extra="") + "max_file_bytes = 60\n"}
    sizes = {path: len(text.encode("utf-8")) for path, text in files.items() if path != "crapkit.toml"}
    measured = analysis_inventory.measure(files, tmp_path)
    kept = sorted(path for path, size in sizes.items() if size <= 60)
    assert sorted(owners(measured)) == kept
    counted = json.loads(drive.Driver(tmp_path / "repo").run("inventory", "--json").stdout)
    assert counted["skipped_max_bytes"] == len(sizes) - len(kept)


# --- the model against crapkit on drawn layouts ------------------------------------------------

DECLARABLE = (".", "./", "src", "./src", "src/", "src/hot", "src" + chr(92) + "hot", "srcx",
              "src/hot/a.py", "lib", "/lib", "src/hot/deep")
FILES = ("a.py", "src/a.py", "src/b.ts", "src/hot/a.py", "src/hot/b.ts", "src/hot/deep/c.py",
         "srcx/a.py", "srcx/b.ts", "lib/a.py", "lib/dist/b.py", "dist/c.ts", "pkg/tests/d.py",
         "pkg/.cache/e.py", "src/hot/conftest.py")
GLOBS = ("**/dist/**", "dist/**", "**/conftest.py", "src/hot/*", "**/*.TS", "lib/*.py")

scopes_drawn = st.lists(
    st.tuples(st.lists(st.sampled_from(DECLARABLE), min_size=1, max_size=2),
              st.sampled_from([PY, TS, PY + TS])),
    min_size=1, max_size=4).map(
    lambda drawn: [(f"s{index}", paths, languages) for index, (paths, languages) in enumerate(drawn)])


@process
@given(scopes=scopes_drawn, globs=st.lists(st.sampled_from(GLOBS), max_size=3, unique=True))
def test_crapkit_assigns_what_the_model_assigns(scopes, globs, tmp_path_factory):
    work = tmp_path_factory.mktemp("drawn")
    model = {path: owner(path, scopes, globs) for path in FILES}
    assert crapkit_owners(work, FILES, scopes, globs) == expected(model)


# --- metamorphic relations -----------------------------------------------------------------------

RICH = ([("a", ["src"], PY), ("b", ["src/deep"], TS), ("hot", ["src/hot.py"], PY),
         ("root", ["."], PY + TS)], ("**/dist/**",))
RICH_FILES = ("a.py", "b.ts", "src/a.py", "src/b.ts", "src/hot.py", "src/deep/x.py",
              "src/deep/y.ts", "srcx/z.py", "web/dist/w.ts", "tests/t.py")


@pytest.fixture(scope="module")
def rich_owners(tmp_path_factory):
    return crapkit_owners(tmp_path_factory.mktemp("rich"), RICH_FILES, *RICH)


def test_declaration_order_of_distinct_paths_changes_nothing(rich_owners, tmp_path):
    scopes, globs = RICH
    assert crapkit_owners(tmp_path, RICH_FILES, scopes[::-1], globs) == rich_owners


def test_unrelated_files_leave_every_owner(rich_owners, tmp_path):
    scopes, globs = RICH
    files = tree(RICH_FILES, scopes, globs)
    files.update({"README.md": "# notes\n", "src/data.json": "{}\n", "src/tool.go":
                  "package tool\n\nfunc Tool() int {\n\treturn 1\n}\n", "notes.txt": "x\n"})
    measured = analysis_inventory.measure(files, tmp_path)
    assert owners(measured) == rich_owners


def test_renamed_scopes_carry_their_files(rich_owners, tmp_path):
    scopes, globs = RICH
    renamed = [(name + "_renamed", paths, languages) for name, paths, languages in scopes]
    got = crapkit_owners(tmp_path, RICH_FILES, renamed, globs)
    assert got == {path: scope + "_renamed" for path, scope in rich_owners.items()}


def test_the_rich_layout_matches_the_model(rich_owners):
    assert rich_owners == expected({path: owner(path, *RICH) for path in RICH_FILES})


# --- one owner on every surface (R78) ----------------------------------------------------------

TARGETS = {"a": 9, "b": 4, "hot": 7, "root": 5}


def _targeted_config() -> str:
    scopes, globs = RICH
    text = config(scopes, globs)
    for name, target in TARGETS.items():
        text = text.replace(f'name = "{name}"\n', f'name = "{name}"\ntarget = {target}\n', 1)
    return text


@pytest.fixture(scope="module")
def targeted(tmp_path_factory):
    files = {**tree(RICH_FILES, *RICH), "crapkit.toml": _targeted_config()}
    work = tmp_path_factory.mktemp("targeted")
    measured = analysis_inventory.measure(files, work)
    assert measured.code == 0, measured.stderr
    driver = drive.Driver(work / "repo")
    coverage = driver.run("coverage", "--json")
    assert coverage.code == 0, coverage.stderr
    return measured, driver, json.loads(coverage.stdout)


def _doctor_lists(text: str) -> dict:
    """{path: scope} from `doctor --show-files`: an `ok   scope 'NAME': N file(s)`
    line, then one indented path per file."""
    listed, scope = {}, None
    for line in text.splitlines():
        if line.startswith(("ok   scope '", "FAIL scope '")):
            scope = line.split("'")[1]
        elif line.startswith("       ") and scope is not None:
            listed[line.strip()] = scope
        else:
            scope = None
    return listed


def test_doctor_lists_the_inventory_owners(targeted):
    measured, driver, _ = targeted
    doctor = driver.run("doctor", "--show-files")
    assert _doctor_lists(doctor.stdout) == owners(measured)


def test_coverage_counts_per_scope_equal_the_inventory(targeted):
    measured, _, coverage = targeted
    counts = {}
    for scope in owners(measured).values():
        counts[scope] = counts.get(scope, 0) + 1
    assert {name: entry["functions"] for name, entry in coverage["by_scope"].items()} == counts
    assert {name: coverage["ceilings"][name] for name in counts} == {
        name: TARGETS[name] for name in counts}


@pytest.mark.parametrize("path", ["src/deep/x.py", "src/deep/y.ts", "src/hot.py", "srcx/z.py"])
def test_one_owner_across_packet_lane_and_ceiling(path, targeted):
    measured, driver, _ = targeted
    (row,) = measured.in_file(path)
    brief = json.loads(driver.run("brief", path, analysis_inventory.bare(row["long_name"]),
                                  "--json").stdout)
    ceiling = TARGETS[owner(path, *RICH)]
    assert (row["scope"], brief["target"], brief["gate_rule"]["ceiling"]) == (
        owner(path, *RICH), ceiling, ceiling)


@pytest.mark.parametrize(("paths", "languages"), [
    # Hand rows from the README "Languages" table.
    (["a.py", "b/c.PY"], ("python",)),
    (["x.h", "y.mm", "z.jsx", "w.psm1"], ("javascript", "cpp", "objectivec", "powershell")),
    (["notes.md", "Makefile"], analysis_inventory.LANGUAGES),
])
def test_a_measured_set_names_only_its_own_languages(paths, languages):
    assert analysis_inventory.languages_of(paths) == languages


# --- the retro tree (analysis_inventory.retro_tree) ---------------------------------------------

LOOSE = {"crapkit.toml": config([("root", ["."], PY)]), "a.py": "", "pkg/b.py": "",
         "pkg/deep/c.py": "", "web/d.ts": "", "README.md": ""}


def test_top_entries_name_each_directory_and_loose_file_once():
    assert analysis_inventory.top_entries(LOOSE) == ["README.md", "a.py", "pkg", "web"]


@pytest.mark.parametrize(("declared", "root"), [
    (".", True), ("./", True), ("." + chr(92), True), ("/", True), ("", True),
    ("src", False), ("./src", False), (".hidden.py", False), (".github", False)])
def test_a_root_path_is_every_spelling_of_the_repo_root(declared, root):
    assert analysis_inventory.is_root(declared) is root


@pytest.mark.parametrize(("paths", "rooted"), [
    (["."], ["a.py", "pkg"]), (["src"], ["src"]), (["./", "src"], ["a.py", "pkg", "src"])])
def test_root_paths_become_the_entries(paths, rooted):
    assert analysis_inventory.rooted(paths, ["a.py", "pkg"]) == rooted


def test_only_a_changed_list_line_is_rewritten():
    text = "[[scope]]\npaths = ['.']\n\n[[scope]]\npaths = ['src" + chr(92) + "']\nname = \"x\"\n"
    got = analysis_inventory.rewrite_lists(text, "paths", lambda found: [p for p in found if p != "."])
    assert got == "[[scope]]\npaths = []\n\n[[scope]]\npaths = ['src" + chr(92) + "']\nname = \"x\"\n"


def test_no_variable_leaves_the_tree_as_given():
    assert analysis_inventory.retro_tree(LOOSE, {}) is LOOSE


def test_the_language_limit_drops_other_sources_and_keys():
    files = {**LOOSE, "crapkit.toml": config([("all", ["pkg", "web"], PY + TS)])}
    got = analysis_inventory.retro_tree(files, {analysis_inventory.LANGUAGES_ENV: "python"})
    assert sorted(got) == ["README.md", "a.py", "crapkit.toml", "pkg/b.py", "pkg/deep/c.py"]
    assert 'languages = ["python"]' in got["crapkit.toml"].split("\n")


def test_entries_replace_the_root_path():
    got = analysis_inventory.retro_tree(LOOSE, {analysis_inventory.ROOT_PATHS_ENV: "entries"})
    assert 'paths = ["README.md", "a.py", "pkg", "web"]' in got["crapkit.toml"].split("\n")


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_entries_keep_every_hand_owner_under_the_model(name):
    """Every hand layout declares its root scope last, so the entries, which tie
    with a same-length path of an earlier scope, still leave each file its owner."""
    scopes, globs, hand = LAYOUTS[name]
    entries = analysis_inventory.top_entries(hand)
    rewritten = [(scope, analysis_inventory.rooted(paths, entries), languages)
                 for scope, paths, languages in scopes]
    assert {path: owner(path, rewritten, globs) for path in hand} == hand


def test_crapkit_reads_the_entries_as_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv(analysis_inventory.ROOT_PATHS_ENV, "entries")
    _named("root-owns-loose-files", tmp_path)
