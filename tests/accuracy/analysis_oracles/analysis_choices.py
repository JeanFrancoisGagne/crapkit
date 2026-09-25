"""crapkit's documented readings of the Sonar paper, as the counter's Choices.

oracles/py_sonar.py counts cognitive complexity and nesting depth with every
place the paper can be read two ways named as a Choices field. PAPER is the
paper's reading; CRAPKIT below is crapkit's, and every field where the two
differ is a rulings.tsv definition row, pinned by a hand probe:

- ternary_nests (AO-N-PY-TERNARY): a conditional expression adds no level;
- lambda_nests (AO-PY-COG-LAMBDA, AO-N-PY-LAMBDA): a lambda adds no level;
- match_nests (AO-N-PY-MATCH): match adds no level (docs/agent-json.md
  "nesting" lists it among the statements that add none);
- element_nests (AO-PY-COG-ELEMENT): a comprehension's element is read outside
  its loops;
- filter_nests (AO-PY-COG-FILTER): a comprehension's if adds no level for a
  later for;
- iter_nests (AO-PY-COG-ITER): a comprehension's iterable is read inside its
  own loop;
- condition_nests (AO-PY-COG-CONDITION, AO-N-PY-CONDITION): an if's condition
  is read at its body's level.

No crapkit import: the values are the rulings rows', which were measured
against hand probes.
"""
from __future__ import annotations

from dataclasses import fields

from accuracy.analysis_oracles.oracles import py_sonar

CRAPKIT = py_sonar.Choices(ternary_nests=False, lambda_nests=False, match_nests=False,
                           element_nests=False, filter_nests=False, iter_nests=True,
                           condition_nests=True)
RULINGS = {
    "ternary_nests": ("AO-N-PY-TERNARY",),
    "lambda_nests": ("AO-PY-COG-LAMBDA", "AO-N-PY-LAMBDA"),
    "match_nests": ("AO-N-PY-MATCH",),
    "element_nests": ("AO-PY-COG-ELEMENT",),
    "filter_nests": ("AO-PY-COG-FILTER",),
    "iter_nests": ("AO-PY-COG-ITER",),
    "condition_nests": ("AO-PY-COG-CONDITION", "AO-N-PY-CONDITION"),
}


def departures() -> list[str]:
    """The Choices fields where crapkit's reading is not the paper's."""
    return [field.name for field in fields(py_sonar.Choices)
            if getattr(CRAPKIT, field.name) != getattr(py_sonar.PAPER, field.name)]
