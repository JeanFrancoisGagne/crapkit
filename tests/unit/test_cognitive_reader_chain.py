"""Cognitive complexity must survive the two lizard readers that drain the stream.

lizard's SwiftReplaceLabel.preprocess RETURNS a list where the other seven
preprocessors YIELD: it runs list() over its input, so every extension ahead of
`preprocessing` is drained to exhaustion before lizard has split the file into
functions. An extension at index 0 counts the whole file against one placeholder
FunctionInfo, and every real function comes out at 0. SwiftReader and
KotlinReader are the only two of lizard's 27 readers that inherit it.

The fix cannot be a global reorder. Python's rules read the whitespace tokens
that lizard's own `preprocessing` strips, so moving the extension behind
`preprocessing` for every reader fixes Swift and Kotlin (0 -> 10) and breaks
Python (10 -> 6). Both directions are measured here on one 6-branch shape, so a
change that buys Swift with Python's number fails this file.

The shape below hand-counts to 10 under the Sonar spec: if +1, for +2 (nested
once), the inner if +3 (nested twice), && +1, else +1, while +2. Its ccn is 6
in all four languages, and ccn was never affected by the drain.
"""
from crapkit.analyze import ANALYSIS_VERSION, analyze_source

# Cognitive of the shape below, measured on Python and TypeScript before the
# two-chain fix and unchanged by it. Swift and Kotlin read 0 before it.
EXPECTED_COGNITIVE = 10
EXPECTED_CCN = 6

PY = '''def probe(a, b):
    if a > 0 and b > 0:
        for i in range(b):
            if i == a:
                return i
    else:
        while b > 0:
            b -= 1
    return 0
'''

TS = '''export function probe(a: number, b: number): number {
  if (a > 0 && b > 0) {
    for (let i = 0; i < b; i++) {
      if (i === a) {
        return i;
      }
    }
  } else {
    while (b > 0) {
      b -= 1;
    }
  }
  return 0;
}
'''

SWIFT = '''func probe(a: Int, b: Int) -> Int {
    var b = b
    if a > 0 && b > 0 {
        for i in 0..<b {
            if i == a {
                return i
            }
        }
    } else {
        while b > 0 {
            b -= 1
        }
    }
    return 0
}
'''

KOTLIN = '''fun probe(a: Int, b: Int): Int {
    var b = b
    if (a > 0 && b > 0) {
        for (i in 0..b) {
            if (i == a) {
                return i
            }
        }
    } else {
        while (b > 0) {
            b -= 1
        }
    }
    return 0
}
'''

SOURCES = {"probe.py": PY, "probe.ts": TS, "probe.swift": SWIFT, "probe.kt": KOTLIN}


def _probe(name: str, code: str | None = None):
    (record,) = analyze_source(name, SOURCES[name] if code is None else code)
    return record


def test_python_keeps_its_cognitive_score_when_swift_is_fixed():
    """The direction a naive global reorder breaks: Python drops 10 -> 6 because
    `preprocessing` strips the whitespace tokens its indent rules read."""
    assert _probe("probe.py").cognitive == EXPECTED_COGNITIVE


def test_typescript_keeps_its_cognitive_score():
    assert _probe("probe.ts").cognitive == EXPECTED_COGNITIVE


def test_swift_scores_the_same_cognitive_as_the_identical_python_function():
    """0 before the fix: SwiftReader drains the stream before the extension runs."""
    assert _probe("probe.swift").cognitive == EXPECTED_COGNITIVE


def test_kotlin_scores_the_same_cognitive_as_the_identical_python_function():
    """KotlinReader is the second and last reader inheriting the draining
    preprocessor, so it is fixed by the same suffix branch."""
    assert _probe("probe.kt").cognitive == EXPECTED_COGNITIVE


def test_kotlin_script_files_take_the_same_chain_as_kotlin():
    """.kts routes to the same KotlinReader, so it must route to the same chain."""
    assert _probe("probe.kts", KOTLIN).cognitive == EXPECTED_COGNITIVE


def test_the_drain_never_touched_ccn_or_nesting():
    """Only the cognitive column was wrong: ccn comes from lizard's own counter,
    which sits downstream of the drain and always saw the tokens, and so does
    nesting for the three brace languages. Python's nesting comes from the
    cognitive pass itself since 0.5.0, and PythonReader drains nothing."""
    for name in SOURCES:
        record = _probe(name)
        assert record.ccn == EXPECTED_CCN, name
        assert record.nesting >= 3, name


def test_analysis_version_invalidates_caches_written_before_the_fix():
    """Cached cognitive values for .swift/.kt/.kts are wrong at version 3, and a
    cache keys on content plus this fingerprint, so 3 must not be reusable."""
    assert ANALYSIS_VERSION > 3
