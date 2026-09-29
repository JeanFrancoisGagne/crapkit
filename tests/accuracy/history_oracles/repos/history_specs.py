"""Git histories for the history packet, as kit.repos specs.

Every date is fixed, so a spec builds the same commits on every OS. The churn
specs pass a `now` beside the spec: the GIT_TEST_DATE_NOW crapkit runs on. The
churn window counts back from HEAD's commit date, never from that clock. Paths
stay legal on Windows; a spec that needs a quote or a control character in a
path lives in a Linux-only test.
"""
from __future__ import annotations

from accuracy.kit.repos import EPOCH, Branch, Checkout, Commit, Merge, Spec

DAY = 86_400
THOR = ("A U Thor", "author@example.com")
THOR_AT_WORK = ("A U Thor", "thor@work.example")  # one name, a second address
BEA = ("Bea Ruiz", "bea@example.com")
CHEN = ("Chen Li", "author@example.com")  # another name on THOR's first address
OLD = ("Old Timer", "old@example.com")
NON_ASCII = "src/ütf.py"


def config(months: int = 12, root_paths: str = "src") -> str:
    """A crapkit.toml that admits every function to the worklist and needs no lane."""
    return (f"[crapkit]\ntarget = 3\nworklist_floor = 1\nworklist_top = 1000\n"
            f"churn_window_months = {months}\n\n[[scope]]\nname = \"src\"\n"
            f"paths = [\"{root_paths}\"]\nlanguages = [\"python\"]\ncoverage_optional = true\n")


def functions(*names: str, branches: int = 1, tag: str = "") -> str:
    """Python source with one function per name, each with `branches` ifs."""
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(branches))
    return "".join(f"def {name}(x):\n{body}    return '{tag}'\n\n\n" for name in names)


# --- hand weights: commits at t = 0, 1/2, 3/4, 11/12 and 1 of the range -------------------

CLOCK_RANGE = 1_200_000
CLOCK_T = {0: 0, 1: 600_000, 2: 900_000, 3: 1_100_000, 4: 1_200_000}
_CLOCK_FILES = {
    0: ("all", "old"), 1: ("all", "mid"), 2: ("all", "mid"), 3: ("all", "late"),
    4: ("all", "new"),
}


def _clock_commit(step: int) -> Commit:
    files = {f"src/{name}.py": functions("f", tag=f"c{step}") for name in _CLOCK_FILES[step]}
    if step == 0:
        files["crapkit.toml"] = config()
    author = BEA if step == 2 else THOR
    return Commit(files=files, date=EPOCH + CLOCK_T[step], message=f"clock {step}", author=author)


CLOCK = Spec(steps=tuple(_clock_commit(step) for step in sorted(CLOCK_T)))
CLOCK_NOW = EPOCH + CLOCK_RANGE + DAY


# --- a mixed history the numstat walk reads path by path -------------------------------------

MIXED = Spec(steps=(
    Commit(files={"src/core.py": functions("ancient"), "src/ancient.py": functions("a")},
           date=EPOCH - 400 * DAY, message="before the window", author=OLD),
    Commit(files={"crapkit.toml": config(), "src/core.py": functions("load", "save"),
                  "src/util.py": functions("pad"), "src/old_name.py": functions("moved", "kept"),
                  "src/gone.py": functions("gone"), "docs/readme.md": "# mixed\n"},
           date=EPOCH, message="seed", author=THOR),
    Commit(files={"src/core.py": functions("load", "save", branches=2),
                  NON_ASCII: functions("u")},
           date=EPOCH + 10 * DAY, message="non-ASCII path", author=BEA),
    Commit(files={"src/util.py": functions("pad", branches=2),
                  "src/with space.py": functions("spaced")},
           date=EPOCH + 20 * DAY, message="second address", author=THOR_AT_WORK),
    Branch("side"),
    Commit(files={"src/core.py": functions("load", "save", "side")},
           date=EPOCH + 25 * DAY, message="on a branch", author=CHEN),
    Checkout("main"),
    Commit(files={"src/new_name.py": functions("moved", "kept", tag="edit")},
           renames={"src/old_name.py": "src/new_name.py"},
           date=EPOCH + 30 * DAY, message="rename", author=THOR),
    Commit(files={"src/gone.py": None, NON_ASCII: functions("u", branches=3)},
           date=EPOCH + 40 * DAY, message="delete", author=BEA),
    Merge("side", message="merge side", date=EPOCH + 45 * DAY),
    Commit(files={"src/new_name.py": functions("moved", "kept", branches=2),
                  "src/core.py": functions("load", "save", "side", branches=2)},
           date=EPOCH + 60 * DAY, message="after merge", author=CHEN),
    Commit(files={"docs/readme.md": "# mixed\n\nmore\n"},
           date=EPOCH + 90 * DAY, message="docs only", author=THOR),
    Commit(files={NON_ASCII: functions("u", branches=4), "src/util.py": functions("pad", "trim")},
           date=EPOCH + 120 * DAY, message="last", author=THOR),
))
MIXED_NOW = EPOCH + 130 * DAY


