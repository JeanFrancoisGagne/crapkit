"""A small repo in the shape change control reads, and the diffs its rules judge.

base() is a consistent tree: two declared changes (C1 the first lock, C2 a fix),
a lock over its goldens, rulings and hand table, two metric-digests rows, a
CHANGELOG naming C2, a bug with its retro row, a mutation floor and test counts.
Its small corpus holds src/a.py: `parse` (radon 7, complexipy 8, worked by hand
below) and eleven one-line functions f1 to f11.

Every value in the goldens is written here from the README definition, not read
from crapkit: CRAP = ccn^2 (1 - cov)^3 + ccn, so parse at ccn 7 and cov 0.5 is
49 * 0.125 + 7 = 13.125, and f1 at ccn 1 and cov 1.0 is 1.0. The base carries one
deliberate wrong value, f1's CRAP 1.5, which the `fixed_crap` change corrects.

Each scenario function takes a tree ({path: text}) and returns the head tree a
second commit makes. Change control's own helpers are used only for what they
define rather than compute: the metric digest (metric_digest, corpus_digest).
"""
from __future__ import annotations

import hashlib

HOME = "tests/accuracy/change_control"
CHANGES = f"{HOME}/CHANGES.tsv"
LOCK = f"{HOME}/goldens.lock"
DIGESTS = f"{HOME}/metric-digests.tsv"
COUNTS = f"{HOME}/test-counts.tsv"
SCORE = "tests/accuracy/score_model"
RULINGS = f"{SCORE}/rulings.tsv"
HAND = f"{SCORE}/hand_score.tsv"
RETRO = f"{SCORE}/retro.tsv"
CALCS = f"{SCORE}/calcs.tsv"
SEED_TEST = f"{SCORE}/test_seed_score.py"
BUGS = "tests/accuracy/suite_strength/retro/bugs.tsv"
FLOORS = "tests/accuracy/suite_strength/mutation/floors.tsv"
SURVIVORS = "tests/accuracy/suite_strength/mutation/survivors.tsv"
GOLDENS = "tests/accuracy/corpus_goldens/goldens/small"
SCORED = f"{GOLDENS}/scored.tsv"
INVENTORY = f"{GOLDENS}/inventory.tsv"
WORKLIST = f"{GOLDENS}/worklist.json"
SOURCE = "tests/accuracy/corpus_goldens/small/src/a.py"
ANALYZE = "src/crapkit/analyze.py"
MODULE = "src/crapkit/score.py"
HOOK = "src/crapkit/hook.py"
LOCKED = (SCORED, INVENTORY, WORKLIST, RULINGS, HAND)
CCN = "ccn_std, ccn_mod and gated ccn"

# radon 6.0.1 on parse: 1 + if + if + `and` + for + if + if = 7 (McCabe decision
# points, booleans counted). complexipy 8.0.1 (Sonar rules): if 1, if 1 + and 1,
# for 1, nested if 2, nested if 2 = 8.
PARSE = '''def parse(text, strict):
    if not text:
        return None
    if strict and text.startswith("#"):
        return None
    for line in text.splitlines():
        if line.endswith(";"):
            continue
        if "=" in line:
            return line
    return text
'''
SMALL = PARSE + "".join(f"\n\ndef f{k}(x):\n    return x\n" for k in range(1, 12))
F_START = {k: 14 + 4 * (k - 1) for k in range(1, 12)}

SCORED_COLUMNS = ("scope", "path", "long_name", "start", "end", "ccn_std", "ccn_mod", "ccn",
                  "nloc", "params", "nesting", "cov", "flag", "crap", "remedy", "cognitive",
                  "occurrence")
INVENTORY_COLUMNS = ("scope", "path", "long_name", "start", "end", "ccn_std", "ccn_mod", "ccn",
                     "nloc", "params", "nesting", "cognitive", "occurrence")
RULINGS_COLUMNS = ("id", "calc", "oracle", "construct", "crapkit_value", "oracle_value", "ruling",
                   "outside_support", "docs_anchor", "test", "issue")


def _tsv(columns, rows) -> str:
    return "\n".join(["\t".join(columns)] + ["\t".join(map(str, row)) for row in rows]) + "\n"


