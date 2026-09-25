"""Git histories for the history packet, as kit.repos specs.

Every date is fixed, so a spec builds the same commits on every OS. The churn
specs pass a `now` beside the spec: the GIT_TEST_DATE_NOW the window is read
at. Paths stay legal on Windows; a spec that needs a quote or a control
character in a path lives in a Linux-only test.
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


def _p(head: str, tail: str) -> str:
    return (f"def p(x):\n    return '{head}'\n\n\ndef q(x):\n    return 1\n\n\n"
            f"TAIL = '{tail}'\n")


ORDER_EDGE = Spec(steps=(
    Commit(files={"crapkit.toml": config(), "src/p.py": _p("o", "o"), "src/z.py": functions("z")},
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