# --- one timestamp: a one-commit repo, and three commits sharing a second -----------------

ONE_COMMIT = Spec(steps=(
    Commit(files={"crapkit.toml": config(), "src/a.py": functions("f", branches=3),
                  "src/b.py": functions("g")}, date=EPOCH, message="only"),
))
SAME_SECOND = Spec(steps=tuple(
    Commit(files={"crapkit.toml": config(), "src/a.py": functions("f", tag=str(step))}
           if step == 0 else {"src/a.py": functions("f", tag=str(step)),
                              "src/b.py": functions("g", tag=str(step))},
           date=EPOCH, message=f"same second {step}", author=(THOR, BEA, CHEN)[step])
    for step in range(3)))
ONE_NOW = EPOCH + DAY


# --- a root below the git top; the newest commit changes nothing under it -----------------

NESTED = Spec(root="app", steps=(
    Commit(files={"app/crapkit.toml": config(), "app/src/a.py": functions("f"),
                  "lib/shared.py": functions("s")}, date=EPOCH, message="seed"),
    Commit(files={"app/src/a.py": functions("f", branches=2), "app/src/b.py": functions("g")},
           date=EPOCH + 5 * DAY, message="inside", author=BEA),
    Commit(files={"lib/shared.py": functions("s", branches=2), "app/src/b.py": functions("g", "h")},
           date=EPOCH + 9 * DAY, message="both sides"),
    Commit(files={"lib/shared.py": functions("s", branches=3)},
           date=EPOCH + 12 * DAY, message="above the root only"),
))
NESTED_NOW = EPOCH + 20 * DAY


# --- a merged branch that puts a weight sum on a 4-place rounding edge (R57) -------------
# src/p.py is changed by oscar (the oldest commit), rita (on a branch), sue and sam
# (the newest). Merged after sam, rita's commit is carried above sam's and sue's while
# git's log lists it below them. The dates are far in the future so that one second
# steps a weight by about 1e-16; they were picked by a search over dates for a sum
# whose correctly rounded value is 0.5720 and whose plain float sum in the carried
# order (rita, sam, sue, oscar) rounds to 0.5719. The expected 0.5720 comes from
# kit.exact, not from that search.
ORDER_O = EPOCH
ORDER_RITA = 3_850_001_337_500_000
ORDER_SUE = 5_467_832_636_718_751
ORDER_SAM = 7_000_001_000_000_000
# The window counts back from HEAD's commit date, here in the year 221,823,171, and
# 12 months back from there hold sam's commit alone. 2.7 billion months, 225 million
# years, reach back past 1970, so the window holds all four commits.
ORDER_EDGE_MONTHS = 2_700_000_000


def _p(head: str, tail: str) -> str:
    return (f"def p(x):\n    return '{head}'\n\n\ndef q(x):\n    return 1\n\n\n"
            f"TAIL = '{tail}'\n")


ORDER_EDGE = Spec(steps=(
    Commit(files={"crapkit.toml": config(months=ORDER_EDGE_MONTHS), "src/p.py": _p("o", "o"),
                  "src/z.py": functions("z")},
           date=ORDER_O, message="oscar", author=("oscar", "oscar@example.com")),
    Branch("side"),
    Commit(files={"src/p.py": _p("r", "o")}, date=ORDER_RITA, message="rita",
           author=("rita", "rita@example.com")),
    Checkout("main"),
    Commit(files={"src/p.py": _p("o", "s")}, date=ORDER_SUE, message="sue",
           author=("sue", "sue@example.com")),
    Commit(files={"src/p.py": _p("o", "m")}, date=ORDER_SAM, message="sam",
           author=("sam", "sam@example.com")),
    Merge("side", message="merge rita", date=ORDER_SAM + 1),
))
ORDER_EDGE_NOW = EPOCH + 30 * DAY