def scored_rows(parse_ccn: int = 7, parse_crap: str = "13.125", f_cov: str = "1.0",
                f_crap: dict | None = None) -> list[dict]:
    crap = {1: "1.5", **(f_crap or {})}
    rows = [dict(zip(SCORED_COLUMNS, ("py", "src/a.py", "parse( text , strict )", 1, 11,
                                      parse_ccn, parse_ccn, parse_ccn, 11, 2, 2, "0.5",
                                      "measured", parse_crap, "add-tests", 8, 1)))]
    rows += [dict(zip(SCORED_COLUMNS, ("py", "src/a.py", f"f{k}( x )", F_START[k],
                                       F_START[k] + 1, 1, 1, 1, 2, 1, 0, f_cov, "measured",
                                       crap.get(k, "1.0"), "ok", 0, 1))) for k in range(1, 12)]
    return rows


def scored(rows: list[dict]) -> str:
    return _tsv(SCORED_COLUMNS, [[row[name] for name in SCORED_COLUMNS] for row in rows])


def inventory(rows: list[dict]) -> str:
    return _tsv(INVENTORY_COLUMNS, [[row[name] for name in INVENTORY_COLUMNS] for row in rows])


RULING_ROWS = (
    ("R-CCN", CCN, "radon", "a boolean operator in a loop condition", "8", "7", "definition",
     "https://radon.readthedocs.io/en/latest/intro.html#cyclomatic-complexity", "README.md#ccn",
     f"{SEED_TEST}::test_ccn", ""),
    ("R-D2", "Cognitive complexity", "complexipy", "the ?? operator", "2", "1", "defect",
     "paper:Sonar cognitive complexity v1.7 B1", "", f"{SEED_TEST}::test_cognitive", "#101"),
    ("R-D5", "CRAP score", "kit.exact", "a 4 dp tie", "1.0001", "1.0002", "definition",
     "https://docs.python.org/3/library/functions.html#round", "README.md#crap",
     f"{SEED_TEST}::test_crap", ""),
)


def base(rows: list[dict] | None = None) -> dict[str, str]:
    """The consistent tree every scenario starts from; `rows` replaces the goldens' rows."""
    rows = rows or scored_rows()
    tree = {
        "README.md": "# crapkit\n\nCRAP is ccn^2 (1 - cov)^3 + ccn.\n",
        "CONTEXT.md": "# Context\n",
        "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- f1's CRAP was fixed. (accuracy change "
                        "C2)\n",
        ANALYZE: "import lizard\n\nANALYSIS_VERSION = 11  # the reader's version\n",
        MODULE: "def crap(ccn, cov):\n    return ccn * ccn * (1 - cov) ** 3 + ccn\n",
        HOOK: "def violations(rows, marks):\n    return [row for row in rows if row not in marks]\n",
        SOURCE: SMALL,
        SCORED: scored(rows),
        INVENTORY: inventory(rows),
        WORKLIST: '{"rows": 12}\n',
        CALCS: _tsv(("calc", "independent_test", "modules", "functions"), [
            ("CRAP score", f"{SEED_TEST}::test_crap", MODULE, f"{MODULE}:crap"),
            (CCN, f"{SEED_TEST}::test_ccn", ANALYZE, f"{ANALYZE}:record"),
            ("Cognitive complexity", f"{SEED_TEST}::test_cognitive", ANALYZE,
             f"{ANALYZE}:record"),
            ("Pre-commit gate", f"{SEED_TEST}::test_crap", HOOK, f"{HOOK}:violations")]),
        SEED_TEST: "def test_crap():\n    assert 7 * 7 * 0.125 + 7 == 13.125\n\n\n"
                   "def test_ccn():\n    assert 1 + 6 == 7\n\n\ndef test_cognitive():\n"
                   "    assert 8 == 8\n",
        RULINGS: _tsv(RULINGS_COLUMNS, RULING_ROWS),
        HAND: _tsv(("ccn", "cov", "crap", "source"),
                   [(7, "0.5", "13.125", "Savoia and Evans, artima weblog 210575 (2007)")]),
        RETRO: _tsv(("id", "test"), [("R01", f"{SEED_TEST}::test_crap")]),
        BUGS: _tsv(("id", "platform", "before_commit", "fix_commit"),
                   [("R01", "any", "1111111", "2222222")]),
        FLOORS: _tsv(("module", "floor"), [(MODULE, 95), (ANALYZE, 85)]),
        SURVIVORS: _tsv(("module", "function", "sha256", "evidence"), []),
        COUNTS: _tsv(("packet", "tests"), [("score_model", 3)]),
        CHANGES: _tsv(("id", "date", "kind", "calcs", "analysis_version", "lizard_version",
                       "changelog", "reason"),
                      [("C1", "2026-09-20", "none", "", "10", "1.24.0", "", "the first lock"),
                       ("C2", "2026-09-21", "fix", "CRAP score", "11", "1.24.0", "#unreleased",
                        "f1's CRAP was fixed")]),
    }
    tree[LOCK] = _lock_text({path: (sha256(tree[path]), "C2" if path == SCORED else "C1")
                             for path in LOCKED})
    tree[DIGESTS] = _tsv(("analysis_version", "lizard_version", "corpus", "digest", "change"),
                         [("10", "1.24.0", corpus_digest(tree), "0123456789abcdef", "C1")])
    return with_digest(tree, "11", "C2")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def lock_rows(tree: dict[str, str]) -> dict[str, tuple[str, str]]:
    cells = (line.split("\t") for line in tree[LOCK].splitlines()[1:])
    return {path: (digest, owner) for path, digest, owner in cells}


