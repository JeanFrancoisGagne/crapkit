"""A fresh install turns a cc-only scope into a measured one, as the upgrade guide's procedure says.

A mixed repo: a Python scope `py` under `coverage_optional` and a Go scope
`go`, both cc-only. The README start's steps after `init` seed it: each of the
three ccn-8 Python functions marks 8.0000. The user adds a coveragepy lane to
`py` and drops `coverage_optional`. doctor WARNs and exits 0, verify refuses at
exit 3 naming the scope, its lane and the procedure, and after `crapkit coverage`
the scoped seed raises the 0% and 50% marks to 72 and 16 and counts 2 rises.
`go`'s stamp line and marks keep their bytes, and verify passes.
"""
from __future__ import annotations

from pathlib import Path

from kit import state
from kit.cells import cell
from kit.state import output

PACKET = "metric-stamp-07"
MARKS = "crapkit-ratchet.tsv"
PROCEDURE = "docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one"
COVERED = {"none_run": 0, "half_run": 7, "all_run": 14}


def _function(name: str) -> str:
    body = "".join(f"    if {arg}:\n        return {n}\n" for n, arg in enumerate("abcdefg", 1))
    return f"def {name}(a, b, c, d, e, f, g):\n{body}    return 0\n\n"


STARTS = {name: 1 + 17 * n for n, name in enumerate(COVERED)}
GO = "package main\n\nfunc Route(code int) string {\n" + "".join(
    f"\tif code == {n} {{\n\t\treturn \"{n}\"\n\t}}\n" for n in range(1, 9)) + "\treturn \"\"\n}\n"
# coverage.py's JSON report at 0, 7 and 14 of 14 branches, which no pytest run gives on demand.
MAKE_COV = f'''import json, os
functions = {{}}
for name, hit in {COVERED!r}.items():
    start = {STARTS!r}[name]
    lines = list(range(start + 1, start + 16))
    ran = len(lines) if hit == 14 else (8 if hit else 0)
    functions[name] = {{"start_line": start, "executed_lines": lines[:ran], "missing_lines": lines[ran:],
                        "excluded_lines": [], "summary": {{"covered_lines": ran, "num_statements": 15,
                        "num_branches": 14, "covered_branches": hit, "excluded_lines": 0}}}}
missing = [line for fn in functions.values() for line in fn["missing_lines"]]
os.makedirs(os.path.join(".crapkit", "cov"), exist_ok=True)
with open(os.path.join(".crapkit", "cov", "py.json"), "w", encoding="utf-8") as fh:
    json.dump({{"meta": {{"branch_coverage": True}},
               "files": {{"py/flip.py": {{"functions": functions, "missing_lines": missing}}}}}}, fh)
'''
SCOPES = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "py"\npaths = ["py"]\nlanguages = ["python"]\n{optional}\n'
          '[[scope]]\nname = "go"\npaths = ["go"]\nlanguages = ["go"]\ncoverage_optional = true\n\n')
LANE = ('[[lane]]\nname = "py"\ncommand = "python make_cov.py"\nartifact = ".crapkit/cov/py.json"\n'
        'parser = "coveragepy"\nscopes = ["py"]\n')


def marks(repo: Path, prefix: str) -> list[str]:
    return [line for line in (repo / MARKS).read_text(encoding="utf-8").splitlines(keepends=True)
            if line.startswith(prefix)]


@cell("lin-cc-only-to-measured", channel="pip venv", harness="none",
      scenario="fresh: README start on a mixed repo, Python cc-only beside Go; a coveragepy lane joins the Python "
               "scope: doctor WARN, verify exit 3 names the procedure, coverage, seed --scope py raises 2; verify OK",
      use_cases="upgrade guide, ratchet lifecycle", os="linux", image="core", cadence="nightly")
def test_lin_cc_only_to_measured(box, candidate):
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={candidate.version}")
    repo = box.root / "mixed"
    state.write(repo, {"py/flip.py": "".join(_function(name) for name in COVERED), "go/route.go": GO,
                       ".gitignore": ".crapkit/\nmake_cov.py\n",
                       "crapkit.toml": SCOPES.format(optional="coverage_optional = true\n")})
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    state.commit(box, repo, "a mixed repo, every scope cc-only")
    state.write(repo, {"make_cov.py": MAKE_COV})
    for step in (["doctor"], ["coverage"], ["worklist"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seed")
    go_before = marks(repo, ("#scope\tgo\t", "go/"))
    assert [line.rsplit("\t", 1)[1] for line in marks(repo, "py/")] == ["8.0000\n"] * 3, box.transcript.text()

    state.write(repo, {"crapkit.toml": SCOPES.format(optional="") + LANE})
    doctor = output(box.run(["crapkit", "doctor"], cwd=repo, expect=0))
    assert "py (coverage cc-only to coveragepy=1)" in doctor and PROCEDURE in doctor, box.transcript.text()
    refusal = output(box.run(["crapkit", "verify"], cwd=repo, expect=3))
    assert "ratchet seed --scope py`" in refusal and PROCEDURE in refusal, box.transcript.text()

    box.run(["crapkit", "coverage", "--export", "export.tsv"], cwd=repo, expect=0)
    seed = output(box.run(["crapkit", "ratchet", "seed", "--scope", "py"], cwd=repo, expect=0))

    assert "scope py: added 0, tightened 0, raised 2 (first py/flip.py" in seed, box.transcript.text()
    assert sorted(line.rsplit("\t", 1)[1] for line in marks(repo, "py/")) == ["16.0000\n", "72.0000\n", "8.0000\n"]
    assert marks(repo, ("#scope\tgo\t", "go/")) == go_before
    state.commit(box, repo, "the flipped scope's marks take the measured run")
    state.verify(box, repo)