# --- an author whose only commit ages out between two runs (R103) ------------------------

EXPIRY = Spec(steps=(
    Commit(files={"crapkit.toml": config(), "src/a.py": functions("f")}, date=EPOCH,
           message="old", author=OLD),
    Commit(files={"src/a.py": functions("f", branches=2)}, date=EPOCH + 200 * DAY,
           message="middle"),
    Commit(files={"src/b.py": functions("g")}, date=EPOCH + 380 * DAY, message="new"),
))
EXPIRY_BEFORE = EPOCH + 250 * DAY  # the old commit is in the window
EXPIRY_AFTER = EPOCH + 400 * DAY  # 12 months back no longer reaches it


# --- co-change: known pairs, a bulk commit, a renamed partner, a non-ASCII name ----------
# The hand counts are in hand_pairs.tsv. Two commits are bulk (over 30 files): the seed
# and the last one. src/x.py is renamed to src/z.py, so its pair with src/y.py names a
# file git no longer tracks.
UMLAUT = "src/\u00fcmlaut.py"
BULK = tuple(f"bulk/f{i:02d}.py" for i in range(29))
_COUPLED_SOURCES = ("src/a.py", "src/b.py", "src/c.py", "src/d.py", UMLAUT, "src/x.py",
                    "src/y.py")


def _touch(paths, step: int) -> dict:
    return {path: functions("f", tag=f"{path}@{step}") for path in paths}


def _coupled_steps() -> tuple:
    groups = ([("src/a.py", "src/b.py")] * 5 + [("src/a.py", "src/c.py")] * 2
              + [("src/c.py", "src/d.py")] + [("src/b.py", UMLAUT)] * 5
              + [("src/x.py", "src/y.py")] * 5)
    steps = [Commit(files={"crapkit.toml": config(), **_touch(_COUPLED_SOURCES + BULK, 0)},
                    date=EPOCH, message="seed: a bulk commit")]
    steps += [Commit(files=_touch(group, step), date=EPOCH + step * DAY, message=f"pair {step}")
              for step, group in enumerate(groups, start=1)]
    steps.append(Commit(files=_touch(("src/y.py",), 40), renames={"src/x.py": "src/z.py"},
                        date=EPOCH + 40 * DAY, message="rename x to z"))
    steps.append(Commit(files=_touch(("src/a.py", "src/b.py") + BULK, 41),
                        date=EPOCH + 41 * DAY, message="a bulk commit"))
    return tuple(steps)


COUPLED = Spec(steps=_coupled_steps())
COUPLED_NOW = EPOCH + 60 * DAY



# --- two pairs whose support x confidence tie exactly (H14) ---------------------------------
# src/a.py and src/b.py share 1 of their 3 commits each: support 1, confidence 1/3 = 0.3333.
# src/y.py and src/z.py share 3 of their 27 each: support 3, confidence 3/27 = 0.1111.
# Both products are 0.3333, so the paths decide: a.py's pair first. In binary floating
# point 3 * 0.1111 is 0.33330000000000004 and 1 * 0.3333 is 0.3333.
TIED_SETS = tuple([("src/a.py", "src/b.py")] + [("src/a.py",), ("src/b.py",)] * 2
                  + [("src/y.py", "src/z.py")] * 3 + [("src/y.py",), ("src/z.py",)] * 24)

# --- a month end: 6 months before Aug 31 is Mar 3, before Sep 1 it is Mar 1 (R59) ---------
# HEAD^1 is committed on Aug 31 and HEAD on Sep 1, so moving HEAD moves the cutoff back.
AUG_31 = 1_756_641_600  # 2025-08-31T12:00:00Z
SEP_1 = AUG_31 + DAY
MARCH_2 = 1_740_916_800  # 2025-03-02T12:00:00Z: out of the window on Aug 31, in on Sep 1

MONTH_END = Spec(steps=(
    Commit(files={"crapkit.toml": config(months=6), "src/a.py": functions("f"),
                  "src/b.py": functions("g")}, date=1_736_510_400, message="january"),
    Commit(files={"src/a.py": functions("f", branches=2)}, date=MARCH_2, message="march 2",
           author=BEA),
    Commit(files={"src/a.py": functions("f", branches=3), "src/b.py": functions("g", "h")},
           date=1_748_779_200, message="june"),
    Commit(files={"src/b.py": functions("g", "h", branches=2)}, date=AUG_31,
           message="august 31", author=CHEN),
    Commit(files={"src/c.py": functions("k")}, date=SEP_1 - 6 * 3_600, message="september 1"),
))