def _lock_text(rows: dict[str, tuple[str, str]]) -> str:
    return _tsv(("path", "sha256", "change"), [(path, *rows[path]) for path in sorted(rows)])


def relock(tree: dict[str, str], change: str, *paths: str) -> dict[str, str]:
    """The lock with only these paths' rows set to their current bytes under
    `change` (a path the tree no longer has leaves the lock); every other row
    keeps its old digest."""
    rows = lock_rows(tree)
    rows.update({path: (sha256(tree[path]), change) for path in paths if path in tree})
    kept = {path: row for path, row in rows.items() if path in tree}
    return {**tree, LOCK: _lock_text(kept)}


def append(tree: dict[str, str], path: str, *cells) -> dict[str, str]:
    return {**tree, path: tree[path] + "\t".join(map(str, cells)) + "\n"}


def replace(tree: dict[str, str], path: str, old: str, new: str) -> dict[str, str]:
    assert old in tree[path], (path, old)
    return {**tree, path: tree[path].replace(old, new, 1)}


def _tree_bytes(tree: dict[str, str]):
    import change_control
    return change_control.DictTree({path: text.encode("utf-8") for path, text in tree.items()})


def corpus_digest(tree: dict[str, str]) -> str:
    import change_control
    return change_control.corpus_digest(_tree_bytes(tree))


def with_digest(tree: dict[str, str], analysis: str, change: str) -> dict[str, str]:
    """A metric-digests row for the tree's goldens under this analysis version."""
    import change_control
    digest = change_control.metric_digest(_tree_bytes(tree))
    return append(tree, DIGESTS, analysis, "1.24.0", corpus_digest(tree), digest, change)


def bump(tree: dict[str, str], version: str) -> dict[str, str]:
    return replace(tree, ANALYZE, "ANALYSIS_VERSION = 11", f"ANALYSIS_VERSION = {version}")


def change(tree: dict[str, str], key: str, kind: str, calcs: str, reason: str = "a reason",
           analysis: str = "11") -> dict[str, str]:
    return append(tree, CHANGES, key, "2026-09-24", kind, calcs, analysis, "1.24.0",
                  "#unreleased" if kind != "none" else "", reason)


def changelog(tree: dict[str, str], key: str) -> dict[str, str]:
    return {**tree, "CHANGELOG.md": tree["CHANGELOG.md"] + f"- a change. (accuracy change {key})\n"}


MOVED_COLUMNS = ("golden", "path", "handle", "column", "old", "new", "oracle", "oracle_value",
                 "ruling")


def moved(tree: dict[str, str], key: str, cells: list[tuple]) -> dict[str, str]:
    return {**tree, f"{HOME}/changes/{key}.moved.tsv": _tsv(MOVED_COLUMNS, cells)}


# --- the declared changes a head can carry ---------------------------------------------------

def fixed_crap(tree: dict[str, str], key: str = "C3", kind: str = "fix",
               calcs: str = "CRAP score", bug: bool = True, new: str = "1.0",
               listed: tuple | None = None) -> dict[str, str]:
    """f1's CRAP goes from the wrong 1.5 to `new` (1.0 at ccn 1, cov 1.0), declared in
    full; `listed` replaces the moved.tsv rows the change records."""
    rows = scored_rows(f_crap={1: new})
    head = {**tree, SCORED: scored(rows)}
    head = relock(change(head, key, kind, calcs, analysis="12"), key, SCORED)
    head = moved(head, key, list(listed if listed is not None else [
        (SCORED, "src/a.py", "f1", "crap", "1.5", new, "kit.exact", "1.0", "")]))
    head = with_digest(bump(changelog(head, key), "12"), "12", key)
    return with_bug(head) if bug else head


