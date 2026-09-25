"""The Hypothesis strategy equivalent.tsv names for crapkit/mutate.py mutants.

`source_texts()` draws (text, None, language) argument tuples for
crapkit.mutate.file_mutants: short runs of the tokens the angle and operator
rules read (comparisons, brackets, `;`, type contexts, a type word per
language, `..<`, `<-`, strings), in every language the mutate tables serve.
tools/accuracy/mutation.py `equivalence_evidence` runs an original and a mutant
on 10,000 of them.
"""
from __future__ import annotations

from hypothesis import strategies as st

LANGUAGES = ("typescript", "tsx", "java", "cpp", "c", "objectivec", "rust", "swift", "go", "python")
TYPE_WORD = {"typescript": "number", "tsx": "number", "java": "int", "cpp": "int", "c": "int",
             "objectivec": "int", "rust": "u8", "swift": "Int", "go": "int", "python": "int"}
TOKENS = ("a", "b", "T", "Foo", "<", ">", "<=", ">=", "==", "!=", "&&", "||", "true", "false",
          ";", "{", "}", "(", ")", "[", "]", ":", "::", "->", "=>", "as", "new", ",", "=", ".",
          "?", "0", "..<", "...", "<-", ":=", "'x > y'", "// c > d", "and", "or", "True")


@st.composite
def _text(draw, language: str) -> str:
    words = draw(st.lists(st.sampled_from((*TOKENS, TYPE_WORD[language])), min_size=1, max_size=12))
    joiner = draw(st.sampled_from((" ", "")))
    return joiner.join(words) + "\n"


def source_texts():
    """(text, changed lines, language) for file_mutants."""
    return st.sampled_from(LANGUAGES).flatmap(
        lambda language: st.tuples(_text(language), st.none(), st.just(language)))