# --- a commit that lands while crapkit reads the window (R58) -----------------------------

INJECT = Spec(steps=(
    Commit(files={"crapkit.toml": config(), "src/a.py": functions("f")}, date=EPOCH,
           message="first"),
    Commit(files={"src/a.py": functions("f", branches=2), "src/b.py": functions("g")},
           date=EPOCH + 3 * DAY, message="second", author=BEA),
))
INJECT_LANDS = EPOCH + 4 * DAY
INJECT_NOW = EPOCH + 10 * DAY


# --- a file of five gated functions and the edits a diff makes to it ----------------------
# Each function has ccn 5 against a target of 3, so every function a diff touches is
# over its ceiling and named by the gate. f2's docstring carries a line starting "-- ",
# and an edit may add one starting "++ ": in a -U0 diff they read "--- " and "+++ ".


def gated(name: str, tag: str = "a", doc: str = "") -> str:
    ifs = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(4))
    return f'def {name}(x):\n    """{name}\n{doc}"""\n{ifs}    return \'{tag}\'\n\n\n'


GATED_TEXT = (gated("f0") + gated("f1") + gated("f2", doc="-- note\n") + gated("f3")
              + gated("f4"))
GATED = Spec(steps=(Commit(files={"crapkit.toml": config(), "src/m.py": GATED_TEXT},
                           date=EPOCH, message="five gated functions"),))
GATED_NOW = EPOCH + DAY
GATED_EDITS = {
    # name: the new text of src/m.py; hand_gated.tsv names the functions each edit changes
    "plus_plus": (gated("f0") + gated("f1", doc="++ marker\n") + gated("f2", doc="-- note\n")
                  + gated("f3", tag="b") + gated("f4")),
    "mixed": (gated("f0") + gated("f1", doc="++ marker\n") + gated("f2") + gated("f3", tag="b")
              + gated("f4", tag="b").rstrip("\n")),
    "deletion_only": gated("f0") + gated("f1") + gated("f2") + gated("f3") + gated("f4"),
}


# --- diffs a -U0 reader must survive: CRLF, no final newline, a non-ASCII name, lines
# starting "++ " and "-- ", and a move read with --no-renames ---------------------------
DIFF_BASE = {
    "src/crlf.py": b"a\r\nb\r\nc\r\nd\r\n",
    "src/noeol.py": b"x\ny\nz\n",
    "src/\u00fc.py": "1\n2\n3\n4\n5\n6\n".encode(),
    "src/plus.py": b"p1\np2\np3\np5\np6\np7\n-- removed\np8\n",
    "src/old.py": b"r1\nr2\nr3\n",
}
DIFF_EDIT = {
    "src/crlf.py": b"a\r\nb\r\nC\r\nd\r\n",
    "src/noeol.py": b"x\ny\nZ",
    "src/\u00fc.py": "1\nTWO\n3\n4\nFIVE\n6\n".encode(),
    "src/plus.py": b"p1\np2\np3\n++ added\np5\np6\np7\np8\n",
}
# hand_ranges.tsv holds the new-side lines each edit changed, worked by hand.
DIFF_CASES = Spec(steps=(
    Commit(files=DIFF_BASE, date=EPOCH, message="base"),
    Commit(files=DIFF_EDIT, renames={"src/old.py": "src/new.py"}, date=EPOCH + DAY,
           message="edits"),
))


# --- renames, moves and copies after a seed (docs/ratchet.md#pruning-and-renames) --------
# Five files of gated functions are seeded; then one commit moves, copies and renames
# them. RENAME_MOVES says what each file becomes and hand_renames.tsv where the docs'
# three conditions put its marks after `ratchet prune`, worked by hand.


def _heavy(name: str) -> str:
    """Only the def line survives: well under git's 50% similarity."""
    body = "".join(f"    total = {i} * x + {i * i}\n    total -= {i}\n" for i in range(12))
    return f"def {name}(x):\n{body}    return total\n\n\n"


RENAME_BASE = {"crapkit.toml": config(), "src/a.py": gated("fa") + gated("ga"),
               "src/b.py": gated("fb"), "src/c.py": gated("fc"), "src/d.py": gated("fd") + gated("gd"),
               "src/e.py": gated("fe")}
