"""One tokenization per file, with both ccn columns falling out of it.

Modified ccn used to cost a second full lizard pass over the same bytes. It is
not a different reading of the file: lizard's modified extension adds +1 on a
switch/match opener and -1 on each arm and touches nothing else, so

    ccn_mod == ccn_std + (switch openers - case arms)

The delta is counted in pass 1 and the second pass is gone. ccn = min(std, mod)
feeds the gate, so the values pinned here are the contract, measured against the
two-pass implementation they replaced.
"""
from pathlib import Path

import lizard

from crapkit.analyze import analyze_one
from crapkit.lizardcognitive import LizardExtension as Cognitive
from crapkit.merge import RawFn, merge_passes

TS_SWITCH = """export function dispatch(kind: string): number {
  switch (kind) {
    case "a": return 1;
    case "b": return 2;
    case "c": return 3;
    default: return 0;
  }
}
"""

PY_MATCH = """def dispatch(kind):
    match kind:
        case "a":
            return 1
        case "b":
            return 2
        case _:
            return 0
"""

PY_IFS = """def grade(n):
    if n > 90:
        return "a"
    if n > 80:
        return "b"
    return "c"
"""


def _records(tmp_path: Path, name: str, source: str):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    _, records = analyze_one((str(p), name))
    return records


def test_a_typescript_switch_counts_as_one_branch_in_the_modified_column(tmp_path):
    (rec,) = _records(tmp_path, "d.ts", TS_SWITCH)

    assert (rec.ccn_std, rec.ccn_mod, rec.ccn) == (4, 2, 2)


def test_a_python_match_counts_as_one_branch_too(tmp_path):
    """`match` and `case` are soft keywords: the reader decides, not the token."""
    (rec,) = _records(tmp_path, "d.py", PY_MATCH)

    assert (rec.ccn_std, rec.ccn_mod, rec.ccn) == (4, 2, 2)


def test_a_file_with_no_switch_has_the_two_columns_equal(tmp_path):
    (rec,) = _records(tmp_path, "g.py", PY_IFS)

    assert (rec.ccn_std, rec.ccn_mod, rec.ccn) == (3, 3, 3)


def test_a_property_named_case_moves_both_columns_exactly_as_it_always_did(tmp_path):
    """lizard judges `case` on the token, not the grammar: `o.case` counts as a
    branch in the standard column and as a switch arm in the modified one. Wrong
    on its face, and shipped, so the single pass has to reproduce it."""
    src = "export function f(o: any) {\n  if (o.case) { return o.case; }\n  return 0;\n}\n"
    (rec,) = _records(tmp_path, "c.ts", src)

    assert (rec.ccn_std, rec.ccn_mod) == (4, 2)


def test_analyze_one_reads_each_file_in_a_single_lizard_pass(tmp_path, monkeypatch):
    """Two passes over a 14k-file corpus cost 10.7 s of the cold run's lizard
    phase; one costs 6.4 s. Counting analyzer runs is what keeps it at one."""
    runs = []
    real = lizard.FileAnalyzer

    class CountingAnalyzer(real):
        def __call__(self, filename):
            runs.append(filename)
            return super().__call__(filename)

    monkeypatch.setattr(lizard, "FileAnalyzer", CountingAnalyzer)
    _records(tmp_path, "d.ts", TS_SWITCH)

    assert len(runs) == 1, f"the file was tokenized {len(runs)} times"


def _two_pass(abs_path: str, rel_path: str):
    """analyze_one as it stood before the single pass, kept as the reference.

    Standard+ND for every column but one, modified for ccn_mod, merged on
    (path, start, end, long_name). Nothing calls this in production any more; it
    is here so the replacement stays provably equal to it.
    """
    std = _raw(abs_path, rel_path, [Cognitive()] + lizard.get_extensions(["ND"]))
    mod = _raw(abs_path, rel_path, lizard.get_extensions(["modified"]))
    return merge_passes(std, mod)


def _raw(abs_path: str, rel_path: str, extensions):
    analysis = lizard.FileAnalyzer(extensions)(abs_path)
    return [RawFn(path=rel_path, long_name=f.long_name, start=f.start_line,
                  end=f.end_line, ccn=f.cyclomatic_complexity, nloc=f.nloc,
                  params=len(f.parameters),
                  nesting=_nesting(rel_path, f),
                  cognitive=getattr(f, "cognitive_complexity", 0) or 0)
            for f in analysis.function_list]


def _nesting(rel_path: str, f) -> int:
    """0.5.0, spec item 15: a Python row's nesting is the depth the cognitive
    pass measured; every other language keeps lizard's ND column. The reference
    spells the rule out rather than importing the production helper."""
    if rel_path.endswith(".py"):
        return getattr(f, "cognitive_nesting", 0) or 0
    return getattr(f, "max_nesting_depth", 0) or 0


def _corpus() -> list[Path]:
    """Every committed source the repo can hand a differential: the fixture repo
    (a 7-case TypeScript switch lives there) and crapkit's own modules."""
    here = Path(__file__).resolve().parent.parent
    fixtures = sorted((here / "fixtures" / "mini_repo").rglob("*.ts"))
    fixtures += sorted((here / "fixtures" / "mini_repo").rglob("*.py"))
    return fixtures + sorted((here.parent / "src" / "crapkit").rglob("*.py"))


def test_the_single_pass_reproduces_the_two_pass_record_for_every_committed_source():
    """The gate reads ccn = min(std, mod), so this equality is the whole licence
    for deleting the second pass. Cognitive is in the comparison on purpose: it
    only holds because the state map stopped keying on id(fn)."""
    files = _corpus()
    assert len(files) > 30, "the differential needs a corpus, not a file"
    split = 0

    for path in files:
        rel = path.name
        _, produced = analyze_one((str(path), rel))
        assert produced == _two_pass(str(path), rel), f"single pass diverged on {path}"
        split += sum(1 for r in produced if r.ccn_mod != r.ccn_std)

    assert split, "no function in the corpus splits the two columns: nothing was proved"


def test_every_column_survives_the_single_pass(tmp_path):
    src = ("export function f(a: number, b: number) {\n"
           "  for (const x of [a, b]) {\n"
           "    if (x > 0 && x < 9) { return x; }\n"
           "  }\n"
           "  return 0;\n"
           "}\n")
    (rec,) = _records(tmp_path, "f.ts", src)

    assert (rec.path, rec.start, rec.end) == ("f.ts", 1, 6)
    assert (rec.nloc, rec.params, rec.nesting, rec.cognitive) == (6, 2, 3, 4)