def uninitialized() -> dict[str, str]:
    """base() before `lock --initial`: the four tables hold headers only and no
    change is declared yet."""
    tree = base()
    headers = {path: tree[path].splitlines()[0] + "\n" for path in (CHANGES, LOCK, DIGESTS,
                                                                    COUNTS)}
    return {**tree, **headers, "CHANGELOG.md": "# Changelog\n"}


def without_line(tree: dict[str, str], path: str, start: str) -> dict[str, str]:
    """The tree with the first line of `path` that starts with `start` removed."""
    line = next(line for line in tree[path].splitlines() if line.startswith(start))
    return replace(tree, path, line + "\n", "")


def with_bug(tree: dict[str, str], bug: str = "R02") -> dict[str, str]:
    tree = append(tree, BUGS, bug, "any", "3333333", "4444444")
    return append(tree, RETRO, bug, f"{SEED_TEST}::test_crap")


def fixed_ccn(tree: dict[str, str], key: str = "C3", calcs: str = CCN) -> dict[str, str]:
    """parse's ccn goes from 8 (base(ccn8())) to 7, where radon puts it; its CRAP
    follows, 64 * 0.125 + 8 = 16.0 to 13.125."""
    rows = scored_rows()
    head = {**tree, SCORED: scored(rows), INVENTORY: inventory(rows)}
    head = relock(change(head, key, "fix", calcs, analysis="12"), key, SCORED, INVENTORY)
    head = moved(head, key, ccn_cells())
    return with_bug(with_digest(bump(changelog(head, key), "12"), "12", key))


def ccn8() -> list[dict]:
    return scored_rows(parse_ccn=8, parse_crap="16.0")


def ccn_cells() -> list[tuple]:
    """The seven cells fixed_ccn moves, in (golden, path, handle, column) order."""
    scored_ = [(SCORED, "src/a.py", "parse", column, "8", "7", "radon", "7", "")
               for column in ("ccn", "ccn_mod", "ccn_std")]
    crap = [(SCORED, "src/a.py", "parse", "crap", "16.0", "13.125", "kit.exact", "13.125", "")]
    inventory_ = [(INVENTORY, "src/a.py", "parse", column, "8", "7", "radon", "7", "")
                  for column in ("ccn", "ccn_mod", "ccn_std")]
    return inventory_ + scored_[:3] + crap


def module_changed(tree: dict[str, str]) -> dict[str, str]:
    """A comment added to the CRAP module: a calc-module diff that moves nothing."""
    return replace(tree, MODULE, "** 3 + ccn", "** 3 + ccn  # the README formula")


def reader_changed(tree: dict[str, str]) -> dict[str, str]:
    """An edit to analyze.py beyond its ANALYSIS_VERSION line."""
    return replace(tree, ANALYZE, "import lizard\n", "import lizard  # the reader\n")


def fixed_gate(tree: dict[str, str], key: str = "C3") -> dict[str, str]:
    """A fix to the pre-commit gate, a calc no golden shows: hook.py changes and
    nothing in the goldens moves."""
    head = replace(tree, HOOK, "row not in marks", "row.key not in marks")
    return with_bug(changelog(change(head, key, "fix", "Pre-commit gate"), key))



# --- the trees as git repos ---------------------------------------------------------------

def delta(before: dict, after: dict) -> dict:
    """The files a commit writes (None deletes) to turn `before` into `after`."""
    gone = {path: None for path in before if path not in after}
    return {**gone, **{path: text for path, text in after.items() if before.get(path) != text}}


def write(top, before: dict, after: dict) -> None:
    """Turn the working tree at `top` from `before` into `after`."""
    for path, text in delta(before, after).items():
        target = top / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))


def commit(top, before: dict, after: dict, number: int) -> None:
    """One commit turning `before` into `after`, with two git calls."""
    from accuracy.kit import repos
    write(top, before, after)
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "--allow-empty", "-m", f"step {number}",
              date=repos.EPOCH + 60 * number)


def seeded(make_repo, *trees: dict):
    """A repo whose commits hold `trees` in order; the first is built once per session."""
    from accuracy.kit import repos
    top = make_repo(repos.Spec(steps=(repos.Commit(files=trees[0], message="base"),))).top
    for number, (before, after) in enumerate(zip(trees, trees[1:]), start=1):
        commit(top, before, after, number)
    return top