RENAMES = Spec(steps=(Commit(files=RENAME_BASE, date=EPOCH, message="five files"),))
RENAME_MOVES = {
    # old path: (new path, new text, None when the old path is removed)
    "exact move": ("src/a.py", "src/moved/a.py", None),
    "copy": ("src/b.py", "src/b_copy.py", "keep"),
    "heavy edit": ("src/c.py", "src/c2.py", _heavy("fc")),
    "light edit": ("src/d.py", "src/d2.py", gated("fd") + gated("gd", tag="edited")),
    "function renamed": ("src/e.py", "src/e2.py", gated("fe_new")),
}
RENAMES_NOW = EPOCH + 2 * DAY
RENAMES_NESTED = Spec(root="pkg", steps=(
    Commit(files={"pkg/crapkit.toml": config(root_paths="calc"),
                  "pkg/calc/grade.py": gated("audit") + gated("classify"),
                  "lib/other.py": gated("other")}, date=EPOCH, message="nested"),
))


# --- the ratchet file's own history (docs/ratchet.md#reporting-the-burn-down) -------------


def marks_file(*rows: tuple[str, str, str], note: str = "") -> str:
    """A ratchet file: the two stamp comments, the header, one row per mark."""
    head = "# crapkit-analysis=11 lizard=1.24.0\n# crapkit-keys=1\n" + note
    return head + "path\tlong_name\tcrap\n" + "".join("\t".join(row) + "\n" for row in rows)


_A = ("src/a.py", "fa( x )")
_B = ("src/b.py", "fb( x )")
_C = ("src/c.py", "fc( x )")
_D = ("src/d.py", "fd( x )")
_BURN_SOURCES = {path: gated(name.split("(")[0]) for path, name in (_A, _B, _C, _D)}
BURN = Spec(steps=(
    Commit(files={"crapkit.toml": config(), **_BURN_SOURCES,
                  "crapkit-ratchet.tsv": marks_file((*_A, "12.0000"), (*_B, "8.0000"))},
           date=EPOCH, message="seed a and b"),
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "12.0000"), (*_B, "8.0000"),
                                                     (*_C, "9.0000"))},
           date=EPOCH + 10 * DAY, message="add c"),
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "10.0000"), (*_B, "8.0000"),
                                                     (*_C, "9.0000"))},
           date=EPOCH + 40 * DAY, message="tighten a"),
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "10.0000"), (*_C, "9.0000"))},
           date=EPOCH + 50 * DAY, message="repay b"),
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "10.0000"), (*_C, "9.0000"),
                                                     note="# a comment and nothing else\n")},
           date=EPOCH + 70 * DAY, message="comment only"),
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "10.0000"), (*_B, "8.0000"))},
           date=EPOCH + 75 * DAY, message="b back, c repaid"),
))
# On disk after the last commit: a tightened again, d added, neither committed.
BURN_WORKING = marks_file((*_A, "9.0000"), (*_B, "8.0000"), (*_D, "7.0000"))
# The newest commit only tightens a mark (R32): it still moves the anchor.
BURN_TIGHTEN = Spec(steps=BURN.steps[:1] + (
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "12.0000"), (*_B, "8.0000"),
                                                     (*_C, "9.0000"))},
           date=EPOCH + 10 * DAY, message="add c"),
    Commit(files={"crapkit-ratchet.tsv": marks_file((*_A, "10.0000"), (*_B, "8.0000"),
                                                     (*_C, "9.0000"))},
           date=EPOCH + 40 * DAY, message="tighten a"),
))
# Seeded by crapkit itself, so the marks carry the stamp this crapkit writes.
BURN_SEEDABLE = Spec(steps=(Commit(files={"crapkit.toml": config(), **_BURN_SOURCES},
                                   date=EPOCH, message="sources"),))


# --- commit messages explain --history lists (README.md explain row) --------------------
# f is changed by every commit below; g by one commit that must not appear in f's list.


def explained(tag: str = "0", other: str = "0") -> str:
    return gated("f", tag=tag) + gated("g", tag=other)


