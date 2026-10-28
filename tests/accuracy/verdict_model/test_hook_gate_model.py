"""The commit gate's warn, held to model_verdict.hook_finding with a diff base.

docs/ratchet.md, the commit gate: a staged marked function over its ceiling
whose range, ccn up to its untested CRAP, straddles its mark passes. When its
ccn rose above its ccn at the diff base, or the base holds no function under its
key, the hook names it in a warn block on stderr and still exits 0, and the
function leaves the count line. The model restates that; the rows below hold
the model to the four base cases, and the generated tree holds crapkit to the
model, never to itself.
"""
from __future__ import annotations

from decimal import Decimal
import re

import pytest

from accuracy.kit import drive, repos
from accuracy.verdict_model import model_verdict as model

CEILING = 2
STEP = Decimal("0.0001")
CONFIG = (f'[crapkit]\ntarget = {CEILING}\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
          'languages = ["python"]\n')
_WARN = re.compile(r"^  ccn (\S+) -> (\d+)  (\S+):\d+  (.+?)  mark \d+\.\d{4}$", re.M)
_RISE = re.compile(r"^  ccn +\d+  (\S+):\d+  (.+?)  mark \d+\.\d{4}$", re.M)
_COUNT = re.compile(r"^crapkit gate: (\d+) staged function\(s\) carry a ratchet mark", re.M)


# --- the model on its own: rose, equal, fell and absent at the base --------------------

def _straddling(ccn: int) -> Decimal:
    """A mark inside the range ccn to ccn^2 + ccn: its low end."""
    low, _ = model.hook_range(ccn)
    return model.mark_value(low)


@pytest.mark.parametrize(("base_ccn", "finding"), [
    pytest.param(11, "unproven_rise", id="rose"),
    pytest.param(12, "unproven", id="equal"),
    pytest.param(13, "unproven", id="fell"),
    pytest.param(None, "unproven_rise", id="absent"),
    pytest.param(model.UNASKED, "unproven", id="no-base-asked"),
])
def test_a_straddling_function_warns_exactly_when_its_ccn_rose_or_the_base_lacks_it(base_ccn, finding):
    """ccn 12 against its own low end, 12.0000: the range 12 to 156 straddles."""
    assert model.hook_finding(12, 6, _straddling(12), base_ccn=base_ccn) == finding
    assert model.hook_warns(12, 6, _straddling(12), base_ccn) == (finding == "unproven_rise")
    assert model.hook_gate(12, 6, _straddling(12), base_ccn=base_ccn) is False


@pytest.mark.parametrize(("mark", "finding"), [
    pytest.param(Decimal("156.0000"), "pardoned", id="the-high-end-pardons"),
    pytest.param(Decimal("11.9999"), "marked_rise", id="ccn-past-the-mark"),
    pytest.param(None, "over_ceiling", id="no-mark"),
])
def test_a_rise_off_the_straddle_never_warns(mark, finding):
    """The base matters only to a range across the mark."""
    assert {model.hook_finding(12, 6, mark, base_ccn=base) for base in (None, 11, 12, 13)} == {finding}


def test_the_example_the_ratchet_page_shows_warns():
    """docs/ratchet.md: classify marked 110.0000 at ccn 10, edited to ccn 12."""
    assert model.hook_finding(12, 6, Decimal("110.0000"), base_ccn=10) == "unproven_rise"


# --- crapkit against the model on a generated tree -------------------------------------

STAGED_CCNS = (3, 5, 9)
# Each case's base ccn for a staged ccn; None is a file the base does not hold.
BASES = {"rose": lambda ccn: ccn - 1, "equal": lambda ccn: ccn, "fell": lambda ccn: ccn + 1,
         "absent": lambda ccn: None}


def _source(name: str, ccn: int, note: bool = False) -> str:
    """`def NAME(n)` with ccn - 1 ifs; `note` puts a comment inside, which
    touches the function and moves no ccn."""
    body = "".join(f"    if n > {i}:\n        n += 1\n" for i in range(1, ccn))
    return f"def {name}(n):\n{'    # note' + chr(10) if note else ''}{body}    return n\n"


def _marks(ccn: int) -> list[Decimal]:
    """One step under the low end (a marked rise), the low end, the middle and
    one step under the high end (all three across the range), and the high
    end (pardoned)."""
    low, high = (model.mark_value(end) for end in model.hook_range(ccn))
    return [low - STEP, low, model.mark_value((low + high) / 2), high - STEP, high]


def _cases() -> list[tuple[str, int, str, int | None, Decimal]]:
    """(path, staged ccn, function name, base ccn, mark) for every function."""
    return [(f"src/c{ccn}_{case}.py", ccn, f"f{i}", base(ccn), mark)
            for ccn in STAGED_CCNS for case, base in BASES.items() for i, mark in enumerate(_marks(ccn))]


def _files(cases, staged: bool) -> dict[str, str]:
    """Each file's source: the staged side, or the base side (no file where the base holds none)."""
    files: dict[str, list[str]] = {}
    for path, ccn, name, base_ccn, _ in cases:
        if staged:
            files.setdefault(path, []).append(_source(name, ccn, note=base_ccn == ccn))
        elif base_ccn is not None:
            files.setdefault(path, []).append(_source(name, base_ccn))
    return {path: "\n\n".join(sources) for path, sources in files.items()}


@pytest.mark.process
def test_every_warn_rise_and_count_on_the_generated_tree_is_the_models(make_repo):
    cases = _cases()
    marks = "path\tlong_name\tcrap\n" + "".join(f"{path}\t{name}( n )\t{mark:.4f}\n"
                                                 for path, _, name, _, mark in cases)
    built = make_repo(repos.Spec(steps=(repos.Commit(
        files={"crapkit.toml": CONFIG, "crapkit-ratchet.tsv": marks, **_files(cases, staged=False)},
        message="base"),)))
    for path, source in _files(cases, staged=True).items():
        (built.root / path).write_bytes(source.encode("utf-8"))
    repos.git(built.top, "add", "-A")
    found = {(path, f"{name}( n )"): model.hook_finding(ccn, CEILING, mark, base_ccn=base_ccn)
             for path, ccn, name, base_ccn, mark in cases}

    result = drive.Driver(built.root, env={"CRAPKIT_OVERRIDE_REASON": None}).run("hook-precommit")

    def kind(wanted: str) -> set:
        return {key for key, finding in found.items() if finding == wanted}

    carried = len(kind("pardoned")) + len(kind("unproven"))
    assert {(path, name) for _, _, path, name in _WARN.findall(result.stderr)} == kind("unproven_rise")
    assert set(_RISE.findall(result.stdout)) == kind("marked_rise"), result.stdout
    assert [int(n) for n in _COUNT.findall(result.stderr)] == [carried], result.stderr
    assert result.code == (6 if kind("marked_rise") else 0), result.stdout + result.stderr
    assert {finding for finding in found.values()} == {"marked_rise", "pardoned", "unproven", "unproven_rise"}