EXPLAIN_PLAIN = Spec(steps=(
    Commit(files={"crapkit.toml": config(), "src/e.py": explained()}, date=EPOCH,
           message="seed f and g"),
    Commit(files={"src/e.py": explained("1")}, date=EPOCH + DAY,
           message="Tighten f\n\nThe first paragraph explains\nwhy, over two lines.\n\n"
                   "  - an indented item\n  - another, caf\u00e9\n"),
    Commit(files={"src/e.py": explained("1", other="1")}, date=EPOCH + 2 * DAY,
           message="Touch g only"),
    Commit(files={"src/e.py": explained("2", other="1")}, date=EPOCH + 3 * DAY,
           message="A subject\nthat wraps\n\nbody after a wrapped subject\n+++ not a header\n"
                   "@@ -1 +1 @@ not a hunk\n"),
    Commit(files={"src/e.py": explained("3", other="1")}, date=EPOCH + 4 * DAY,
           message="Subject only"),
))
# Commits whose bodies hold the characters explain's log format frames records with
# (\x01 opens one, \x02 closes it), or characters Python splits lines at.
EXPLAIN_ODD_BODIES = {
    "stx_line": "stx body\n\nline one\n\x02\nafter the stx line\n",
    "soh_line": "soh body\n\nfirst\n\x01zz 2025-01-01 fake subject\nlast\n",
    "soh_word": "soh short\n\n\x01only\nlast\n",
    "separators": "separators\n\nhalf\rway, before\x0cafter\nsep\x1crecord, split\x85here\n",
}


def explain_odd(body: str) -> Spec:
    return Spec(steps=(
        Commit(files={"crapkit.toml": config(), "src/e.py": explained()}, date=EPOCH,
               message="seed"),
        Commit(files={"src/e.py": explained("odd")}, date=EPOCH + DAY, message=body),
    ))


# --- a commit rebased later: its committer date is not its author date (H4) ---------------
SKEW_RANGE = CLOCK_RANGE
SKEW = Spec(steps=(
    Commit(files={"crapkit.toml": config(), "src/x.py": functions("x"), "src/y.py": functions("y")},
           date=EPOCH, message="seed"),
))
SKEW_AUTHOR = EPOCH + SKEW_RANGE // 4  # t = 1/4 by the author's clock
SKEW_COMMITTER = EPOCH + SKEW_RANGE * 9 // 10  # t = 9/10 by the committer's
SKEW_LAST = EPOCH + SKEW_RANGE


# --- a synthetic 60-commit history for the corpus checks ----------------------------------
# Drawn once from a seeded generator, so every build is the same: 60 commits over 13
# months (the oldest fall out of a 12-month window), four authors, a pool of paths with
# a non-ASCII name and a space, a rename, a branch merged back, a commit on the last
# day of a month and one of 31 files.
_POOL = ("src/core.py", "src/util.py", "src/io.py", "src/cli.py", "src/\u00e9t\u00e9.py",
         "src/with space.py", "src/model.py", "docs/guide.md", "tests/test_core.py",
         "tests/test_io.py", "setup.cfg", "src/extra.py")
_AUTHORS = (THOR, BEA, CHEN, THOR_AT_WORK)
SYNTHETIC_START = 1_720_000_000  # 2024-07-03
SYNTHETIC_NOW = SEP_1  # 12 months back drops the first nine weeks


def _synthetic_commit(rng, step: int, when: int) -> Commit:
    paths = rng.sample(_POOL, rng.randint(1, 4))
    files = {path: functions("f", tag=f"{step}") for path in paths}
    if step == 0:
        files.update({"crapkit.toml": config(), **{p: functions("f") for p in _POOL}})
    return Commit(files=files, date=when, message=f"step {step}", author=rng.choice(_AUTHORS))


def _synthetic_steps() -> tuple:
    import random

    rng, steps = random.Random(60), []
    for step in range(56):
        steps.append(_synthetic_commit(rng, step, SYNTHETIC_START + step * 7 * DAY))
        if step == 30:  # a branch whose one commit only it touches, merged back later
            steps += [Branch("side"),
                      Commit(files={"src/side.py": functions("s")}, date=SYNTHETIC_START + 211 * DAY,
                             message="on a branch", author=BEA),
                      Checkout("main")]
    steps += [
        Merge("side", message="merge side", date=SYNTHETIC_START + 395 * DAY),
        Commit(files={"src/renamed.py": functions("f", tag="moved")},
               renames={"src/extra.py": "src/renamed.py"}, date=SYNTHETIC_START + 396 * DAY,
               message="rename extra"),
        Commit(files={f"bulk/b{i:02d}.txt": f"{i}\n" for i in range(31)},
               date=SYNTHETIC_START + 397 * DAY, message="a bulk commit"),
        Commit(files={"src/core.py": functions("f", tag="month end")},
               date=1_756_684_799, message="the last second of August 2025"),
    ]
    return tuple(steps)


SYNTHETIC = Spec(steps=_synthetic_steps())
